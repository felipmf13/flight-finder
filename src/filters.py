from datetime import time
from typing import Optional

from .models import FlightOffer
from .config import SearchConfig, TimeWindow


def _parse_time(t: str) -> Optional[time]:
    if not t:
        return None
    parts = t.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid time format '{t}': expected HH:MM.")
    h, m = parts
    return time(int(h), int(m))


def _in_window(dt_time: time, earliest: Optional[time], latest: Optional[time]) -> bool:
    if earliest and dt_time < earliest:
        return False
    if latest and dt_time > latest:
        return False
    return True


def _any_filter_active(cfg: SearchConfig) -> bool:
    return bool(
        cfg.outbound_departure_window.earliest
        or cfg.outbound_departure_window.latest
        or cfg.inbound_departure_window.earliest
        or cfg.inbound_departure_window.latest
        or cfg.max_stops is not None
        or cfg.include_airlines
        or cfg.exclude_airlines
        or cfg.max_price_per_person is not None
    )


def apply_filters(offers: list, cfg: SearchConfig) -> list:
    if not _any_filter_active(cfg):
        return list(offers)

    out_earliest = _parse_time(cfg.outbound_departure_window.earliest)
    out_latest = _parse_time(cfg.outbound_departure_window.latest)
    in_earliest = _parse_time(cfg.inbound_departure_window.earliest)
    in_latest = _parse_time(cfg.inbound_departure_window.latest)

    results = []
    for offer in offers:
        # Outbound departure time window
        if out_earliest or out_latest:
            if not _in_window(offer.outbound.departure.time(), out_earliest, out_latest):
                continue

        # Inbound departure time window
        if offer.inbound and (in_earliest or in_latest):
            if not _in_window(offer.inbound.departure.time(), in_earliest, in_latest):
                continue

        # Max stops
        if cfg.max_stops is not None:
            if offer.outbound.stops > cfg.max_stops:
                continue
            if offer.inbound and offer.inbound.stops > cfg.max_stops:
                continue

        # Include airlines (any segment of outbound must match)
        if cfg.include_airlines:
            out_airlines = {s.airline for s in offer.outbound.segments}
            in_airlines = {s.airline for s in offer.inbound.segments} if offer.inbound else set()
            if not (out_airlines | in_airlines) & set(cfg.include_airlines):
                continue

        # Exclude airlines
        if cfg.exclude_airlines:
            out_airlines = {s.airline for s in offer.outbound.segments}
            in_airlines = {s.airline for s in offer.inbound.segments} if offer.inbound else set()
            if (out_airlines | in_airlines) & set(cfg.exclude_airlines):
                continue

        # Price cap per person — skip for schedule-only offers (no price data)
        if cfg.max_price_per_person is not None and offer.price_available:
            if offer.price_per_person > cfg.max_price_per_person:
                continue

        results.append(offer)

    return results
