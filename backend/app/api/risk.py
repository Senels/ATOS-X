"""Risk yönetimi API endpoint'leri: VaR, Stres Testi, Risk Özeti.

Canlı açık pozisyonlar ve DB kapalı trade geçmişi üzerinde anlık risk
metrikleri hesaplar.
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.strategy.analytics import portfolio_stats
from app.strategy.stress import BUILTIN_SCENARIOS, stress_test
from app.strategy.var import cvar, historical_var

router = APIRouter(prefix="/api/v1/risk", tags=["risk"])


# ---------------------------------------------------------------------------
# Request modeli
# ---------------------------------------------------------------------------

class StressTestRequest(BaseModel):
    scenario_keys: Optional[List[str]] = None
    custom_shock_pct: Optional[float] = None


# ---------------------------------------------------------------------------
# Yardımcı: açık pozisyonları endpoint uyumlu formata çevirir
# ---------------------------------------------------------------------------

def _open_positions_list(request: Request) -> List[Dict[str, Any]]:
    """auto_trader.active_positions'dan stres testi uyumlu liste üretir."""
    auto_trader = getattr(request.app.state, "auto_trader", None)
    if auto_trader is None:
        return []
    positions = []
    for symbol, pos in auto_trader.active_positions.items():
        positions.append({
            "symbol": symbol,
            "side": pos.get("side", "BUY"),
            "entry_price": float(pos.get("entry_price", 0) or 0),
            "quantity": float(pos.get("quantity", 0) or 0),
        })
    return positions


def _equity(request: Request) -> float:
    auto_trader = getattr(request.app.state, "auto_trader", None)
    if auto_trader is None:
        return 10000.0
    return float(getattr(auto_trader, "equity", 10000.0) or 10000.0)


# ---------------------------------------------------------------------------
# Endpoint'ler
# ---------------------------------------------------------------------------

@router.get("/var", summary="Anlık pozisyonlar için VaR")
async def get_var(
    request: Request,
    confidence: float = 0.95,
    lookback_days: int = 30,
):
    """Canlı açık pozisyonlar için tarihsel VaR ve CVaR hesaplar.

    Pozisyon başına getiri serisi DB kapalı trade PnL'lerinden tahmin edilir.
    """
    from app.core.database import Database
    db = Database()
    closed = db.get_closed_trades(limit=500)

    if not closed:
        return {"var": None, "cvar": None, "message": "Yeterli trade geçmişi yok"}

    pnl_returns = [
        float(t.get("pnl") or 0) / max(float(t.get("entry") or 1) *
                                        float(t.get("qty") or 1), 1e-9)
        for t in closed
        if t.get("pnl") is not None
    ]
    pnl_returns = pnl_returns[-lookback_days * 5:]  # Yaklaşık gün sınırı

    if len(pnl_returns) < 5:
        return {"var": None, "cvar": None, "message": "Yetersiz veri"}

    equity = _equity(request)
    var_pct = historical_var(pnl_returns, confidence)
    cvar_pct = cvar(pnl_returns, confidence)

    return {
        "confidence": confidence,
        "var_pct": round(var_pct * 100, 3),
        "var_usdt": round(equity * abs(var_pct), 2),
        "cvar_pct": round(cvar_pct * 100, 3),
        "cvar_usdt": round(equity * abs(cvar_pct), 2),
        "equity": equity,
        "sample_size": len(pnl_returns),
    }


@router.post("/stress", summary="Senaryo bazlı stres testi")
async def run_stress_test(
    request: Request,
    body: StressTestRequest,
):
    """Seçili senaryoları açık pozisyonlara uygular.

    ``scenario_keys`` listesi BUILTIN_SCENARIOS anahtarlarını içermelidir.
    ``custom_shock_pct`` ile özel şok yüzdesi de belirtilebilir.
    """
    positions = _open_positions_list(request)
    equity = _equity(request)

    if body.scenario_keys:
        unknown = [k for k in body.scenario_keys if k not in BUILTIN_SCENARIOS]
        if unknown:
            raise HTTPException(status_code=400,
                                detail=f"Bilinmeyen senaryo: {unknown}")
        scenarios = {k: BUILTIN_SCENARIOS[k] for k in body.scenario_keys}
    else:
        scenarios = None  # Tüm senaryolar

    if body.custom_shock_pct is not None:
        if scenarios is None:
            scenarios = {}
        scenarios["custom"] = {
            "name": f"Özel Şok (%{body.custom_shock_pct:+.1f})",
            "description": "Kullanıcı tanımlı fiyat şoku",
            "price_shock_pct": float(body.custom_shock_pct),
        }

    result = stress_test(positions, scenarios, equity)
    return result


@router.get("/scenarios", summary="Mevcut stres testi senaryoları")
async def list_scenarios():
    """Yerleşik stres testi senaryolarını listeler."""
    return {"scenarios": {
        k: {
            "name": v["name"],
            "description": v["description"],
            "price_shock_pct": v["price_shock_pct"],
        }
        for k, v in BUILTIN_SCENARIOS.items()
    }}


@router.get("/summary", summary="Kapsamlı risk özeti")
async def get_risk_summary(request: Request, lookback: int = 500):
    """VaR, stres testi ve portföy istatistiklerini birleştiren özet."""
    from app.core.database import Database
    db = Database()
    closed = db.get_closed_trades(limit=lookback)
    equity = _equity(request)
    positions = _open_positions_list(request)

    pnls = [float(t.get("pnl") or 0) for t in closed if t.get("pnl") is not None]
    stats = portfolio_stats(closed) if closed else {}

    # Hızlı VaR
    var_data: Dict[str, Any] = {}
    if len(pnls) >= 5:
        pnl_returns = [
            p / max(float(closed[i].get("entry") or 1) *
                    float(closed[i].get("qty") or 1), 1e-9)
            for i, p in enumerate(pnls)
        ]
        var_data = {
            "var_95_usdt": round(equity * abs(historical_var(pnl_returns, 0.95)), 2),
            "cvar_95_usdt": round(equity * abs(cvar(pnl_returns, 0.95)), 2),
        }

    # Hızlı stres: yalnızca COVID ve FTX
    quick_stress = {}
    if positions:
        st = stress_test(positions, {
            "covid_2020": BUILTIN_SCENARIOS["covid_2020"],
            "ftx_2022": BUILTIN_SCENARIOS["ftx_2022"],
        }, equity)
        for key, sc in st["scenarios"].items():
            quick_stress[key] = {
                "name": sc["name"],
                "total_pnl_usdt": sc["total_pnl_usdt"],
                "total_pnl_pct": sc["total_pnl_pct"],
            }

    return {
        "equity": equity,
        "open_positions": len(positions),
        "closed_trades": len(closed),
        "portfolio_stats": stats,
        "var": var_data,
        "quick_stress": quick_stress,
    }
