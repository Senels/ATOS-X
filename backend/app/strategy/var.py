"""Value at Risk (VaR) hesaplama modülü.

Portföy düzeyinde istatistiksel risk ölçümü:
- Tarihsel simülasyon VaR
- Parametrik (normal dağılım) VaR
- Conditional VaR (CVaR / Expected Shortfall)
- Çoklu pozisyon portföy VaR (korelasyon matrisi ile)
"""
from typing import Any, Dict, List, Optional

import numpy as np


# ---------------------------------------------------------------------------
# Tek-varlık VaR
# ---------------------------------------------------------------------------

def historical_var(returns: List[float], confidence: float = 0.95) -> float:
    """Tarihsel simülasyon VaR.

    ``returns`` dizisinin (1 - confidence) yüzdelik dilimini döndürür.
    Negatif değer kayıp anlamına gelir (ör. -0.03 = %3 kayıp).

    Parametreler
    ------------
    returns    : Yüzde veya mutlak getiri listesi.
    confidence : Güven düzeyi (0–1), varsayılan 0.95.
    """
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) == 0:
        return 0.0
    return round(float(np.percentile(r, (1 - confidence) * 100)), 6)


def parametric_var(returns: List[float], confidence: float = 0.95) -> float:
    """Parametrik (normal dağılım varsayımı) VaR.

    μ - z * σ formülüyle hesaplanır; z değeri güven düzeyine göre seçilir.
    """
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) < 2:
        return 0.0
    mu = float(r.mean())
    sd = float(r.std(ddof=1))
    try:
        from scipy.stats import norm  # type: ignore[import]
        z = float(norm.ppf(1 - confidence))
    except ImportError:
        # scipy yoksa numpy ile yaklaşık z-skoru hesapla
        # Yaygın güven seviyeleri için sabit değerler
        _Z = {0.90: -1.2816, 0.95: -1.6449, 0.99: -2.3263}
        z = _Z.get(round(confidence, 2), -1.6449)
    return round(float(mu + z * sd), 6)


def cvar(returns: List[float], confidence: float = 0.95) -> float:
    """Conditional VaR (Expected Shortfall).

    VaR eşiğinin altındaki getirilerin ortalaması — gerçekleşen kayıpların
    beklenen değeri.
    """
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) == 0:
        return 0.0
    threshold = float(np.percentile(r, (1 - confidence) * 100))
    tail = r[r <= threshold]
    if len(tail) == 0:
        return threshold
    return round(float(tail.mean()), 6)


def portfolio_var(
    position_returns: List[List[float]],
    weights: Optional[List[float]] = None,
    confidence: float = 0.95,
) -> Dict[str, float]:
    """Çoklu pozisyon portföy VaR (korelasyon matrisi ile).

    Parametreler
    ------------
    position_returns : Her pozisyon için ayrı getiri serisi listesi.
    weights          : Portföy ağırlıkları (None ise eşit ağırlık).
    confidence       : Güven düzeyi.

    Dönüş
    ------
    Dict: ``var``, ``cvar``, ``correlation_avg``
    """
    n = len(position_returns)
    if n == 0:
        return {"var": 0.0, "cvar": 0.0, "correlation_avg": 0.0}

    if weights is None:
        weights = [1.0 / n] * n
    w = np.asarray(weights, dtype=float)
    w = w / w.sum()

    # En kısa seriye göre hizala
    min_len = min(len(r) for r in position_returns)
    matrix = np.array([np.asarray(r, dtype=float)[-min_len:] for r in position_returns])

    # Portföy getirisi = ağırlıklı toplam
    portfolio_rets = (matrix.T @ w)

    var = historical_var(portfolio_rets.tolist(), confidence)
    es = cvar(portfolio_rets.tolist(), confidence)

    # Ortalama korelasyon
    if n >= 2:
        try:
            corr = np.corrcoef(matrix)
            # Köşegen dışı elemanların ortalaması
            mask = ~np.eye(n, dtype=bool)
            corr_avg = round(float(corr[mask].mean()), 4)
        except Exception:
            corr_avg = 0.0
    else:
        corr_avg = 1.0

    return {"var": var, "cvar": es, "correlation_avg": corr_avg}


# ---------------------------------------------------------------------------
# Kolay yardımcı: pozisyon listesinden anlık kayıp tahmini
# ---------------------------------------------------------------------------

def position_dollar_var(
    notional: float,
    daily_returns: List[float],
    confidence: float = 0.95,
) -> Dict[str, float]:
    """Belirli bir notional için günlük VaR (USDT cinsinden).

    Parametreler
    ------------
    notional      : Pozisyon değeri (USDT).
    daily_returns : Tarihsel günlük yüzde getiriler.
    confidence    : Güven düzeyi.

    Dönüş
    ------
    ``{"var_pct", "var_usdt", "cvar_pct", "cvar_usdt"}``
    """
    var_pct = historical_var(daily_returns, confidence)
    cvar_pct = cvar(daily_returns, confidence)
    return {
        "var_pct": round(var_pct * 100, 4),
        "var_usdt": round(notional * abs(var_pct), 2),
        "cvar_pct": round(cvar_pct * 100, 4),
        "cvar_usdt": round(notional * abs(cvar_pct), 2),
    }
