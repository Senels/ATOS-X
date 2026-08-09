import numpy as np
import pytest

from app.ai.validation import chronological_split, purged_sequence_split


def test_chronological_split_preserves_order_and_purge_gap():
    split = chronological_split(100, val_fraction=0.2, purge=5)

    assert np.array_equal(split.train, np.arange(0, 75))
    assert np.array_equal(split.validation, np.arange(80, 100))
    assert split.train[-1] < split.validation[0]


def test_split_rejects_invalid_fraction():
    with pytest.raises(ValueError):
        chronological_split(100, val_fraction=1.0)


def test_purged_sequence_split_accounts_for_overlap_and_horizon():
    split = purged_sequence_split(
        n_sequences=200,
        val_fraction=0.2,
        sequence_length=20,
        horizon=24,
    )

    # raw validation starts at 160; purge = 20 + 24 - 1 = 43
    assert split.train[-1] == 116
    assert split.validation[0] == 160
    assert len(split.validation) == 40
    assert split.train[-1] < split.validation[0]


def test_purged_sequence_split_fails_when_gap_consumes_training_set():
    with pytest.raises(ValueError):
        purged_sequence_split(
            n_sequences=30,
            val_fraction=0.5,
            sequence_length=20,
            horizon=24,
        )
