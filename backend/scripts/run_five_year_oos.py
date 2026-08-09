"""Validate local Binance Global USD-M Futures archive coverage.

Research-only: no network access and no exchange orders.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def discover(data_dir: Path, interval: str, years: float, min_bars: int) -> list[dict]:
    cutoff = pd.Timestamp.now(tz="UTC") - pd.DateOffset(days=365.25 * years)
    rows = []
    for path in sorted(data_dir.glob(f"*_{interval}.csv")):
        try:
            df = pd.read_csv(path, usecols=["timestamp"])
            if len(df) < min_bars:
                continue
            raw = df["timestamp"]
            if pd.api.types.is_numeric_dtype(raw):
                ts = pd.to_datetime(pd.to_numeric(raw, errors="coerce"), unit="ms", utc=True).dropna()
            else:
                ts = pd.to_datetime(raw, utc=True, errors="coerce").dropna()
            if ts.empty:
                continue
            first, last = ts.min(), ts.max()
            rows.append({
                "symbol": path.name.rsplit("_", 1)[0],
                "file": str(path),
                "bars": int(len(ts)),
                "first": first.isoformat(),
                "last": last.isoformat(),
                "coverage_days": float((last - first).total_seconds() / 86400),
                "five_year_coverage": bool(first <= cutoff),
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
    qualified = [r for r in rows if r["five_year_coverage"]]
    payload = {
        "exchange": "Binance Global USD-M Futures",
        "interval": args.interval,
        "requested_years": args.years,
        "qualified_symbols": len(qualified),
        "symbols": rows,
        "status": "READY" if qualified else "INSUFFICIENT_HISTORY",
        "note": "Coverage validation only; no model P&L is inferred from archive metadata.",
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    if not qualified:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
