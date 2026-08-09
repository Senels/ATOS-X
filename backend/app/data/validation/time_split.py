"""Leakage-safe, chronological splits for market time series.

The splitter works on ordered sample timestamps. Validation/test windows are
separated from training by an embargo, and an optional label horizon purges
training samples whose forward-looking label could overlap the evaluation
window.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Sequence


@dataclass(frozen=True)
class TimeWindow:
    start: int
    end: int


class PurgedWalkForward:
    """Generate expanding-window train/validation/test index ranges.

    Parameters are expressed in sample counts so the splitter stays independent
    of a particular timeframe. Timestamps are only required when a caller wants
    to perform an additional calendar-based leakage audit.
    """

    def __init__(
        self,
        train_size: int,
        validation_size: int,
        test_size: int,
        step: int | None = None,
        embargo: int = 0,
        label_horizon: int = 0,
    ) -> None:
        for name, value in {
            "train_size": train_size,
            "validation_size": validation_size,
            "test_size": test_size,
            "embargo": embargo,
            "label_horizon": label_horizon,
        }.items():
            if value < 0:
                raise ValueError(f"{name} must be >= 0")
        if train_size == 0 or validation_size == 0 or test_size == 0:
            raise ValueError("train_size, validation_size and test_size must be > 0")
        self.train_size = train_size
        self.validation_size = validation_size
        self.test_size = test_size
        self.step = step or test_size
        if self.step <= 0:
            raise ValueError("step must be > 0")
        self.embargo = embargo
        self.label_horizon = label_horizon

    def split(self, n_samples: int) -> list[tuple[TimeWindow, TimeWindow, TimeWindow]]:
        if n_samples < self.train_size + self.validation_size + self.test_size:
            return []

        folds: list[tuple[TimeWindow, TimeWindow, TimeWindow]] = []
        test_start = self.train_size + self.validation_size
        while test_start + self.test_size <= n_samples:
            val_end = test_start
            val_start = val_end - self.validation_size

            # Training must end before the label horizon, validation window and
            # embargo can leak information backwards into the training set.
            train_end = val_start - self.embargo - self.label_horizon
            train_start = max(0, train_end - self.train_size)

            if train_end > train_start:
                folds.append(
                    (
                        TimeWindow(train_start, train_end),
                        TimeWindow(val_start, val_end),
                        TimeWindow(test_start, test_start + self.test_size),
                    )
                )
            test_start += self.step
        return folds


def assert_chronological(
    timestamps: Sequence[int],
    train: TimeWindow,
    validation: TimeWindow,
    test: TimeWindow,
) -> None:
    """Fail fast if a generated fold is not strictly chronological."""
    if not timestamps:
        raise ValueError("timestamps cannot be empty")
    if not (0 <= train.start < train.end <= validation.start < validation.end <= test.start < test.end <= len(timestamps)):
        raise ValueError("invalid chronological windows")
    if timestamps != sorted(timestamps):
        raise ValueError("timestamps must be sorted ascending")
