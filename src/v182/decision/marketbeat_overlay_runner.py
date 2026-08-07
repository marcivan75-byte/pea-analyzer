"""Compatibility entry point for MarketBeat W09B.

All implementation lives in :mod:`v182.decision.marketbeat_overlay`.
This module deliberately contains no selection, scoring, mapping or overlay logic;
it only preserves the stable module path used by GitHub Actions workflows.
"""

from v182.decision.marketbeat_overlay import apply_marketbeat_overlay, main

__all__ = ["apply_marketbeat_overlay", "main"]


if __name__ == "__main__":
    main()
