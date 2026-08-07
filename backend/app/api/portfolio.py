"""Portföy istatistikleri API endpoint'i.

Canlı DB'deki kapalı işlemlerden Sharpe/Sortino/Calmar ve diğer
risk/getiri metriklerini hesaplayarak döner.
"""
from fastapi import APIRouter

from app.core.database import Database
from app.strategy.analytics import portfolio_stats, monthly_returns_table

router = APIRouter(prefix="/api/v1/portfolio", tags=["portfolio"])

_db = Database()


@router.get("/stats", summary="Portföy risk/getiri metrikleri")
async def get_portfolio_stats(limit: int = 1000):
    """Kapalı işlemlerden Sharpe, Sortino, Calmar, MaxDD ve diğer metrikleri döndürür.

    ``limit`` kadar son kapalı işlem kullanılır (varsayılan 1000).
    """
    trades = _db.get_closed_trades(limit=limit)
    stats = portfolio_stats(trades)
    return {"stats": stats, "trade_count": len(trades)}


@router.get("/monthly", summary="Aylık getiri tablosu")
async def get_monthly_returns(limit: int = 2000):
    """Ay × yıl PnL tablosu döndürür (JSON formatında)."""
    trades = _db.get_closed_trades(limit=limit)
    table = monthly_returns_table(trades)
    if table.empty:
        return {"monthly_returns": {}, "message": "Yeterli veri yok"}
    # DataFrame -> JSON uyumlu dict
    result = {
        str(col): {str(idx): float(val) for idx, val in table[col].items()}
        for col in table.columns
    }
    return {"monthly_returns": result}


@router.get("/summary", summary="Sembol bazında PnL özeti")
async def get_symbol_summary(limit: int = 100):
    """Sembol bazında toplam PnL, işlem sayısı ve kazanma oranı döndürür."""
    rows = _db.get_symbol_pnl(limit=limit)
    for r in rows:
        r["win_rate"] = round(r["wins"] / r["trades"] * 100, 1) if r["trades"] else 0.0
    return {"symbols": rows}
