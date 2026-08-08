"""Parametre optimizasyonu API route'lari.

GridSearch uzerinde sembol + grid boyutlari query parametresi ile
calistirilabilen grid arama saglar. Grid boyutlari virgulle ayrilmis
listeler halinde verilir (orn. rangefilt_length=2,3,4).
"""
import math
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException

from app.data import loader
from app.optimization.search import (
    DEFAULT_GRID,
    DEFAULT_TTP_GRID,
    DEFAULT_V24_GRID,
    GridSearch,
    best_settings_to_file,
)

router = APIRouter(prefix="/api/v1", tags=["strategy"])


def _jsonable(obj: Any) -> Any:
    """numpy skalerlerini JSON uyumlu python turlerine cevirir."""
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, float):
        if not math.isfinite(obj):
            return None
        return obj
    if hasattr(obj, "item"):
        try:
            return _jsonable(obj.item())
        except Exception:
            return obj
    return obj


def _split_ints(raw: Optional[str]) -> Optional[List[int]]:
    if raw is None or not raw.strip():
        return None
    try:
        return [int(v.strip()) for v in raw.split(",") if v.strip()]
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Tamsayi listesi gecersiz: {raw}")


def _split_floats(raw: Optional[str]) -> Optional[List[float]]:
    if raw is None or not raw.strip():
        return None
    try:
        return [float(v.strip()) for v in raw.split(",") if v.strip()]
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Sayi listesi gecersiz: {raw}")


@router.get("/optimize/defaults")
async def optimize_defaults():
    return {
        "grid": DEFAULT_GRID,
        "ttp_grid": DEFAULT_TTP_GRID,
        "v24_grid": DEFAULT_V24_GRID,
        "objectives": ["combined", "return", "sharpe", "pf"],
    }


@router.get("/optimize")
async def run_optimize(
    strategy: str = "v23",
    symbols: str = "BTCUSDT,ETHUSDT",
    interval: str = "4h",
    limit: int = 300,
    objective: str = "combined",
    max_workers: int = 1,
    rangefilt_length: Optional[str] = None,
    range_filt_mult: Optional[str] = None,
    signal_expiry: Optional[str] = None,
    rr_ratio: Optional[str] = None,
    sl_lookback: Optional[str] = None,
    fast_ma_len: Optional[str] = None,
    slow_ma_len: Optional[str] = None,
    atr_len: Optional[str] = None,
    tp_long_rr: Optional[str] = None,
    tp_short_rr: Optional[str] = None,
    ema_fast: Optional[str] = None,
    ema_slow: Optional[str] = None,
    rsi_long: Optional[str] = None,
    rsi_short: Optional[str] = None,
    save_to_file: bool = False,
):
    if strategy not in ("v23", "ttp", "v24"):
        raise HTTPException(status_code=400, detail="Strateji v23, ttp veya v24 olabilir")
    symbol_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not symbol_list:
        raise HTTPException(status_code=400, detail="En az bir sembol gerekli")
    available = set(loader.list_symbols(interval))
    missing = [s for s in symbol_list if s not in available]
    if missing:
        raise HTTPException(status_code=400, detail=f"Sembol verisi yok: {', '.join(missing)}")

    if strategy == "ttp":
        int_fields = {
            "fast_ma_len": _split_ints(fast_ma_len),
            "slow_ma_len": _split_ints(slow_ma_len),
            "atr_len": _split_ints(atr_len),
        }
        float_fields = {
            "tp_long_rr": _split_floats(tp_long_rr),
            "tp_short_rr": _split_floats(tp_short_rr),
        }
        default_grid = DEFAULT_TTP_GRID
    elif strategy == "v24":
        int_fields = {
            "ema_fast": _split_ints(ema_fast),
            "ema_slow": _split_ints(ema_slow),
            "sl_lookback": _split_ints(sl_lookback),
        }
        float_fields = {
            "rsi_long": _split_floats(rsi_long),
            "rsi_short": _split_floats(rsi_short),
            "rr_ratio": _split_floats(rr_ratio),
        }
        default_grid = DEFAULT_V24_GRID
    else:
        int_fields = {
            "rangefilt_length": _split_ints(rangefilt_length),
            "signal_expiry": _split_ints(signal_expiry),
            "sl_lookback": _split_ints(sl_lookback),
        }
        float_fields = {
            "range_filt_mult": _split_floats(range_filt_mult),
            "rr_ratio": _split_floats(rr_ratio),
        }
        default_grid = DEFAULT_GRID

    grid: Dict[str, List[Any]] = {}
    for key, value in {**int_fields, **float_fields}.items():
        if value:
            grid[key] = value

    try:
        search = GridSearch(
            grid=grid or None,
            objective=objective,
            max_workers=max_workers,
            strategy=strategy,
        )
        result = search.run(symbols=symbol_list, interval=interval, limit=int(limit))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Optimizasyon hatasi: {e}")

    payload = _jsonable(result)
    payload["strategy"] = strategy
    payload["objective"] = objective
    payload["grid"] = {k: list(v) for k, v in (grid or default_grid).items()}
    payload["symbols"] = symbol_list
    payload["interval"] = interval
    payload["limit"] = int(limit)

    best = payload.get("best")
    if save_to_file and best is not None:
        try:
            path = best_settings_to_file(best, strategy=strategy)
            payload["saved_to"] = str(path)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Dosya yazma hatasi: {e}")
    return payload


@router.post("/optimize/apply")
async def optimize_apply():
    """Kayitli en iyi kombinasyonu (optimized_settings.json) canli ayarlara uygular."""
    from app.strategy import settings as strat_settings

    result = strat_settings.apply_optimized()
    if not result["applied"]:
        raise HTTPException(status_code=404, detail="Uygulanacak optimize edilmis ayar yok")
    return {"status": "ok", **result}
