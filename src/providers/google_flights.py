"""
Google Flights — primp TLS-fingerprinted requests + aria-label HTML parser.

Uses Google's internal protobuf/HTML endpoint (same one the website uses).
The SOCS=CAI cookie bypasses the GDPR consent wall without requiring a browser.
No API key required.

Parsing strategy: extracts all flight details from the accessibility aria-label
attribute on the flight row link. This is more stable than CSS class selectors,
which Google changes frequently via A/B testing.

Round-trip handling:
  Searches each leg as a separate one-way. The cheapest available return flight
  for each inbound date is paired with every outbound option. Displayed price is
  the per-person outbound + per-person return combined.

Dependencies (installed via fast-flights in requirements.txt):
  primp, selectolax, protobuf
"""
import re
import time
import uuid
from datetime import date, datetime, timedelta
from typing import Optional

from ..config import SearchConfig
from ..models import FlightOffer, Itinerary, Segment


def search(cfg: SearchConfig) -> list:
    try:
        import primp
        from fast_flights.filter import TFSData
        from fast_flights import FlightData, Passengers
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

    def _fetch(from_iata: str, to_iata: str, d: date) -> str:
        tfs = TFSData.from_interface(
            flight_data=[FlightData(date=d.strftime("%Y-%m-%d"), from_airport=from_iata, to_airport=to_iata)],
            trip="one-way",
            passengers=passengers,
            seat=seat,
            max_stops=cfg.max_stops,
        )
        params = {"tfs": tfs.as_b64().decode(), "hl": "en", "tfu": "EgQIABABIgA", "curr": cfg.currency}
        client = primp.Client(impersonate="chrome_126", verify=False)
        resp = client.get("https://www.google.com/travel/flights", params=params, cookies={"SOCS": "CAI"})
        return resp.text

    # Pre-fetch cheapest inbound per return date (round-trip only)
    inbound_best: dict = {}
    inbound_fetch_errors: list = []
    in_window = cfg.inbound_departure_window
    use_in_window = bool(in_window.earliest or in_window.latest)
    for in_date in (cfg.inbound_dates or []):
        try:
            html = _fetch(cfg.destination.iata, cfg.origin.iata, in_date)
            flights = _parse_html(html)
            valid = []
            for f in flights:
                if not _parse_price(f["price"]):
                    continue
                if use_in_window:
                    itin = _build_itinerary(f, in_date, cfg.destination.iata, cfg.origin.iata)
                    if not itin or not _dep_in_window(itin.departure, in_window):
                        continue
                valid.append(f)
            if valid:
                best = min(valid, key=lambda f: _parse_price(f["price"]))
                itin = _build_itinerary(best, in_date, cfg.destination.iata, cfg.origin.iata)
                if itin:
                    inbound_best[in_date] = (best, itin)
        except Exception as e:
            inbound_fetch_errors.append(str(e))
        time.sleep(1)

    all_offers: list = []
    seen: set = set()

    for out_date in cfg.outbound_dates:
        try:
            html = _fetch(cfg.origin.iata, cfg.destination.iata, out_date)
        except Exception as e:
            raise RuntimeError(f"Google Flights search failed ({out_date}): {e}")
        time.sleep(1)

        for fd in _parse_html(html):
            out_price = _parse_price(fd["price"])
            if not out_price:
                continue
            out_itin = _build_itinerary(fd, out_date, cfg.origin.iata, cfg.destination.iata)
            if not out_itin:
                continue

            if not cfg.inbound_dates or not inbound_best:
                _add(_make_offer(out_itin, None, out_price, 0.0, cfg), seen, all_offers)
            else:
                for in_date in cfg.inbound_dates:
                    if in_date not in inbound_best:
                        continue
                    in_fd, in_itin = inbound_best[in_date]
                    in_price = _parse_price(in_fd["price"])
                    _add(_make_offer(out_itin, in_itin, out_price, in_price, cfg), seen, all_offers)

    if inbound_fetch_errors and not inbound_best:
        raise RuntimeError(
            f"Google Flights: inbound search failed — {inbound_fetch_errors[0]}. "
            "If this repeats, wait a minute before retrying."
        )

    return all_offers


