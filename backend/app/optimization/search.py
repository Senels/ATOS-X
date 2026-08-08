"""Parametre optimizasyonu - TradeBotV23 + TTPTSL + BacktestEngine grid search.

Grid search, ThreadPoolExecutor ile paralel calisir (Windows'ta spawn +
`python -m uvicorn` kombinasyonunda ProcessPoolExecutor kullanilamaz;
cocuk surecler uvicorn'un __main__'ini yeniden calistirir). pandas/numpy
ic islemleri GIL'i biraktigi icin thread paralelligi yeterli hiz saglar.

Strateji secimi (`strategy`): "v23" parametreleri duz (top-level) settings
anahtarlarina, "ttp" parametreleri `settings["ttp"]` bloguna yazilir ve
`get_strategy` fabrikasi uzerinden dogru motorla degerlendirilir.
"""
import itertools
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

REPO_BACKEND = Path(__file__).resolve().parents[2]
if str(REPO_BACKEND) not in sys.path:
    sys.path.insert(0, str(REPO_BACKEND))

from app.backtest.engine import BacktestEngine  # noqa: E402
from app.data import loader  # noqa: E402
from app.strategy import get_strategy  # noqa: E402
from app.strategy import settings as strat_settings  # noqa: E402
from loguru import logger

DEFAULT_GRID: Dict[str, List[Any]] = {
    "rangefilt_length": [2, 3, 4, 5],
    "range_filt_mult": [1.5, 2.0, 2.5, 3.0],
    "signal_expiry": [1, 2, 3, 4],
    "rr_ratio": [1.0, 1.5, 2.0, 3.0],
    "sl_lookback": [3, 5, 7],
}

# TTPTSL icin kucuk, kuerat edilmis grid (23 Optuna parametresinin tam
# kartezyen carpimi cok buyuk; optimize_ttp.py agir optuna yolu olarak kalir).
DEFAULT_TTP_GRID: Dict[str, List[Any]] = {
    "fast_ma_len": [15, 25, 31],
    "slow_ma_len": [60, 92, 120],
    "atr_len": [14, 24],
    "tp_long_rr": [2.0, 2.5, 3.1],
    "tp_short_rr": [1.5, 1.95],
}

# v24 Lite icin grid: parametreler `v24` blogunda (rr_ratio/sl_lookback/atr_mult
# dahil) - namespace "v24" olarak yazilir.
DEFAULT_V24_GRID: Dict[str, List[Any]] = {
    "ema_fast": [20, 50, 100],
    "ema_slow": [100, 150, 200],
    "rsi_long": [50, 55, 60],
    "rsi_short": [40, 45, 50],
    "rr_ratio": [1.0, 1.8, 2.5],
    "sl_lookback": [3, 5, 7],
}

_CTX: Dict[str, Any] = {}


def _clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def score_metrics(metrics: Dict[str, Any], objective: str = "combined") -> float:
    """Tek backtest sonucunu 0-100 arasi standart skora cevirir."""
    if metrics.get("total_trades", 0) == 0:
        return float("-inf")
    ret = _clip(metrics.get("total_return_pct", 0.0), -20, 40) / 40 * 100
    win = float(metrics.get("win_rate", 0.0))
    pf_raw = metrics.get("profit_factor") or 0.0
    pf = _clip(float(pf_raw), 0.0, 3.0) / 3.0 * 100
    if objective == "return":
        return ret
    if objective == "sharpe":
        return float(_clip(metrics.get("sharpe", 0.0), -5, 5)) / 5 * 100
    if objective == "pf":
        return pf
    # combined: getiri %50, kazanc orani %30, PF %20
    return 0.5 * ret + 0.3 * win + 0.2 * pf


def _init_worker(ctx: Dict[str, Any]) -> None:
    global _CTX
    _CTX = ctx


