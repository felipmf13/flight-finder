import json
from datetime import datetime
from pathlib import Path

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .config import SearchConfig
from .models import FlightOffer

console = Console()

CURRENCY_SYMBOLS = {
    "EUR": "€",
    "USD": "$",
    "GBP": "£",
    "JPY": "¥",
    "CHF": "CHF ",
}


def _sym(currency: str) -> str:
    return CURRENCY_SYMBOLS.get(currency, currency + " ")


def _fmt_duration(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    return f"{h}h {m:02d}m"


def _fmt_stops(stops: int) -> tuple:
    if stops == 0:
        return "Direct", "bold green"
    if stops == 1:
        return "1 stop", "yellow"
    return f"{stops} stops", "red"


def _sort_offers(offers: list, sort_by: str) -> list:
    if sort_by == "price":
        # Schedule-only offers (no price) always sort last
        return sorted(offers, key=lambda o: o.price_per_person if o.price_available else float("inf"))
    if sort_by == "duration":
        return sorted(offers, key=lambda o: o.outbound.duration_minutes)
    if sort_by == "departure_time":
        return sorted(offers, key=lambda o: o.outbound.departure)
    return offers


def print_results(offers: list, cfg: SearchConfig) -> None:
    sym = _sym(cfg.currency)
    origin_label = cfg.origin.iata or cfg.origin.city or "?"
    dest_label = cfg.destination.iata or cfg.destination.city or "?"
    is_round_trip = any(o.inbound for o in offers)
    trip_label = "Round Trip" if is_round_trip else "One Way"

    if not offers:
        console.print()
        console.print(
            Panel(
                "[yellow]No flights found matching your criteria.[/yellow]\n"
                "Try relaxing filters (time windows, max price, max stops) or expanding your dates.",
                title=f"[bold]{origin_label} → {dest_label}[/bold]",
                border_style="yellow",
            )
        )
        return

    offers = _sort_offers(offers, cfg.sort_by)[: cfg.max_results]

    console.print()
    console.print(
        Panel(
            f"[bold cyan]{origin_label}  →  {dest_label}[/bold cyan]   "
            f"[dim]{trip_label}  ·  {len(offers)} result{'s' if len(offers) != 1 else ''}  ·  "
            f"sorted by {cfg.sort_by}[/dim]",
            border_style="cyan",
            padding=(0, 1),
        )
    )
    console.print()

    # --- Outbound table ---
    out_table = Table(
        box=box.ROUNDED,
        show_header=True,
        header_style="bold magenta",
        border_style="bright_black",
        title="[bold]Outbound[/bold]" + (" [dim](price covers full round trip)[/dim]" if is_round_trip else ""),
        title_style="bold white",
        padding=(0, 1),
    )
    out_table.add_column("Airline", style="cyan", no_wrap=True, min_width=18)
    out_table.add_column("Date", style="white", no_wrap=True)
    out_table.add_column("Dep.", style="bold green", no_wrap=True)
    out_table.add_column("Arr.", style="green", no_wrap=True)
    out_table.add_column("Duration", style="yellow", no_wrap=True)
    out_table.add_column("Stops", no_wrap=True)
    out_table.add_column("Flight", style="bright_black", no_wrap=True)
    out_table.add_column("Price/pax", style="bold green", justify="right", no_wrap=True)
    out_table.add_column("Via", style="bright_black", no_wrap=True)

    for offer in offers:
        itin = offer.outbound
        stops_label, stops_style = _fmt_stops(itin.stops)
        via = " → ".join(s.destination for s in itin.segments[:-1]) if itin.stops > 0 else ""
        price_str = (
            f"{sym}{offer.price_per_person:.2f}"
            if offer.price_available
            else Text("— (schedule only)", style="dim")
        )
        out_table.add_row(
            f"{itin.airline_name} ({itin.airline})",
            itin.departure.strftime("%b %d"),
            itin.departure.strftime("%H:%M"),
            itin.arrival.strftime("%H:%M"),
            _fmt_duration(itin.duration_minutes),
            Text(stops_label, style=stops_style),
            itin.segments[0].flight_number,
            price_str,
            via,
        )

    console.print(out_table)

    # --- Inbound table (round trips) ---
    inbound_offers = [o for o in offers if o.inbound]
    if inbound_offers:
        console.print()
        in_table = Table(
            box=box.ROUNDED,
            show_header=True,
            header_style="bold magenta",
            border_style="bright_black",
            title="[bold]Return[/bold]",
            title_style="bold white",
            padding=(0, 1),
        )
        in_table.add_column("Airline", style="cyan", no_wrap=True, min_width=18)
        in_table.add_column("Date", style="white", no_wrap=True)
        in_table.add_column("Dep.", style="bold green", no_wrap=True)
        in_table.add_column("Arr.", style="green", no_wrap=True)
        in_table.add_column("Duration", style="yellow", no_wrap=True)
        in_table.add_column("Stops", no_wrap=True)
        in_table.add_column("Flight", style="bright_black", no_wrap=True)
        in_table.add_column("Via", style="bright_black", no_wrap=True)

        for offer in inbound_offers:
            itin = offer.inbound
            stops_label, stops_style = _fmt_stops(itin.stops)
            via = " → ".join(s.destination for s in itin.segments[:-1]) if itin.stops > 0 else ""
            in_table.add_row(
                f"{itin.airline_name} ({itin.airline})",
                itin.departure.strftime("%b %d"),
                itin.departure.strftime("%H:%M"),
                itin.arrival.strftime("%H:%M"),
                _fmt_duration(itin.duration_minutes),
                Text(stops_label, style=stops_style),
                itin.segments[0].flight_number,
                via,
            )

        console.print(in_table)

    # --- Best deal summary (priced offers only) ---
    priced = [o for o in offers if o.price_available]
    console.print()
    if priced:
        best = priced[0]
        out = best.outbound
        stops_label, _ = _fmt_stops(out.stops)
        console.print(
            f"  [bold green]Best deal:[/bold green] "
            f"[cyan]{out.airline_name} ({out.airline})[/cyan] "
            f"{out.segments[0].flight_number} — "
            f"{out.departure.strftime('%b %d')}, "
            f"{out.departure.strftime('%H:%M')} → {out.arrival.strftime('%H:%M')}, "
            f"{stops_label}, "
            f"[bold green]{sym}{best.price_per_person:.2f}/person[/bold green]"
            + (f"  [dim]({trip_label})[/dim]" if is_round_trip else "")
        )
    else:
        console.print(
            "  [dim]Schedule data only — no prices available. "
            "Add a price provider (duffel) to see costs.[/dim]"
        )
    console.print()


def export_results(offers: list, cfg: SearchConfig, fmt: str) -> None:
    output_dir = Path("data/results")
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    if fmt == "csv":
        try:
            import pandas as pd
        except ImportError:
            console.print("[red]pandas not installed. Run: pip install pandas[/red]")
            return

        rows = []
        for offer in offers:
            row = {
                "provider": offer.provider,
                "outbound_airline": offer.outbound.airline,
                "outbound_airline_name": offer.outbound.airline_name,
                "outbound_flight": offer.outbound.segments[0].flight_number,
                "outbound_date": offer.outbound.departure.strftime("%Y-%m-%d"),
                "outbound_dep": offer.outbound.departure.strftime("%H:%M"),
                "outbound_arr": offer.outbound.arrival.strftime("%H:%M"),
                "outbound_duration_min": offer.outbound.duration_minutes,
                "outbound_stops": offer.outbound.stops,
                "price_per_person": round(offer.price_per_person, 2),
                "total_price": round(offer.price, 2),
                "currency": offer.currency,
                "cabin_class": offer.cabin_class,
            }
            if offer.inbound:
                row.update({
                    "inbound_airline": offer.inbound.airline,
                    "inbound_flight": offer.inbound.segments[0].flight_number,
                    "inbound_date": offer.inbound.departure.strftime("%Y-%m-%d"),
                    "inbound_dep": offer.inbound.departure.strftime("%H:%M"),
                    "inbound_arr": offer.inbound.arrival.strftime("%H:%M"),
                    "inbound_duration_min": offer.inbound.duration_minutes,
                    "inbound_stops": offer.inbound.stops,
                })
            rows.append(row)

        path = output_dir / f"flights_{timestamp}.csv"
        pd.DataFrame(rows).to_csv(path, index=False)
        console.print(f"[green]Exported {len(rows)} results → {path}[/green]")

    elif fmt == "json":
        rows = []
        for offer in offers:
            def _seg_dict(s):
                return {
                    "origin": s.origin,
                    "destination": s.destination,
                    "departure": s.departure.isoformat(),
                    "arrival": s.arrival.isoformat(),
                    "airline": s.airline,
                    "airline_name": s.airline_name,
                    "flight_number": s.flight_number,
                    "duration_minutes": s.duration_minutes,
                }

            def _itin_dict(itin):
                return {
                    "origin": itin.origin,
                    "destination": itin.destination,
                    "departure": itin.departure.isoformat(),
                    "arrival": itin.arrival.isoformat(),
                    "duration_minutes": itin.duration_minutes,
                    "stops": itin.stops,
                    "segments": [_seg_dict(s) for s in itin.segments],
                }

            rows.append({
                "id": offer.id,
                "provider": offer.provider,
                "price_per_person": round(offer.price_per_person, 2),
                "total_price": round(offer.price, 2),
                "currency": offer.currency,
                "cabin_class": offer.cabin_class,
                "outbound": _itin_dict(offer.outbound),
                "inbound": _itin_dict(offer.inbound) if offer.inbound else None,
            })

        path = output_dir / f"flights_{timestamp}.json"
        path.write_text(json.dumps(rows, indent=2, ensure_ascii=False))
        console.print(f"[green]Exported {len(rows)} results → {path}[/green]")
