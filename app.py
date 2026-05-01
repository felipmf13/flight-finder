import bisect
import io
import json
import re
import sys
from collections import defaultdict, deque
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

sys.path.insert(0, str(Path(__file__).parent))

MAX_RESULTS = 200

AIRPORTS_PATH = Path("data/airports.json")
AIRPORTS_CSV_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"

CURRENCIES = ["GBP", "EUR", "USD", "CHF", "CAD", "AUD", "JPY", "SEK", "NOK", "DKK"]
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
CABINS = {
    "Economy": "economy",
    "Premium Economy": "premium_economy",
    "Business": "business",
    "First": "first",
}


def _window_str(h: int, is_upper: bool) -> str:
    """Convert slider hour value to HH:MM string; returns '' for boundary defaults."""
    if (is_upper and h == 24) or (not is_upper and h == 0):
        return ""
    return f"{h:02d}:00"


_AIRPORTS_MAX_AGE_DAYS = 30


def _airport_sort_key(a: dict) -> tuple:
    return (0 if a["type"] == "large_airport" else 1, a["iata"])


@st.cache_data(ttl=timedelta(days=_AIRPORTS_MAX_AGE_DAYS), show_spinner="Downloading airport database…")
def load_airports() -> dict:
    if AIRPORTS_PATH.exists():
        raw_cache = json.loads(AIRPORTS_PATH.read_text())

        # New format: has a 'cities' wrapper with 'generated_at'
        if "cities" in raw_cache:
            cities = raw_cache["cities"]
            first_airports = next(iter(cities.values()), [])
            # Return if schema is current (has 'type' field); otherwise re-download
            if not first_airports or "type" in first_airports[0]:
                return cities

        # Old format or stale schema — delete and re-download
        AIRPORTS_PATH.unlink()

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

    cities = {k: sorted(v, key=_airport_sort_key) for k, v in sorted(cities.items())}
    AIRPORTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    AIRPORTS_PATH.write_text(json.dumps({"generated_at": datetime.now().isoformat(), "cities": cities}))
    return cities


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


def flights_to_df(offers: list) -> tuple:
    """Return (DataFrame, list[bool]) where True marks a segment sub-row (yellowish)."""
    rows = []
    is_stop: list = []
    for o in offers:
        segments = o.outbound.segments
        if len(segments) <= 1:
            dur = o.outbound.duration_minutes
            rows.append({
                "Airline": o.outbound.airline_name or o.outbound.airline,
                "Route": f"{o.outbound.origin} → {o.outbound.destination}",
                "Date": o.outbound.departure.strftime("%Y-%m-%d"),
                "Dep": o.outbound.departure.strftime("%H:%M"),
                "Arr": o.outbound.arrival.strftime("%H:%M"),
                "Duration": f"{dur // 60}h {dur % 60:02d}m",
                "Stops": "Direct",
                "Price/pax": round(o.price_per_person),
                "Currency": o.currency,
            })
            is_stop.append(False)
        else:
            n_legs = len(segments)
            last = n_legs - 1
            for i, seg in enumerate(segments):
                dur = seg.duration_minutes
                # Only dep[0] and arr[last] come from Google; all intermediate times are estimated
                dep_str = seg.departure.strftime("%H:%M") + ("" if i == 0 else " (est.)")
                arr_str = seg.arrival.strftime("%H:%M") + ("" if i == last else " (est.)")
                rows.append({
                    "Airline": seg.airline_name or seg.airline,
                    "Route": f"{seg.origin} → {seg.destination}",
                    "Date": seg.departure.strftime("%Y-%m-%d") if i == 0 else "↳",
                    "Dep": dep_str,
                    "Arr": arr_str,
                    "Duration": f"{dur // 60}h {dur % 60:02d}m",
                    "Stops": f"{n_legs - 1} stop{'s' if n_legs > 2 else ''}" if i == 0 else f"↳ leg {i + 1}/{n_legs}",
                    "Price/pax": round(o.price_per_person) if i == 0 else None,
                    "Currency": o.currency if i == 0 else "",
                })
                is_stop.append(True)
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.reset_index(drop=True)
        df["Price/pax"] = df["Price/pax"].astype("Int64")
    return df, is_stop


def _apply_stop_style(df: pd.DataFrame, is_stop: list):
    stop_indices = {i for i, v in enumerate(is_stop) if v}
    if not stop_indices:
        return df
    def _row_bg(row):
        return ["background-color: #fffbe6" if row.name in stop_indices else "" for _ in row]
    return df.style.apply(_row_bg, axis=1)


