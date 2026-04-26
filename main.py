#!/usr/bin/env python3
"""
flights — flight price and schedule finder
Usage:
  python main.py                         # use config.yaml
  python main.py --config my.yaml        # use a custom config
  python main.py --export csv            # also export results to data/results/
  python main.py --export json
"""
import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Find flights matching your criteria using official APIs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        metavar="FILE",
        help="Path to YAML config file (default: config.yaml)",
    )
    parser.add_argument(
        "--export",
        choices=["csv", "json"],
        metavar="FORMAT",
        help="Export results to data/results/ (csv or json)",
    )
    args = parser.parse_args()

    # Ensure we import from the project root regardless of cwd
    sys.path.insert(0, str(Path(__file__).parent))

    from src.config import load_config
    from src.output import console, export_results, print_results
    from src.search import run_search

    try:
        cfg = load_config(args.config)
    except FileNotFoundError as e:
        console.print(f"[red]Error:[/red] {e}")
        console.print(
            "Copy [bold]config.example.yaml[/bold] → [bold]config.yaml[/bold] "
            "and fill in your search parameters."
        )
        sys.exit(1)
    except (ValueError, Exception) as e:
        console.print(f"[red]Config error:[/red] {e}")
        sys.exit(1)

    offers = run_search(cfg)
    print_results(offers, cfg)

    if args.export and offers:
        export_results(offers, cfg, args.export)


if __name__ == "__main__":
    main()
