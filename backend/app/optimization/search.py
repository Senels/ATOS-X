"""Parametre optimizasyonu - TradeBotV23 + BacktestEngine uzerinde grid search.

Grid search, ProcessPoolExecutor ile paralel calisir. Calisan process'lerde
veri yukleme `_init_worker` ile bir kez yapilir (her sembol icin CSV okunur);
her kombinasyon tum semboller uzerinde degerlendirilip ortalama skorla siralanir.
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
from app.strategy import settings as strat_settings  # noqa: E402
from app.strategy.tradebot_v23 import TradeBotV23  # noqa: E402

DEFAULT_GRID: Dict[str, List[Any]] = {
    "rangefilt_length": [2, 3, 4, 5],
    "range_filt_mult": [1.5, 2.0, 2.5, 3.0],
    "signal_expiry": [1, 2, 3, 4],
    "rr_ratio": [1.0, 1.5, 2.0, 3.0],
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


def _evaluate_combo(combo: Dict[str, Any]) -> Dict[str, Any]:
    """Bir parametre kombinasyonunu _CTX sembollerinde degerlendirir."""
    ctx = _CTX
    settings = deepcopy(ctx["base_settings"])
    for key, value in combo.items():
        settings[key] = value

    bot = TradeBotV23(settings)
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
        except Exception:
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
    ):
        if grid is None:
            grid = deepcopy(DEFAULT_GRID)
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
        }

        if self.max_workers > 1:
            from concurrent.futures import ProcessPoolExecutor

            with ProcessPoolExecutor(
                max_workers=self.max_workers,
                initializer=_init_worker,
                initargs=(ctx,),
            ) as ex:
                results = list(ex.map(_evaluate_combo, combos))
        else:
            _init_worker(ctx)
            results = [_evaluate_combo(combo) for combo in combos]

        results.sort(key=lambda r: r["score"], reverse=True)
        return {"results": results, "best": results[0] if results else None}


def best_settings_to_file(best: Dict[str, Any], path: Optional[Path] = None) -> Path:
    """En iyi kombinasyonu optimized_settings.json dosyasina yazar."""
    if path is None:
        path = Path(strat_settings._OPTIMIZED_FILE)
    import json

    payload = dict(best["combo"])
    payload["_objective_score"] = round(float(best["score"]), 4)
    payload["_symbols_count"] = best.get("count", 0)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
