"""Monte Carlo simülasyonu: bootstrap ile equity eğrisi güven bantları.

Gerçekleşen trade PnL dizisinin sırası karıştırılarak ``n_sims`` adet
senaryodan oluşan equity eğrisi üretilir. Yüzdelik dilimlerden güven
bantları ve en kötü drawdown hesaplanır.
"""
from typing import Any, Dict, List, Optional

import numpy as np


def bootstrap_returns(
    trade_pnls: List[float],
    initial_equity: float = 10000.0,
    n_sims: int = 1000,
    seed: Optional[int] = 42,
) -> np.ndarray:
    """Trade PnL listesini karıştırarak ``n_sims`` adet equity eğrisi üretir.

    Dönüş: ``(n_sims, len(trade_pnls) + 1)`` boyutunda ndarray; her satır
    bir simülasyonun equity eğrisidir (başlangıç eşiti dahil).
    """
    pnls = np.asarray(trade_pnls, dtype=float)
    n = len(pnls)
    if n == 0:
        return np.full((n_sims, 1), initial_equity)

    rng = np.random.default_rng(seed)
    # Rastgele örnekleme: yerine koyarak (bootstrap)
    idx = rng.integers(0, n, size=(n_sims, n))
    samples = pnls[idx]  # (n_sims, n)

    equity = np.empty((n_sims, n + 1))
    equity[:, 0] = initial_equity
    equity[:, 1:] = initial_equity + np.cumsum(samples, axis=1)
    return equity


def _drawdown_series(eq: np.ndarray) -> np.ndarray:
    """1-D equity dizisinin her adımdaki drawdown yüzdesini döndürür (negatif)."""
    peak = np.maximum.accumulate(eq)
    with np.errstate(invalid="ignore", divide="ignore"):
        dd = np.where(peak > 0, (eq - peak) / peak * 100, 0.0)
    return dd


def confidence_bands(
    equity_curves: np.ndarray,
    percentiles: List[int] = None,
) -> Dict[str, Any]:
    """Equity eğrilerinden yüzdelik dilim bantları hesaplar.

    Parametreler
    ------------
    equity_curves : (n_sims, n_bars) ndarray
    percentiles   : Hesaplanacak yüzdelik dilimler (varsayılan [5, 25, 50, 75, 95])

    Dönüş
    ------
    Dict içinde ``p5``, ``p25``, ``median``, ``p75``, ``p95`` equity eğrileri
    (liste) ve ``worst_drawdown_p95`` (en kötü %5 senaryonun MaxDD yüzdesi).
    """
    if percentiles is None:
        percentiles = [5, 25, 50, 75, 95]

    result: Dict[str, Any] = {}
    pct_arrays = np.percentile(equity_curves, percentiles, axis=0)
    for pct, arr in zip(percentiles, pct_arrays):
        result[f"p{pct}"] = [round(float(v), 2) for v in arr]

    # En kötü %5 senaryosunun MaxDD'si
    final_equities = equity_curves[:, -1]
    worst_idx = np.argsort(final_equities)[: max(1, len(final_equities) // 20)]
    worst_dd = float("inf")
    for i in worst_idx:
        dd = float(np.min(_drawdown_series(equity_curves[i])))
        if dd < worst_dd:
            worst_dd = dd
    result["worst_drawdown_p95"] = round(worst_dd, 2) if worst_dd != float("inf") else 0.0

    # Medyan istatistikler
    med = pct_arrays[percentiles.index(50)] if 50 in percentiles else np.median(equity_curves, axis=0)
    result["median_final"] = round(float(med[-1]), 2)
    result["n_sims"] = int(equity_curves.shape[0])
    result["n_trades"] = int(equity_curves.shape[1] - 1)

    return result


def run_monte_carlo(
    trade_pnls: List[float],
    initial_equity: float = 10000.0,
    n_sims: int = 1000,
    seed: int = 42,
) -> Dict[str, Any]:
    """Monte Carlo simülasyonunu çalıştırır ve tam rapor döndürür."""
    if not trade_pnls:
        return {"error": "Trade verisi yok", "n_sims": 0}

    curves = bootstrap_returns(trade_pnls, initial_equity, n_sims, seed)
    bands = confidence_bands(curves)

    initial = float(initial_equity)
    win_rate = round(float(np.mean(curves[:, -1] > initial)) * 100, 1)
    avg_return = round(
        float(np.mean((curves[:, -1] - initial) / initial * 100)), 2
    )

    bands["win_rate_pct"] = win_rate
    bands["avg_return_pct"] = avg_return
    bands["initial_equity"] = initial
    return bands
