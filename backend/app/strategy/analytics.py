"""Portföy analitik modülü: risk/getiri metrikleri.

Backtest sonuçları ve canlı trade geçmişi üzerinde istatistiksel ölçümler
üretir. Tüm fonksiyonlar saf hesaplama içerir — IO veya FastAPI bağımlılığı
yoktur.
"""
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Temel getiri serisi yardımcıları
# ---------------------------------------------------------------------------

def equity_returns(equity_curve: List[float]) -> np.ndarray:
    """Equity eğrisinden bar-bazlı yüzde getirileri hesaplar."""
    arr = np.asarray(equity_curve, dtype=float)
    prev = arr[:-1]
    prev_safe = np.where(prev > 0, prev, np.nan)
    rets = np.diff(arr) / prev_safe
    return rets[np.isfinite(rets)]


# ---------------------------------------------------------------------------
# Risk/getiri oranları
# ---------------------------------------------------------------------------

def sharpe_ratio(returns: np.ndarray, bars_per_year: int = 2190,
                 risk_free: float = 0.0) -> float:
    """Yıllıklaştırılmış Sharpe oranı.

    ``bars_per_year`` varsayılanı 4h bar (2190). Sıfır ya da tek elemanlı
    getiri dizisi için 0.0 döner.
    """
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) < 2:
        return 0.0
    excess = r - risk_free / bars_per_year
    mu = float(excess.mean())
    sd = float(excess.std(ddof=1))
    if sd <= 0:
        return 0.0
    return round(float(mu / sd * np.sqrt(bars_per_year)), 3)


def sortino_ratio(returns: np.ndarray, bars_per_year: int = 2190,
                  target: float = 0.0) -> float:
    """Yıllıklaştırılmış Sortino oranı (yalnızca negatif sapma).

    ``target`` eşiğinin altındaki getiriler downside olarak kabul edilir.
    """
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) < 2:
        return 0.0
    excess = r - target / bars_per_year
    mu = float(excess.mean())
    downside = excess[excess < 0]
    if len(downside) == 0:
        return 0.0
    dd_std = float(np.sqrt((downside ** 2).mean()))
    if dd_std <= 0:
        return 0.0
    return round(float(mu / dd_std * np.sqrt(bars_per_year)), 3)


def calmar_ratio(returns: np.ndarray, equity_curve: List[float],
                 bars_per_year: int = 2190) -> float:
    """Calmar oranı: yıllıklaştırılmış CAGR / maksimum drawdown yüzdesi.

    MaxDD sıfırsa 0.0 döner.
    """
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) < 1:
        return 0.0
    cagr = float(r.mean()) * bars_per_year
    mdd_abs = max_drawdown_pct(equity_curve)
    if abs(mdd_abs) < 1e-9:
        return 0.0
    return round(float(cagr / abs(mdd_abs)), 3)


# ---------------------------------------------------------------------------
# Drawdown
# ---------------------------------------------------------------------------

def max_drawdown(equity_curve: List[float]) -> float:
    """Equity eğrisinin mutlak maksimum drawdown'ı (USDT cinsinden, negatif)."""
    arr = np.asarray(equity_curve, dtype=float)
    if len(arr) == 0:
        return 0.0
    peak = np.maximum.accumulate(arr)
    dd = arr - peak
    return round(float(dd.min()), 2)


def max_drawdown_pct(equity_curve: List[float]) -> float:
    """Equity eğrisinin maksimum drawdown yüzdesi (negatif, 0–100 ölçeği)."""
    arr = np.asarray(equity_curve, dtype=float)
    if len(arr) == 0:
        return 0.0
    peak = np.maximum.accumulate(arr)
    with np.errstate(invalid="ignore", divide="ignore"):
        dd_pct = np.where(peak > 0, (arr - peak) / peak * 100, 0.0)
    return round(float(dd_pct.min()), 2)


# ---------------------------------------------------------------------------
# Trade tabanlı istatistikler
# ---------------------------------------------------------------------------

