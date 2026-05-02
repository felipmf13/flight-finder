# Flight Finder

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![Streamlit](https://img.shields.io/badge/Streamlit-1.x-FF4B4B.svg)](https://streamlit.io)
[![Last Commit](https://img.shields.io/github/last-commit/felipmf13/flight-finder)](https://github.com/felipmf13/flight-finder/commits/main)
[![Stars](https://img.shields.io/github/stars/felipmf13/flight-finder?style=flat)](https://github.com/felipmf13/flight-finder/stargazers)

Search Google Flights across multiple dates and routes from a clean web UI — no API key required.

Built with Python and Streamlit. Uses TLS fingerprinting to talk directly to Google Flights' internal endpoint, the same one the website uses.

---

## Features

- **Multi-date search** — pick a date range and search every day at once
- **Round-trip support** — outbound and return searched independently and displayed side by side
- **Parallel searches** — multiple airport combinations run concurrently
- **Interactive timeline** — Plotly chart showing all flights by departure time, colour-coded by price
- **Filters** — max stops, departure time windows, price cap, days of week
- **Dark mode** — toggle in the sidebar
- **CSV export** — download results with one click
- **CLI mode** — headless alternative driven by `config.yaml`

---

## Quick Start

Requires Python 3.10+.

```bash
git clone https://github.com/felipmf13/flight-finder.git
cd flight-finder

python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

streamlit run app.py
```

Open [http://localhost:8501](http://localhost:8501) in your browser.

---

## How it works

Google Flights does not expose a public API. This tool uses [`fast-flights`](https://github.com/AWeirdDev/flights), which:

1. Encodes the search as a protobuf payload (the same format the Google Flights website sends)
2. Sends it via [`primp`](https://github.com/deedy5/primp), which impersonates Chrome's TLS fingerprint to avoid bot detection
3. Passes a `SOCS=CAI` cookie to bypass the GDPR consent wall

Results are parsed from the `aria-label` accessibility attributes on flight row elements — more stable than CSS class selectors, which Google rotates via A/B testing.

---

## Web UI

Run `streamlit run app.py` and use the sidebar to configure:

| Section | Options |
|---|---|
| Route | Origin and destination city + airport selection |
| Dates | Outbound and return date ranges, day-of-week filter |
| Departure times | Earliest / latest departure window per leg |
| Passengers & Cabin | Adults, children, infants, cabin class |
| Filters | Max stops, price cap per person |

Results are shown as a sortable table (by price, duration, or departure time) and an interactive timeline chart.

---

## CLI

The CLI reads from `config.yaml` and prints results to the terminal via Rich.

```bash
python main.py                    # uses config.yaml
python main.py --config my.yaml   # custom config file
python main.py --export csv       # also saves to data/results/
```

### Config reference

```yaml
# Route
origin:
  city: "Barcelona"
  iata: "BCN"
destination:
  city: "London"
  iata: ""          # empty = search all airports in the city (LHR, LGW, STN…)

# Dates — list of YYYY-MM-DD, or a range
outbound_dates:
  - "2026-07-10"
  - "2026-07-11"
# outbound_dates:
#   range: { from: "2026-07-07", to: "2026-07-14" }

inbound_dates:
  - "2026-07-17"   # leave empty for one-way

# Departure windows (24h, leave empty for no constraint)
outbound_departure_window:
  earliest: "06:00"
  latest:   "14:00"
inbound_departure_window:
  earliest: ""
  latest:   ""

# Passengers
adults: 1
children: 0
infants: 0

# Cabin: economy | premium_economy | business | first
cabin_class: "economy"

# Airlines (IATA codes — leave empty for all)
include_airlines: []
exclude_airlines: []

# Stops: 0 = direct only, 1 = up to 1 stop, omit for any
max_stops: 1

# Price cap per person (omit for no limit)
max_price_per_person: 300
currency: "GBP"

# Output
max_results: 20
sort_by: "price"    # price | duration | departure_time

# Delay between requests in seconds (increase if getting blocked)
request_delay_seconds: 1.0
```

A fully documented template is available at [`config.example.yaml`](config.example.yaml).

---

## Environment Variables

Create a `.env` file in the project root (never committed):

```
# Optional: rotating proxy for sustained or automated use
# Format: http://user:pass@host:port
PROXY_URL=

# Optional: override the Chrome version primp impersonates
PRIMP_IMPERSONATE=chrome_126
```

---

## Project Structure

```
flight-finder/
├── app.py                      # Streamlit web UI
├── main.py                     # CLI entry point
├── config.yaml                 # Your search config (gitignored)
├── config.example.yaml         # Documented config template
├── requirements.txt
├── src/
│   ├── providers/
│   │   └── google_flights.py   # Google Flights scraper
│   ├── config.py               # Config loader and validator
│   ├── search.py               # Search orchestration
│   ├── filters.py              # Post-fetch filtering
│   ├── output.py               # CLI table renderer
│   └── models.py               # FlightOffer / Itinerary / Segment
└── data/
    ├── airports.json           # Cached airport list (auto-refreshed every 30 days)
    └── results/                # CSV/JSON exports (CLI only)
```

---

## Tips

- **Getting blocked?** Increase `request_delay_seconds` in your config, or set `PROXY_URL` in `.env` for sustained use.
- **Date ranges multiply requests** — 7 outbound × 7 return = 14 requests per airport pair. Prefer specific dates when you can.
- **Airport cache** — `data/airports.json` refreshes automatically every 30 days. Delete it to force an immediate update.
- **Multi-airport cities** — selecting multiple destination airports (e.g. LHR + LGW + STN) runs all combinations in parallel.

---

## Dependencies

| Package | Purpose |
|---|---|
| `fast-flights` | Google Flights scraper (bundles primp, selectolax, protobuf) |
| `streamlit` | Web UI |
| `plotly` | Interactive flight timeline chart |
| `pandas` | Table display and CSV export |
| `pyyaml` | Config file parsing |
| `rich` | Terminal table output (CLI) |
| `python-dotenv` | `.env` loading |
