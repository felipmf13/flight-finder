import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))

AIRPORTS_PATH = Path("data/airports.json")
AIRPORTS_CSV_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"

CURRENCIES = ["EUR", "USD", "GBP", "CHF", "CAD", "AUD", "JPY", "SEK", "NOK", "DKK"]
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
CABINS = {
    "Economy": "economy",
    "Premium Economy": "premium_economy",
    "Business": "business",
    "First": "first",
}


@st.cache_data(show_spinner="Downloading airport database…")
def load_airports() -> dict:
    if AIRPORTS_PATH.exists():
        data = json.loads(AIRPORTS_PATH.read_text())
        # Migrate old cache that lacks the "type" field
        first_airports = next(iter(data.values()), [])
        if first_airports and "type" not in first_airports[0]:
            AIRPORTS_PATH.unlink()
        else:
            return data

    import csv
    import io
    import urllib.request

    with urllib.request.urlopen(AIRPORTS_CSV_URL) as r:
        content = r.read().decode("utf-8")

    cities: dict = {}
    for row in csv.DictReader(io.StringIO(content)):
        iata = (row.get("iata_code") or "").strip().upper()
        if not iata or len(iata) != 3:
            continue
        airport_type = row.get("type", "")
        if airport_type not in ("large_airport", "medium_airport"):
            continue
        municipality = re.split(r"[,(]", row.get("municipality") or "")[0].strip()
        country = (row.get("iso_country") or "").strip()
        name = (row.get("name") or "").strip()
        if not municipality:
            continue
        key = f"{municipality}, {country}"
        cities.setdefault(key, []).append({"iata": iata, "name": name, "type": airport_type})

    # Sort each city: large airports first, then medium; alphabetical within each tier
    def _sort_key(a):
        return (0 if a["type"] == "large_airport" else 1, a["iata"])

    data = {k: sorted(v, key=_sort_key) for k, v in sorted(cities.items())}
    AIRPORTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    AIRPORTS_PATH.write_text(json.dumps(data))
    return data


def _large_airports(airports: list) -> list:
    large = [a for a in airports if a.get("type") == "large_airport"]
    return large if large else airports


def expand_dates(start: date, end: date, dow_filter: list) -> list:
    result, cur = [], start
    while cur <= end:
        if not dow_filter or cur.weekday() in dow_filter:
            result.append(cur)
        cur += timedelta(days=1)
    return result


def _date_range(raw) -> tuple:
    if isinstance(raw, (list, tuple)):
        if len(raw) == 2:
            return raw[0], raw[1]
        return raw[0], raw[0]
    return raw, raw


def _itin_row(itin, price_per_person: float, currency: str) -> dict:
    dur = itin.duration_minutes
    return {
        "Airline": itin.airline_name or itin.airline,
        "Route": f"{itin.origin} → {itin.destination}",
        "Date": itin.departure.strftime("%Y-%m-%d"),
        "Dep": itin.departure.strftime("%H:%M"),
        "Arr": itin.arrival.strftime("%H:%M"),
        "Duration": f"{dur // 60}h {dur % 60:02d}m",
        "Stops": itin.stops,
        "Price/pax": round(price_per_person, 2),
        "Currency": currency,
    }


def outbound_to_df(offers: list) -> pd.DataFrame:
    seen: set = set()
    rows = []
    for o in sorted(offers, key=lambda x: x.outbound_price):
        key = (o.outbound.departure, o.outbound.airline_name, o.outbound.origin)
        if key in seen:
            continue
        seen.add(key)
        rows.append(_itin_row(o.outbound, o.outbound_price or o.price_per_person, o.currency))
    df = pd.DataFrame(rows)
    return df.reset_index(drop=True)


def inbound_to_df(offers: list) -> pd.DataFrame:
    seen: set = set()
    rows = []
    for o in sorted(offers, key=lambda x: x.inbound_price):
        if not o.inbound:
            continue
        key = (o.inbound.departure, o.inbound.airline_name, o.inbound.origin)
        if key in seen:
            continue
        seen.add(key)
        rows.append(_itin_row(o.inbound, o.inbound_price, o.currency))
    df = pd.DataFrame(rows)
    return df.reset_index(drop=True)


