"""Leakage-safe time-series validation utilities for ATOS X.

The AI training path must never use random train/validation splits. These
helpers keep observations ordered in time and support a purge/embargo gap
between the training and validation windows.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np


@dataclass(frozen=True)
class TimeSplit:
    """Indices for a chronological train/validation split."""

    train: np.ndarray
    validation: np.ndarray


def chronological_split(
    n_samples: int,
    val_fraction: float = 0.2,
    purge: int = 0,
    embargo: int = 0,
) -> TimeSplit:
    """Return ordered train/validation indices with an optional gap.

    ``purge`` removes observations immediately before validation from the
    training set. ``embargo`` removes observations immediately after the
    training boundary as well; the resulting validation set starts after the
    complete gap. This is deliberately index-based so callers can apply it to
    dense or sequence datasets without shuffling.
    """
    if n_samples < 2:
        raise ValueError("n_samples must be >= 2")
    if not 0.0 < val_fraction < 1.0:
        raise ValueError("val_fraction must be between 0 and 1")
    if purge < 0 or embargo < 0:
        raise ValueError("purge and embargo must be >= 0")

    val_size = max(1, int(np.ceil(n_samples * val_fraction)))
    raw_train_end = n_samples - val_size
    gap = max(purge, embargo)
    train_end = raw_train_end - gap

    if train_end < 1:
        raise ValueError("purge/embargo leave no training samples")

    train = np.arange(0, train_end, dtype=np.int64)
    validation = np.arange(raw_train_end, n_samples, dtype=np.int64)
    if len(validation) == 0:
        raise ValueError("split leaves no validation samples")
    return TimeSplit(train=train, validation=validation)


def purged_sequence_split(
    n_sequences: int,
    val_fraction: float = 0.2,
    sequence_length: int = 20,
    horizon: int = 24,
    embargo: int = 0,
) -> TimeSplit:
    """Split overlapping sequence samples without temporal contamination.

    A sequence ending at ``t`` can contain information from ``t-sequence_length``
    through ``t`` and its target can depend on future bars through ``horizon``.
    The purge therefore removes at least ``sequence_length + horizon - 1``
    samples from the end of the training region before validation begins.
    """
    if sequence_length < 1 or horizon < 1:
        raise ValueError("sequence_length and horizon must be >= 1")
    required_purge = sequence_length + horizon - 1
    return chronological_split(
        n_samples=n_sequences,
        val_fraction=val_fraction,
        purge=required_purge,
        embargo=embargo,
    )


def apply_split(values: Sequence, split: TimeSplit) -> tuple[list, list]:
    """Apply a :class:`TimeSplit` to an arbitrary indexable sequence."""
    return (
        [values[int(i)] for i in split.train],
        [values[int(i)] for i in split.validation],
    )
