"""Regression tests: public model training API must use safe trainer."""

import pandas as pd


def test_public_train_from_dataframe_delegates_to_safe_trainer(monkeypatch):
    from app.ai import model

    called = {}

    def fake_safe(**kwargs):
        called.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr("app.ai.safe_trainer.train_from_dataframe_safe", fake_safe)
    result = model.train_from_dataframe(
        [pd.DataFrame()], horizon=24, atr_mult=1.5, epochs=2,
        val_fraction=0.25, seed=11, model_name="test", model_type="dense",
        lstm_seq_len=20,
    )

    assert result == {"ok": True}
    assert called["horizon"] == 24
    assert called["val_fraction"] == 0.25
    assert called["seed"] == 11
    assert called["model_name"] == "test"


def test_public_archive_training_delegates_to_safe_trainer(monkeypatch):
    from app.ai import model

    called = {}

    def fake_safe(**kwargs):
        called.update(kwargs)
        return {"safe": True}

    monkeypatch.setattr("app.ai.safe_trainer.train_from_archive_safe", fake_safe)
    result = model.train_from_archive(interval="4h", max_symbols=3, min_bars=300, epochs=1)

    assert result == {"safe": True}
    assert called["interval"] == "4h"
    assert called["max_symbols"] == 3
    assert called["min_bars"] == 300
