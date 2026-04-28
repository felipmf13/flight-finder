import io
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


def flights_to_df(offers: list) -> pd.DataFrame:
    rows = []
    for o in offers:
        dur = o.outbound.duration_minutes
        rows.append({
            "Airline": o.outbound.airline_name or o.outbound.airline,
            "Route": f"{o.outbound.origin} → {o.outbound.destination}",
            "Date": o.outbound.departure.strftime("%Y-%m-%d"),
            "Dep": o.outbound.departure.strftime("%H:%M"),
            "Arr": o.outbound.arrival.strftime("%H:%M"),
            "Duration": f"{dur // 60}h {dur % 60:02d}m",
            "Stops": o.outbound.stops,
            "Price/pax": round(o.price_per_person, 2),
            "Currency": o.currency,
        })
    df = pd.DataFrame(rows)
    return df.reset_index(drop=True) if not df.empty else df


def make_flight_chart(outbound_offers: list, inbound_offers: list):
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    def _hour(dt) -> float:
        return dt.hour + dt.minute / 60

    def _price_color(p: float, min_p: float, max_p: float) -> str:
        t = max(0.0, min(1.0, (p - min_p) / (max_p - min_p) if max_p > min_p else 0.0))
        if t <= 0.5:
            s = t * 2
            r, g, b = round(39 + (241 - 39) * s), round(174 + (193 - 174) * s), round(96 + (15 - 96) * s)
        else:
            s = (t - 0.5) * 2
            r, g, b = round(241 + (231 - 241) * s), round(193 + (76 - 193) * s), round(15 + (60 - 15) * s)
        return f"rgb({r},{g},{b})"

    def _build_cats(dates: list) -> tuple:
        d_to_cat: dict = {}
        cats: list = []
        for i, d in enumerate(dates):
            label = f"{d.strftime('%a')} {d.day} {d.strftime('%b')}"
            d_to_cat[d] = label
            cats.append(label)
            if i < len(dates) - 1 and (dates[i + 1] - d).days > 1:
                cats.append("")  # visual gap for non-consecutive days
        return d_to_cat, cats

    all_prices = [o.price_per_person for o in outbound_offers + inbound_offers]
    if not all_prices:
        return None

    min_p = min(all_prices)
    max_p = max(all_prices) if max(all_prices) > min(all_prices) else min(all_prices) + 1
    colorscale = [[0, "#27ae60"], [0.5, "#f1c40f"], [1, "#e74c3c"]]

    has_inbound = bool(inbound_offers)
    n_cols = 2 if has_inbound else 1

    fig = make_subplots(
        rows=1, cols=n_cols,
        subplot_titles=["Outbound"] + (["Return"] if has_inbound else []),
        shared_yaxes=True,
        horizontal_spacing=0.08,
    )

    def _add_leg(offers: list, col: int) -> None:
        dates = sorted(set(o.outbound.departure.date() for o in offers))
        d_to_cat, cats = _build_cats(dates)
        currency = offers[0].currency

        xs, bases, heights, colors, hovers = [], [], [], [], []
        for o in offers:
            dep_h = _hour(o.outbound.departure)
            dur_h = min(24 - dep_h, o.outbound.duration_minutes / 60)
            dur_h = max(0.3, dur_h)
            xs.append(d_to_cat[o.outbound.departure.date()])
            bases.append(dep_h)
            heights.append(dur_h)
            colors.append(_price_color(o.price_per_person, min_p, max_p))
            hovers.append(
                f"<b>{o.outbound.airline_name}</b><br>"
                f"{o.outbound.departure.strftime('%H:%M')} → {o.outbound.arrival.strftime('%H:%M')}<br>"
                f"{currency} {o.price_per_person:.0f} / pax"
            )

        fig.add_trace(go.Bar(
            x=xs,
            y=heights,
            base=bases,
            marker=dict(
                color=colors,
                opacity=0.85,
                line=dict(width=0.5, color="rgba(0,0,0,0.25)"),
            ),
            hovertext=hovers,
            hoverinfo="text",
            showlegend=False,
        ), row=1, col=col)

        # Invisible dummy scatter solely to render the colorbar
        is_last = col == n_cols
        if is_last:
            fig.add_trace(go.Scatter(
                x=[None], y=[None],
                mode="markers",
                marker=dict(
                    color=[min_p, max_p],
                    colorscale=colorscale,
                    cmin=min_p, cmax=max_p,
                    showscale=True,
                    colorbar=dict(
                        title=dict(text=f"{currency}/pax", side="right"),
                        thickness=12, len=0.75, x=1.03,
                    ),
                ),
                showlegend=False,
                hoverinfo="skip",
            ), row=1, col=col)

        fig.update_xaxes(
            type="category",
            categoryorder="array", categoryarray=cats,
            showgrid=False, tickfont=dict(size=11),
            row=1, col=col,
        )

    _add_leg(outbound_offers, 1)
    if has_inbound:
        _add_leg(inbound_offers, 2)

    tick_vals = list(range(0, 25, 2))
    tick_text = [f"{h:02d}:00" for h in tick_vals]
    fig.update_yaxes(
        range=[24, 0], tickvals=tick_vals, ticktext=tick_text,
        gridcolor="rgba(0,0,0,0.07)", zeroline=False,
    )
    fig.update_layout(
        barmode="overlay",
        height=540, showlegend=False,
        plot_bgcolor="#f8f9fa", paper_bgcolor="rgba(0,0,0,0)",
        margin=dict(t=50, b=20, l=55, r=90),
        hoverlabel=dict(bgcolor="white", font_size=13),
    )
    return fig


