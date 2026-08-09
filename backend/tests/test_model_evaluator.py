import numpy as np

from app.backtest.model_evaluator import build_probability_ensemble, probabilities_to_signals


def test_probability_threshold_and_direction_mapping():
    p = np.array([[0.8, 0.1, 0.1], [0.2, 0.7, 0.1], [0.1, 0.1, 0.8]])
    assert probabilities_to_signals(p, threshold=0.55).tolist() == [-1, 0, 1]


def test_ensemble_normalizes_weighted_probabilities():
    a = np.array([[0.8, 0.1, 0.1]])
    b = np.array([[0.1, 0.2, 0.7]])
    out = build_probability_ensemble({"dense": a, "lstm": b}, {"dense": 0.25, "lstm": 0.75})
    assert np.allclose(out.sum(axis=1), 1.0)
    assert out.shape == (1, 3)
