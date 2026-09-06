"""Ejecuta el job una vez y sale. Util para cron/systemd o para probar a mano.

    python scripts/run_job.py
    python scripts/run_job.py --tickers AAPL,SAN.MC
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.jobs import run_daily_job  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Job diario de la cartera")
    parser.add_argument("--tickers", help="lista separada por comas; por defecto los del .env")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    only = None
    if args.tickers:
        only = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]

    stats = run_daily_job(only)
    print(stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
