import asyncio
from pathlib import Path

import pytest

from app.api import optimization as opt
from app.data import loader


@pytest.fixture(scope="module", autouse=True)
def skip_without_data():
    data_dir = loader.DEFAULT_DATA_DIR / "futures_4h_data"
    if not (data_dir / "BTCUSDT_4h.csv").exists():
        pytest.skip("BTCUSDT_4h.csv yok; optimizasyon API testleri atlandi")
    yield


def test_optimize_runs_small_grid():
    res = asyncio.run(opt.run_optimize(
        symbols="BTCUSDT",
        interval="4h",
        limit=100,
        objective="combined",
        rangefilt_length="2,3",
        signal_expiry="1,2",
    ))
    assert len(res["results"]) == 4
    scores = [r["score"] for r in res["results"]]
    assert scores == sorted(scores, reverse=True)
    assert res["best"] == res["results"][0]
    assert res["best"]["combo"]["rangefilt_length"] in (2, 3)
    assert res["grid"]["rangefilt_length"] == [2, 3]
    assert res["symbols"] == ["BTCUSDT"]


def test_optimize_float_grid_and_objective():
    res = asyncio.run(opt.run_optimize(
        symbols="BTCUSDT",
        interval="4h",
        limit=100,
        objective="return",
        rr_ratio="1.5,2.0",
        sl_lookback="3,5",
    ))
    assert len(res["results"]) == 4
    for r in res["results"]:
        assert r["combo"]["rr_ratio"] in (1.5, 2.0)
        assert r["combo"]["sl_lookback"] in (3, 5)


def test_optimize_missing_symbol_raises():
    with pytest.raises(Exception):
        asyncio.run(opt.run_optimize(
            symbols="NOTREALXX",
            interval="4h",
            limit=50,
        ))


def test_optimize_invalid_int_list_raises():
    with pytest.raises(Exception):
        asyncio.run(opt.run_optimize(
            symbols="BTCUSDT",
            interval="4h",
            limit=50,
            rangefilt_length="2,abc",
        ))


def test_optimize_save_to_file(tmp_path, monkeypatch):
    def fake_save(best, path=None):
        p = tmp_path / "optimized_settings.json"
        p.write_text("{}", encoding="utf-8")
        return p
    monkeypatch.setattr(opt, "best_settings_to_file", fake_save)
    res = asyncio.run(opt.run_optimize(
        symbols="BTCUSDT",
        interval="4h",
        limit=100,
        rangefilt_length="2,3",
        signal_expiry="1,2",
        save_to_file=True,
    ))
    assert "saved_to" in res
    assert res["saved_to"].endswith("optimized_settings.json")


def test_defaults():
    res = asyncio.run(opt.optimize_defaults())
    assert "rangefilt_length" in res["grid"]
    assert "combined" in res["objectives"]


def test_apply_optimized_applies_and_persists(monkeypatch):
    import app.strategy.settings as ss

    monkeypatch.setattr(ss, "load_optimized",
                        lambda: {"rangefilt_length": 4, "range_filt_mult": 2.0,
                                 "signal_expiry": 2, "rr_ratio": 2.0, "sl_lookback": 3,
                                 "_objective_score": 1.5, "_symbols_count": 10})
    persisted = []
    monkeypatch.setattr(ss, "persist",
                        lambda: persisted.append(True) or ss.get_settings())
    prev = ss.get_settings()
    try:
        result = ss.apply_optimized()
        assert set(result["applied"]) == {"rangefilt_length", "range_filt_mult",
                                          "signal_expiry", "rr_ratio", "sl_lookback"}
        assert ss.get_settings()["rangefilt_length"] == 4
        assert ss.get_settings()["range_filt_mult"] == 2.0
        assert persisted == [True]
    finally:
        ss.update_settings(prev)


def test_apply_optimized_empty_no_persist(monkeypatch):
    import app.strategy.settings as ss

    monkeypatch.setattr(ss, "load_optimized", lambda: {})
    persisted = []
    monkeypatch.setattr(ss, "persist",
                        lambda: persisted.append(True) or ss.get_settings())
    prev = ss.get_settings()
    try:
        result = ss.apply_optimized()
        assert result["applied"] == []
        assert persisted == []
    finally:
        ss.update_settings(prev)


def test_optimize_apply_endpoint_applies(monkeypatch):
    import app.strategy.settings as ss

    monkeypatch.setattr(ss, "apply_optimized",
                        lambda: {"applied": ["rr_ratio"], "settings": {"rr_ratio": 2.0}})
    res = asyncio.run(opt.optimize_apply())
    assert res["status"] == "ok"
    assert res["applied"] == ["rr_ratio"]


def test_optimize_apply_endpoint_empty_raises(monkeypatch):
    import app.strategy.settings as ss

    monkeypatch.setattr(ss, "apply_optimized",
                        lambda: {"applied": [], "settings": {}})
    with pytest.raises(Exception):
        asyncio.run(opt.optimize_apply())
