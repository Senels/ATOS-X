"""Backtest + canli sinyal API route'lari.

Kaynak:
  - source=csv    -> legacy/data/futures_4h_data arsivinden (hizli)
  - source=binance-> Binance futures kline'dan canli ceker

AI denetimi:
  - ai_filter=True  -> predictor ile `ai_blocked_mask` uretilir, motor
    `ai_blocks=` ile calistirilir (AI yonu sinyalle uyusmayan veya guven
    esiginin altindaki sinyaller engellenir).
  - ab_mode=True    -> temiz + AI filtreli iki kosu karsilastirilir.
"""
import asyncio
import os
import time
import uuid
from typing import Any, Dict, Optional

import pandas as pd
from fastapi import APIRouter, HTTPException

from app.backtest.engine import BacktestEngine
from app.core.database import Database
from app.data import loader
from app.strategy import get_strategy
from app.strategy import settings as strat_settings

router = APIRouter(prefix="/api/v1", tags=["strategy"])
_db = Database()

_ai_predictor_cache = None

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CACHE_DIR = os.path.join(_APP_DIR, "data", "cache_binance")
_CACHE_MAX_AGE_SEC = 12 * 3600

_scan_jobs: Dict[str, Dict[str, Any]] = {}


def _cache_path(interval: str, symbol: str) -> str:
    return os.path.join(_CACHE_DIR, interval, f"{symbol}.csv")


def _cache_fresh(path: str) -> bool:
    try:
        return (time.time() - os.path.getmtime(path)) < _CACHE_MAX_AGE_SEC
    except OSError:
        return False


async def _fetch_binance_history(symbol: str, interval: str, target: int) -> Any:
    """Binance futures kline'larini parcali cekerek `target` bara tamamlar.

    Her parca 1500 bar istenir; sonraki parcanin baslangici bir onceki
    parcanin ilk barinin `1500 * periyot` kadar gerisine alinir (Binance
    startTime'dan itibaren ileriye dogru doner). Boylece parcalar bitisik
    dizilir ve `target` bara eksiksiz ulasilir.
    """
    from app.data.collector import _period_ms
    from app.exchange.binance_client import BinanceClient

    period_ms = _period_ms(interval)
    client = BinanceClient()
    await client.connect()
    chunks = []
    remaining = target
    start_time = None
    while remaining > 0:
        take = min(1500, remaining)
        df = await client.get_klines(symbol, interval, limit=1500, start_time=start_time)
        if df is None or df.empty:
            break
        chunks.append(df)
        if len(df) < take:
            break
        remaining -= take
        start_time = int(df.index[0].timestamp() * 1000) - 1500 * period_ms
    if not chunks:
        raise Exception("Binance kline donmedi")
    out = pd.concat(chunks, ignore_index=False) if len(chunks) > 1 else chunks[0]
    out = out[~out.index.duplicated(keep="first")].sort_index()
    return out.iloc[-target:]


async def _load_binance_cached(symbol: str, interval: str, limit: int) -> Any:
    """Onbellekli Binance verisi: taze dosya varsa diskten, yoksa indirir.

    Onbellek dosyasi istenen bar sayisindan az veri iceriyorsa bayat sayilir
    (or. onceki kosu daha kucuk limit ile doldurmus olabilir) ve yeniden
    indirilir.
    """
    path = _cache_path(interval, symbol)
    if _cache_fresh(path):
        try:
            df = pd.read_csv(path, index_col=0, parse_dates=True)
            if len(df) >= int(limit):
                return df.iloc[-int(limit):]
        except Exception:
            pass
    df = await _fetch_binance_history(symbol, interval, max(int(limit), 100))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    df.to_csv(path, encoding="utf-8")
    return df.iloc[-int(limit):]


def _get_predictor():
    """Yuklenmis AI predictorunu modul seviyesinde otelemeli dondurur."""
    global _ai_predictor_cache
    if _ai_predictor_cache is None:
        try:
            from app.ai.model import load_predictor
            model_name = str(strat_settings.get_settings().get("ai_model_path", "ai_direction"))
            _ai_predictor_cache = load_predictor(model_name) or False
        except Exception:
            _ai_predictor_cache = False
    return None if _ai_predictor_cache is False else _ai_predictor_cache