def win_rate(trades: List[Dict[str, Any]]) -> float:
    """Kazanan işlem yüzdesi (0–100)."""
    if not trades:
        return 0.0
    wins = sum(1 for t in trades if (t.get("pnl") or 0) > 0)
    return round(wins / len(trades) * 100, 2)


def profit_factor(trades: List[Dict[str, Any]]) -> Optional[float]:
    """Brüt kâr / brüt zarar oranı; zararlı işlem yoksa None."""
    gross_profit = sum((t.get("pnl") or 0) for t in trades if (t.get("pnl") or 0) > 0)
    gross_loss = abs(sum((t.get("pnl") or 0) for t in trades if (t.get("pnl") or 0) < 0))
    if gross_loss <= 0:
        return None
    return round(gross_profit / gross_loss, 2)


def avg_rr(trades: List[Dict[str, Any]]) -> float:
    """Ortalama R katı (r_multiple alanı kullanılır; yoksa 0)."""
    rrs = [float(t.get("r_multiple", 0) or 0) for t in trades]
    if not rrs:
        return 0.0
    return round(float(np.mean(rrs)), 3)


# ---------------------------------------------------------------------------
# Aylık getiri tablosu
# ---------------------------------------------------------------------------

def monthly_returns_table(trades: List[Dict[str, Any]]) -> pd.DataFrame:
    """Ay×yıl PnL tablosu (Pandas DataFrame).

    ``trades`` listesindeki her eleman ``{"pnl": float, "exit_time": str}``
    (backtest formatı) veya ``{"pnl": float, "time": str}`` (canlı trade
    formatı) içerebilir. Eksik timestamp olan satırlar atlanır.

    Dönüş değeri: satırlar = aylar (1–12), sütunlar = yıllar, değer = net PnL.
    """
    rows = []
    for t in trades:
        pnl = t.get("pnl") or 0
        ts = t.get("exit_time") or t.get("time") or ""
        ts = str(ts)
        if len(ts) < 7:
            continue
        try:
            year = int(ts[:4])
            month = int(ts[5:7])
        except (ValueError, IndexError):
            continue
        rows.append({"year": year, "month": month, "pnl": pnl})
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    table = df.pivot_table(index="month", columns="year", values="pnl",
                           aggfunc="sum", fill_value=0.0)
    table = table.round(2)
    return table


# ---------------------------------------------------------------------------
# Özet hesaplama
# ---------------------------------------------------------------------------

def portfolio_stats(trades: List[Dict[str, Any]],
                    equity_curve: Optional[List[float]] = None,
                    bars_per_year: int = 2190) -> Dict[str, Any]:
    """Tüm metrikleri tek sözlükte döndürür.

    ``equity_curve`` verilmezse yalnızca trade-bazlı metrikler hesaplanır.
    """
    wr = win_rate(trades)
    pf = profit_factor(trades)
    rr = avg_rr(trades)
    pnls = [(t.get("pnl") or 0) for t in trades]
    net_pnl = round(sum(pnls), 2)
    avg_pnl = round(float(np.mean(pnls)), 2) if pnls else 0.0

    result: Dict[str, Any] = {
        "total_trades": len(trades),
        "win_rate": wr,
        "profit_factor": pf,
        "avg_rr": rr,
        "net_pnl": net_pnl,
        "avg_pnl": avg_pnl,
        "sharpe": None,
        "sortino": None,
        "calmar": None,
        "max_drawdown": None,
        "max_drawdown_pct": None,
    }

    if equity_curve and len(equity_curve) >= 2:
        rets = equity_returns(equity_curve)
        result["sharpe"] = sharpe_ratio(rets, bars_per_year)
        result["sortino"] = sortino_ratio(rets, bars_per_year)
        result["calmar"] = calmar_ratio(rets, equity_curve, bars_per_year)
        result["max_drawdown"] = max_drawdown(equity_curve)
        result["max_drawdown_pct"] = max_drawdown_pct(equity_curve)

    return result