def _parse_html(html: str) -> list:
    from selectolax.lexbor import LexborHTMLParser

    parser = LexborHTMLParser(html)
    results = []

    for fl in parser.css('div[jsname="IWWDBc"], div[jsname="YdtKid"]'):
        for item in fl.css("ul.Rk10dc li"):
            link = item.css_first("div.JMc5Xc[aria-label]")
            if not link:
                continue
            lbl = link.attributes.get("aria-label", "")

            # Stops + airline from the opening phrase
            flight_m = re.search(r"(Nonstop|\d+ stops?) flight with (.+?)\. Leaves", lbl, re.I)
            if not flight_m:
                continue
            stops_str = flight_m.group(1).lower()
            stops_num = 0 if stops_str == "nonstop" else int(stops_str.split()[0])
            name = flight_m.group(2).strip()

            # Departure: "Leaves ... at {time} on {weekday}, {month} {day}"
            dep_m = re.search(r"Leaves .+? at ([\d:]+\s*(?:AM|PM)) on \w+, (\w+ \d+)", lbl)
            # Arrival: "arrives ... at {time} on {weekday}, {month} {day}"
            arr_m = re.search(r"arrives .+? at ([\d:]+\s*(?:AM|PM)) on \w+, (\w+ \d+)", lbl)
            if not dep_m or not arr_m:
                continue

            # Duration: "Total duration N hr M min"
            dur_m = re.search(r"Total duration (.+?)\.", lbl)
            duration = dur_m.group(1).strip() if dur_m else ""

            # Price from DOM (more precise than the aria-label "From X euros")
            price_el = item.css_first(".YMlIz.FpEdX")
            price = (price_el.text(strip=True) if price_el else "").replace(",", "")
            if not price:
                continue

            results.append({
                "name": name,
                "departure": dep_m.group(1),
                "dep_date": dep_m.group(2),
                "arrival": arr_m.group(1),
                "arr_date": arr_m.group(2),
                "duration": duration,
                "stops": stops_num,
                "price": price,
            })

    return results


def _add(offer, seen: set, all_offers: list) -> None:
    if offer is None:
        return
    key = (offer.outbound.departure, offer.price)
    if key not in seen:
        seen.add(key)
        all_offers.append(offer)


def _make_offer(out_itin: Itinerary, in_itin: Optional[Itinerary], out_price: float, in_price: float, cfg: SearchConfig) -> FlightOffer:
    return FlightOffer(
        id=str(uuid.uuid4()),
        provider="google_flights",
        outbound=out_itin,
        inbound=in_itin,
        price=(out_price + in_price) * cfg.adults,
        outbound_price=out_price,
        inbound_price=in_price,
        currency=cfg.currency,
        cabin_class=cfg.cabin_class or "economy",
        adults=cfg.adults,
        price_available=True,
        raw={},
    )


def _build_itinerary(fd: dict, flight_date: date, origin_iata: str, dest_iata: str) -> Optional[Itinerary]:
    try:
        dep_dt = _parse_aria_dt(fd["departure"], fd.get("dep_date", ""), flight_date.year)
        arr_dt = _parse_aria_dt(fd["arrival"], fd.get("arr_date", ""), flight_date.year)
        duration = _parse_duration(fd["duration"])
        stops = fd["stops"]
        airline_name = (fd["name"] or "Unknown").split("·")[0].strip() or "Unknown"
        code = _airline_code(airline_name)
        segments = _build_segments(stops, origin_iata, dest_iata, dep_dt, arr_dt, duration, code, airline_name)
        return Itinerary(segments=segments)
    except Exception:
        return None


def _parse_aria_dt(time_str: str, date_str: str, year: int) -> datetime:
    """Parse from aria-label pieces like '9:35 PM' + 'July 10'."""
    full = f"{time_str.strip()} {date_str.strip()}"
    for fmt in ("%I:%M %p %B %d", "%I:%M %p %b %d"):
        try:
            return datetime.strptime(full, fmt).replace(year=year)
        except ValueError:
            continue
    raise ValueError(f"Cannot parse datetime: time={time_str!r} date={date_str!r}")


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


def _dep_in_window(dt: "datetime", window) -> bool:
    from ..filters import _in_window
    return _in_window(dt.time(), window)


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
    "tui": "BY", "jet2": "LS",
}


def _airline_code(name: str) -> str:
    key = name.lower()
    for partial, code in _AIRLINE_CODES.items():
        if partial in key:
            return code
    return (name[:2].upper() if len(name) >= 2 else "??")
