from app.strategy import settings as s


def _reset():
    s._state = s.default_settings()


def test_update_nested_merge():
    _reset()
    s.update_settings({"confirmations": {"ema": True}})
    state = s.get_settings()
    assert state["confirmations"]["ema"] is True
    assert state["confirmations"]["rqk"] is True  # digerleri korunur


def test_engine_params_present():
    _reset()
    state = s.get_settings()
    assert state["initial_equity"] == 10000.0
    assert state["risk_per_trade"] == 0.02
    assert state["fee_rate"] == 0.0005
    assert state["max_leverage"] == 10.0
    assert state["max_open_positions"] == 3


def test_persist_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(s, "_STRATEGY_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(s, "_OPTIMIZED_FILE", tmp_path / "optimized.json")
    _reset()
    s.update_settings({"rr_ratio": 3.0, "confirmations": {"macd": True}})
    s.persist()

    s._state = s.default_settings()
    s.load()
    state = s.get_settings()
    assert state["rr_ratio"] == 3.0
    assert state["confirmations"]["macd"] is True


def test_load_tolerates_bom(tmp_path, monkeypatch):
    """BOM'lu (utf-8-sig) settings.json yuklenmeli; sessizce default'a dusmemeli."""
    import json

    target = tmp_path / "settings.json"
    payload = json.dumps({"active_strategy": "ttp", "scan_limit": 77})
    target.write_bytes(b"\xef\xbb\xbf" + payload.encode("utf-8"))
    monkeypatch.setattr(s, "_STRATEGY_FILE", target)
    monkeypatch.setattr(s, "_OPTIMIZED_FILE", tmp_path / "optimized.json")

    _reset()
    s._state = s.default_settings()
    state = s.load()
    assert state["active_strategy"] == "ttp"
    assert state["scan_limit"] == 77


def test_optimized_file_preferred(tmp_path, monkeypatch):
    import json

    opt = tmp_path / "optimized.json"
    opt.write_text(json.dumps({"rangefilt_length": 5, "range_filt_mult": 3.5}),
                   encoding="utf-8")
    monkeypatch.setattr(s, "_OPTIMIZED_FILE", opt)
    monkeypatch.setattr(s, "_STRATEGY_FILE", tmp_path / "settings.json")
    defaults = s.default_settings()
    assert defaults["rangefilt_length"] == 5
    assert defaults["range_filt_mult"] == 3.5


def test_get_settings_returns_copy():
    _reset()
    a = s.get_settings()
    b = s.get_settings()
    assert a is not b


def test_council_defaults():
    _reset()
    state = s.get_settings()
    assert state["use_decision_council"] is True
    assert state["council_min_confidence"] == 0.6
    assert state["use_score_ranking"] is True
    assert state["min_signal_strength"] == 0.6
    assert state["scan_limit"] == 50


def test_data_freshness_defaults():
    _reset()
    state = s.get_settings()
    assert state["data_backfill_hours"] == 24.0
    assert state["data_freshness_hours"] == 12.0


def test_confirmations_all_enabled_by_default():
    _reset()
    state = s.get_settings()
    assert all(state["confirmations"].values())
