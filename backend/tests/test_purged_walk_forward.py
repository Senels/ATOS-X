from app.data.validation.leakage import (
    assert_forward_label_safe,
    assert_no_overlap,
    assert_unique_sorted_timestamps,
)
from app.data.validation.time_split import PurgedWalkForward


def test_walk_forward_is_chronological_and_purged():
    splitter = PurgedWalkForward(
        train_size=50,
        validation_size=10,
        test_size=10,
        step=10,
        embargo=2,
        label_horizon=3,
    )
    folds = splitter.split(100)

    assert folds
    for train, validation, test in folds:
        assert train.end <= validation.start
        assert validation.end <= test.start
        assert train.end + 3 <= validation.start


def test_timestamp_and_leakage_guards():
    timestamps = list(range(10))
    assert_unique_sorted_timestamps(timestamps)
    assert_no_overlap(4, 4, 7)
    assert_forward_label_safe(4, 7, 3)
