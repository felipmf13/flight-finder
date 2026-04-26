"""
Google Flights — primp TLS-fingerprinted requests + fast-flights HTML parser.

Uses Google's internal protobuf/HTML endpoint (same one the website uses).
The SOCS=CAI cookie bypasses the GDPR consent wall without requiring a browser.
No API key required.

Round-trip handling:
  Searches each leg as a separate one-way. The cheapest available return flight
  for each inbound date is paired with every outbound option. Displayed price is
  the per-person outbound + per-person return combined.

Dependencies (installed via fast-flights in requirements.txt):
  primp, selectolax, protobuf

Optional .env vars: none.
"""
import re
import time
import uuid
from datetime import datetime, timedelta
from typing import Optional

from ..config import SearchConfig
from ..models import FlightOffer, Itinerary, Segment


def search(cfg: SearchConfig) -> list:
    try:
        import primp
        from fast_flights.filter import TFSData
        from fast_flights import FlightData, Passengers
        from fast_flights.core import parse_response
    except ImportError:
        raise RuntimeError("fast-flights not installed. Run: pip install fast-flights")

    if not cfg.origin.iata or not cfg.destination.iata:
        raise ValueError("Google Flights provider requires IATA codes for both origin and destination.")

    seat = _seat_class(cfg.cabin_class)
    passengers = Passengers(
        adults=cfg.adults,
        children=cfg.children,
        infants_in_seat=0,
        infants_on_lap=cfg.infants,
    )

    def _fetch(from_iata: str, to_iata: str, date):
        tfs = TFSData.from_interface(
            flight_data=[FlightData(date=date.strftime("%Y-%m-%d"), from_airport=from_iata, to_airport=to_iata)],
            trip="one-way",
            passengers=passengers,
            seat=seat,
            max_stops=cfg.max_stops,
        )
        params = {"tfs": tfs.as_b64().decode(), "hl": "en", "tfu": "EgQIABABIgA", "curr": cfg.currency}
        client = primp.Client(impersonate="chrome_126", verify=False)
        resp = client.get(
            "https://www.google.com/travel/flights",
            params=params,
            cookies={"SOCS": "CAI"},
        )
        return parse_response(resp)

    # Pre-fetch the cheapest inbound option per inbound date (one request per date).
    # If an inbound fetch fails we still return outbound-only results rather than nothing.
    inbound_best: dict = {}
    inbound_fetch_errors: list = []
    for in_date in (cfg.inbound_dates or []):
        try:
            result = _fetch(cfg.destination.iata, cfg.origin.iata, in_date)
            if result.flights:
                best = min(result.flights, key=lambda f: _parse_price(f.price))
                itin = _build_itinerary(best, in_date, cfg.destination.iata, cfg.origin.iata)
                if itin:
                    inbound_best[in_date] = (best, itin)
        except Exception as e:
            inbound_fetch_errors.append(str(e))
        time.sleep(1)  # small courtesy delay between requests

    all_offers: list = []
    seen: set = set()

    for out_date in cfg.outbound_dates:
        try:
            result = _fetch(cfg.origin.iata, cfg.destination.iata, out_date)
        except Exception as e:
            raise RuntimeError(f"Google Flights search failed ({out_date}): {e}")
        time.sleep(1)

        for flight in result.flights:
            out_itin = _build_itinerary(flight, out_date, cfg.origin.iata, cfg.destination.iata)
            if not out_itin:
                continue

            out_price = _parse_price(flight.price)
            if not out_price:
                continue

            if not cfg.inbound_dates or not inbound_best:
                # One-way (or inbound fetch failed entirely — degrade gracefully)
                offer = _make_offer(out_itin, None, out_price, cfg)
                _add(offer, seen, all_offers)
            else:
                for in_date in cfg.inbound_dates:
                    if in_date not in inbound_best:
                        continue
                    _, in_itin = inbound_best[in_date]
                    in_price = _parse_price(inbound_best[in_date][0].price)
                    offer = _make_offer(out_itin, in_itin, out_price + in_price, cfg)
                    _add(offer, seen, all_offers)

    if inbound_fetch_errors and not inbound_best:
        raise RuntimeError(
            f"Google Flights: inbound search failed — {inbound_fetch_errors[0]}. "
            "If you're seeing this repeatedly, wait a minute before retrying."
        )

    return all_offers