def _banned_symbols() -> set:
    """`banned_symbols` listesini buyuk harfli sete cevirir (canli trader ile ayni kural)."""
    return {str(x).upper() for x in strat_settings.get_settings().get("banned_symbols", [])}


async def _load_data(symbol: str, interval: str, limit: int, source: str) -> Any:
    if source == "csv":
        return loader.load_csv(symbol, interval, limit=limit)
    if source == "binance":
        return await _load_binance_cached(symbol, interval, limit)
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
    sl_timeframe: Optional[str] = None,
    ttp: Optional[str] = None,
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
    if sl_timeframe is not None:
        overrides["sl_timeframe"] = sl_timeframe
    if ttp:
        try:
            import json
            parsed = json.loads(ttp)
            if isinstance(parsed, dict) and parsed:
                cur = dict(settings.get("ttp", {}))
                cur.update(parsed)
                overrides["ttp"] = cur
        except Exception:
            pass
    if confirmations is not None:
        enabled = confirmations.replace(" ", "").split(",")
        enabled = [e for e in enabled if e]
        overrides["confirmations"] = {k: k in enabled for k in settings["confirmations"]}
    settings.update(overrides)
    return settings


def _engine_kwargs(
    initial_equity=None, risk_per_trade=None, fee_rate=None, leverage=None,
    max_drawdown_pct=None, max_consecutive_losses=None, max_daily_loss_pct=None,
    min_equity=None, trailing_activate_pct=None, trailing_sl_pct=None,
    trailing_min_move_pct=None, breakeven_activate_pct=None,
    max_position_age_hours=None, min_signal_strength=None,
) -> Dict[str, Any]:
    """Settings varsayilanlari ile birlestirilmis BacktestEngine kwargs'lari."""
    engine_cfg = strat_settings.get_settings()
    return {
        "initial_equity": initial_equity if initial_equity is not None else engine_cfg["initial_equity"],
        "risk_per_trade": risk_per_trade if risk_per_trade is not None else engine_cfg["risk_per_trade"],
        "fee_rate": fee_rate if fee_rate is not None else engine_cfg["fee_rate"],
        "slippage": 0.0001,
        "max_leverage": leverage if leverage is not None else engine_cfg["max_leverage"],
        "max_drawdown_pct": max_drawdown_pct if max_drawdown_pct is not None else engine_cfg.get("max_drawdown_pct", 0.0),
        "max_consecutive_losses": max_consecutive_losses if max_consecutive_losses is not None else engine_cfg.get("max_consecutive_losses", 0),
        "max_daily_loss_pct": max_daily_loss_pct if max_daily_loss_pct is not None else engine_cfg.get("max_daily_loss_pct", 0.0),
        "min_equity": min_equity if min_equity is not None else engine_cfg.get("min_equity", 0.0),
        "trailing_activate_pct": trailing_activate_pct if trailing_activate_pct is not None else engine_cfg.get("trailing_activate_pct", 0.0),
        "trailing_sl_pct": trailing_sl_pct if trailing_sl_pct is not None else engine_cfg.get("trailing_sl_pct", 0.0),
        "trailing_min_move_pct": trailing_min_move_pct if trailing_min_move_pct is not None else engine_cfg.get("trailing_min_move_pct", 0.0),
        "breakeven_activate_pct": breakeven_activate_pct if breakeven_activate_pct is not None else engine_cfg.get("breakeven_activate_pct", 0.0),
        "max_position_age_hours": max_position_age_hours if max_position_age_hours is not None else engine_cfg.get("max_position_age_hours", 0.0),
        "min_signal_strength": min_signal_strength if min_signal_strength is not None else engine_cfg.get("min_signal_strength", 0.0),
        "vol_sizing_enabled": bool(engine_cfg.get("vol_sizing_enabled", False)),
        "vol_mult_hi": float(engine_cfg.get("vol_mult_hi", 1.5)),
        "vol_mult_lo": float(engine_cfg.get("vol_mult_lo", 0.6)),
        "vol_mult_factor": float(engine_cfg.get("vol_mult_factor", 0.5)),
    }