def main():
    st.set_page_config(page_title="Flight Finder", page_icon="✈", layout="wide")
    st.title("✈ Flight Finder")

    airports_data = load_airports()
    city_list = [""] + list(airports_data.keys())

    # Persist search results across reruns (widget interactions trigger reruns)
    if "search_offers" not in st.session_state:
        st.session_state.search_offers = []
    if "search_label" not in st.session_state:
        st.session_state.search_label = ""

    # ── Sidebar ────────────────────────────────────────────────────────────────
    with st.sidebar:
        st.header("Search")

        currency = st.selectbox("Currency", CURRENCIES)

        st.subheader("Route")
        origin_city = st.selectbox("Origin city", city_list, index=0)
        origin_iatas: list = []
        if origin_city:
            city_airports = airports_data[origin_city]
            opts = [f"{a['iata']} – {a['name']}" for a in city_airports]
            default_opts = [f"{a['iata']} – {a['name']}" for a in _large_airports(city_airports)]
            sel = st.multiselect("Origin airport(s)", opts, default=default_opts)
            origin_iatas = [s.split(" – ")[0] for s in sel]

        dest_city = st.selectbox("Destination city", city_list, index=0)
        dest_iatas: list = []
        if dest_city:
            city_airports = airports_data[dest_city]
            opts = [f"{a['iata']} – {a['name']}" for a in city_airports]
            default_opts = [f"{a['iata']} – {a['name']}" for a in _large_airports(city_airports)]
            sel = st.multiselect("Destination airport(s)", opts, default=default_opts)
            dest_iatas = [s.split(" – ")[0] for s in sel]

        st.subheader("Dates")
        round_trip = st.checkbox("Round trip", value=True)
        today = date.today()

        out_raw = st.date_input(
            "Outbound dates",
            value=(today + timedelta(days=7), today + timedelta(days=14)),
            min_value=today,
        )
        out_start, out_end = _date_range(out_raw)

        in_start = in_end = today + timedelta(days=21)
        if round_trip:
            in_raw = st.date_input(
                "Return dates",
                value=(today + timedelta(days=14), today + timedelta(days=21)),
                min_value=today,
            )
            in_start, in_end = _date_range(in_raw)

        dow_sel = st.multiselect("Days of week (empty = all)", DAYS, default=[])
        dow_idx = [DAYS.index(d) for d in dow_sel]

        st.subheader("Departure times")
        out_window = st.slider("Outbound departure (h)", 0, 24, (0, 24), format="%d:00")
        in_window = (0, 24)
        if round_trip:
            in_window = st.slider("Return departure (h)", 0, 24, (0, 24), format="%d:00")

        st.subheader("Passengers & Cabin")
        adults = int(st.number_input("Adults (12+)", 1, 9, 1))
        children = int(st.number_input("Children (2–11)", 0, 9, 0))
        infants = int(st.number_input("Infants (<2)", 0, 9, 0))
        cabin_label = st.selectbox("Cabin", list(CABINS.keys()))

        st.subheader("Filters")
        stops_label = st.selectbox("Max stops", ["Any", "Direct only", "Up to 1 stop"])
        stops_val = {"Any": None, "Direct only": 0, "Up to 1 stop": 1}[stops_label]
        max_price_input = int(st.number_input("Max price/person (0 = no limit)", 0, 10000, 0, step=50))
        max_price_val = float(max_price_input) if max_price_input > 0 else None

        search_clicked = st.button("Search", type="primary", use_container_width=True)

    # ── Run search when button clicked ─────────────────────────────────────────
    if search_clicked:
        errors = []
        if not origin_iatas:
            errors.append("Select an origin airport.")
        if not dest_iatas:
            errors.append("Select a destination airport.")
        for e in errors:
            st.error(e)
        if errors:
            return

        outbound_dates = expand_dates(out_start, out_end, dow_idx)
        if not outbound_dates:
            st.error("No outbound dates match the selected days-of-week filter.")
            return

        inbound_dates: list = []
        if round_trip:
            inbound_dates = expand_dates(in_start, in_end, dow_idx)
            if not inbound_dates:
                st.error("No return dates match the selected days-of-week filter.")
                return

        from src.config import AirportConfig, SearchConfig, TimeWindow
        from src.search import run_search

        def _window_str(h: int, is_upper: bool) -> str:
            if (is_upper and h == 24) or (not is_upper and h == 0):
                return ""
            return f"{h:02d}:00"

        combos = [(o, d) for o in origin_iatas for d in dest_iatas]
        all_offers: list = []

        with st.status(f"Searching {len(combos)} route(s)…", expanded=True) as status:
            for i, (orig, dest) in enumerate(combos, 1):
                st.write(f"({i}/{len(combos)}) {orig} → {dest} — {len(outbound_dates)} outbound date(s)")
                try:
                    cfg = SearchConfig(
                        origin=AirportConfig(city=origin_city, iata=orig),
                        destination=AirportConfig(city=dest_city, iata=dest),
                        outbound_dates=outbound_dates,
                        inbound_dates=inbound_dates,
                        outbound_departure_window=TimeWindow(
                            earliest=_window_str(out_window[0], False),
                            latest=_window_str(out_window[1], True),
                        ),
                        inbound_departure_window=TimeWindow(
                            earliest=_window_str(in_window[0], False) if round_trip else "",
                            latest=_window_str(in_window[1], True) if round_trip else "",
                        ),
                        adults=adults,
                        children=children,
                        infants=infants,
                        cabin_class=CABINS[cabin_label],
                        include_airlines=[],
                        exclude_airlines=[],
                        max_stops=stops_val,
                        max_price_per_person=max_price_val,
                        currency=currency,
                        providers=["google_flights"],
                        max_results=200,
                        sort_by="price",
                    )
                    offers = run_search(cfg)
                    all_offers.extend(offers)
                    st.write(f"  ✓ {len(offers)} offer(s)")
                except Exception as e:
                    st.write(f"  ✗ {e}")
            status.update(label="Search complete", state="complete")

        # Store in session state so results survive widget reruns
        st.session_state.search_offers = all_offers
        st.session_state.search_label = f"{len(all_offers)} offer(s) across {len(combos)} route(s)"

        if not all_offers:
            st.error("No results found. Try adjusting your filters or expanding the date range.")
            return

    # ── Display results (persists across reruns) ────────────────────────────────
    if not st.session_state.search_offers:
        st.info("Configure your search in the sidebar and click **Search**.")
        return

    all_offers = st.session_state.search_offers
    is_round_trip = any(o.inbound for o in all_offers)

    st.success(f"Found **{st.session_state.search_label}**")

    sort_by = st.radio("Sort by", ["Price", "Duration", "Departure time"], horizontal=True)

    out_sort_key = {
        "Price": lambda o: o.outbound_price or o.price_per_person,
        "Duration": lambda o: o.outbound.duration_minutes,
        "Departure time": lambda o: o.outbound.departure,
    }[sort_by]
    in_sort_key = {
        "Price": lambda o: o.inbound_price,
        "Duration": lambda o: o.inbound.duration_minutes if o.inbound else 0,
        "Departure time": lambda o: o.inbound.departure if o.inbound else o.outbound.departure,
    }[sort_by]

    # ── Outbound table ──────────────────────────────────────────────────────────
    st.subheader("Outbound flights")
    out_df = outbound_to_df(sorted(all_offers, key=out_sort_key))
    st.dataframe(out_df, use_container_width=True, hide_index=True)

    # ── Return table ────────────────────────────────────────────────────────────
    if is_round_trip:
        st.subheader("Return flights")
        in_df = inbound_to_df(sorted(all_offers, key=in_sort_key))
        st.dataframe(in_df, use_container_width=True, hide_index=True)
        st.caption("Total price/pax = outbound price + return price")

    # ── Download ────────────────────────────────────────────────────────────────
    import io
    buf = io.StringIO()
    out_df.to_csv(buf, index=False)
    if is_round_trip:
        buf.write("\nReturn flights\n")
        in_df.to_csv(buf, index=False)
    st.download_button(
        "Download CSV",
        buf.getvalue().encode(),
        file_name="flights.csv",
        mime="text/csv",
    )


if __name__ == "__main__":
    main()
