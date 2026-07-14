"""
CLI flags that must be applied before `polybot.config` loads (dotenv does not
override existing environment variables by default).
"""
from __future__ import annotations

import argparse
import os
import sys


def apply_cli_env() -> None:
    parser = argparse.ArgumentParser(
        description="Polymarket weather bot",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Live trading (requires --confirm and --i-understand-risks)",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Confirm intent to trade with real funds",
    )
    parser.add_argument(
        "--i-understand-risks",
        action="store_true",
        dest="risks",
        help="Acknowledge risk of capital loss",
    )
    args, _unknown = parser.parse_known_args()
    if args.live:
        if not (args.confirm and args.risks):
            print(
                "Live trading requires: --live --confirm --i-understand-risks",
                file=sys.stderr,
            )
            sys.exit(2)
        os.environ["PAPER_MODE"] = "false"
