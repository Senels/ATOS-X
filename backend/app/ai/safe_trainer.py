"""Leakage-safe replacement training path for the ATOS X AI model.

The existing model architecture is reused, but dataset splitting is performed
per symbol in chronological order. The scaler is fitted only on training
observations. This module is training/backtest only and does not place orders.
"""

from __future__ import annotations

from typing import Any, Dict, List

import joblib
import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler

from app.ai import model as legacy
from app.ai.validation import chronological_split, purged_sequence_split


def _split_frame(df: pd.DataFrame, horizon: int, atr_mult: float, val_fraction: float, purge: int):
    X, y = legacy._dataset(df, horizon=horizon, atr_mult=atr_mult)
    if len(X) < 2:
        return X, y, X[:0], y[:0]
    split = chronological_split(len(X), val_fraction=val_fraction, purge=purge)
    return X[split.train], y[split.train], X[split.validation], y[split.validation]


def _scale(scaler: StandardScaler, X: np.ndarray) -> np.ndarray:
    return scaler.transform(X).astype(np.float32)


def train_from_dataframe_safe(
    dfs: List[pd.DataFrame], horizon: int = 24, atr_mult: float = 1.0,
    epochs: int = 30, val_fraction: float = 0.2, seed: int = 7,
    model_name: str = "ai_direction", model_type: str = "dense",
    lstm_seq_len: int = 20,
) -> Dict[str, Any]:
    """Train the existing model architecture without random time splits."""
    if model_type not in {"dense", "lstm", "ensemble"}:
        raise ValueError("model_type must be dense, lstm or ensemble")
    legacy._require_tf()
    np.random.seed(seed)
    try:
        legacy.tf.random.set_seed(seed)
    except AttributeError:
        pass

    # Split independently per symbol so concatenation order cannot leak future data.
    dense_parts = []
    for df in dfs:
        xtr, ytr, xva, yva = _split_frame(df, horizon, atr_mult, val_fraction, purge=horizon)
        if len(xtr) >= 50 and len(xva) >= 10:
            dense_parts.append((xtr, ytr, xva, yva))
    if not dense_parts:
        raise ValueError("Leakage-safe training split has insufficient data")

    X_tr = np.vstack([p[0] for p in dense_parts]).astype(np.float32)
    y_tr = np.concatenate([p[1] for p in dense_parts]).astype(np.int32)
    X_val = np.vstack([p[2] for p in dense_parts]).astype(np.float32)
    y_val = np.concatenate([p[3] for p in dense_parts]).astype(np.int32)

    scaler = StandardScaler()
    X_tr_s = scaler.fit_transform(X_tr).astype(np.float32)
    X_val_s = _scale(scaler, X_val)

    out_dir = legacy.model_dir(model_name)
    out_dir.mkdir(parents=True, exist_ok=True)

    dense_metrics: Dict[str, Any] = {}
    if model_type in {"dense", "ensemble"}:
        dense_model = legacy.build_model(X_tr.shape[1])
        hist = dense_model.fit(X_tr_s, y_tr, validation_data=(X_val_s, y_val), epochs=epochs, batch_size=256, verbose=0)
        dense_metrics = {
            "val_loss": float(hist.history["val_loss"][-1]),
            "val_acc": float(hist.history["val_accuracy"][-1]),
        }
        dense_model.save(out_dir / "model.keras")

    lstm_metrics: Dict[str, Any] = {}
    if model_type in {"lstm", "ensemble"}:
        seq_train, seq_val = [], []
        for df in dfs:
            raw_x, raw_y = legacy._dataset(df, horizon=horizon, atr_mult=atr_mult)
            if len(raw_x) < lstm_seq_len + horizon + 10:
                continue
            split = purged_sequence_split(
                len(raw_x), val_fraction=val_fraction,
                sequence_length=lstm_seq_len, horizon=horizon,
            )
            tr_x, tr_y = raw_x[split.train], raw_y[split.train]
            va_x, va_y = raw_x[split.validation], raw_y[split.validation]
            if len(tr_x) <= lstm_seq_len or len(va_x) <= lstm_seq_len:
                continue
            tr_seq = np.asarray([tr_x[i-lstm_seq_len:i] for i in range(lstm_seq_len, len(tr_x))])
            tr_lab = tr_y[lstm_seq_len:]
            va_seq = np.asarray([va_x[i-lstm_seq_len:i] for i in range(lstm_seq_len, len(va_x))])
            va_lab = va_y[lstm_seq_len:]
            if len(tr_seq) and len(va_seq):
                seq_train.append((tr_seq, tr_lab))
                seq_val.append((va_seq, va_lab))

        if seq_train:
            Xs_tr = np.vstack([x for x, _ in seq_train]).astype(np.float32)
            ys_tr = np.concatenate([y for _, y in seq_train]).astype(np.int32)
            Xs_val = np.vstack([x for x, _ in seq_val]).astype(np.float32)
            ys_val = np.concatenate([y for _, y in seq_val]).astype(np.int32)
            n, t, f = Xs_tr.shape
            # Use the same train-only scaler contract as Predictor.
            Xs_tr_s = scaler.transform(Xs_tr.reshape(-1, f)).reshape(n, t, f).astype(np.float32)
            nv = Xs_val.shape[0]
            Xs_val_s = scaler.transform(Xs_val.reshape(-1, f)).reshape(nv, t, f).astype(np.float32)
            lstm_model = legacy.build_lstm_model(f, seq_len=lstm_seq_len)
            hist_lstm = lstm_model.fit(
                Xs_tr_s, ys_tr, validation_data=(Xs_val_s, ys_val),
                epochs=epochs, batch_size=128, verbose=0,
            )
            lstm_metrics = {
                "val_loss": float(hist_lstm.history["val_loss"][-1]),
                "val_acc": float(hist_lstm.history["val_accuracy"][-1]),
            }
            lstm_model.save(out_dir / "lstm_model.keras")

    meta = {
        "features": legacy.FEATURE_NAMES, "n_classes": legacy._N_CLASSES,
        "horizon": horizon, "atr_mult": atr_mult,
        "model_type": model_type, "lstm_seq_len": lstm_seq_len,
        "validation": "chronological_per_symbol",
        "purge_bars": horizon,
    }
    joblib.dump(meta, out_dir / "meta.joblib")
    joblib.dump(scaler, out_dir / "scaler.joblib")

    if model_type == "dense":
        final = dense_metrics
    elif model_type == "lstm":
        final = lstm_metrics
    else:
        final = {
            "val_loss": round((dense_metrics.get("val_loss", 0.0) + lstm_metrics.get("val_loss", 0.0)) / 2, 4),
            "val_acc": round((dense_metrics.get("val_acc", 0.0) + lstm_metrics.get("val_acc", 0.0)) / 2, 4),
            "dense": dense_metrics, "lstm": lstm_metrics,
        }

    metrics = {
        "samples_train": int(len(X_tr)), "samples_validation": int(len(X_val)),
        "model_type": model_type, "horizon": horizon, "atr_mult": atr_mult,
        "validation": "chronological_per_symbol", **final,
    }
    joblib.dump(metrics, out_dir / "metrics.joblib")
    return {"model_dir": str(out_dir), **metrics}


def train_from_archive_safe(interval: str = "4h", max_symbols: int = 400, min_bars: int = 300, **kwargs):
    """Load the local Binance futures archive and use the safe trainer."""
    symbols = legacy.loader.list_symbols(interval)
    dfs = []
    for sym in symbols[:max_symbols]:
        try:
            df = legacy.loader.load_csv(sym, interval)
        except Exception:
            continue
        if len(df) >= min_bars:
            dfs.append(df)
    if not dfs:
        raise ValueError("Arsivde yeterli sembol verisi yok")
    return train_from_dataframe_safe(dfs, **kwargs)
