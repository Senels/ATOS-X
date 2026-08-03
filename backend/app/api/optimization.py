"""Parametre optimizasyonu API route'lari.

GridSearch uzerinde sembol + grid boyutlari query parametresi ile
calistirilabilen grid arama saglar. Grid boyutlari virgulle ayrilmis
listeler halinde verilir (orn. rangefilt_length=2,3,4).
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException

from app.data import loader
from app.optimization.search import DEFAULT_GRID, GridSearch, best_settings_to_file

router = APIRouter(prefix="/api/v1", tags=["strategy"])


def _jsonable(obj: Any) -> Any:
    """numpy skalerlerini JSON uyumlu python turlerine cevirir."""
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if hasattr(obj, "item"):
        try:
            return obj.item()
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
    return {"grid": DEFAULT_GRID, "objectives": ["combined", "return", "sharpe", "pf"]}


@router.get("/optimize")
async def run_optimize(
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
    save_to_file: bool = False,
):
    symbol_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not symbol_list:
        raise HTTPException(status_code=400, detail="En az bir sembol gerekli")
    available = set(loader.list_symbols(interval))
    missing = [s for s in symbol_list if s not in available]
    if missing:
        raise HTTPException(status_code=400, detail=f"Sembol verisi yok: {', '.join(missing)}")

    grid: Dict[str, List[Any]] = {}
    int_fields = {
        "rangefilt_length": _split_ints(rangefilt_length),
        "signal_expiry": _split_ints(signal_expiry),
        "sl_lookback": _split_ints(sl_lookback),
    }
    float_fields = {
        "range_filt_mult": _split_floats(range_filt_mult),
        "rr_ratio": _split_floats(rr_ratio),
    }
    for key, value in {**int_fields, **float_fields}.items():
        if value:
            grid[key] = value

    search = GridSearch(grid=grid or None, objective=objective, max_workers=max_workers)
    try:
        result = search.run(symbols=symbol_list, interval=interval, limit=int(limit))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Optimizasyon hatasi: {e}")

    payload = _jsonable(result)
    payload["objective"] = objective
    payload["grid"] = {k: list(v) for k, v in (grid or DEFAULT_GRID).items()}
    payload["symbols"] = symbol_list
    payload["interval"] = interval
    payload["limit"] = int(limit)

    best = payload.get("best")
    if save_to_file and best is not None:
        try:
            path = best_settings_to_file(best)
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
