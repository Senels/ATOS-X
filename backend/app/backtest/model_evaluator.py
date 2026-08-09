"""Evaluate model probabilities on an untouched OOS frame."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .oos_engine import BacktestConfig, run_oos_backtest
from .scorecard import rank_scorecards


def probabilities_to_signals(probabilities: np.ndarray, threshold: float = 0.55) -> np.ndarray:
    """Convert [short, hold, long] probabilities to {-1,0,+1}."""
    p = np.asarray(probabilities, dtype=float)
    if p.ndim != 2 or p.shape[1] != 3:
        raise ValueError("probabilities shape'i (n, 3) olmali: short/hold/long")
    cls = np.argmax(p, axis=1)
    confidence = np.max(p, axis=1)
    signals = np.zeros(len(p), dtype=np.int8)
    signals[(cls == 0) & (confidence >= threshold)] = -1
    signals[(cls == 2) & (confidence >= threshold)] = 1
    return signals


def evaluate_oos_models(frame: pd.DataFrame, predictions: dict[str, np.ndarray],
                        config: BacktestConfig | None = None,
                        threshold: float = 0.55) -> list[dict[str, Any]]:
    """Run identical OOS trading assumptions for each model prediction set."""
    rows = []
    for name, probs in predictions.items():
        signals = probabilities_to_signals(probs, threshold=threshold)
        result = run_oos_backtest(frame, signals, config=config)
        rows.append({"name": name, **result})
    return rank_scorecards(rows)


def build_probability_ensemble(predictions: dict[str, np.ndarray], weights: dict[str, float] | None = None) -> np.ndarray:
    """Build a normalized probability-weighted ensemble.

    This function does not learn weights from the test period. Callers must
    supply weights derived exclusively from prior training/validation data.
    """
    if not predictions:
        raise ValueError("Ensemble icin prediction yok")
    names = list(predictions)
    arrays = [np.asarray(predictions[n], dtype=float) for n in names]
    if any(a.ndim != 2 or a.shape[1] != 3 for a in arrays):
        raise ValueError("Tum prediction'lar (n,3) olmali")
    if len({a.shape[0] for a in arrays}) != 1:
        raise ValueError("Prediction uzunluklari esit olmali")
    w = np.asarray([1.0 if weights is None else weights.get(n, 0.0) for n in names], dtype=float)
    if np.any(w < 0) or w.sum() <= 0:
        raise ValueError("Ensemble agirliklari pozitif olmali")
    w /= w.sum()
    out = sum(weight * arr for weight, arr in zip(w, arrays))
    out /= np.maximum(out.sum(axis=1, keepdims=True), 1e-12)
    return out
