from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Optional
import yaml


@dataclass
class AirportConfig:
    city: str = ""
    iata: str = ""


@dataclass
class TimeWindow:
    earliest: str = ""
    latest: str = ""


@dataclass
class SearchConfig:
    origin: AirportConfig
    destination: AirportConfig
    outbound_dates: list
    inbound_dates: list
    outbound_departure_window: TimeWindow
    inbound_departure_window: TimeWindow
    adults: int
    children: int
    infants: int
    cabin_class: str
    include_airlines: list
    exclude_airlines: list
    max_stops: Optional[int]
    max_price_per_person: Optional[float]
    currency: str
    providers: list
    max_results: int
    sort_by: str
    request_delay_seconds: float = 1.0


def _expand_dates(raw) -> list:
    if not raw:
        return []
    if isinstance(raw, list):
        return [date.fromisoformat(str(d)) for d in raw]
    if isinstance(raw, dict):
        r = raw.get("range", raw)
        start = date.fromisoformat(str(r["from"]))
        end = date.fromisoformat(str(r["to"]))
        result, current = [], start
        while current <= end:
            result.append(current)
            current += timedelta(days=1)
        return result
    return []


def load_config(path: str = "config.yaml") -> SearchConfig:
    config_path = Path(path)
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(config_path) as f:
        raw = yaml.safe_load(f) or {}

    origin_raw = raw.get("origin") or {}
    dest_raw = raw.get("destination") or {}
    out_window = raw.get("outbound_departure_window") or {}
    in_window = raw.get("inbound_departure_window") or {}

    cfg = SearchConfig(
        origin=AirportConfig(
            city=str(origin_raw.get("city") or ""),
            iata=str(origin_raw.get("iata") or "").upper(),
        ),
        destination=AirportConfig(
            city=str(dest_raw.get("city") or ""),
            iata=str(dest_raw.get("iata") or "").upper(),
        ),
        outbound_dates=_expand_dates(raw.get("outbound_dates")),
        inbound_dates=_expand_dates(raw.get("inbound_dates")),
        outbound_departure_window=TimeWindow(
            earliest=str(out_window.get("earliest") or ""),
            latest=str(out_window.get("latest") or ""),
        ),
        inbound_departure_window=TimeWindow(
            earliest=str(in_window.get("earliest") or ""),
            latest=str(in_window.get("latest") or ""),
        ),
        adults=int(raw.get("adults") or 1),
        children=int(raw.get("children") or 0),
        infants=int(raw.get("infants") or 0),
        cabin_class=str(raw.get("cabin_class") or ""),
        include_airlines=[str(c) for c in (raw.get("include_airlines") or [])],
        exclude_airlines=[str(c) for c in (raw.get("exclude_airlines") or [])],
        max_stops=raw.get("max_stops"),
        max_price_per_person=raw.get("max_price_per_person"),
        currency=str(raw.get("currency") or "EUR").upper(),
        providers=list(raw.get("providers") or ["google_flights"]),
        max_results=int(raw.get("max_results") or 20),
        sort_by=str(raw.get("sort_by") or "price"),
        request_delay_seconds=float(raw.get("request_delay_seconds") or 1.0),
    )

    _validate(cfg)
    return cfg


def _validate(cfg: SearchConfig) -> None:
    if not cfg.outbound_dates:
        raise ValueError(
            "No outbound_dates specified. Provide at least one date or a date range."
        )
    if not cfg.origin.iata and not cfg.origin.city:
        raise ValueError("origin.iata or origin.city is required.")
    if not cfg.destination.iata and not cfg.destination.city:
        raise ValueError("destination.iata or destination.city is required.")
    if cfg.sort_by not in ("price", "duration", "departure_time"):
        raise ValueError(f"Invalid sort_by: '{cfg.sort_by}'. Use price, duration, or departure_time.")
