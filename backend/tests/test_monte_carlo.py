"""Monte Carlo ve Walk-Forward modülleri için birim testler (Sprint 18)."""
import numpy as np

from app.backtest.monte_carlo import (
    bootstrap_returns,
    confidence_bands,
    run_monte_carlo,
)

# ── bootstrap_returns ─────────────────────────────────────────────────────────

def test_bootstrap_empty():
    result = bootstrap_returns([], initial_equity=10000, n_sims=10)
    assert result.shape == (10, 1)
    assert result[0, 0] == 10000.0


def test_bootstrap_shape():
    pnls = [10.0, -5.0, 20.0, -3.0, 15.0]
    result = bootstrap_returns(pnls, initial_equity=1000, n_sims=50, seed=42)
    assert result.shape == (50, len(pnls) + 1)
    # Tüm başlangıç değerleri eşit
    assert np.all(result[:, 0] == 1000.0)


def test_bootstrap_deterministic():
    pnls = [10.0, -5.0, 20.0]
    r1 = bootstrap_returns(pnls, seed=42)
    r2 = bootstrap_returns(pnls, seed=42)
    np.testing.assert_array_equal(r1, r2)


def test_bootstrap_different_seeds():
    pnls = [10.0, -5.0, 20.0] * 5
    r1 = bootstrap_returns(pnls, seed=1)
    r2 = bootstrap_returns(pnls, seed=2)
    assert not np.array_equal(r1, r2)


# ── confidence_bands ─────────────────────────────────────────────────────────

def test_confidence_bands_keys():
    curves = np.array([
        [100, 110, 120, 130],
        [100, 90, 80, 70],
        [100, 105, 115, 125],
    ], dtype=float)
    bands = confidence_bands(curves)
    assert "p5" in bands
    assert "p50" in bands
    assert "p95" in bands
    assert "median_final" in bands
    assert "worst_drawdown_p95" in bands


def test_confidence_bands_ordering():
    """p5 ≤ p50 ≤ p95 her noktada."""
    np.random.seed(99)
    curves = np.cumsum(np.random.randn(200, 50), axis=1) + 100
    bands = confidence_bands(curves)
    p5 = np.array(bands["p5"])
    p50 = np.array(bands["p50"])
    p95 = np.array(bands["p95"])
    assert np.all(p5 <= p50 + 1e-9)
    assert np.all(p50 <= p95 + 1e-9)


# ── run_monte_carlo ───────────────────────────────────────────────────────────

def test_run_monte_carlo_empty():
    result = run_monte_carlo([])
    assert "error" in result


def test_run_monte_carlo_basic():
    pnls = [10.0, -5.0, 15.0, -3.0, 20.0] * 10
    result = run_monte_carlo(pnls, initial_equity=10000, n_sims=100)
    assert "p5" in result
    assert "p50" in result
    assert "p95" in result
    assert "win_rate_pct" in result
    assert 0.0 <= result["win_rate_pct"] <= 100.0
    assert result["initial_equity"] == 10000.0


def test_run_monte_carlo_avg_return():
    """Tutarlı pozitif PnL → pozitif ortalama getiri."""
    pnls = [50.0] * 20
    result = run_monte_carlo(pnls, initial_equity=1000, n_sims=50)
    assert result["avg_return_pct"] > 0