def _run_once(df, orders, interval: str, kwargs: Dict[str, Any],
              ai_mask=None) -> Dict[str, Any]:
    return BacktestEngine(**kwargs).run(df, orders, interval, ai_blocks=ai_mask)


def _ai_mask(predictor, df, signal_arr, threshold: float):
    from app.ai.backtest_sim import ai_blocked_mask
    return ai_blocked_mask(predictor, df, signal_arr, threshold)


def _signal_stats(df, signal_arr, mask, horizon: int = 12):
    from app.ai.backtest_sim import signal_accuracy
    return signal_accuracy(df, signal_arr, mask, horizon)


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
    sl_timeframe: Optional[str] = None,
    ttp: Optional[str] = None,
    max_drawdown_pct: Optional[float] = None,
    max_consecutive_losses: Optional[int] = None,
    max_daily_loss_pct: Optional[float] = None,
    min_equity: Optional[float] = None,
    trailing_activate_pct: Optional[float] = None,
    trailing_sl_pct: Optional[float] = None,
    trailing_min_move_pct: Optional[float] = None,
    breakeven_activate_pct: Optional[float] = None,
    max_position_age_hours: Optional[float] = None,
    min_signal_strength: Optional[float] = None,
    ai_filter: bool = False,
    ai_threshold: float = 0.55,
    ab_mode: bool = False,
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
        sl_timeframe=sl_timeframe, ttp=ttp,
    )

    kwargs = _engine_kwargs(
        initial_equity, risk_per_trade, fee_rate, leverage,
        max_drawdown_pct, max_consecutive_losses, max_daily_loss_pct,
        min_equity, trailing_activate_pct, trailing_sl_pct,
        trailing_min_move_pct, breakeven_activate_pct,
        max_position_age_hours, min_signal_strength,
    )
    bot = get_strategy(settings)
    analyze = getattr(bot, "analyze_full", None)
    result = analyze(df) if analyze else bot.analyze(df)
    sig = result["orders"]["signal"].to_numpy(int)

    predictor = _get_predictor()
    ai_applied = False
    ai_blocked = 0
    ab = None
    metrics = None
    mask = None

    if ab_mode:
        if predictor is None:
            metrics = _run_once(df, result["orders"], interval, kwargs)
        else:
            mask = _ai_mask(predictor, df, sig, ai_threshold)
            base = _run_once(df, result["orders"], interval, kwargs)
            ai_res = _run_once(df, result["orders"], interval, kwargs, mask)
            ab = {
                "baseline": base,
                "with_ai": ai_res,
                "signal_stats": _signal_stats(df, sig, mask),
            }
            ai_applied = True
            ai_blocked = int(mask.sum())
            metrics = ai_res
    elif ai_filter:
        if predictor is None:
            metrics = _run_once(df, result["orders"], interval, kwargs)
        else:
            mask = _ai_mask(predictor, df, sig, ai_threshold)
            metrics = _run_once(df, result["orders"], interval, kwargs, mask)
            ai_applied = True
            ai_blocked = int(mask.sum())
    else:
        metrics = _run_once(df, result["orders"], interval, kwargs)

    # Sonucu DB'ye kaydet (equity_curve/trades buyuk oldugu icin saklanmaz)
    summary = {k: v for k, v in metrics.items() if k not in ("equity_curve", "trades")}
    _db.save_backtest_run(
        symbol=symbol,
        interval=interval,
        source=source,
        params={**strat_settings.get_settings(), "strategy": settings},
        metrics=summary,
    )

    return {
        "symbol": symbol,
        "interval": interval,
        "source": source,
        "settings": settings,
        "signal_bars": int((sig != 0).sum()),
        "ai_filter": bool(ai_filter or ab_mode),
        "ai_applied": ai_applied,
        "ai_blocked": ai_blocked,
        "ab": ab,
        **metrics,
    }


