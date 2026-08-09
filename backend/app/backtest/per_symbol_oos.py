"""Per-symbol OOS evaluation helpers.

Keeps each Binance Futures symbol as an independent price series so OHLC
and model samples are never concatenated across symbols.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from app.ai.features import FEATURE_NAMES, build_features
from app.ai.labeling import make_labels
from app.data.validation.time_split import PurgedWalkForward
from app.backtest.oos_engine import BacktestConfig, run_oos_backtest


def load_symbol_archive(path: str | Path, min_bars: int = 300) -> pd.DataFrame:
    p = Path(path)
    df = pd.read_csv(p)
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        df = df.set_index("timestamp")
    else:
        df.index = pd.to_datetime(df.index, utc=True)
    df = df.sort_index()
    if len(df) < min_bars:
        raise ValueError(f"Yetersiz bar: {p.name} ({len(df)} < {min_bars})")
    return df


def prepare_symbol(df: pd.DataFrame, horizon: int = 12, atr_mult: float = 1.0):
    features = build_features(df).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    labels = make_labels(df, horizon=horizon, atr_mult=atr_mult).rename("y")
    both = pd.concat([features, labels], axis=1).dropna()
    both = both[both["y"] != 0.0]
    X = both[FEATURE_NAMES].to_numpy(dtype=np.float32)
    y = (both["y"].to_numpy(dtype=np.float32) + 1.0).astype(np.int32)
    return both, X, y


def symbol_oos_fold(df: pd.DataFrame, horizon: int = 12, embargo: int = 0):
    both, X, y = prepare_symbol(df, horizon=horizon)
    n = len(X)
    train = max(100, int(n * 0.60))
    val = max(50, int(n * 0.15))
    test = max(50, int(n * 0.15))
    if train + val + test > n:
        test = max(1, int(n * 0.10))
        val = max(1, int(n * 0.10))
        train = n - val - test
    splitter = PurgedWalkForward(train, val, test, step=test, embargo=embargo, label_horizon=horizon)
    folds = splitter.split(n)
    if not folds:
        raise ValueError("OOS fold olusturulamadi")
    return both, X, y, folds


def labels_to_oos_signals(labels: np.ndarray) -> np.ndarray:
    # 0=short, 1=hold, 2=long
    return np.where(labels == 0, -1, np.where(labels == 2, 1, 0)).astype(np.int8)


def run_symbol_smoke_backtest(df: pd.DataFrame, horizon: int = 12, config: BacktestConfig | None = None) -> dict[str, Any]:
    both, X, y, folds = symbol_oos_fold(df, horizon=horizon)
    _, _, test_w = folds[0]
    test_index = both.index[test_w.start:test_w.end]
    price = df.reindex(test_index).dropna(subset=["close"])
    y_test = y[test_w.start:test_w.end]
    signals = labels_to_oos_signals(y_test)
    signals = signals[: len(price)]
    result = run_oos_backtest(price, signals, config)
    result["mode"] = "label_pipeline_smoke_test"
    return result
