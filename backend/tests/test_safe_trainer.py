import numpy as np
import pandas as pd

from app.ai import safe_trainer


def test_safe_split_is_chronological_and_purged(monkeypatch):
    x = np.arange(100 * 2, dtype=np.float32).reshape(100, 2)
    y = np.tile(np.array([0, 1], dtype=np.int32), 50)
    monkeypatch.setattr(safe_trainer.legacy, "_dataset", lambda *a, **k: (x, y))

    df = pd.DataFrame({"close": np.arange(100)})
    xtr, ytr, xva, yva = safe_trainer._split_frame(
        df, horizon=10, atr_mult=1.0, val_fraction=0.2, purge=10
    )

    assert len(xtr) == 70
    assert len(xva) == 20
    assert np.array_equal(xtr[-1], x[69])
    assert np.array_equal(xva[0], x[80])
    assert np.array_equal(ytr, y[:70])
    assert np.array_equal(yva, y[80:])