@router.get("/backtest/scan")
async def run_backtest_scan(
    symbols: str = "BTCUSDT",
    interval: str = "4h",
    limit: int = 400,
    source: str = "csv",
    ai_filter: bool = False,
    ai_threshold: float = 0.55,
    ab_mode: bool = False,
    leading_indicator: Optional[str] = None,
    signal_expiry: Optional[int] = None,
    alternate_signal: Optional[bool] = None,
    rr_ratio: Optional[float] = None,
    sl_lookback: Optional[int] = None,
    atr_fallback: Optional[bool] = None,
    atr_mult: Optional[float] = None,
    confirmations: Optional[str] = None,
    sl_timeframe: Optional[str] = None,
    ttp: Optional[str] = None,
    max_position_age_hours: Optional[float] = None,
    min_signal_strength: Optional[float] = None,
    initial_equity: Optional[float] = None,
    risk_per_trade: Optional[float] = None,
    fee_rate: Optional[float] = None,
    leverage: Optional[float] = None,
    max_drawdown_pct: Optional[float] = None,
    max_consecutive_losses: Optional[int] = None,
    max_daily_loss_pct: Optional[float] = None,
    min_equity: Optional[float] = None,
    trailing_activate_pct: Optional[float] = None,
    trailing_sl_pct: Optional[float] = None,
    trailing_min_move_pct: Optional[float] = None,
    breakeven_activate_pct: Optional[float] = None,
):
    """Coklu sembol backtest taramasi (tek + toplu).

    Her sembol icin ayri kosu; `ab_mode=True` ise temiz + AI filtreli iki
    kosu satir bazinda ve toplamda karsilastirilir. Equity curve'leri cok
    buyuk oldugu icin donulmez (metrikler + isabet istatistikleri).
    """
    symbol_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    banned = _banned_symbols()
    if banned:
        symbol_list = [s for s in symbol_list if s not in banned]
    if not symbol_list:
        raise HTTPException(status_code=400, detail="symbols bos")
    return await _run_scan(
        symbol_list, interval, limit, source, ai_filter, ai_threshold, ab_mode,
        leading_indicator, signal_expiry, alternate_signal, rr_ratio, sl_lookback,
        atr_fallback, atr_mult, confirmations, sl_timeframe, ttp,
        max_position_age_hours, min_signal_strength, initial_equity, risk_per_trade,
        fee_rate, leverage, max_drawdown_pct, max_consecutive_losses,
        max_daily_loss_pct, min_equity, trailing_activate_pct, trailing_sl_pct,
        trailing_min_move_pct, breakeven_activate_pct,
        on_progress=None,
    )


