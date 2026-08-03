"""Backtest + canli sinyal API route'lari.

Kaynak:
  - source=csv    -> legacy/data/futures_4h_data arsivinden (hizli)
  - source=binance-> Binance futures kline'dan canli ceker
"""
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException

from app.backtest.engine import BacktestEngine
from app.core.database import Database
from app.data import loader
from app.strategy import settings as strat_settings
from app.strategy.tradebot_v23 import TradeBotV23

router = APIRouter(prefix="/api/v1", tags=["strategy"])
_db = Database()


async def _load_data(symbol: str, interval: str, limit: int, source: str) -> Any:
    if source == "csv":
        return loader.load_csv(symbol, interval, limit=limit)
    if source == "binance":
        from app.exchange.binance_client import BinanceClient
        client = BinanceClient()
        await client.connect()
        return await client.get_klines(symbol, interval, limit)
    raise HTTPException(status_code=400, detail="source: 'csv' | 'binance'")


def _build_settings(
    leading_indicator: Optional[str],
    signal_expiry: Optional[int],
    alternate_signal: Optional[bool],
    rr_ratio: Optional[float],
    sl_lookback: Optional[int],
    atr_fallback: Optional[bool],
    atr_mult: Optional[float],
    confirmations: Optional[str],
) -> Dict[str, Any]:
    settings = strat_settings.get_settings()
    overrides: Dict[str, Any] = {}
    if leading_indicator is not None:
        overrides["leading_indicator"] = leading_indicator
    if signal_expiry is not None:
        overrides["signal_expiry"] = signal_expiry
    if alternate_signal is not None:
        overrides["alternate_signal"] = alternate_signal
    if rr_ratio is not None:
        overrides["rr_ratio"] = rr_ratio
    if sl_lookback is not None:
        overrides["sl_lookback"] = sl_lookback
    if atr_fallback is not None:
        overrides["atr_fallback"] = atr_fallback
    if atr_mult is not None:
        overrides["atr_mult"] = atr_mult
    if confirmations is not None:
        enabled = confirmations.replace(" ", "").split(",")
        enabled = [e for e in enabled if e]
        overrides["confirmations"] = {k: k in enabled for k in settings["confirmations"]}
    settings.update(overrides)
    return settings


@router.get("/backtest/symbols")
async def backtest_symbols(interval: str = "4h"):
    return {"symbols": loader.list_symbols(interval), "interval": interval}


@router.get("/backtest")
async def run_backtest(
    symbol: str = "BTCUSDT",
    interval: str = "4h",
    limit: int = 1000,
    source: str = "csv",
    initial_equity: Optional[float] = None,
    risk_per_trade: Optional[float] = None,
    fee_rate: Optional[float] = None,
    leverage: Optional[float] = None,
    leading_indicator: Optional[str] = None,
    signal_expiry: Optional[int] = None,
    alternate_signal: Optional[bool] = None,
    rr_ratio: Optional[float] = None,
    sl_lookback: Optional[int] = None,
    atr_fallback: Optional[bool] = None,
    atr_mult: Optional[float] = None,
    confirmations: Optional[str] = None,
    max_drawdown_pct: Optional[float] = None,
    max_consecutive_losses: Optional[int] = None,
    max_daily_loss_pct: Optional[float] = None,
    min_equity: Optional[float] = None,
    trailing_activate_pct: Optional[float] = None,
    trailing_sl_pct: Optional[float] = None,
    trailing_min_move_pct: Optional[float] = None,
    breakeven_activate_pct: Optional[float] = None,
    max_position_age_hours: Optional[float] = None,
):
    try:
        df = await _load_data(symbol, interval, limit, source)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Veri yuklenemedi: {e}")

    settings = _build_settings(
        leading_indicator, signal_expiry, alternate_signal, rr_ratio,
        sl_lookback, atr_fallback, atr_mult, confirmations,
    )

    engine_cfg = strat_settings.get_settings()
    engine = BacktestEngine(
        initial_equity=initial_equity if initial_equity is not None else engine_cfg["initial_equity"],
        risk_per_trade=risk_per_trade if risk_per_trade is not None else engine_cfg["risk_per_trade"],
        fee_rate=fee_rate if fee_rate is not None else engine_cfg["fee_rate"],
        slippage=0.0001,
        max_leverage=leverage if leverage is not None else engine_cfg["max_leverage"],
        max_drawdown_pct=max_drawdown_pct if max_drawdown_pct is not None else engine_cfg.get("max_drawdown_pct", 0.0),
        max_consecutive_losses=max_consecutive_losses if max_consecutive_losses is not None else engine_cfg.get("max_consecutive_losses", 0),
        max_daily_loss_pct=max_daily_loss_pct if max_daily_loss_pct is not None else engine_cfg.get("max_daily_loss_pct", 0.0),
        min_equity=min_equity if min_equity is not None else engine_cfg.get("min_equity", 0.0),
        trailing_activate_pct=trailing_activate_pct if trailing_activate_pct is not None else engine_cfg.get("trailing_activate_pct", 0.0),
        trailing_sl_pct=trailing_sl_pct if trailing_sl_pct is not None else engine_cfg.get("trailing_sl_pct", 0.0),
        trailing_min_move_pct=trailing_min_move_pct if trailing_min_move_pct is not None else engine_cfg.get("trailing_min_move_pct", 0.0),
        breakeven_activate_pct=breakeven_activate_pct if breakeven_activate_pct is not None else engine_cfg.get("breakeven_activate_pct", 0.0),
        max_position_age_hours=max_position_age_hours if max_position_age_hours is not None else engine_cfg.get("max_position_age_hours", 0.0),
    )
    bot = TradeBotV23(settings)
    result = bot.analyze(df)
    metrics = engine.run(df, result["orders"], interval)

    # Sonucu DB'ye kaydet (equity_curve/trades buyuk oldugu icin saklanmaz)
    summary = {k: v for k, v in metrics.items() if k not in ("equity_curve", "trades")}
    _db.save_backtest_run(
        symbol=symbol,
        interval=interval,
        source=source,
        params={**engine_cfg, "strategy": settings},
        metrics=summary,
    )

    return {
        "symbol": symbol,
        "interval": interval,
        "source": source,
        "settings": settings,
        "signal_bars": int((result["orders"]["signal"] != 0).sum()),
        **metrics,
    }


@router.get("/backtest/history")
async def backtest_history(symbol: Optional[str] = None, limit: int = 20):
    return {"runs": _db.get_backtest_runs(limit=limit, symbol=symbol)}


@router.get("/backtest/compare")
async def backtest_compare(a: int, b: int):
    runs = {r["id"]: r for r in _db.get_backtest_runs(limit=500)}
    if a not in runs or b not in runs:
        raise HTTPException(status_code=404, detail="Backtest kaydi bulunamadi")
    return {"runs": {str(a): runs[a], str(b): runs[b]}}


@router.get("/strategy/signal")
async def strategy_signal(
    symbol: str = "BTCUSDT",
    interval: str = "4h",
    limit: int = 400,
    source: str = "binance",
):
    try:
        df = await _load_data(symbol, interval, limit, source)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Veri yuklenemedi: {e}")
    bot = TradeBotV23(strat_settings.get_settings())
    signal = bot.generate_signal(df)
    return {"symbol": symbol, "interval": interval, **signal}
