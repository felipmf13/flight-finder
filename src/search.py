import io
import sys

from rich.console import Console

from .config import SearchConfig
from .filters import apply_filters

console = Console(file=io.StringIO()) if "streamlit" in sys.modules else Console()

_PROVIDER_MODULES = {
    "google_flights": "src.providers.google_flights",
}


def _load(name: str):
    import importlib
    module_path = _PROVIDER_MODULES.get(name)
    if not module_path:
        raise ValueError(f"Unknown provider: '{name}'. Valid options: {list(_PROVIDER_MODULES)}")
    return importlib.import_module(module_path)


def run_search(cfg: SearchConfig) -> list:
    all_offers: list = []
    tried: list = []

    for provider_name in cfg.providers:
        console.print(f"\n[cyan]Searching via [bold]{provider_name}[/bold]...[/cyan]")
        tried.append(provider_name)
        try:
            provider = _load(provider_name)
            offers = provider.search(cfg)
            console.print(f"  [green]✓[/green] {len(offers)} offer{'s' if len(offers) != 1 else ''} returned")
            all_offers = offers
            break
        except (RuntimeError, ValueError) as e:
            console.print(f"  [yellow]⚠  {e}[/yellow]")
        except Exception as e:
            console.print(f"  [red]✗  {provider_name} failed unexpectedly: {e}[/red]")

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