async def _run_scan(
    symbol_list, interval, limit, source, ai_filter, ai_threshold, ab_mode,
    leading_indicator, signal_expiry, alternate_signal, rr_ratio, sl_lookback,
    atr_fallback, atr_mult, confirmations, sl_timeframe, ttp,
    max_position_age_hours, min_signal_strength, initial_equity, risk_per_trade,
    fee_rate, leverage, max_drawdown_pct, max_consecutive_losses,
    max_daily_loss_pct, min_equity, trailing_activate_pct, trailing_sl_pct,
    trailing_min_move_pct, breakeven_activate_pct,
    on_progress=None,
) -> Dict[str, Any]:
    """Coklu sembol backtest taramasi (tek + toplu) — arka plan job ortak gövdesi."""
    settings = _build_settings(
        leading_indicator, signal_expiry, alternate_signal, rr_ratio,
        sl_lookback, atr_fallback, atr_mult, confirmations,
        sl_timeframe=sl_timeframe, ttp=ttp,
    )
    kwargs = _engine_kwargs(
        initial_equity, risk_per_trade, fee_rate, leverage,
        max_drawdown_pct=max_drawdown_pct,
        max_consecutive_losses=max_consecutive_losses,
        max_daily_loss_pct=max_daily_loss_pct,
        min_equity=min_equity,
        trailing_activate_pct=trailing_activate_pct,
        trailing_sl_pct=trailing_sl_pct,
        trailing_min_move_pct=trailing_min_move_pct,
        breakeven_activate_pct=breakeven_activate_pct,
        max_position_age_hours=max_position_age_hours,
        min_signal_strength=min_signal_strength,
    )
    predictor = _get_predictor()
    rows = []
    total = len(symbol_list)

    async def _process_one(symbol: str) -> Dict[str, Any]:
        try:
            df = await _load_data(symbol, interval, limit, source)
        except Exception as e:
            return {"symbol": symbol, "error": str(e)}
        bot = get_strategy(settings)
        analyze = getattr(bot, "analyze_full", None)
        result = analyze(df) if analyze else bot.analyze(df)
        sig = result["orders"]["signal"].to_numpy(int)
        row: Dict[str, Any] = {
            "symbol": symbol,
            "signals": int((sig != 0).sum()),
            "base_trades": 0, "base_wins": 0, "base_net": 0.0, "base_win_rate": 0.0,
            "ai_trades": 0, "ai_wins": 0, "ai_net": 0.0, "ai_win_rate": 0.0,
            "blocked": 0,
        }
        if ab_mode and predictor is not None:
            mask = _ai_mask(predictor, df, sig, ai_threshold)
            base = _run_once(df, result["orders"], interval, kwargs)
            ai_res = _run_once(df, result["orders"], interval, kwargs, mask)
            row.update({
                "base_trades": base["total_trades"],
                "base_wins": base["winning_trades"],
                "base_net": base["net_profit"],
                "base_win_rate": base["win_rate"],
                "base_max_dd": base["max_drawdown_pct"],
                "base_pf": base["profit_factor"],
                "ai_trades": ai_res["total_trades"],
                "ai_wins": ai_res["winning_trades"],
                "ai_net": ai_res["net_profit"],
                "ai_win_rate": ai_res["win_rate"],
                "ai_max_dd": ai_res["max_drawdown_pct"],
                "ai_pf": ai_res["profit_factor"],
                "blocked": int(mask.sum()),
                "signal_stats": _signal_stats(df, sig, mask),
            })
        else:
            mask = None
            if ai_filter and predictor is not None:
                mask = _ai_mask(predictor, df, sig, ai_threshold)
                row["blocked"] = int(mask.sum())
            res = _run_once(df, result["orders"], interval, kwargs, mask)
            if mask is not None:
                row["signal_stats"] = _signal_stats(df, sig, mask)
            row.update({
                "trades": res["total_trades"],
                "wins": res["winning_trades"],
                "net": res["net_profit"],
                "win_rate": res["win_rate"],
                "max_dd": res["max_drawdown_pct"],
                "pf": res["profit_factor"],
                "base_trades": res["total_trades"],
                "base_wins": res["winning_trades"],
                "base_net": res["net_profit"],
                "base_win_rate": res["win_rate"],
            })
        return row

    batch_size = 6
    done = 0
    for start in range(0, total, batch_size):
        batch = symbol_list[start:start + batch_size]
        batch_rows = await asyncio.gather(*[_process_one(s) for s in batch])
        for symbol, row in zip(batch, batch_rows):
            done += 1
            rows.append(row)
            if on_progress:
                on_progress(done, total, symbol, row)

    from app.ai.backtest_sim import summarize_scan
    return {
        "symbols": symbol_list,
        "interval": interval,
        "source": source,
        "ai_filter": bool(ai_filter or ab_mode),
        "ai_applied": bool(predictor is not None),
        "ab_mode": bool(ab_mode),
        "results": rows,
        "summary": summarize_scan(rows),
    }


@router.get("/backtest/market-symbols")
async def market_symbols():
    """Binance USDM Futures'taki tum USDT pariteleri (taramada kullanilir)."""
    from app.exchange.binance_client import BinanceClient
    client = BinanceClient()
    try:
        symbols = await client.load_all_symbols()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Binance sembol listesi alinamadi: {e}")
    return {"count": len(symbols), "symbols": symbols}


