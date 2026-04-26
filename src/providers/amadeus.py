"""
Amadeus for Developers — https://developers.amadeus.com
Uses the official `amadeus` Python SDK (pip install amadeus).
Credentials: AMADEUS_CLIENT_ID, AMADEUS_CLIENT_SECRET in .env
Default environment: test (synthetic data, generous free quota).
"""
import os
import re

from ..config import SearchConfig
from ..models import FlightOffer, Itinerary, Segment

_CABIN_MAP = {
    "economy": "ECONOMY",
    "premium_economy": "PREMIUM_ECONOMY",
    "business": "BUSINESS",
    "first": "FIRST",
}


def _parse_duration(iso: str) -> int:
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?", iso or "")
    if not match:
        return 0
    return int(match.group(1) or 0) * 60 + int(match.group(2) or 0)


def _parse_segment(seg: dict, carriers: dict) -> Segment:
    dep = seg["departure"]
    arr = seg["arrival"]
    code = seg["carrierCode"]
    return Segment(
        origin=dep["iataCode"],
        destination=arr["iataCode"],
        departure=_parse_dt(dep["at"]),
        arrival=_parse_dt(arr["at"]),
        airline=code,
        airline_name=carriers.get(code, code),
        flight_number=f"{code}{seg['number']}",
        duration_minutes=_parse_duration(seg.get("duration", "")),
    )


def _parse_dt(s: str):
    from datetime import datetime
    # Amadeus returns ISO 8601 with optional offset, e.g. "2026-07-10T07:10:00"
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _parse_itinerary(itin: dict, carriers: dict) -> Itinerary:
    return Itinerary(segments=[_parse_segment(s, carriers) for s in itin["segments"]])


def _parse_offer(raw: dict, carriers: dict, currency: str, adults: int) -> FlightOffer:
    itins = raw["itineraries"]
    outbound = _parse_itinerary(itins[0], carriers)
    inbound = _parse_itinerary(itins[1], carriers) if len(itins) > 1 else None

    price = float(raw["price"]["grandTotal"])

    cabin = "economy"
    try:
        cabin = (
            raw["travelerPricings"][0]["fareDetailsBySegment"][0]
            .get("cabin", "ECONOMY")
            .lower()
        )
    except (KeyError, IndexError):
        pass

    return FlightOffer(
        id=raw["id"],
        provider="amadeus",
        outbound=outbound,
        inbound=inbound,
        price=price,
        currency=currency,
        cabin_class=cabin,
        adults=adults,
        raw=raw,
    )


def search(cfg: SearchConfig) -> list:
    try:
        from amadeus import Client, ResponseError
    except ImportError:
        raise RuntimeError("Run: pip install amadeus")

    client_id = os.getenv("AMADEUS_CLIENT_ID", "")
    client_secret = os.getenv("AMADEUS_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise RuntimeError(
            "Set AMADEUS_CLIENT_ID and AMADEUS_CLIENT_SECRET in .env  "
            "(register at https://developers.amadeus.com)"
        )

    if not cfg.origin.iata or not cfg.destination.iata:
        raise ValueError("Amadeus requires IATA codes for both origin and destination.")

    amadeus = Client(client_id=client_id, client_secret=client_secret)
    cabin = _CABIN_MAP.get(cfg.cabin_class) if cfg.cabin_class else None

    all_offers: list = []

    for out_date in cfg.outbound_dates:
        params = {
            "originLocationCode": cfg.origin.iata,
            "destinationLocationCode": cfg.destination.iata,
            "departureDate": out_date.isoformat(),
            "adults": cfg.adults,
            "currencyCode": cfg.currency,
            "max": 50,
        }
        if cfg.children:
            params["children"] = cfg.children
        if cfg.infants:
            params["infants"] = cfg.infants
        if cabin:
            params["travelClass"] = cabin
        if cfg.max_stops is not None:
            params["nonStop"] = "true" if cfg.max_stops == 0 else "false"
        if cfg.include_airlines:
            params["includedAirlineCodes"] = ",".join(cfg.include_airlines)
        if cfg.exclude_airlines:
            params["excludedAirlineCodes"] = ",".join(cfg.exclude_airlines)
        if cfg.max_price_per_person:
            params["maxPrice"] = int(cfg.max_price_per_person * cfg.adults)

        for in_date in (cfg.inbound_dates or [None]):
            p = dict(params)
            if in_date:
                p["returnDate"] = in_date.isoformat()
            try:
                resp = amadeus.shopping.flight_offers_search.get(**p)
                carriers = resp.result.get("dictionaries", {}).get("carriers", {})
                for raw_offer in resp.data:
                    try:
                        all_offers.append(_parse_offer(raw_offer, carriers, cfg.currency, cfg.adults))
                    except Exception:
                        continue
            except ResponseError as e:
                raise RuntimeError(f"Amadeus API error: {e.response.body}") from e

    return all_offers
