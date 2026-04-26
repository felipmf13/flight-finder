"""
Skyscanner — reverse-engineered Android JSON API
Source: https://github.com/irrisolto/skyscanner

How it works:
  Targets Skyscanner's internal mobile API (same one the Android app uses) rather than
  scraping HTML. Uses curl_cffi for JA3/TLS fingerprinting and a built-in PerimeterX
  challenge solver — no headless browser required, no proxies needed for casual use.

Install:
  pip install "git+https://github.com/irrisolto/skyscanner.git" curl_cffi orjson

Optional .env vars:
  SKYSCANNER_LOCALE  — e.g. "en-GB" (default), "es-ES", "en-US"
  SKYSCANNER_MARKET  — e.g. "UK" (default), "ES", "US"
  PROXY_URL          — http://user:pass@host:port  (for repeated runs at scale)

Caveats:
  - Reverse-engineered; Skyscanner may change their API without notice.
  - If you hit CAPTCHA errors repeatedly, wait a few minutes or add PROXY_URL.
  - price.raw from Skyscanner is the total for all passengers in the search.
"""
import os
from datetime import datetime
from typing import Optional

from ..config import SearchConfig
from ..models import FlightOffer, Itinerary, Segment


def search(cfg: SearchConfig) -> list:
    try:
        from skyscanner import SkyScanner
        from skyscanner.types import CabinClass
    except ImportError:
        raise RuntimeError(
            "Skyscanner library not found. "
            "Ensure the skyscanner/ directory is present at the project root "
            "and run: pip install curl_cffi typeguard orjson"
        )

    locale = os.getenv("SKYSCANNER_LOCALE", "en-GB")
    market = os.getenv("SKYSCANNER_MARKET", "UK")
    proxy = os.getenv("PROXY_URL") or None

    scanner_kwargs = dict(locale=locale, currency=cfg.currency, market=market)
    if proxy:
        scanner_kwargs["proxies"] = {"http": proxy, "https": proxy}

    scanner = SkyScanner(**scanner_kwargs)

    origin_airport = _resolve_airport(scanner, cfg.origin.iata, cfg.origin.city)
    dest_airport = _resolve_airport(scanner, cfg.destination.iata, cfg.destination.city)
    cabin = _cabin_class(CabinClass, cfg.cabin_class)
    child_ages = [7] * cfg.children + [1] * cfg.infants  # approx ages, library needs a list

    all_offers: list = []

    for out_date in cfg.outbound_dates:
        dep_dt = datetime(out_date.year, out_date.month, out_date.day)

        for in_date in (cfg.inbound_dates or [None]):
            ret_dt = datetime(in_date.year, in_date.month, in_date.day) if in_date else None

            try:
                response = scanner.get_flight_prices(
                    origin=origin_airport,
                    destination=dest_airport,
                    depart_date=dep_dt,
                    return_date=ret_dt,
                    adults=cfg.adults,
                    cabinClass=cabin,
                    childAges=child_ages,
                )
                all_offers.extend(_parse_response(response, cfg))
            except Exception as e:
                if "BannedWithCaptcha" in type(e).__name__:
                    raise RuntimeError(
                        "Skyscanner returned a CAPTCHA challenge. "
                        "Wait a few minutes and retry, or set PROXY_URL in .env."
                    )
                raise

    return all_offers


def _resolve_airport(scanner, iata: str, city: str):
    if iata:
        try:
            airport = scanner.get_airport_by_code(iata)
            if airport:
                return airport
        except Exception:
            pass
    if city:
        results = scanner.search_airports(city)
        if results:
            return results[0]
    raise ValueError(
        f"Could not resolve airport — iata={iata!r}, city={city!r}. "
        "Check spelling or provide a valid IATA code."
    )


def _cabin_class(CabinClass, cabin_str: str):
    mapping = {
        "economy": "ECONOMY",
        "premium_economy": "PREMIUM_ECONOMY",
        "business": "BUSINESS",
        "first": "FIRST",
    }
    attr = mapping.get((cabin_str or "economy").lower(), "ECONOMY")
    return getattr(CabinClass, attr, CabinClass.ECONOMY)


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def _parse_response(response, cfg: SearchConfig) -> list:
    data = response.json
    if not data or "itineraries" not in data:
        return []

    itins = data["itineraries"]
    legs_by_id = {leg["id"]: leg for leg in itins.get("legs", [])}

    offers = []
    seen: set = set()

    for bucket in itins.get("buckets", []):
        for item in bucket.get("items", []):
            item_id = item.get("id", "")
            if item_id in seen:
                continue
            seen.add(item_id)
            try:
                offer = _parse_item(item, legs_by_id, cfg)
                if offer:
                    offers.append(offer)
            except Exception:
                continue

    return offers


def _parse_item(item: dict, legs_by_id: dict, cfg: SearchConfig) -> Optional[FlightOffer]:
    price_data = item.get("price", {})
    price_raw = price_data.get("raw")
    if not price_raw:
        return None

    leg_ids = item.get("legs", [])
    if not leg_ids:
        return None

    outbound_leg = legs_by_id.get(leg_ids[0])
    if not outbound_leg:
        return None
    inbound_leg = legs_by_id.get(leg_ids[1]) if len(leg_ids) > 1 else None

    return FlightOffer(
        id=item.get("id", ""),
        provider="skyscanner",
        outbound=_parse_leg(outbound_leg),
        inbound=_parse_leg(inbound_leg) if inbound_leg else None,
        price=float(price_raw),  # Skyscanner price.raw = total for all passengers
        currency=cfg.currency,
        cabin_class=cfg.cabin_class or "economy",
        adults=cfg.adults,
        price_available=True,
        raw=item,
    )


def _parse_leg(leg: dict) -> Itinerary:
    marketing = (leg.get("carriers") or {}).get("marketing") or [{}]
    primary = marketing[0]
    primary_code = primary.get("id", "??")
    primary_name = primary.get("name", primary_code)

    segments = []
    for seg in (leg.get("segments") or []):
        carrier = seg.get("marketingCarrier") or primary
        code = carrier.get("id", primary_code)
        name = carrier.get("name", primary_name)
        segments.append(Segment(
            origin=seg.get("origin", {}).get("displayCode", "???"),
            destination=seg.get("destination", {}).get("displayCode", "???"),
            departure=datetime.fromisoformat(seg["departure"]),
            arrival=datetime.fromisoformat(seg["arrival"]),
            airline=code,
            airline_name=name,
            flight_number=f"{code}{seg.get('flightNumber', '????')}",
            duration_minutes=int(seg.get("durationInMinutes", 0)),
        ))

    if not segments:
        # Leg has no segment breakdown — build a single segment from leg-level data
        segments.append(Segment(
            origin=leg.get("origin", {}).get("displayCode", "???"),
            destination=leg.get("destination", {}).get("displayCode", "???"),
            departure=datetime.fromisoformat(leg["departure"]),
            arrival=datetime.fromisoformat(leg["arrival"]),
            airline=primary_code,
            airline_name=primary_name,
            flight_number=f"{primary_code}????",
            duration_minutes=int(leg.get("durationInMinutes", 0)),
        ))

    return Itinerary(segments=segments)
