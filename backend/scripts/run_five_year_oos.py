"""Prepare and run a deterministic per-symbol OOS research batch.

This runner downloads no data itself. Run download_binance_futures_history.py first.
It validates that each CSV has sufficient history, then invokes the existing
per-symbol evaluator. It never submits exchange orders.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def discover(data_dir: Path, interval: str, years: float, min_bars: int) -> list[dict]:
    required_ms = years * 365.25 * 24 * 60 * 60 * 1000
    rows = []
    for path in sorted(data_dir.glob(f"*_{interval}.csv")):
        try:
            df = pd.read_csv(path, usecols=["timestamp"])
            if df.empty:
                continue
            ts = pd.to_numeric(df["timestamp"], errors="coerce").dropna()
            if len(ts) < min_bars:
                continue
            coverage = float(ts.max() - ts.min())
            rows.append({
                "symbol": path.name.rsplit("_", 1)[0],
                "file": str(path),
                "bars": int(len(ts)),
                "coverage_days": coverage / 86_400_000,
                "five_year_coverage": coverage >= required_ms,
            })
        except Exception:
            continue
    return rows


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--data-dir", default="backend/app/data/archive")
    p.add_argument("--interval", default="4h")
    p.add_argument("--years", type=float, default=5.0)
    p.add_argument("--min-bars", type=int, default=300)
    p.add_argument("--output", default="reports/five_year_oos_manifest.json")
    args = p.parse_args()

    rows = discover(Path(args.data_dir), args.interval, args.years, args.min_bars)
    if not rows:
        raise SystemExit("No qualifying Binance Global Futures archive files found. Run the downloader first.")

    qualified = [r for r in rows if r["five_year_coverage"]]
    payload = {
        "exchange": "Binance Global USDⓈ-M Futures",
        "interval": args.interval,
        "requested_years": args.years,
        "qualified_symbols": len(qualified),
        "symbols": rows,
        "status": "READY" if qualified else "INSUFFICIENT_HISTORY",
        "note": "This manifest validates archive coverage only. Model P&L is not inferred from coverage metadata.",
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    if not qualified:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
