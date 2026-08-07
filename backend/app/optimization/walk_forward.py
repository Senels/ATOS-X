"""Walk-Forward Optimizasyon: zamana göre bölünmüş IS/OOS değerlendirme.

Mevcut ``GridSearch`` motorunu kullanarak TimeSeriesSplit mantığıyla
her fold'da optimize edilip out-of-sample test yapılır. Overfitting oranı
(IS/OOS performans farkı) raporlanır.
"""
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
from app.optimization.search import GridSearch, score_metrics  # noqa: E402
from app.strategy import get_strategy  # noqa: E402
from app.strategy import settings as strat_settings  # noqa: E402


def _split_df(df, train_frac: float):
    """DataFrame'i train/test olarak böler."""
    cut = int(len(df) * train_frac)
    return df.iloc[:cut], df.iloc[cut:]


def walk_forward(
    symbol: str = "",
    interval: str = "4h",
    n_splits: int = 5,
    train_frac: float = 0.7,
    train_pct: Optional[float] = None,  # alias for train_frac
    param_grid: Optional[Dict[str, List[Any]]] = None,
    strategy: str = "v23",
    objective: str = "combined",
    limit: int = 1000,
    data_dir: Optional[str] = None,
    df=None,  # pre-loaded DataFrame (skips CSV loading when provided)
) -> Dict[str, Any]:
    """Walk-Forward optimizasyon motoru.

    Her fold için:
    1. Eğitim bölümünde GridSearch ile en iyi parametreler bulunur.
    2. Test bölümünde bu parametreler değerlendirilir (OOS).
    3. IS ve OOS skorları karşılaştırılır.

    Parametreler
    ------------
    symbol       : Analiz edilecek sembol (CSV gerektirir).
    interval     : Zaman dilimi.
    n_splits     : Fold sayısı.
    train_frac   : Her fold'da eğitim oranı (0–1).
    param_grid   : GridSearch grid'i; None ise strateji varsayılanı.
    strategy     : "v23" | "ttp".
    objective    : GridSearch skoru.
    limit        : Yüklenecek bar sayısı.

    Dönüş
    ------
    Dict: fold sonuçları, IS/OOS ortalamaları, overfitting oranı.
    """
    # train_pct is an alias for train_frac
    if train_pct is not None:
        train_frac = train_pct

    # df can be pre-loaded (used in tests / API); otherwise load from CSV
    if df is None:
        try:
            df = loader.load_csv(symbol, interval, limit=limit, data_dir=data_dir)
        except FileNotFoundError as exc:
            return {"error": str(exc), "symbol": symbol}

    if len(df) < 100:
        return {"error": "Yetersiz veri (min 100 bar)", "symbol": symbol}

    # Kayan pencereli fold'lar
    fold_size = len(df) // n_splits
    if fold_size < 20:
        return {"error": "Fold başına yetersiz veri", "symbol": symbol}

    base_settings = strat_settings.default_settings()
    base_settings["active_strategy"] = strategy
    engine_cfg = strat_settings.default_settings()
    engine_kwargs = {
        "initial_equity": engine_cfg["initial_equity"],
        "risk_per_trade": engine_cfg["risk_per_trade"],
        "fee_rate": engine_cfg["fee_rate"],
        "max_leverage": engine_cfg["max_leverage"],
    }

    folds: List[Dict[str, Any]] = []
    is_scores: List[float] = []
    oos_scores: List[float] = []

    for fold_idx in range(n_splits):
        # Aralık hesapla: kayan pencere
        start = fold_idx * fold_size
        end = start + fold_size * 2
        if end > len(df):
            end = len(df)
        fold_df = df.iloc[start:end]
        if len(fold_df) < 40:
            continue

        df_train, df_oos = _split_df(fold_df, train_frac)
        if len(df_train) < 20 or len(df_oos) < 10:
            continue

        # IS: GridSearch
        gs = GridSearch(grid=deepcopy(param_grid) if param_grid else None,
                        objective=objective, strategy=strategy)
        try:
            is_result = gs.run(
                symbols=[symbol],
                base_settings=deepcopy(base_settings),
                engine_kwargs=engine_kwargs,
                interval=interval,
                limit=len(df_train),
            )
            best_params = is_result.get("best_combo") or {}
            is_score = float(is_result.get("best_score", 0.0))
        except Exception as exc:
            folds.append({"fold": fold_idx, "error": str(exc)})
            continue

        # OOS: en iyi parametrelerle değerlendir
        oos_settings = deepcopy(base_settings)
        ns = oos_settings if strategy == "v23" else oos_settings.setdefault("ttp", {})
        ns.update(best_params)
        oos_settings["active_strategy"] = strategy

        try:
            bot = get_strategy(oos_settings)
            orders = bot.analyze(df_oos)["orders"]
            oos_metrics = BacktestEngine(**engine_kwargs).run(df_oos, orders, interval)
            oos_score = score_metrics(oos_metrics, objective)
        except Exception as exc:
            oos_score = float("-inf")
            oos_metrics = {}

        is_scores.append(is_score)
        oos_scores.append(float(oos_score) if np.isfinite(oos_score) else 0.0)

        folds.append({
            "fold": fold_idx,
            "train_bars": len(df_train),
            "test_bars": len(df_oos),
            "best_params": best_params,
            "is_score": round(is_score, 3),
            "oos_score": round(float(oos_score), 3) if np.isfinite(oos_score) else None,
            "oos_metrics": {
                k: oos_metrics.get(k)
                for k in ("total_return_pct", "win_rate", "profit_factor",
                          "sharpe", "max_drawdown_pct", "total_trades")
            },
        })

    avg_is = round(float(np.mean(is_scores)), 3) if is_scores else None
    avg_oos = round(float(np.mean(oos_scores)), 3) if oos_scores else None
    overfit_ratio = None
    if avg_is and avg_oos is not None and abs(avg_is) > 1e-9:
        overfit_ratio = round(1.0 - avg_oos / avg_is, 4)

    return {
        "symbol": symbol,
        "interval": interval,
        "strategy": strategy,
        "n_splits": n_splits,
        "train_frac": train_frac,
        "folds": folds,
        "avg_is_score": avg_is,
        "avg_oos_score": avg_oos,
        "overfit_ratio": overfit_ratio,
        "note": (
            "overfit_ratio 0 = mükemmel genelleme, 1 = tam overfit"
        ),
    }