def _evaluate_combo(combo: Dict[str, Any], ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Bir parametre kombinasyonunu ctx sembollerinde degerlendirir."""
    if ctx is None:
        ctx = _CTX
    settings = deepcopy(ctx["base_settings"])
    namespace = ctx.get("param_namespace")
    target = settings if namespace is None else settings.setdefault(namespace, {})
    for key, value in combo.items():
        target[key] = value

    bot = get_strategy(settings)
    engine_kwargs = ctx["engine_kwargs"]
    interval = ctx["interval"]
    limit = ctx["limit"]
    objective = ctx["objective"]

    scores: List[float] = []
    details: List[Dict[str, Any]] = []
    for symbol in ctx["symbols"]:
        try:
            df = loader.load_csv(symbol, interval, limit=limit)
            orders = bot.analyze(df)["orders"]
            engine = BacktestEngine(**engine_kwargs)
            metrics = engine.run(df, orders, interval)
        except Exception as e:
            logger.debug(f"parametre kombinasyonu icin backtest basarisiz: {e}")
            continue
        score = score_metrics(metrics, objective)
        if np.isfinite(score):
            scores.append(score)
            details.append({
                "symbol": symbol,
                "total_return_pct": metrics.get("total_return_pct"),
                "total_trades": metrics.get("total_trades"),
                "win_rate": metrics.get("win_rate"),
                "profit_factor": metrics.get("profit_factor"),
            })

    if not scores:
        return {"combo": combo, "score": float("-inf"), "count": 0, "details": []}

    return {
        "combo": combo,
        "score": float(np.mean(scores)),
        "count": len(scores),
        "details": details,
    }


class GridSearch:
    def __init__(
        self,
        grid: Optional[Dict[str, List[Any]]] = None,
        objective: str = "combined",
        max_workers: Optional[int] = None,
        strategy: str = "v23",
    ):
        self.strategy = strategy
        if grid is None:
            grid = deepcopy(DEFAULT_TTP_GRID if strategy == "ttp" else
                            DEFAULT_V24_GRID if strategy == "v24" else DEFAULT_GRID)
        self.grid = grid
        self.objective = objective
        self.max_workers = max_workers or 1

    def run(
        self,
        symbols: List[str],
        base_settings: Optional[Dict[str, Any]] = None,
        engine_kwargs: Optional[Dict[str, Any]] = None,
        interval: str = "4h",
        limit: int = 1000,
    ) -> Dict[str, Any]:
        if base_settings is None:
            base_settings = strat_settings.default_settings()
        base_settings["active_strategy"] = self.strategy
        if engine_kwargs is None:
            s = strat_settings.default_settings()
            engine_kwargs = {
                "initial_equity": s["initial_equity"],
                "risk_per_trade": s["risk_per_trade"],
                "fee_rate": s["fee_rate"],
                "max_leverage": s["max_leverage"],
                "max_drawdown_pct": s.get("max_drawdown_pct", 0.0),
                "max_consecutive_losses": s.get("max_consecutive_losses", 0),
                "max_daily_loss_pct": s.get("max_daily_loss_pct", 0.0),
                "min_equity": s.get("min_equity", 0.0),
                "trailing_activate_pct": s.get("trailing_activate_pct", 0.0),
                "trailing_sl_pct": s.get("trailing_sl_pct", 0.0),
                "trailing_min_move_pct": s.get("trailing_min_move_pct", 0.0),
                "breakeven_activate_pct": s.get("breakeven_activate_pct", 0.0),
                "max_position_age_hours": s.get("max_position_age_hours", 0.0),
                "min_signal_strength": s.get("min_signal_strength", 0.0),
            }

        combos = [
            dict(zip(self.grid.keys(), values))
            for values in itertools.product(*self.grid.values())
        ]
        ctx = {
            "symbols": list(symbols),
            "base_settings": deepcopy(base_settings),
            "engine_kwargs": engine_kwargs,
            "interval": interval,
            "limit": int(limit),
            "objective": self.objective,
            "param_namespace": None if self.strategy == "v23" else self.strategy,
        }

        if self.max_workers > 1:
            from concurrent.futures import ThreadPoolExecutor
            from functools import partial

            with ThreadPoolExecutor(max_workers=self.max_workers) as ex:
                results = list(ex.map(partial(_evaluate_combo, ctx=ctx), combos))
        else:
            results = [_evaluate_combo(combo, ctx) for combo in combos]

        results.sort(key=lambda r: r["score"], reverse=True)
        return {"results": results, "best": results[0] if results else None}


def best_settings_to_file(
    best: Dict[str, Any],
    path: Optional[Path] = None,
    strategy: str = "v23",
) -> Path:
    """En iyi kombinasyonu optimized_settings.json dosyasina yazar.

    "ttp" stratejisinde parametreler `ttp` blogu olarak + `active_strategy`
    switch'i ile yazilir (apply_optimized + `_defaults` merge uyumlu); "v24"
    icin `v24` blogu; "v23" duz (top-level) formati korunur.
    """
    if path is None:
        path = Path(strat_settings._OPTIMIZED_FILE)
    import json

    score = round(float(best["score"]), 4)
    count = best.get("count", 0)
    if strategy in ("ttp", "v24"):
        payload: Dict[str, Any] = {
            "active_strategy": strategy,
            strategy: dict(best["combo"]),
            "_strategy": strategy,
            "_objective_score": score,
            "_symbols_count": count,
        }
    else:
        payload = dict(best["combo"])
        payload["_objective_score"] = score
        payload["_symbols_count"] = count
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
