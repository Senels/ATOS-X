"""VaR ve Stres Testi modülleri için birim testler (Sprint 19)."""
import pytest

from app.strategy.var import (
    historical_var,
    cvar,
    portfolio_var,
    position_dollar_var,
)
from app.strategy.stress import (
    scenario_pnl,
    stress_test,
    available_scenarios,
    BUILTIN_SCENARIOS,
)


# ── historical_var ────────────────────────────────────────────────────────────

def test_historical_var_empty():
    assert historical_var([]) == 0.0


def test_historical_var_basic():
    """%5'lik en kötü getiri: -10, -9, ... listesinde %95 VaR negatif."""
    returns = [-0.10, -0.05, 0.01, 0.02, 0.03, 0.04, 0.05] * 3
    v = historical_var(returns, confidence=0.95)
    assert v < 0


def test_historical_var_all_positive():
    """Tüm getiriler pozitif → VaR pozitif veya sıfır."""
    returns = [0.01, 0.02, 0.03, 0.04] * 5
    v = historical_var(returns, confidence=0.95)
    assert v >= 0


def test_historical_var_confidence():
    """Daha yüksek güven = daha negatif VaR (daha tutucu)."""
    returns = list(range(-10, 10))
    v95 = historical_var(returns, confidence=0.95)
    v99 = historical_var(returns, confidence=0.99)
    assert v99 <= v95


# ── cvar ──────────────────────────────────────────────────────────────────────

def test_cvar_empty():
    assert cvar([]) == 0.0


def test_cvar_worse_than_var():
    """CVaR ≤ VaR (daha kötümser)."""
    returns = [-0.10, -0.08, -0.05, 0.01, 0.02, 0.03] * 4
    v = historical_var(returns, 0.95)
    es = cvar(returns, 0.95)
    assert es <= v


# ── portfolio_var ─────────────────────────────────────────────────────────────

def test_portfolio_var_empty():
    result = portfolio_var([], confidence=0.95)
    assert result["var"] == 0.0


def test_portfolio_var_single():
    returns = [[-0.05, -0.03, 0.01, 0.02, 0.04] * 5]
    result = portfolio_var(returns, confidence=0.95)
    assert "var" in result
    assert "cvar" in result
    assert "correlation_avg" in result


def test_portfolio_var_correlation():
    """Tam pozitif korelasyonlu varlıklar → korelasyon ~1."""
    r = [-0.01, 0.02, -0.03, 0.04] * 10
    result = portfolio_var([r, r], confidence=0.95)
    assert result["correlation_avg"] == pytest.approx(1.0, abs=0.01)


# ── position_dollar_var ───────────────────────────────────────────────────────

def test_position_dollar_var_keys():
    returns = [-0.05, -0.03, 0.01, 0.02] * 5
    result = position_dollar_var(10000, returns, 0.95)
    assert "var_pct" in result
    assert "var_usdt" in result
    assert "cvar_pct" in result
    assert "cvar_usdt" in result


def test_position_dollar_var_positive_notional():
    returns = [-0.05, 0.01, -0.03] * 5
    result = position_dollar_var(1000, returns, 0.95)
    assert result["var_usdt"] >= 0
    assert result["cvar_usdt"] >= 0


# ── scenario_pnl ─────────────────────────────────────────────────────────────

def test_scenario_pnl_empty():
    result = scenario_pnl([], -30.0)
    assert result == []


def test_scenario_pnl_buy_loss():
    """BUY pozisyon + negatif şok → negatif PnL."""
    positions = [{"symbol": "BTCUSDT", "side": "BUY",
                  "entry_price": 10000.0, "quantity": 1.0}]
    result = scenario_pnl(positions, -30.0)
    assert len(result) == 1
    assert result[0]["pnl_usdt"] == pytest.approx(-3000.0, abs=0.1)


def test_scenario_pnl_sell_profit():
    """SELL pozisyon + negatif şok → pozitif PnL (short kazanır)."""
    positions = [{"symbol": "BTCUSDT", "side": "SELL",
                  "entry_price": 10000.0, "quantity": 1.0}]
    result = scenario_pnl(positions, -30.0)
    assert result[0]["pnl_usdt"] == pytest.approx(3000.0, abs=0.1)


def test_scenario_pnl_buy_profit():
    """BUY pozisyon + pozitif şok → pozitif PnL."""
    positions = [{"symbol": "ETHUSDT", "side": "BUY",
                  "entry_price": 2000.0, "quantity": 5.0}]
    result = scenario_pnl(positions, 50.0)
    assert result[0]["pnl_usdt"] == pytest.approx(5000.0, abs=0.1)


# ── stress_test ───────────────────────────────────────────────────────────────

def test_stress_test_empty_positions():
    result = stress_test([], equity=10000.0)
    assert "scenarios" in result
    assert result["position_count"] == 0


def test_stress_test_all_scenarios():
    """Tüm yerleşik senaryolar çalışmalı."""
    positions = [{"symbol": "BTCUSDT", "side": "BUY",
                  "entry_price": 50000.0, "quantity": 0.1}]
    result = stress_test(positions, equity=10000.0)
    for key in BUILTIN_SCENARIOS:
        assert key in result["scenarios"]


def test_stress_test_worst_scenario():
    """En kötü senaryo en büyük negatif PnL'e sahip olmalı."""
    positions = [{"symbol": "BTCUSDT", "side": "BUY",
                  "entry_price": 50000.0, "quantity": 0.2}]
    result = stress_test(positions, equity=10000.0)
    worst_key = result["worst_scenario"]
    worst_pnl = result["scenarios"][worst_key]["total_pnl_usdt"]
    for key, sc in result["scenarios"].items():
        assert sc["total_pnl_usdt"] >= worst_pnl - 0.01


# ── available_scenarios ───────────────────────────────────────────────────────

def test_available_scenarios():
    scenarios = available_scenarios()
    assert len(scenarios) >= 4
    for key, name in scenarios.items():
        assert isinstance(key, str)
        assert isinstance(name, str)
