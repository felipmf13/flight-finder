from rich.console import Console

from .config import SearchConfig
from .filters import apply_filters

console = Console()

_PROVIDER_MODULES = {
    "skyscanner": "src.providers.skyscanner",
    "scraper": "src.providers.scraper",
}

_SCHEDULE_ONLY: set = set()


def _load(name: str):
    import importlib
    module_path = _PROVIDER_MODULES.get(name)
    if not module_path:
        raise ValueError(f"Unknown provider: '{name}'. Valid options: {list(_PROVIDER_MODULES)}")
    return importlib.import_module(module_path)


def run_search(cfg: SearchConfig) -> list:
    price_providers = [p for p in cfg.providers if p not in _SCHEDULE_ONLY]
    schedule_providers = [p for p in cfg.providers if p in _SCHEDULE_ONLY]

    price_offers: list = []
    schedule_offers: list = []
    tried: list = []

    # Price providers: try in order, stop at first success
    for provider_name in price_providers:
        console.print(f"\n[cyan]Searching via [bold]{provider_name}[/bold]...[/cyan]")
        tried.append(provider_name)
        try:
            provider = _load(provider_name)
            offers = provider.search(cfg)
            console.print(f"  [green]✓[/green] {len(offers)} offer{'s' if len(offers) != 1 else ''} returned")
            price_offers = offers
            break
        except (RuntimeError, ValueError) as e:
            console.print(f"  [yellow]⚠  {e}[/yellow]")
        except Exception as e:
            console.print(f"  [red]✗  {provider_name} failed unexpectedly: {e}[/red]")

    # Schedule providers: always run, supplement price results
    for provider_name in schedule_providers:
        console.print(f"\n[cyan]Fetching schedules via [bold]{provider_name}[/bold]...[/cyan]")
        tried.append(provider_name)
        try:
            provider = _load(provider_name)
            offers = provider.search(cfg)
            console.print(
                f"  [green]✓[/green] {len(offers)} schedule{'s' if len(offers) != 1 else ''} "
                f"returned [dim](no prices)[/dim]"
            )
            schedule_offers.extend(offers)
        except (RuntimeError, ValueError) as e:
            console.print(f"  [yellow]⚠  {e}[/yellow]")
        except Exception as e:
            console.print(f"  [red]✗  {provider_name} failed unexpectedly: {e}[/red]")

    all_offers = price_offers + schedule_offers

    if not all_offers:
        console.print(
            f"\n[red]No results from any provider ({', '.join(tried)}).[/red] "
            "Check your .env credentials and that IATA codes are correct."
        )
        return []

    console.print(f"\n[dim]Applying filters...[/dim]")
    filtered = apply_filters(all_offers, cfg)
    removed = len(all_offers) - len(filtered)
    if removed:
        console.print(
            f"  [dim]{removed} offer{'s' if removed != 1 else ''} removed by filters "
            f"({len(filtered)} remaining)[/dim]"
        )
    else:
        console.print(f"  [dim]All {len(filtered)} offers passed filters[/dim]")

    return filtered
