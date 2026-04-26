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
        return json.loads(AIRPORTS_PATH.read_text())

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
        if row.get("type", "") not in ("large_airport", "medium_airport"):
            continue
        municipality = re.split(r"[,(]", row.get("municipality") or "")[0].strip()
        country = (row.get("iso_country") or "").strip()
        name = (row.get("name") or "").strip()
        if not municipality:
            continue
        key = f"{municipality}, {country}"
        cities.setdefault(key, []).append({"iata": iata, "name": name})

    data = {k: sorted(v, key=lambda a: a["iata"]) for k, v in sorted(cities.items())}
    AIRPORTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    AIRPORTS_PATH.write_text(json.dumps(data))
    return data


def expand_dates(start: date, end: date, dow_filter: list) -> list:
    result, cur = [], start
    while cur <= end:
        if not dow_filter or cur.weekday() in dow_filter:
            result.append(cur)
        cur += timedelta(days=1)
    return result


def _date_range(raw) -> tuple:
    """Normalise date_input output (may be a single date or 1/2-element tuple)."""
    if isinstance(raw, (list, tuple)):
        if len(raw) == 2:
            return raw[0], raw[1]
        return raw[0], raw[0]
    return raw, raw


def offers_to_df(offers: list) -> pd.DataFrame:
    rows = []
    for o in offers:
        row: dict = {
            "Airline": o.outbound.airline_name or o.outbound.airline,
            "Route": f"{o.outbound.origin} → {o.outbound.destination}",
            "Out Date": o.outbound.departure.strftime("%Y-%m-%d"),
            "Out Dep": o.outbound.departure.strftime("%H:%M"),
            "Out Arr": o.outbound.arrival.strftime("%H:%M"),
            "Out Duration": f"{o.outbound.duration_minutes // 60}h {o.outbound.duration_minutes % 60:02d}m",
            "Out Stops": o.outbound.stops,
        }
        if o.inbound:
            row.update({
                "In Date": o.inbound.departure.strftime("%Y-%m-%d"),
                "In Dep": o.inbound.departure.strftime("%H:%M"),
                "In Arr": o.inbound.arrival.strftime("%H:%M"),
                "In Duration": f"{o.inbound.duration_minutes // 60}h {o.inbound.duration_minutes % 60:02d}m",
                "In Stops": o.inbound.stops,
            })
        row["Price/pax"] = round(o.price_per_person, 2)
        row["Currency"] = o.currency
        rows.append(row)

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("Price/pax").reset_index(drop=True)
    return df


def main():
    st.set_page_config(page_title="Flight Finder", page_icon="✈", layout="wide")
    st.title("✈ Flight Finder")

    airports_data = load_airports()
    city_list = [""] + list(airports_data.keys())

    # ── Sidebar ────────────────────────────────────────────────────────────────
    with st.sidebar:
        st.header("Search")

        currency = st.selectbox("Currency", CURRENCIES)

        # Route
        st.subheader("Route")
        origin_city = st.selectbox("Origin city", city_list, index=0)
        origin_iatas: list = []
        if origin_city:
            opts = [f"{a['iata']} – {a['name']}" for a in airports_data[origin_city]]
            sel = st.multiselect("Origin airport(s)", opts, default=opts[:1])
            origin_iatas = [s.split(" – ")[0] for s in sel]

        dest_city = st.selectbox("Destination city", city_list, index=0)
        dest_iatas: list = []
        if dest_city:
            opts = [f"{a['iata']} – {a['name']}" for a in airports_data[dest_city]]
            sel = st.multiselect("Destination airport(s)", opts, default=opts[:1])
            dest_iatas = [s.split(" – ")[0] for s in sel]

        # Dates
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

        # Departure windows
        st.subheader("Departure times")
        out_window = st.slider("Outbound departure (h)", 0, 24, (0, 24), format="%d:00")
        in_window = (0, 24)
        if round_trip:
            in_window = st.slider("Return departure (h)", 0, 24, (0, 24), format="%d:00")

        # Passengers & cabin
        st.subheader("Passengers & Cabin")
        adults = int(st.number_input("Adults (12+)", 1, 9, 1))
        children = int(st.number_input("Children (2–11)", 0, 9, 0))
        infants = int(st.number_input("Infants (<2)", 0, 9, 0))
        cabin_label = st.selectbox("Cabin", list(CABINS.keys()))

        # Filters
        st.subheader("Filters")
        stops_label = st.selectbox("Max stops", ["Any", "Direct only", "Up to 1 stop"])
        stops_val = {"Any": None, "Direct only": 0, "Up to 1 stop": 1}[stops_label]
        max_price_input = int(st.number_input("Max price/person (0 = no limit)", 0, 10000, 0, step=50))
        max_price_val = float(max_price_input) if max_price_input > 0 else None

        search_clicked = st.button("Search", type="primary", use_container_width=True)

    # ── Main area ──────────────────────────────────────────────────────────────
    if not search_clicked:
        st.info("Configure your search in the sidebar and click **Search**.")
        return

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

    if not all_offers:
        st.error("No results found. Try adjusting your filters or expanding the date range.")
        return

    st.success(f"Found **{len(all_offers)}** offer(s) across **{len(combos)}** route(s)")

    sort_by = st.radio("Sort by", ["Price", "Duration", "Departure time"], horizontal=True)
    sort_key = {
        "Price": lambda o: o.price_per_person,
        "Duration": lambda o: o.outbound.duration_minutes,
        "Departure time": lambda o: o.outbound.departure,
    }[sort_by]
    all_offers.sort(key=sort_key)

    df = offers_to_df(all_offers)
    st.dataframe(df, use_container_width=True, hide_index=True)
    st.download_button(
        "Download CSV",
        df.to_csv(index=False).encode(),
        file_name="flights.csv",
        mime="text/csv",
    )


if __name__ == "__main__":
    main()
