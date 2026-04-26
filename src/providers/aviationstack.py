"""
AviationStack — https://aviationstack.com
Free tier: 100 requests/month (real-time data only).
Provides flight schedule and status data. Does NOT provide ticket prices.
Offers returned have price_available=False and are shown as schedule reference.

Limitation: the free tier returns real-time / same-day data only.
            Future flight schedules require a paid plan.

Credentials: AVIATIONSTACK_API_KEY in .env
"""
import os
import uuid
from datetime import datetime

import requests

from ..config import SearchConfig
from ..models import FlightOffer, Itinerary, Segment

BASE_URL = "http://api.aviationstack.com/v1"


def search(cfg: SearchConfig) -> list:
    api_key = os.getenv("AVIATIONSTACK_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "Set AVIATIONSTACK_API_KEY in .env  "
            "(register at https://aviationstack.com — free tier: 100 req/month)"
        )

    if not cfg.origin.iata or not cfg.destination.iata:
        raise ValueError("AviationStack requires IATA codes for both origin and destination.")

    all_offers: list = []

    for out_date in cfg.outbound_dates:
        params = {
            "access_key": api_key,
            "dep_iata": cfg.origin.iata,
            "arr_iata": cfg.destination.iata,
            "flight_date": out_date.isoformat(),
            "limit": 100,
        }
        # API supports filtering by a single airline code
        if cfg.include_airlines:
            params["airline_iata"] = cfg.include_airlines[0]

        resp = requests.get(f"{BASE_URL}/flights", params=params, timeout=15)

        if not resp.ok:
            raise RuntimeError(f"AviationStack error {resp.status_code}: {resp.text[:200]}")

        body = resp.json()
        if "error" in body:
            err = body["error"]
            raise RuntimeError(f"AviationStack API error: {err.get('info', err)}")

        for flight in body.get("data", []):
            try:
                offer = _parse_flight(flight, cfg)
                if offer:
                    all_offers.append(offer)
            except Exception:
                continue

    return all_offers


def _parse_flight(flight: dict, cfg: SearchConfig) -> FlightOffer | None:
    dep = flight.get("departure") or {}
    arr = flight.get("arrival") or {}
    airline = flight.get("airline") or {}
    fl = flight.get("flight") or {}

    dep_iata = dep.get("iata", "")
    arr_iata = arr.get("iata", "")
    if not dep_iata or not arr_iata:
        return None

    # Prefer scheduled times; fall back to estimated/actual
    dep_str = dep.get("scheduled") or dep.get("estimated") or dep.get("actual")
    arr_str = arr.get("scheduled") or arr.get("estimated") or arr.get("actual")
    if not dep_str or not arr_str:
        return None

    dep_dt = datetime.fromisoformat(dep_str.replace("Z", "+00:00"))
    arr_dt = datetime.fromisoformat(arr_str.replace("Z", "+00:00"))
    duration = max(int((arr_dt - dep_dt).total_seconds() / 60), 0)

    airline_code = airline.get("iata", "??")
    airline_name = airline.get("name", airline_code)
    flight_num = fl.get("iata") or f"{airline_code}????"

    segment = Segment(
        origin=dep_iata,
        destination=arr_iata,
        departure=dep_dt,
        arrival=arr_dt,
        airline=airline_code,
        airline_name=airline_name,
        flight_number=flight_num,
        duration_minutes=duration,
    )

    return FlightOffer(
        id=flight_num or str(uuid.uuid4()),
        provider="aviationstack",
        outbound=Itinerary(segments=[segment]),
        inbound=None,
        price=0.0,
        currency=cfg.currency,
        cabin_class=cfg.cabin_class or "economy",
        adults=cfg.adults,
        price_available=False,
        raw=flight,
    )
