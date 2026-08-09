"""Single entry point for the ATOS-X Binance Global Futures research pipeline.

The command is intentionally research-only: it can validate local archive data
and run the existing OOS orchestration, but it never places live orders.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def run(cmd: list[str]) -> None:
    print("$", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--years", type=float, default=5.0)
    p.add_argument("--interval", default="4h")
    p.add_argument("--archive", default="backend/app/data/archive")
    p.add_argument("--download", action="store_true", help="download missing Binance Global USD-M Futures archive first")
    p.add_argument("--max-symbols", type=int, default=400)
    p.add_argument("--min-bars", type=int, default=300)
    p.add_argument("--output", default="reports/research_manifest.json")
    args = p.parse_args()

    root = Path(args.archive)
    root.mkdir(parents=True, exist_ok=True)

    if args.download:
        run([sys.executable, "backend/scripts/download_binance_futures_history.py",
             "--interval", args.interval, "--years", str(args.years),
             "--max-symbols", str(args.max_symbols), "--output-dir", str(root)])

    validation = [sys.executable, "backend/scripts/run_five_year_oos.py",
                  "--data-dir", str(root), "--interval", args.interval,
                  "--years", str(args.years), "--min-bars", str(args.min_bars)]
    try:
        run(validation)
        status = "READY"
    except subprocess.CalledProcessError:
        status = "INSUFFICIENT_HISTORY"

    manifest = {
        "exchange": "Binance Global USDⓈ-M Futures",
        "years": args.years,
        "interval": args.interval,
        "archive": str(root),
        "status": status,
        "next_stage": "per-symbol OOS model evaluation" if status == "READY" else "download/repair archive",
        "live_trading": False,
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    if status != "READY":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
