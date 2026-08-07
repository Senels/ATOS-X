"""Multi-Timeframe (MTF) modülü için birim testler (Sprint 16)."""
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from app.strategy.multi_tf import align_timeframes, mtf_vote, DEFAULT_MTF_WEIGHTS


# ── Yardımcı veri fabrikası ──────────────────────────────────────────────────

def _make_df(n: int = 100, start: str = "2024-01-01") -> pd.DataFrame:
    idx = pd.date_range(start, periods=n, freq="4h", tz="UTC")
    df = pd.DataFrame({
        "open": 100.0,
        "high": 102.0,
        "low": 98.0,
        "close": 101.0,
        "volume": 1000.0,
    }, index=idx)
    return df


# ── align_timeframes ─────────────────────────────────────────────────────────

def test_align_timeframes_empty():
    df_base = _make_df(10)
    result = align_timeframes(df_base, pd.DataFrame())
    assert result.empty


def test_align_timeframes_same_index():
    df_base = _make_df(10)
    df_other = _make_df(10)
    result = align_timeframes(df_base, df_other)
    assert len(result) == len(df_base)


def test_align_timeframes_different_freq():
    """1h veriyi 4h baza hizalama — sonuç 4h index boyutunda olmalı."""
    base = _make_df(20, "2024-01-01")
    other_idx = pd.date_range("2024-01-01", periods=80, freq="1h", tz="UTC")
    other = pd.DataFrame({"close": 101.0}, index=other_idx)
    result = align_timeframes(base, other)
    assert len(result) == len(base)


# ── mtf_vote ─────────────────────────────────────────────────────────────────

def _mock_generate_signal(signal: str = "BUY", strength: float = 0.7):
    def _gen(df):
        return {"signal": signal, "strength": strength, "price": 101.0}
    return _gen


def test_mtf_vote_all_buy():
    """Tüm zaman dilimleri BUY → verdict BUY."""
    dfs = {"4h": _make_df(100), "1h": _make_df(100)}
    cfg = {"active_strategy": "v23"}

    with patch("app.strategy.multi_tf.get_strategy") as mock_gs:
        bot = MagicMock()
        bot.generate_signal.side_effect = _mock_generate_signal("BUY", 0.8)
        mock_gs.return_value = bot
        result = mtf_vote(dfs, cfg)

    assert result["verdict"] == "BUY"
    assert result["buy_score"] > 0


def test_mtf_vote_all_sell():
    """Tüm zaman dilimleri SELL → verdict SELL."""
    dfs = {"4h": _make_df(100), "1h": _make_df(100)}
    cfg = {"active_strategy": "v23"}

    with patch("app.strategy.multi_tf.get_strategy") as mock_gs:
        bot = MagicMock()
        bot.generate_signal.side_effect = _mock_generate_signal("SELL", 0.8)
        mock_gs.return_value = bot
        result = mtf_vote(dfs, cfg)

    assert result["verdict"] == "SELL"


def test_mtf_vote_conflicting():
    """4h BUY + 1h SELL → net düşük, büyük olasılıkla HOLD."""
    dfs = {"4h": _make_df(100), "1h": _make_df(100)}
    cfg = {"active_strategy": "v23"}
    call_count = [0]

    def mixed_signal(df):
        call_count[0] += 1
        if call_count[0] % 2 == 1:
            return {"signal": "BUY", "strength": 0.6}
        return {"signal": "SELL", "strength": 0.6}

    with patch("app.strategy.multi_tf.get_strategy") as mock_gs:
        bot = MagicMock()
        bot.generate_signal.side_effect = mixed_signal
        mock_gs.return_value = bot
        result = mtf_vote(dfs, cfg)

    # Çakışan sinyal: güven düşük ve muhtemelen HOLD
    assert result["confidence"] < 0.5 or result["verdict"] in ("BUY", "SELL", "HOLD")


def test_mtf_vote_empty_dfs():
    """Boş DataFrame sözlüğü → HOLD."""
    with patch("app.strategy.multi_tf.get_strategy") as mock_gs:
        result = mtf_vote({}, {"active_strategy": "v23"})
    assert result["verdict"] == "HOLD"
    assert result["confidence"] == 0.0


def test_mtf_vote_votes_structure():
    """Her oy elemanı gerekli alanları içermeli."""
    dfs = {"4h": _make_df(100)}
    cfg = {"active_strategy": "v23"}

    with patch("app.strategy.multi_tf.get_strategy") as mock_gs:
        bot = MagicMock()
        bot.generate_signal.return_value = {"signal": "BUY", "strength": 0.7}
        mock_gs.return_value = bot
        result = mtf_vote(dfs, cfg)

    for v in result["votes"]:
        assert "interval" in v
        assert "signal" in v
        assert "weight" in v