def _add(offer, seen: set, all_offers: list) -> None:
    if offer is None:
        return
    key = (offer.outbound.departure, offer.price)
    if key not in seen:
        seen.add(key)
        all_offers.append(offer)


def _make_offer(out_itin: Itinerary, in_itin: Optional[Itinerary], price_per_person: float, cfg: SearchConfig) -> FlightOffer:
    return FlightOffer(
        id=str(uuid.uuid4()),
        provider="google_flights",
        outbound=out_itin,
        inbound=in_itin,
        price=price_per_person * cfg.adults,
        currency=cfg.currency,
        cabin_class=cfg.cabin_class or "economy",
        adults=cfg.adults,
        price_available=True,
        raw={},
    )


def _build_itinerary(flight, date, origin_iata: str, dest_iata: str) -> Optional[Itinerary]:
    try:
        dep_dt = _parse_dt(flight.departure, date.year)
        arr_dt = _parse_dt(flight.arrival, date.year)
        duration = _parse_duration(flight.duration)
        stops = flight.stops if isinstance(flight.stops, int) else 0
        airline_name = (flight.name or "Unknown").split("·")[0].strip() or "Unknown"
        code = _airline_code(airline_name)
        segments = _build_segments(stops, origin_iata, dest_iata, dep_dt, arr_dt, duration, code, airline_name)
        return Itinerary(segments=segments)
    except Exception:
        return None


def _build_segments(stops: int, origin: str, dest: str, dep_dt: datetime, arr_dt: datetime,
                    total_min: int, code: str, name: str) -> list:
    if stops == 0:
        return [Segment(
            origin=origin, destination=dest,
            departure=dep_dt, arrival=arr_dt,
            airline=code, airline_name=name,
            flight_number=f"{code}????",
            duration_minutes=total_min,
        )]
    # We know the stop count but not intermediate airports; split duration evenly.
    leg_min = total_min // (stops + 1)
    segments = []
    cur = dep_dt
    for i in range(stops + 1):
        is_last = i == stops
        seg_arr = arr_dt if is_last else cur + timedelta(minutes=leg_min)
        segments.append(Segment(
            origin=origin if i == 0 else "???",
            destination=dest if is_last else "???",
            departure=cur,
            arrival=seg_arr,
            airline=code, airline_name=name,
            flight_number=f"{code}????",
            duration_minutes=leg_min,
        ))
        cur = seg_arr + timedelta(minutes=45)
    return segments


def _parse_dt(s: str, year: int) -> datetime:
    # Google uses regular or narrow/non-breaking spaces depending on the date.
    # Normalise all Unicode whitespace to plain ASCII space before parsing.
    clean = s.replace("\u202f", " ").replace("\xa0", " ")
    clean = re.sub(r"\s+on\s+", " ", clean).strip()
    for fmt in ("%I:%M %p %a, %b %d", "%I:%M %p %b %d"):
        try:
            return datetime.strptime(clean, fmt).replace(year=year)
        except ValueError:
            continue
    raise ValueError(f"Cannot parse datetime string: {s!r}")

def _parse_duration(s: str) -> int:
    h = re.search(r"(\d+)\s*hr", s)
    m = re.search(r"(\d+)\s*min", s)
    return (int(h.group(1)) if h else 0) * 60 + (int(m.group(1)) if m else 0)


def _parse_price(s: str) -> float:
    digits = re.sub(r"[^\d.]", "", (s or "").replace(",", ""))
    return float(digits) if digits else 0.0


def _seat_class(cabin_str: str) -> str:
    return {
        "economy": "economy",
        "premium_economy": "premium-economy",
        "business": "business",
        "first": "first",
    }.get((cabin_str or "economy").lower(), "economy")


_AIRLINE_CODES = {
    "vueling": "VY", "ryanair": "FR", "iberia": "IB", "british airways": "BA",
    "easyjet": "U2", "lufthansa": "LH", "air france": "AF", "klm": "KL",
    "american airlines": "AA", "delta": "DL", "united": "UA", "wizz air": "W6",
    "norwegian": "DY", "tap air portugal": "TP", "swiss": "LX",
    "brussels airlines": "SN", "finnair": "AY", "turkish airlines": "TK",
    "aer lingus": "EI", "transavia": "TO", "volotea": "V7", "binter": "NT",
}


def _airline_code(name: str) -> str:
    key = name.lower()
    for partial, code in _AIRLINE_CODES.items():
        if partial in key:
            return code
    return (name[:2].upper() if len(name) >= 2 else "??")
