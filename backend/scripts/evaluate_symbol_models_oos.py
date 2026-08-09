"""Evaluate saved Dense/LSTM models per Binance Futures symbol on untouched OOS bars.

Research-only. No exchange API calls or live orders are performed.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import tensorflow as tf

from app.ai.model import _archive_frames
from app.ai.sequence import build_sequences
from app.ai.features import FEATURE_NAMES, build_features
from app.ai.labeling import make_labels
from app.data.validation.time_split import PurgedWalkForward
from app.backtest.oos_engine import BacktestConfig, run_oos_backtest
from app.backtest.scorecard import rank_scorecards


def prepare_symbol(df: pd.DataFrame, horizon: int):
    features = build_features(df).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    labels = make_labels(df, horizon=horizon, atr_mult=1.0).rename("y")
    both = pd.concat([features, labels], axis=1).dropna()
    both = both[both["y"] != 0.0]
    return both, both[FEATURE_NAMES].to_numpy(np.float32), (both["y"].to_numpy(np.float32) + 1).astype(np.int32)


def first_oos_fold(n: int, horizon: int):
    train = max(100, int(n * 0.60))
    val = max(50, int(n * 0.15))
    test = max(50, int(n * 0.15))
    if train + val + test > n:
        test = max(1, int(n * 0.10))
        val = max(1, int(n * 0.10))
        train = n - val - test
    splitter = PurgedWalkForward(train, val, test, step=test, embargo=0, label_horizon=horizon)
    folds = splitter.split(n)
    if not folds:
        raise ValueError("OOS fold olusturulamadi")
    return folds[0][2]


def predict_dense(model, scaler, X):
    return model.predict(scaler.transform(X).astype(np.float32), verbose=0)


def predict_lstm(model, scaler, X, seq_len):
    scaled = scaler.transform(X).astype(np.float32)
    dummy = np.zeros(len(scaled), dtype=np.int32)
    seq = build_sequences(scaled, dummy, seq_len)
    if len(seq.X) == 0:
        return np.empty((0, 3), dtype=np.float32), np.empty(0, dtype=np.int64)
    return model.predict(seq.X, verbose=0), seq.timestamps


def probs_to_signals(p: np.ndarray, threshold: float) -> np.ndarray:
    if len(p) == 0:
        return np.empty(0, dtype=np.int8)
    cls = np.argmax(p, axis=1)
    conf = np.max(p, axis=1)
    return np.where(conf < threshold, 0, np.where(cls == 0, -1, np.where(cls == 2, 1, 0))).astype(np.int8)


def evaluate_symbol(name, df, dense_model, dense_scaler, lstm_model, lstm_scaler, seq_len, horizon, threshold, cfg):
    both, X, y = prepare_symbol(df, horizon)
    if len(X) < 300:
        return None
    test_w = first_oos_fold(len(X), horizon)
    X_test = X[test_w.start:test_w.end]
    idx_test = both.index[test_w.start:test_w.end]

    dense_p = predict_dense(dense_model, dense_scaler, X_test)
    lstm_p, lstm_ts = predict_lstm(lstm_model, lstm_scaler, X_test, seq_len)
    dense_ts = pd.to_datetime(idx_test, utc=True).astype("int64").to_numpy()

    # LSTM sequence timestamps are local offsets within the test fold.
    # Build an explicit mapping so no positional assumption crosses a boundary.
    lstm_ts = lstm_ts.astype(np.int64)
    common = np.intersect1d(dense_ts, lstm_ts)
    if len(common) == 0:
        return {"symbol": name, "status": "no_common_dense_lstm_timestamps"}
    dmap = {int(t): i for i, t in enumerate(dense_ts)}
    lmap = {int(t): i for i, t in enumerate(lstm_ts)}
    di = np.array([dmap[int(t)] for t in common])
    li = np.array([lmap[int(t)] for t in common])
    dense_common = dense_p[di]
    lstm_common = lstm_p[li]
    ensemble_p = 0.5 * dense_common + 0.5 * lstm_common

    price = df.copy().sort_index()
    price["_ts"] = pd.to_datetime(price.index, utc=True).astype("int64")
    price = price[price["_ts"].isin(common)].sort_values("_ts")
    ordered_common = price["_ts"].to_numpy(np.int64)
    order = np.array([np.where(common == t)[0][0] for t in ordered_common])

    results = {}
    for model_name, p in (("dense", dense_common), ("lstm", lstm_common), ("ensemble", ensemble_p)):
        p = p[order]
        signals = probs_to_signals(p, threshold)
        results[model_name] = run_oos_backtest(price, signals, cfg)
    return {"symbol": name, "status": "ok", "models": results}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--interval", default="4h")
    p.add_argument("--max-symbols", type=int, default=400)
    p.add_argument("--min-bars", type=int, default=300)
    p.add_argument("--horizon", type=int, default=12)
    p.add_argument("--dense-dir", default="backend/app/models/ai_direction")
    p.add_argument("--lstm-dir", default="backend/app/models/ai_direction_lstm")
    p.add_argument("--threshold", type=float, default=0.55)
    p.add_argument("--output", default="reports/symbol_oos_scorecard.json")
    p.add_argument("--initial-equity", type=float, default=1000.0)
    p.add_argument("--fee-rate", type=float, default=0.0005)
    p.add_argument("--slippage-bps", type=float, default=2.0)
    p.add_argument("--funding-rate", type=float, default=0.0)
    p.add_argument("--stop-loss-pct", type=float, default=0.02)
    p.add_argument("--take-profit-pct", type=float, default=0.04)
    args = p.parse_args()

    dense_dir, lstm_dir = Path(args.dense_dir), Path(args.lstm_dir)
    dense_scaler = joblib.load(dense_dir / "scaler.joblib")
    lstm_scaler = joblib.load(lstm_dir / "scaler.joblib")
    lstm_meta = joblib.load(lstm_dir / "meta.joblib")
    dense_model = tf.keras.models.load_model(dense_dir / "model.keras")
    lstm_model = tf.keras.models.load_model(lstm_dir / "model.keras")
    seq_len = int(lstm_meta["sequence_length"])

    cfg = BacktestConfig(args.initial_equity, args.fee_rate, args.slippage_bps, args.funding_rate, args.stop_loss_pct, args.take_profit_pct)
    frames = _archive_frames(args.interval, args.max_symbols, args.min_bars)
    reports = []
    for df in frames:
        try:
            name = getattr(df, "name", None) or "unknown"
            report = evaluate_symbol(name, df, dense_model, dense_scaler, lstm_model, lstm_scaler, seq_len, args.horizon, args.threshold, cfg)
            if report:
                reports.append(report)
        except Exception as exc:
            reports.append({"symbol": "unknown", "status": "error", "error": str(exc)})

    payload = {"mode": "per_symbol_model_oos", "reports": reports}
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=float), encoding="utf-8")
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=float))


if __name__ == "__main__":
    main()
