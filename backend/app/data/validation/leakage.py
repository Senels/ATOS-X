"""Data-quality and leakage assertions used before model training."""
from __future__ import annotations

from typing import Sequence


def assert_unique_sorted_timestamps(timestamps: Sequence[int]) -> None:
    if list(timestamps) != sorted(timestamps):
        raise ValueError("timestamps must be sorted ascending")
    if len(set(timestamps)) != len(timestamps):
        raise ValueError("duplicate timestamps detected")


def assert_no_overlap(train_end: int, validation_start: int, test_start: int) -> None:
    if train_end > validation_start:
        raise ValueError("training overlaps validation")
    if validation_start > test_start:
        raise ValueError("validation overlaps test")


def assert_forward_label_safe(train_end: int, evaluation_start: int, label_horizon: int) -> None:
    if label_horizon < 0:
        raise ValueError("label_horizon must be >= 0")
    if train_end + label_horizon > evaluation_start:
        raise ValueError("forward-looking training labels overlap evaluation data")
