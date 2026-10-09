#!/usr/bin/env python3
"""CLI entry point for tracking Royal Caribbean Club Royale casino offers."""

from __future__ import annotations

import argparse

# Pull in explicit __all__ lists from submodules
from royal_caribbean.config.loaders import __all__ as _loaders_all
from royal_caribbean.core.casino import __all__ as _casino_all
from royal_caribbean.utils.logging import __all__ as _logging_all

from royal_caribbean.config.loaders import build_apprise, load_config_file
from royal_caribbean.core.casino import CasinoOffer, build_account, fetch_casino_offers, report_offers
from royal_caribbean.utils.logging import log, setup_hybrid_logging

# Deduplicate names while maintaining deterministic order
__all__ = list(dict.fromkeys(_loaders_all + _casino_all + _logging_all + ["main"]))


def main() -> None:
    """Loads config, authenticates, fetches Club Royale offers, and reports deadlines."""
    parser = argparse.ArgumentParser(
        description="Check Royal Caribbean Casino Offers"
    )
    parser.add_argument(
        "-c",
        "--config",
        type=str,
        default="config.yaml",
        help="Path to configuration YAML file (default: config.yaml)",
    )
    parser.add_argument(
        "--warn-days",
        type=int,
        default=14,
        help="Alert when an offer's reserve-by date is within this many days (default: 14)",
    )
    args = parser.parse_args()

    data = load_config_file(args.config)
    setup_hybrid_logging(data.get("logFile"))

    apobj = build_apprise(data.get("apprise") or [])
    account_info = build_account(data)
    offers = fetch_casino_offers(account_info)
    report_offers(offers, args.warn_days, apobj)


if __name__ == "__main__":
    main()
