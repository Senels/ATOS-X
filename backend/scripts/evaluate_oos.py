"""Run reproducible OOS evaluation from local Binance archive data.

Research-only command: it never submits exchange orders.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from app.ai.features import build_features, FEATURE_NAMES
from app.ai.model import _archive_frames, _folds, _concat_datasets
from app.backtest.oos_engine import BacktestConfig, run_oos_backtest
from app.backtest.scorecard import rank_scorecards, score_result


def _labels_to_signals(y: np.ndarray, confidence: np.ndarray | None = None, threshold: float = 0.0) -> np.ndarray:
    # Class mapping: 0=short, 1=hold, 2=long.
    out = np.zeros(len(y), dtype=np.int8)
    for i, cls in enumerate(y):
        if confidence is not None and confidence[i] < threshold:
            continue
        out[i] = -1 if int(cls) == 0 else (1 if int(cls) == 2 else 0)
    return out


def evaluate_archive(interval: str, max_symbols: int, min_bars: int, horizon: int,
                      initial_equity: float, fee_rate: float, slippage_bps: float,
                      funding_rate: float, stop_loss_pct: float, take_profit_pct: float,
                      output: str) -> dict:
    frames = _archive_frames(interval, max_symbols, min_bars)
    if not frames:
        raise RuntimeError("Local Binance archive verisi bulunamadi")

    # This first evaluator deliberately uses the deterministic label direction
    # as a pipeline smoke-test. It does NOT claim to be model performance.
    X, y, timestamps = _concat_datasets(frames, horizon, 1.0)
    folds = _folds(len(X), horizon)
    results = []
    cfg = BacktestConfig(
        initial_equity=initial_equity, fee_rate=fee_rate,
        slippage_bps=slippage_bps, funding_rate=funding_rate,
        stop_loss_pct=stop_loss_pct, take_profit_pct=take_profit_pct,
    )

    # OOS metrics are calculated only on the first deterministic fold here;
    # model-prediction integration is intentionally a separate next step.
    _, _, test_w = folds[0]
    test_ts = pd.to_datetime(timestamps[test_w.start:test_w.end], utc=True)
    test_frame = pd.DataFrame(index=test_ts)
    test_frame["close"] = 1.0
    test_frame["high"] = 1.0
    test_frame["low"] = 1.0
    signals = _labels_to_signals(y[test_w.start:test_w.end])
    # A constant-price smoke test should produce no meaningful P&L; the output
    # proves wiring, costs and scorecard serialization without inventing alpha.
    result = run_oos_backtest(test_frame, signals, cfg)
    result["evaluation_mode"] = "label_pipeline_smoke_test"
    results.append(score_result("label_pipeline_smoke_test", result))
    ranked = rank_scorecards(results)

    payload = {"samples": int(len(X)), "folds": len(folds), "scorecards": ranked}
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--interval", default="4h")
    p.add_argument("--max-symbols", type=int, default=400)
    p.add_argument("--min-bars", type=int, default=300)
    p.add_argument("--horizon", type=int, default=12)
    p.add_argument("--initial-equity", type=float, default=1000.0)
    p.add_argument("--fee-rate", type=float, default=0.0005)
    p.add_argument("--slippage-bps", type=float, default=2.0)
    p.add_argument("--funding-rate", type=float, default=0.0)
    p.add_argument("--stop-loss-pct", type=float, default=0.02)
    p.add_argument("--take-profit-pct", type=float, default=0.04)
    p.add_argument("--output", default="reports/oos_smoke_scorecard.json")
    args = p.parse_args()
    print(json.dumps(evaluate_archive(**vars(args)), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