def main():
    st.set_page_config(page_title="Flight Finder", page_icon="✈", layout="wide")
    st.title("✈ Flight Finder")

    airports_data = load_airports()
    city_list = [""] + list(airports_data.keys())

    if "outbound_offers" not in st.session_state:
        st.session_state.outbound_offers = []
    if "inbound_offers" not in st.session_state:
        st.session_state.inbound_offers = []
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
            sel = st.multiselect("Origin airport(s)", opts, default=opts)
            origin_iatas = [s.split(" – ")[0] for s in sel]

        dest_city = st.selectbox("Destination city", city_list, index=0)
        dest_iatas: list = []
        if dest_city:
            city_airports = airports_data[dest_city]
            opts = [f"{a['iata']} – {a['name']}" for a in city_airports]
            sel = st.multiselect("Destination airport(s)", opts, default=opts)
            dest_iatas = [s.split(" – ")[0] for s in sel]

        st.subheader("Dates")
        round_trip = st.checkbox("Round trip", value=True)
        today = date.today()

        out_raw = st.date_input(
            "Outbound dates",
            value=(today, today),
            min_value=today,
        )
        out_start, out_end = _date_range(out_raw)

        in_start = in_end = today
        if round_trip:
            in_raw = st.date_input(
                "Return dates",
                value=(today, today),
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
        all_outbound: list = []
        all_inbound: list = []

        n_out = len(outbound_dates)
        n_in = len(inbound_dates)
        total_steps = len(combos) * (2 if round_trip else 1)

        with st.status(f"Searching {len(combos)} route(s)…", expanded=True) as status:
            step = 0
            for orig, dest in combos:
                step += 1
                st.write(f"({step}/{total_steps}) Outbound {orig} → {dest} — {n_out} date(s)")
                try:
                    cfg = SearchConfig(
                        origin=AirportConfig(city=origin_city, iata=orig),
                        destination=AirportConfig(city=dest_city, iata=dest),
                        outbound_dates=outbound_dates,
                        inbound_dates=[],
                        outbound_departure_window=TimeWindow(
                            earliest=_window_str(out_window[0], False),
                            latest=_window_str(out_window[1], True),
                        ),
                        inbound_departure_window=TimeWindow(earliest="", latest=""),
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
                    all_outbound.extend(offers)
                    st.write(f"  ✓ {len(offers)} flight(s)")
                except Exception as e:
                    st.write(f"  ✗ {e}")

                if round_trip:
                    step += 1
                    st.write(f"({step}/{total_steps}) Return {dest} → {orig} — {n_in} date(s)")
                    try:
                        cfg_ret = SearchConfig(
                            origin=AirportConfig(city=dest_city, iata=dest),
                            destination=AirportConfig(city=origin_city, iata=orig),
                            outbound_dates=inbound_dates,
                            inbound_dates=[],
                            outbound_departure_window=TimeWindow(
                                earliest=_window_str(in_window[0], False),
                                latest=_window_str(in_window[1], True),
                            ),
                            inbound_departure_window=TimeWindow(earliest="", latest=""),
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
                        ret_offers = run_search(cfg_ret)
                        all_inbound.extend(ret_offers)
                        st.write(f"  ✓ {len(ret_offers)} flight(s)")
                    except Exception as e:
                        st.write(f"  ✗ {e}")

            status.update(label="Search complete", state="complete")

        st.session_state.outbound_offers = all_outbound
        st.session_state.inbound_offers = all_inbound
        out_count = len(all_outbound)
        in_count = len(all_inbound)
        label = f"{out_count} outbound"
        if round_trip:
            label += f", {in_count} return"
        st.session_state.search_label = label

        if not all_outbound and not all_inbound:
            st.error("No results found. Try adjusting your filters or expanding the date range.")
            return

    # ── Display results (persists across reruns) ────────────────────────────────
    if not st.session_state.outbound_offers and not st.session_state.inbound_offers:
        st.info("Configure your search in the sidebar and click **Search**.")
        return

    st.success(f"Found **{st.session_state.search_label}**")

    sort_by = st.radio("Sort by", ["Price", "Duration", "Departure time"], horizontal=True)
    sort_key = {
        "Price": lambda o: o.price_per_person,
        "Duration": lambda o: o.outbound.duration_minutes,
        "Departure time": lambda o: o.outbound.departure,
    }[sort_by]

    out_sorted = sorted(st.session_state.outbound_offers, key=sort_key)
    in_sorted = sorted(st.session_state.inbound_offers, key=sort_key)

    out_df = flights_to_df(out_sorted)
    in_df = flights_to_df(in_sorted)

    st.subheader("Outbound flights")
    st.dataframe(out_df, use_container_width=True, hide_index=True)

    if st.session_state.inbound_offers:
        st.subheader("Return flights")
        st.dataframe(in_df, use_container_width=True, hide_index=True)

    # ── Timeline chart ──────────────────────────────────────────────────────────
    st.subheader("Flight timeline")
    fig = make_flight_chart(
        st.session_state.outbound_offers,
        st.session_state.inbound_offers,
    )
    if fig:
        st.plotly_chart(fig, use_container_width=True)

    buf = io.StringIO()
    buf.write("Outbound flights\n")
    out_df.to_csv(buf, index=False)
    if st.session_state.inbound_offers:
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
