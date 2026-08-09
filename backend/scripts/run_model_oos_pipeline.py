"""Run the per-symbol Dense/LSTM OOS pipeline after archive readiness.

Research-only. No live exchange orders are submitted.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--archive", default="backend/app/data/archive")
    p.add_argument("--interval", default="4h")
    p.add_argument("--years", type=int, default=5)
    p.add_argument("--min-bars", type=int, default=300)
    p.add_argument("--dense-dir", default="backend/app/models/ai_direction")
    p.add_argument("--lstm-dir", default="backend/app/models/ai_direction_lstm")
    p.add_argument("--output", default="reports/model_oos_pipeline.json")
    args = p.parse_args()

    archive = Path(args.archive)
    model_dirs = {"dense": Path(args.dense_dir), "lstm": Path(args.lstm_dir)}
    missing_models = [name for name, path in model_dirs.items() if not path.exists()]
    csvs = sorted(archive.glob(f"*_{args.interval}.csv"))

    result = {
        "exchange": "Binance Global USDⓈ-M Futures",
        "archive": str(archive),
        "interval": args.interval,
        "years": args.years,
        "symbols_found": len(csvs),
        "models_available": {k: v.exists() for k, v in model_dirs.items()},
        "live_trading": False,
        "status": "READY_FOR_OOS" if csvs and not missing_models else "BLOCKED",
        "blocking_reasons": (["missing_archive"] if not csvs else []) + ([f"missing_{m}_model" for m in missing_models]),
        "next": "invoke per-symbol evaluator with verified model/data compatibility",
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if result["status"] != "READY_FOR_OOS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
