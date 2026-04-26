"""
Duffel — https://duffel.com/docs
Uses the Duffel REST API directly via `requests` (no SDK dependency).
Credentials: DUFFEL_ACCESS_TOKEN in .env
Sandbox tokens start with "duffel_test_".
"""
import os
import re
from datetime import datetime

import requests

from ..config import SearchConfig
from ..models import FlightOffer, Itinerary, Segment

BASE_URL = "https://api.duffel.com"
API_VERSION = "v1"

_CABIN_MAP = {
    "economy": "economy",
    "premium_economy": "premium_economy",
    "business": "business",
    "first": "first",
}


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Duffel-Version": API_VERSION,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _parse_duration(iso: str) -> int:
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?", iso or "")
    if not match:
        return 0
    return int(match.group(1) or 0) * 60 + int(match.group(2) or 0)


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _parse_segment(seg: dict) -> Segment:
    carrier = seg.get("operating_carrier") or seg.get("marketing_carrier") or {}
    code = carrier.get("iata_code", "??")
    name = carrier.get("name", code)
    flight_num = f"{code}{seg.get('operating_carrier_flight_number', '')}"
    return Segment(
        origin=seg["origin"]["iata_code"],
        destination=seg["destination"]["iata_code"],
        departure=_parse_dt(seg["departing_at"]),
        arrival=_parse_dt(seg["arriving_at"]),
        airline=code,
        airline_name=name,
        flight_number=flight_num,
        duration_minutes=_parse_duration(seg.get("duration", "")),
    )


def _parse_slice(slc: dict) -> Itinerary:
    return Itinerary(segments=[_parse_segment(s) for s in slc["segments"]])


def _parse_offer(raw: dict, currency: str, adults: int) -> FlightOffer:
    slices = raw["slices"]
    outbound = _parse_slice(slices[0])
    inbound = _parse_slice(slices[1]) if len(slices) > 1 else None

    price = float(raw["total_amount"])
    offer_currency = raw.get("total_currency", currency)

    cabin = "economy"
    try:
        pax_details = slices[0]["segments"][0].get("passengers", [{}])
        cabin = pax_details[0].get("cabin_class", "economy")
    except (IndexError, KeyError):
        pass

    return FlightOffer(
        id=raw["id"],
        provider="duffel",
        outbound=outbound,
        inbound=inbound,
        price=price,
        currency=offer_currency,
        cabin_class=cabin,
        adults=adults,
        raw=raw,
    )


def _build_passengers(cfg: SearchConfig) -> list:
    passengers = [{"type": "adult"} for _ in range(cfg.adults)]
    # Use approximate ages since config only tracks counts
    passengers += [{"age": 7} for _ in range(cfg.children)]
    passengers += [{"age": 1} for _ in range(cfg.infants)]
    return passengers


def search(cfg: SearchConfig) -> list:
    token = os.getenv("DUFFEL_ACCESS_TOKEN", "")
    if not token:
        raise RuntimeError(
            "Set DUFFEL_ACCESS_TOKEN in .env  "
            "(register at https://duffel.com)"
        )

    if not cfg.origin.iata or not cfg.destination.iata:
        raise ValueError("Duffel requires IATA codes for both origin and destination.")

    cabin = _CABIN_MAP.get(cfg.cabin_class, "economy") if cfg.cabin_class else "economy"
    passengers = _build_passengers(cfg)
    hdrs = _headers(token)
    all_offers: list = []

    for out_date in cfg.outbound_dates:
        for in_date in (cfg.inbound_dates or [None]):
            slices = [
                {
                    "origin": cfg.origin.iata,
                    "destination": cfg.destination.iata,
                    "departure_date": out_date.isoformat(),
                }
            ]
            if in_date:
                slices.append({
                    "origin": cfg.destination.iata,
                    "destination": cfg.origin.iata,
                    "departure_date": in_date.isoformat(),
                })

            body = {
                "data": {
                    "slices": slices,
                    "passengers": passengers,
                    "cabin_class": cabin,
                }
            }

            resp = requests.post(
                f"{BASE_URL}/air/offer_requests?return_offers=true",
                headers=hdrs,
                json=body,
                timeout=30,
            )

            if not resp.ok:
                raise RuntimeError(f"Duffel API error {resp.status_code}: {resp.text[:300]}")

            data = resp.json().get("data", {})
            raw_offers = data.get("offers", [])

            for raw_offer in raw_offers:
                try:
                    all_offers.append(_parse_offer(raw_offer, cfg.currency, cfg.adults))
                except Exception:
                    continue

    return all_offers