@router.post("/backtest/scan/start")
async def scan_start(
    symbols: str = "market",
    interval: str = "4h",
    limit: int = 2190,
    source: str = "binance",
    ai_filter: bool = False,
    ai_threshold: float = 0.55,
    ab_mode: bool = False,
    leading_indicator: Optional[str] = None,
    signal_expiry: Optional[int] = None,
    alternate_signal: Optional[bool] = None,
    rr_ratio: Optional[float] = None,
    sl_lookback: Optional[int] = None,
    atr_fallback: Optional[bool] = None,
    atr_mult: Optional[float] = None,
    confirmations: Optional[str] = None,
    sl_timeframe: Optional[str] = None,
    ttp: Optional[str] = None,
    max_position_age_hours: Optional[float] = None,
    min_signal_strength: Optional[float] = None,
    initial_equity: Optional[float] = None,
    risk_per_trade: Optional[float] = None,
    fee_rate: Optional[float] = None,
    leverage: Optional[float] = None,
    max_drawdown_pct: Optional[float] = None,
    max_consecutive_losses: Optional[int] = None,
    max_daily_loss_pct: Optional[float] = None,
    min_equity: Optional[float] = None,
    trailing_activate_pct: Optional[float] = None,
    trailing_sl_pct: Optional[float] = None,
    trailing_min_move_pct: Optional[float] = None,
    breakeven_activate_pct: Optional[float] = None,
):
    """Arka plan taramasi baslatir; `symbols=market` tum USDT paritelerini tarar.

    Job ilerlemesi GET /backtest/scan/status/{id} ile izlenir; sonuc ayni
    endpoint'ten `result` alaninda doner.
    """
    job_id = uuid.uuid4().hex[:12]
    job: Dict[str, Any] = {
        "id": job_id,
        "status": "running",
        "started_at": time.time(),
        "total": 0,
        "done": 0,
        "current_symbol": "",
        "errors": [],
        "result": None,
    }

    if symbols.strip().lower() == "market":
        from app.exchange.binance_client import BinanceClient
        client = BinanceClient()
        try:
            symbol_list = await client.load_all_symbols()
        except Exception as e:
            raise HTTPException(status_code=503, detail=f"Piyasa listesi alinamadi: {e}")
        if not symbol_list:
            raise HTTPException(status_code=503, detail="Piyasa listesi bos")
        banned = _banned_symbols()
        if banned:
            symbol_list = [s for s in symbol_list if s.upper() not in banned]
    else:
        symbol_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
        banned = _banned_symbols()
        if banned:
            symbol_list = [s for s in symbol_list if s not in banned]
    if not symbol_list:
        raise HTTPException(status_code=400, detail="symbols bos")
    job["total"] = len(symbol_list)
    _scan_jobs[job_id] = job

    def on_progress(done: int, total: int, symbol: str, row: Optional[Dict[str, Any]]):
        job["done"] = done
        job["total"] = total
        job["current_symbol"] = symbol
        if row and "error" in row:
            job["errors"].append(f"{symbol}: {row['error']}")

    async def _worker():
        try:
            result = await _run_scan(
                symbol_list, interval, limit, source, ai_filter, ai_threshold,
                ab_mode, leading_indicator, signal_expiry, alternate_signal,
                rr_ratio, sl_lookback, atr_fallback, atr_mult, confirmations,
                sl_timeframe, ttp, max_position_age_hours, min_signal_strength,
                initial_equity, risk_per_trade, fee_rate, leverage,
                max_drawdown_pct, max_consecutive_losses, max_daily_loss_pct,
                min_equity, trailing_activate_pct, trailing_sl_pct,
                trailing_min_move_pct, breakeven_activate_pct,
                on_progress=on_progress,
            )
            job["result"] = result
            job["status"] = "done"
        except Exception as e:
            job["status"] = "failed"
            job["error"] = str(e)
        job["finished_at"] = time.time()

    asyncio.create_task(_worker())
    return {"job_id": job_id, "total": job["total"]}


@router.get("/backtest/scan/status/{job_id}")
async def scan_status(job_id: str):
    job = _scan_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job bulunamadi")
    out = {k: v for k, v in job.items() if k != "errors"}
    out["errors"] = job["errors"][-20:]
    if job["status"] in ("done", "failed"):
        out["elapsed"] = round((job.get("finished_at") or time.time()) - job["started_at"], 1)
    return out


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
    bot = get_strategy(strat_settings.get_settings())
    signal = bot.generate_signal(df)
    return {"symbol": symbol, "interval": interval, **signal}
