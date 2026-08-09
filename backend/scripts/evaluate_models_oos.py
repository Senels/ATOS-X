"""Evaluate saved Dense/LSTM models on a common, untouched OOS fold.

Research-only: no exchange orders. Model artifacts must already exist.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from app.ai.model import _archive_frames, _concat_datasets, _folds
from app.ai.sequence import build_sequences
from app.backtest.oos_engine import BacktestConfig, run_oos_backtest
from app.backtest.model_evaluator import probabilities_to_signals
from app.backtest.scorecard import rank_scorecards, score_result


def _load_model(model_dir: Path):
    return joblib.load(model_dir / "meta.joblib"), joblib.load(model_dir / "scaler.joblib")


def _predict_dense(model, scaler, X):
    return model.predict(scaler.transform(X).astype(np.float32), verbose=0)


def _predict_lstm(model, scaler, X, y, seq_len):
    Xs = scaler.transform(X).astype(np.float32)
    seq = build_sequences(Xs, y, seq_len)
    if not len(seq.X):
        return np.empty((0, 3), dtype=np.float32), seq
    return model.predict(seq.X, verbose=0), seq


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--interval", default="4h")
    p.add_argument("--max-symbols", type=int, default=400)
    p.add_argument("--min-bars", type=int, default=300)
    p.add_argument("--horizon", type=int, default=12)
    p.add_argument("--dense-dir", default="backend/app/models/ai_direction")
    p.add_argument("--lstm-dir", default="backend/app/models/ai_direction_lstm")
    p.add_argument("--threshold", type=float, default=0.55)
    p.add_argument("--output", default="reports/oos_model_scorecard.json")
    args = p.parse_args()

    frames = _archive_frames(args.interval, args.max_symbols, args.min_bars)
    if not frames:
        raise RuntimeError("Local Binance archive verisi bulunamadi")
    X, y, timestamps = _concat_datasets(frames, args.horizon, 1.0)
    folds = _folds(len(X), args.horizon)
    _, _, test_w = folds[0]

    dense_meta, dense_scaler = _load_model(Path(args.dense_dir))
    lstm_meta, lstm_scaler = _load_model(Path(args.lstm_dir))
    dense_model = joblib.load(Path(args.dense_dir) / "model.joblib") if (Path(args.dense_dir) / "model.joblib").exists() else None
    import tensorflow as tf
    dense_model = dense_model or tf.keras.models.load_model(Path(args.dense_dir) / "model.keras")
    lstm_model = tf.keras.models.load_model(Path(args.lstm_dir) / "model.keras")

    X_test = X[test_w.start:test_w.end]
    y_test = y[test_w.start:test_w.end]
    ts = pd.to_datetime(timestamps[test_w.start:test_w.end], utc=True)
    base = pd.DataFrame(index=ts)
    # The archive has multiple symbols; a single aggregated OHLC stream cannot
    # be reconstructed from _concat_datasets. This evaluator therefore only
    # exports model probabilities for the common OOS sample, not P&L.
    dense_p = _predict_dense(dense_model, dense_scaler, X_test)
    lstm_p, lstm_seq = _predict_lstm(lstm_model, lstm_scaler, X_test, y_test, int(lstm_meta["sequence_length"]))
    payload = {
        "mode": "model_probability_oos",
        "samples": int(len(X_test)),
        "dense": {"samples": int(len(dense_p)), "mean_confidence": float(np.max(dense_p, axis=1).mean())},
        "lstm": {"samples": int(len(lstm_p)), "mean_confidence": float(np.max(lstm_p, axis=1).mean()) if len(lstm_p) else 0.0},
        "warning": "Multi-symbol concatenation is not a price series; trading P&L requires per-symbol OOS OHLC alignment and is intentionally not fabricated here.",
    }
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
