"""
Playwright-based scraper fallback.

WARNING: Use only when official APIs are unavailable.
- Scraping flight aggregators likely violates their Terms of Service.
- Anti-bot measures (Cloudflare, DataDome) block naive scrapers.
- A rotating residential proxy (PROXY_URL in .env) is required to avoid IP bans.
- Google Flights' DOM changes frequently — expect maintenance overhead.

Install: pip install playwright && playwright install chromium
"""
import os
import re
from datetime import datetime

from ..config import SearchConfig
from ..models import FlightOffer, Itinerary, Segment


def search(cfg: SearchConfig) -> list:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError(
            "Run: pip install playwright && playwright install chromium"
        )

    origin = cfg.origin.iata or cfg.origin.city
    dest = cfg.destination.iata or cfg.destination.city
    if not origin or not dest:
        raise ValueError("Scraper needs at least a city or IATA for origin and destination.")

    proxy_url = os.getenv("PROXY_URL", "")
    proxy = {"server": proxy_url} if proxy_url else None

    all_offers: list = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            proxy=proxy,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            locale="en-US",
            viewport={"width": 1280, "height": 800},
        )

        for out_date in cfg.outbound_dates:
            for in_date in (cfg.inbound_dates or [None]):
                try:
                    offers = _scrape_google_flights(context, origin, dest, out_date, in_date, cfg)
                    all_offers.extend(offers)
                except Exception as e:
                    print(f"[scraper] {out_date}/{in_date}: {e}")

        browser.close()

    return all_offers


def _build_url(origin: str, dest: str, out_date, in_date) -> str:
    """Build a Google Flights search URL."""
    base = "https://www.google.com/travel/flights"
    out_str = out_date.strftime("%Y-%m-%d")
    if in_date:
        # Round trip
        return (
            f"{base}?q=flights+from+{origin}+to+{dest}"
            f"&tfs=CBwQAhoeEgoyMDI2LTA3LTEwagcIARIDQkNOcgcIARIDTEhSGh4SCjIwMjYtMDctMTdqBwgBEgNMSFJyBwgBEgNCQ04"
        )
    return f"{base}?q=flights+from+{origin}+to+{dest}+{out_str}"


def _scrape_google_flights(context, origin, dest, out_date, in_date, cfg) -> list:
    """
    Scrape Google Flights for a single date pair.

    Implementation note: Google Flights heavily obfuscates its DOM and uses
    dynamically generated class names. The selectors below target relatively
    stable ARIA roles and data attributes, but they WILL break as Google
    updates their frontend. Treat this as a starting skeleton that needs
    re-validation against the current DOM.
    """
    page = context.new_page()
    url = _build_url(origin, dest, out_date, in_date)

    page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    # Wait for flight cards to appear
    page.wait_for_timeout(4_000)

    offers = []

    # Try to find flight result rows — selector must be kept up to date with Google's DOM
    cards = page.query_selector_all("li[class*='pIav2d']")
    if not cards:
        # Fallback: look for any list items that contain price-like text
        cards = page.query_selector_all("ul.Rk10dc > li")

    for card in cards[:30]:
        offer = _parse_card(card, origin, dest, out_date, in_date, cfg)
        if offer:
            offers.append(offer)

    page.close()
    return offers


def _parse_card(card, origin, dest, out_date, in_date, cfg) -> FlightOffer | None:
    """
    Parse a single Google Flights card into a FlightOffer.

    This is intentionally left as a documented skeleton. The actual parsing
    depends on current DOM structure and requires frequent updates.
    Key data points to extract:
      - Departure and arrival times (look for elements with time patterns HH:MM)
      - Airline name (usually in an img alt attribute or span)
      - Duration (usually "X hr Y min")
      - Stops (usually "Nonstop", "1 stop", etc.)
      - Price (look for elements containing currency symbols)
    """
    try:
        text = card.inner_text()
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

        # Extract price — find the first line that looks like a currency amount
        price_str = next(
            (ln for ln in lines if re.search(r"[\$€£]\s*[\d,]+", ln)), None
        )
        if not price_str:
            return None
        price_match = re.search(r"[\d,]+(?:\.\d+)?", price_str.replace(",", ""))
        if not price_match:
            return None
        price = float(price_match.group())

        # Extract times — look for HH:MM patterns
        times = re.findall(r"\b\d{1,2}:\d{2}\s*(?:AM|PM)?\b", text)
        if len(times) < 2:
            return None

        def _to_24h(t: str) -> str:
            t = t.strip()
            if "AM" in t or "PM" in t:
                return datetime.strptime(t, "%I:%M %p").strftime("%H:%M")
            return t

        dep_str = _to_24h(times[0])
        arr_str = _to_24h(times[1])

        from datetime import timedelta
        dep = datetime.strptime(
            f"{out_date.isoformat()} {dep_str}", "%Y-%m-%d %H:%M"
        )
        arr = datetime.strptime(
            f"{out_date.isoformat()} {arr_str}", "%Y-%m-%d %H:%M"
        )
        if arr < dep:
            arr += timedelta(days=1)

        # Extract duration
        dur_match = re.search(r"(\d+)\s*hr\s*(?:(\d+)\s*min)?", text)
        duration = 0
        if dur_match:
            duration = int(dur_match.group(1)) * 60 + int(dur_match.group(2) or 0)
        else:
            duration = int((arr - dep).total_seconds() / 60)

        # Extract stops
        stops = 0
        if "1 stop" in text:
            stops = 1
        elif re.search(r"\d+\s*stop", text):
            m = re.search(r"(\d+)\s*stop", text)
            stops = int(m.group(1)) if m else 1

        # Airline — best-effort from alt text or first line
        airline_name = lines[0] if lines else "Unknown"
        airline_code = "??"

        # Try to get airline img alt
        img = card.query_selector("img")
        if img:
            alt = img.get_attribute("alt") or ""
            if alt:
                airline_name = alt
                # Map known airline names to codes (extend as needed)
                _name_to_code = {
                    "Ryanair": "FR", "Vueling": "VY", "Iberia": "IB",
                    "British Airways": "BA", "EasyJet": "U2", "Wizz Air": "W6",
                    "American Airlines": "AA", "Delta": "DL", "United": "UA",
                    "Lufthansa": "LH", "Air France": "AF", "KLM": "KL",
                }
                airline_code = _name_to_code.get(airline_name, airline_name[:2].upper())

        segment = Segment(
            origin=origin,
            destination=dest,
            departure=dep,
            arrival=arr,
            airline=airline_code,
            airline_name=airline_name,
            flight_number=f"{airline_code}????",
            duration_minutes=duration,
        )
        outbound = Itinerary(segments=[segment])

        import uuid
        return FlightOffer(
            id=str(uuid.uuid4()),
            provider="scraper",
            outbound=outbound,
            inbound=None,
            price=price * cfg.adults,
            currency=cfg.currency,
            cabin_class=cfg.cabin_class or "economy",
            adults=cfg.adults,
        )
    except Exception:
        return None