def make_flight_chart(outbound_offers: list, inbound_offers: list):
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

    BAR_H = 10 / 60       # every rectangle is exactly 10 minutes tall
    FULL_W = 0.8          # fraction of column width available for bars

    def _add_leg(offers: list, col: int) -> None:
        dates = sorted(set(o.outbound.departure.date() for o in offers))
        currency = offers[0].currency

        # Numeric x-axis: consecutive days → x += 1, non-consecutive → x += 2 (visible gap)
        date_to_x: dict = {}
        tick_vals_x: list = []
        tick_texts_x: list = []
        x = 0
        for i, d in enumerate(dates):
            date_to_x[d] = x
            tick_vals_x.append(x)
            tick_texts_x.append(f"{d.strftime('%a')} {d.day} {d.strftime('%b')}")
            if i < len(dates) - 1:
                x += 2 if (dates[i + 1] - d).days > 1 else 1

        # Greedy lane packing per day
        by_date: dict = defaultdict(list)
        for o in offers:
            by_date[o.outbound.departure.date()].append(o)

        lane_idx: dict = {}
        for d, day_offers in by_date.items():
            lane_ends: list = []
            for o in sorted(day_offers, key=lambda o: o.outbound.departure):
                dep_h = _hour(o.outbound.departure)
                placed = False
                for li, le in enumerate(lane_ends):
                    if dep_h >= le:
                        lane_ends[li] = dep_h + BAR_H
                        lane_idx[id(o)] = li
                        placed = True
                        break
                if not placed:
                    lane_idx[id(o)] = len(lane_ends)
                    lane_ends.append(dep_h + BAR_H)

        # Local concurrency: width shrinks only for flights that actually overlap.
        # Sweep-line O(n log n): for each flight i, local_n = max simultaneous flights
        # in any BAR_H window that contains dep[i]. Equivalent to max clique in the
        # overlap graph, computed via sliding-window max over per-position counts.
        local_n: dict = {}
        for d, day_offers in by_date.items():
            sorted_day = sorted(day_offers, key=lambda o: _hour(o.outbound.departure))
            deps = [_hour(o.outbound.departure) for o in sorted_day]
            n_day = len(sorted_day)
            # cnt[j] = number of flights starting in [deps[j], deps[j]+BAR_H)
            cnt = [bisect.bisect_left(deps, deps[j] + BAR_H) - j for j in range(n_day)]
            # Sliding window max of cnt over [lo_i, i]: lo_i = first j with deps[j] > deps[i]-BAR_H
            dq: deque = deque()
            for i in range(n_day):
                new_lo = bisect.bisect_right(deps, deps[i] - BAR_H)
                while dq and dq[0] < new_lo:
                    dq.popleft()
                while dq and cnt[dq[-1]] <= cnt[i]:
                    dq.pop()
                dq.append(i)
                local_n[id(sorted_day[i])] = cnt[dq[0]]

        # Pre-compute color for each unique price (avoids repeated float arithmetic)
        unique_prices = {o.price_per_person for o in offers}
        price_to_color = {p: _price_color(p, min_p, max_p) for p in unique_prices}

        # Build per-bar arrays
        xs, ys, bases, widths, colors, hovers = [], [], [], [], [], []
        border_colors, border_widths = [], []
        for o in offers:
            d = o.outbound.departure.date()
            dep_h = _hour(o.outbound.departure)
            n = local_n[id(o)]
            lane = lane_idx[id(o)]
            bar_w = FULL_W / n
            x_centre = date_to_x[d] + (lane - (n - 1) / 2) * bar_w
            xs.append(x_centre)
            bases.append(dep_h)
            ys.append(BAR_H)
            widths.append(bar_w * 0.92)
            colors.append(price_to_color[o.price_per_person])
            stops_label = f"{o.outbound.stops} stop{'s' if o.outbound.stops != 1 else ''}" if o.outbound.stops else "Direct"
            hovers.append(
                f"<b>{o.outbound.airline_name}</b> · {stops_label}<br>"
                f"{o.outbound.departure.strftime('%H:%M')} → {o.outbound.arrival.strftime('%H:%M')}<br>"
                f"{currency} {o.price_per_person:.0f} / pax"
            )
            if o.outbound.stops > 0:
                border_colors.append("rgba(0,0,0,0.85)")
                border_widths.append(1.5)
            else:
                border_colors.append("rgba(0,0,0,0.15)")
                border_widths.append(0.4)

        fig.add_trace(go.Bar(
            x=xs, y=ys, base=bases, width=widths,
            marker=dict(
                color=colors, opacity=0.9,
                line=dict(color=border_colors, width=border_widths),
            ),
            hovertext=hovers, hoverinfo="text",
            showlegend=False,
        ), row=1, col=col)

        # Dummy invisible scatter — solely to attach the price colorbar
        if col == n_cols:
            fig.add_trace(go.Scatter(
                x=[None], y=[None], mode="markers",
                marker=dict(
                    color=[min_p, max_p], colorscale=colorscale,
                    cmin=min_p, cmax=max_p, showscale=True,
                    colorbar=dict(
                        title=dict(text=f"{currency}/pax", side="right"),
                        thickness=12, len=0.75, x=1.03,
                    ),
                ),
                showlegend=False, hoverinfo="skip",
            ), row=1, col=col)

        x_max = max(tick_vals_x) if tick_vals_x else 0
        fig.update_xaxes(
            tickmode="array", tickvals=tick_vals_x, ticktext=tick_texts_x,
            range=[-0.6, x_max + 0.6],
            showgrid=False, tickfont=dict(size=11),
            row=1, col=col,
        )

    _add_leg(outbound_offers, 1)
    if has_inbound:
        _add_leg(inbound_offers, 2)

    tick_vals_y = list(range(0, 25))
    tick_text_y = [f"{h:02d}:00" for h in tick_vals_y]
    fig.update_yaxes(
        range=[24, 0], tickvals=tick_vals_y, ticktext=tick_text_y,
        gridcolor="rgba(0,0,0,0.08)", zeroline=False, tickfont=dict(size=10),
    )
    fig.update_layout(
        barmode="overlay",
        height=660, showlegend=False,
        plot_bgcolor="#f8f9fa", paper_bgcolor="rgba(0,0,0,0)",
        margin=dict(t=50, b=20, l=60, r=90),
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
        stops_label = st.selectbox("Max stops", ["Direct only", "Up to 1 stop", "Any"])
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

        combos = [(o, d) for o in origin_iatas for d in dest_iatas]
        all_outbound: list = []
        all_inbound: list = []

        n_out = len(outbound_dates)
        n_in = len(inbound_dates)
        total_steps = len(combos) * (2 if round_trip else 1)

        # Fields shared by both outbound and return configs
        common_cfg = dict(
            inbound_dates=[],
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
            max_results=MAX_RESULTS,
            sort_by="price",
        )

        progress_box = st.empty()
        search_errors: list = []

        def _show_progress(step: int, direction: str, route: str, n_dates: int) -> None:
            with progress_box.container():
                col_l, col_r = st.columns([6, 1])
                col_l.markdown(f"**{direction}** &nbsp; {route}")
                col_r.markdown(
                    f"<div style='text-align:right;color:#888;font-size:0.85em'>"
                    f"{step}&thinsp;/&thinsp;{total_steps}</div>",
                    unsafe_allow_html=True,
                )
                st.progress(step / total_steps)
                st.caption(f"{n_dates} date{'s' if n_dates != 1 else ''} · fetching…")

        step = 0
        for orig, dest in combos:
            step += 1
            _show_progress(step, "Outbound", f"{orig} → {dest}", n_out)
            try:
                cfg = SearchConfig(
                    origin=AirportConfig(city=origin_city, iata=orig),
                    destination=AirportConfig(city=dest_city, iata=dest),
                    outbound_dates=outbound_dates,
                    outbound_departure_window=TimeWindow(
                        earliest=_window_str(out_window[0], False),
                        latest=_window_str(out_window[1], True),
                    ),
                    **common_cfg,
                )
                offers = run_search(cfg)
                all_outbound.extend(offers)
            except Exception as e:
                search_errors.append(str(e))

            if round_trip:
                step += 1
                _show_progress(step, "Return", f"{dest} → {orig}", n_in)
                try:
                    cfg_ret = SearchConfig(
                        origin=AirportConfig(city=dest_city, iata=dest),
                        destination=AirportConfig(city=origin_city, iata=orig),
                        outbound_dates=inbound_dates,
                        outbound_departure_window=TimeWindow(
                            earliest=_window_str(in_window[0], False),
                            latest=_window_str(in_window[1], True),
                        ),
                        **common_cfg,
                    )
                    ret_offers = run_search(cfg_ret)
                    all_inbound.extend(ret_offers)
                except Exception as e:
                    search_errors.append(str(e))

        progress_box.empty()
        for err in search_errors:
            st.warning(err)

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

    out_df, out_stop = flights_to_df(out_sorted)
    in_df, in_stop = flights_to_df(in_sorted)

    st.subheader("Outbound flights")
    st.dataframe(_apply_stop_style(out_df, out_stop), use_container_width=True, hide_index=True)

    if st.session_state.inbound_offers:
        st.subheader("Return flights")
        st.dataframe(_apply_stop_style(in_df, in_stop), use_container_width=True, hide_index=True)

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
