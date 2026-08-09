"""Leakage-safe sequence construction for temporal AI models.

Sequences are built independently inside each chronological fold. No sequence
is allowed to consume rows from another fold.
"""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np


@dataclass(frozen=True)
class SequenceBatch:
    X: np.ndarray
    y: np.ndarray
    end_positions: np.ndarray


def build_sequences(X: np.ndarray, y: np.ndarray, seq_len: int) -> SequenceBatch:
    if seq_len < 2:
        raise ValueError("seq_len en az 2 olmali")
    if len(X) != len(y):
        raise ValueError("X ve y uzunluklari esit olmali")
    if len(X) <= seq_len:
        return SequenceBatch(
            np.empty((0, seq_len, X.shape[1]), dtype=np.float32),
            np.empty(0, dtype=np.int32),
            np.empty(0, dtype=np.int64),
        )
    xs, ys, ends = [], [], []
    for end in range(seq_len - 1, len(X)):
        start = end - seq_len + 1
        xs.append(X[start:end + 1])
        ys.append(y[end])
        ends.append(end)
    return SequenceBatch(
        np.asarray(xs, dtype=np.float32),
        np.asarray(ys, dtype=np.int32),
        np.asarray(ends, dtype=np.int64),
    )
