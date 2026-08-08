"""Portföy analitik modülü için birim testler (Sprint 15)."""
import numpy as np
import pytest

from app.strategy.analytics import (
    avg_rr,
    calmar_ratio,
    equity_returns,
    max_drawdown,
    max_drawdown_pct,
    monthly_returns_table,
    portfolio_stats,
    profit_factor,
    sharpe_ratio,
    sortino_ratio,
    win_rate,
)

# ── Temel metrikler ──────────────────────────────────────────────────────────

def test_sharpe_positive():
    """Pozitif ortalama getiri → pozitif Sharpe."""
    rets = np.array([0.01, 0.02, 0.01, 0.03, 0.01])
    s = sharpe_ratio(rets, bars_per_year=252)
    assert s > 0


def test_sharpe_zero_variance():
    """Sıfır sapma → Sharpe 0."""
    rets = np.array([0.0, 0.0, 0.0])
    assert sharpe_ratio(rets) == 0.0


def test_sortino_only_positive():
    """Kayıp yok → Sortino 0 (downside std = 0)."""
    rets = np.array([0.01, 0.02, 0.03])
    assert sortino_ratio(rets) == 0.0


def test_sortino_mixed():
    """Karışık getiri → Sortino hesaplanabilir."""
    rets = np.array([0.01, -0.02, 0.03, -0.01, 0.02])
    s = sortino_ratio(rets, bars_per_year=252)
    assert np.isfinite(s)


def test_max_drawdown_monoton_rising():
    """Sürekli artan equity → MaxDD 0."""
    eq = [100, 110, 120, 130]
    assert max_drawdown(eq) == 0.0


def test_max_drawdown_drop():
    """Bilinen düşüş → doğru MaxDD."""
    eq = [100, 120, 80, 90]
    dd = max_drawdown(eq)
    assert dd == pytest.approx(-40.0, abs=0.01)


def test_max_drawdown_pct_drop():
    """MaxDD yüzdesi: 120'den 80'e = -%33.33."""
    eq = [100, 120, 80]
    pct = max_drawdown_pct(eq)
    assert pct == pytest.approx(-33.33, abs=0.1)


def test_calmar_ratio():
    """Calmar: CAGR / MaxDD; her ikisi de 0'dan farklıysa sonlu olmalı."""
    rets = np.array([-0.01, 0.02, -0.01, 0.03] * 10)
    eq = [100] + list(np.cumprod(1 + rets) * 100)
    c = calmar_ratio(rets, eq, bars_per_year=252)
    assert np.isfinite(c)


# ── Trade istatistikleri ─────────────────────────────────────────────────────

def test_win_rate_empty():
    assert win_rate([]) == 0.0


def test_win_rate_all_wins():
    trades = [{"pnl": 10}, {"pnl": 5}, {"pnl": 1}]
    assert win_rate(trades) == 100.0


def test_win_rate_half():
    trades = [{"pnl": 10}, {"pnl": -5}]
    assert win_rate(trades) == 50.0


def test_profit_factor():
    trades = [{"pnl": 20}, {"pnl": -10}, {"pnl": 5}]
    pf = profit_factor(trades)
    assert pf == pytest.approx(2.5, abs=0.01)


def test_profit_factor_no_losses():
    trades = [{"pnl": 20}, {"pnl": 5}]
    assert profit_factor(trades) is None


def test_avg_rr():
    trades = [{"r_multiple": 2.0}, {"r_multiple": -1.0}]
    assert avg_rr(trades) == pytest.approx(0.5, abs=0.01)


# ── Aylık getiri tablosu ──────────────────────────────────────────────────────

def test_monthly_returns_table_empty():
    table = monthly_returns_table([])
    assert table.empty


def test_monthly_returns_table_basic():
    trades = [
        {"pnl": 100, "time": "2024-01-15 10:00:00"},
        {"pnl": -50, "time": "2024-01-20 10:00:00"},
        {"pnl": 200, "time": "2024-02-05 10:00:00"},
    ]
    table = monthly_returns_table(trades)
    assert not table.empty
    assert 2024 in table.columns
    assert table.loc[1, 2024] == pytest.approx(50.0)
    assert table.loc[2, 2024] == pytest.approx(200.0)


# ── equity_returns yardımcısı ─────────────────────────────────────────────────

def test_equity_returns_basic():
    eq = [100.0, 110.0, 99.0]
    rets = equity_returns(eq)
    assert len(rets) == 2
    assert rets[0] == pytest.approx(0.1, abs=0.001)
    assert rets[1] == pytest.approx(-0.1, abs=0.001)


# ── portfolio_stats özeti ─────────────────────────────────────────────────────

def test_portfolio_stats_no_equity():
    trades = [{"pnl": 50}, {"pnl": -20}, {"pnl": 30}]
    stats = portfolio_stats(trades)
    assert stats["total_trades"] == 3
    assert stats["win_rate"] == pytest.approx(100 * 2 / 3, abs=0.1)
    assert stats["sharpe"] is None


def test_portfolio_stats_with_equity():
    trades = [{"pnl": 50}, {"pnl": -20}]
    eq = [100.0, 150.0, 130.0]
    stats = portfolio_stats(trades, equity_curve=eq, bars_per_year=252)
    assert stats["max_drawdown"] is not None
    assert stats["sharpe"] is not None
