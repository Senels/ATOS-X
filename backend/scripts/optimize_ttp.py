"""TTPTSL parametre optimizer — Pine Script stratejisinin birebir Python cevirisi.

Strateji: hizli/yavas MA crossover ile giris, yuzde/ATR/RR TP, yuzde/ATR SL,
kismi TP kapanisi + trailing SL ve opsiyonel trailing TP. Optuna ile her
sembol icin en iyi parametreler aranir; ardindan semboller arasi ortak
(unified) en iyi parametre seti cikarilir.

Kullanim:
  python scripts/optimize_ttp.py                          # BTCUSDT + top 5
  python scripts/optimize_ttp.py --top 10                 # top 10 USDT futures
  python scripts/optimize_ttp.py --symbols BTCUSDT,ETHUSDT,SOLUSDT
  python scripts/optimize_ttp.py --trials 800
  python scripts/optimize_ttp.py --trials 300 --top 20 --bars 30000
"""
import argparse
import json
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

try:
    import ccxt
except ImportError:  # pragma: no cover - ccxt opsiyonel
    ccxt = None

try:
    import optuna
except ImportError:  # pragma: no cover - optuna opsiyonel
    optuna = None

OUTPUT_FILE = "best_params_ttp_1m.json"
CSV_CANDIDATES = [
    "{sym}_1m_90d.csv",
    "{sym_lower}_1m_90d.csv",
    "{sym_lower}_1m_3mo.csv",
    "bot/{sym_lower}_1m_90d.csv",
    "legacy/bot/{sym_lower}_1m_90d.csv",
    "legacy/bot/{sym}_1m_90d.csv",
]

# ---------------------------------------------------------------------------
# #1 DATA LOADER
# ---------------------------------------------------------------------------


def _repo_root() -> str:
    """Script dizininden proje kokunu bulur (backend/scripts -> kok)."""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", ".."))


def load_data(symbol: str, days: int = 60, bars: int = 0) -> pd.DataFrame:
    """Yerel CSV ara; yoksa ccxt ile Binance'ten cek ve cache yaz.

    Donus: DataFrame[timestamp, open, high, low, close, volume]
    (timestamp ms epoch).
    """
    df = _load_local_csv(symbol)
    if df is None:
        if ccxt is None:
            raise RuntimeError(
                "Yerel CSV bulunamadi ve ccxt kurulu degil; pip install ccxt gerekli."
            )
        df = _fetch_from_binance(symbol, days)

    df = df[["timestamp", "open", "high", "low", "close", "volume"]].astype(float)

    if days > 0:
        cutoff = df["timestamp"].max() - days * 86400_000
        df = df[df["timestamp"] >= cutoff]
    if bars > 0:
        df = df.tail(bars)
    return df.reset_index(drop=True)


def _load_local_csv(symbol: str) -> Optional[pd.DataFrame]:
    names = {"sym": symbol, "sym_lower": symbol.lower()}
    bases = [os.getcwd(), _repo_root(), os.path.join(_repo_root(), "backend")]
    for base in bases:
        for pat in CSV_CANDIDATES:
            path = os.path.join(base, pat.format(**names))
            if os.path.exists(path):
                try:
                    return pd.read_csv(path)
                except Exception:
                    continue
    return None


def _fetch_from_binance(symbol: str, days: int) -> pd.DataFrame:
    ex = ccxt.binance({"options": {"defaultType": "future"}})
    since = ex.milliseconds() - days * 86400_000
    rows: List[list] = []
    while True:
        batch = ex.fetch_ohlcv(symbol, "1m", since=since, limit=1000)
        if not batch:
            break
        rows += batch
        if len(batch) < 1000:
            break
        since = batch[-1][0] + 1
    if not rows:
        raise RuntimeError(f"Binance'ten veri alinamadi: {symbol}")

    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df = df.drop_duplicates(subset="timestamp").sort_values("timestamp")
    os.makedirs("futures_1m_data", exist_ok=True)
    path = os.path.join("futures_1m_data", f"{symbol}.csv")
    df.to_csv(path, index=False)
    return df


def get_top_usdt_symbols(top_n: int = 20) -> List[str]:
    """Binance Futures USDT pairlerini quoteVolume'a gore siralar."""
    if ccxt is None:
        raise RuntimeError("ccxt kurulu degil; pip install ccxt gerekli.")
    ex = ccxt.binance({"options": {"defaultType": "future"}})
    tickers = ex.fetch_tickers()
    ranked = [
        (sym, t.get("quoteVolume") or 0.0)
        for sym, t in tickers.items()
        if sym.endswith(":USDT") and "/USDT:" in sym
    ]
    ranked.sort(key=lambda x: -x[1])
    return [sym.split("/")[0] + "USDT" for sym, _ in ranked[:top_n]]


# ---------------------------------------------------------------------------
# #2 BACKTEST ENGINE (Pine -> Python)
# ---------------------------------------------------------------------------


def sma(arr: np.ndarray, period: int) -> np.ndarray:
    """Basit hareketli ortalama (numpy convolve)."""
    if period <= 0:
        return np.full(len(arr), np.nan)
    kernel = np.ones(period) / period
    valid = np.convolve(arr, kernel, mode="valid")
    out = np.full(len(arr), np.nan)
    out[period - 1:] = valid
    return out


def atr_wilder(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
    """Wilder's RMA ile True Range ortalamasi (ewm alpha=1/period)."""
    prev_close = np.roll(close, 1)
    prev_close[0] = np.nan
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
    series = pd.Series(tr).ewm(alpha=1.0 / period, adjust=False).mean()
    return series.to_numpy()


def _cross_flags(fast: np.ndarray, slow: np.ndarray) -> tuple:
    """crossover / crossunder boolean dizileri (ilk gecerli bar oncesi False)."""
    up = (fast > slow) & ~np.isnan(fast) & ~np.isnan(slow)
    down = (fast < slow) & ~np.isnan(fast) & ~np.isnan(slow)
    cross_up = up & ~np.roll(up, 1)
    cross_dn = down & ~np.roll(down, 1)
    cross_up[0] = False
    cross_dn[0] = False
    return cross_up, cross_dn


def get_long_sl(base: float, open_atr_val: float, p: dict) -> float:
    if p["sl_method"] == "perc":
        return base * (1 - p["sl_long_perc"])
    return base - p["sl_long_atr_mul"] * open_atr_val


def get_short_sl(base: float, open_atr_val: float, p: dict) -> float:
    if p["sl_method"] == "perc":
        return base * (1 + p["sl_short_perc"])
    return base + p["sl_short_atr_mul"] * open_atr_val


def get_long_tp(close: float, sl: float, open_atr_val: float, p: dict) -> float:
    if p["tp_method"] == "perc":
        return close * (1 + p["tp_long_perc"])
    if p["tp_method"] == "atr":
        return close + p["tp_long_atr_mul"] * open_atr_val
    return close + p["tp_long_rr"] * (close - sl)


def get_short_tp(close: float, sl: float, open_atr_val: float, p: dict) -> float:
    if p["tp_method"] == "perc":
        return close * (1 - p["tp_short_perc"])
    if p["tp_method"] == "atr":
        return close - p["tp_short_atr_mul"] * open_atr_val
    return close - p["tp_short_rr"] * (sl - close)


def trail_offset_price(base_price: float, open_atr_val: float, p: dict) -> float:
    if p["dist_method"] == "perc":
        return base_price * p["dist_perc"]
    return p["dist_atr_mul"] * open_atr_val


def run_backtest(df: pd.DataFrame, p: dict) -> dict:
    """TTPTSL durum makinesi. Sonuc: trades/wins/win_rate/net_profit/profit_factor."""
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    close = df["close"].to_numpy()
    n = len(df)

    fast = sma(close, p["fast_ma_len"])
    slow = sma(close, p["slow_ma_len"])
    atr = atr_wilder(high, low, close, p["atr_len"])
    cross_up, cross_dn = _cross_flags(fast, slow)

    warmup = max(p["fast_ma_len"], p["slow_ma_len"], p["atr_len"]) + 1

    net_profit = 0.0
    gross_profit = 0.0
    gross_loss = 0.0
    trades = 0
    wins = 0

    active = False
    direction = 0          # +1 long, -1 short
    entry = 0.0
    open_atr = 0.0
    sl = 0.0
    tp = 0.0
    qty = 0.0
    trailing_sl = False
    tp_hit = False
    tp_trailing = False
    trail_exit = 0.0
    be_active = False

    def _close(exit_price: float, at: float, label: str) -> None:
        nonlocal active, qty, trades, wins, net_profit, gross_profit, gross_loss
        ret = ((exit_price - entry) / entry if direction == 1 else (entry - exit_price) / entry)
        contrib = ret * qty * 100.0
        net_profit += contrib
        if ret > 0:
            wins += 1
            gross_profit += contrib
        else:
            gross_loss += -contrib
        trades += 1
        active = False

    for i in range(warmup, n):
        if not active:
            if cross_up[i]:
                active, direction = True, 1
                entry = close[i]
                open_atr = atr[i]
                sl = get_long_sl(entry, open_atr, p)
                tp = get_long_tp(close[i], get_long_sl(close[i], open_atr, p), open_atr, p)
                qty = 1.0
                trailing_sl = p["sl_trail_mode"] == "ON"
                tp_hit = False
                tp_trailing = False
                be_active = False
            elif cross_dn[i]:
                active, direction = True, -1
                entry = close[i]
                open_atr = atr[i]
                sl = get_short_sl(entry, open_atr, p)
                tp = get_short_tp(close[i], get_short_sl(close[i], open_atr, p), open_atr, p)
                qty = 1.0
                trailing_sl = p["sl_trail_mode"] == "ON"
                tp_hit = False
                tp_trailing = False
                be_active = False
            continue

        # --- EXIT CONDITIONS (sirasiyla) ---
        if direction == 1:
            sl_hit = low[i] <= sl
            tp_hit_now = (not tp_hit) and high[i] >= tp
            trail_hit = tp_trailing and low[i] <= trail_exit
            reversal = cross_dn[i]
        else:
            sl_hit = high[i] >= sl
            tp_hit_now = (not tp_hit) and low[i] <= tp
            trail_hit = tp_trailing and high[i] >= trail_exit
            reversal = cross_up[i]

        if sl_hit:
            _close(sl, low[i] if direction == 1 else high[i], "sl")
        elif tp_hit_now:
            if p["tp_trail_enabled"]:
                tp_hit = True
                tp_trailing = True
                trail_exit = tp
            else:
                exit_qty = qty * p["tp_qty_pct"]
                ret = ((tp - entry) / entry if direction == 1 else (entry - tp) / entry)
                contrib = ret * exit_qty * 100.0
                net_profit += contrib
                if ret > 0:
                    wins += 1
                    gross_profit += contrib
                else:
                    gross_loss += -contrib
                trades += 1
                qty -= exit_qty
                tp_hit = True
                if qty < 1e-12:
                    active = False
                    continue
            if p["sl_trail_mode"] == "TP":
                trailing_sl = True
            if p["be_enabled"]:
                be_active = True
        elif trail_hit:
            _close(trail_exit, trail_exit, "trail_tp")
        elif reversal:
            _close(close[i], close[i], "reversal")

        if not active:
            continue

        # --- STOP LOSS UPDATE ---
        base = high[i] if direction == 1 else low[i]
        if not trailing_sl:
            base = entry
        new_sl = get_long_sl(base, open_atr, p) if direction == 1 else get_short_sl(base, open_atr, p)
        if direction == 1:
            sl = max(sl, new_sl)
        else:
            sl = min(sl, new_sl)
        if be_active:
            sl = max(sl, entry) if direction == 1 else min(sl, entry)

        # --- TRAILING TP UPDATE ---
        if tp_trailing:
            dist = trail_offset_price(base, open_atr, p)
            if direction == 1:
                trail_exit = max(trail_exit, high[i] - dist)
            else:
                trail_exit = min(trail_exit, low[i] + dist)

    return {
        "trades": trades,
        "wins": wins,
        "win_rate": (wins / trades) if trades else 0.0,
        "net_profit_pct": net_profit,
        "profit_factor": (gross_profit / gross_loss) if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0),
        "gross_profit_pct": gross_profit,
        "gross_loss_pct": gross_loss,
    }


# ---------------------------------------------------------------------------
# #3/#4 OPTIMIZATION + SCORING
# ---------------------------------------------------------------------------


def define_params(trial) -> dict:
    return {
        "fast_ma_len": trial.suggest_int("fast_ma_len", 5, 50),
        "slow_ma_len": trial.suggest_int("slow_ma_len", 20, 200),
        "atr_len": trial.suggest_int("atr_len", 3, 30),
        "sl_method": trial.suggest_categorical("sl_method", ["perc", "atr"]),
        "sl_long_perc": trial.suggest_float("sl_long_perc", 0.005, 0.15, step=0.0025),
        "sl_short_perc": trial.suggest_float("sl_short_perc", 0.005, 0.15, step=0.0025),
        "sl_long_atr_mul": trial.suggest_float("sl_long_atr_mul", 0.5, 8.0, step=0.25),
        "sl_short_atr_mul": trial.suggest_float("sl_short_atr_mul", 0.5, 8.0, step=0.25),
        "sl_trail_mode": trial.suggest_categorical("sl_trail_mode", ["TP", "ON", "OFF"]),
        "be_enabled": trial.suggest_categorical("be_enabled", [True, False]),
        "tp_qty_pct": trial.suggest_float("tp_qty_pct", 0.25, 1.0, step=0.05),
        "tp_method": trial.suggest_categorical("tp_method", ["perc", "atr", "rr"]),
        "tp_long_perc": trial.suggest_float("tp_long_perc", 0.01, 0.30, step=0.005),
        "tp_short_perc": trial.suggest_float("tp_short_perc", 0.01, 0.30, step=0.005),
        "tp_long_atr_mul": trial.suggest_float("tp_long_atr_mul", 1.0, 20.0, step=0.5),
        "tp_short_atr_mul": trial.suggest_float("tp_short_atr_mul", 1.0, 20.0, step=0.5),
        "tp_long_rr": trial.suggest_float("tp_long_rr", 0.5, 5.0, step=0.1),
        "tp_short_rr": trial.suggest_float("tp_short_rr", 0.5, 5.0, step=0.1),
        "tp_trail_enabled": trial.suggest_categorical("tp_trail_enabled", [True, False]),
        "dist_method": trial.suggest_categorical("dist_method", ["perc", "atr"]),
        "dist_perc": trial.suggest_float("dist_perc", 0.001, 0.05, step=0.0005),
        "dist_atr_mul": trial.suggest_float("dist_atr_mul", 0.1, 5.0, step=0.1),
    }


def score_results(res: dict) -> float:
    trades = res["trades"]
    if trades < 5:
        return -9999.0
    win_rate = res["win_rate"]
    pf = res["profit_factor"]
    if pf == float("inf"):
        pf = 100.0
    score = res["net_profit_pct"] * (win_rate ** 0.3) * (pf ** 0.4)
    if trades < 10:
        score *= 0.2
    if trades > 400:
        score *= 0.7
    if win_rate < 0.40:
        score *= 0.5
    return score


def objective(trial, df: pd.DataFrame) -> float:
    params = define_params(trial)
    res = run_backtest(df, params)
    return score_results(res)


def optimize_symbol(df: pd.DataFrame, trials: int, seed: int) -> dict:
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=seed))
    study.optimize(lambda t: objective(t, df), n_trials=trials, show_progress_bar=False)
    best = study.best_params
    res = run_backtest(df, best)
    return {"best_params": best, "best_score": study.best_value, "results": res}


def _average_params(param_sets: List[dict]) -> dict:
    keys = list(param_sets[0].keys())
    out: dict = {}
    for k in keys:
        vals = [s[k] for s in param_sets]
        if all(isinstance(v, bool) for v in vals):
            out[k] = sum(vals) / len(vals)
        elif all(isinstance(v, (int, float)) for v in vals):
            out[k] = float(np.mean(vals))
        else:
            out[k] = vals[0]
    return out


def test_params_on(df: pd.DataFrame, params: dict) -> dict:
    test_params = dict(params)
    for k in ("fast_ma_len", "slow_ma_len", "atr_len"):
        test_params[k] = int(round(test_params[k]))
    return run_backtest(df, test_params)


def _fmt_results(res: dict) -> dict:
    return {
        "trades": res["trades"],
        "wins": res["wins"],
        "win_rate_pct": round(res["win_rate"] * 100, 1),
        "net_profit_pct": round(res["net_profit_pct"], 2),
        "profit_factor": round(res["profit_factor"], 2) if res["profit_factor"] != float("inf") else None,
    }


# ---------------------------------------------------------------------------
# #5 MAIN
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="TTPTSL parametre optimizer (Optuna)")
    parser.add_argument("--top", type=int, default=5, help="Otomatik sembol secimi: ilk N USDT futures")
    parser.add_argument("--symbols", default="", help="Virgulle ayrilmis semboller (bos = --top kullanir)")
    parser.add_argument("--trials", type=int, default=500, help="Sembol basi trial sayisi")
    parser.add_argument("--days", type=int, default=60, help="Kullanilacak veri gunu")
    parser.add_argument("--bars", type=int, default=0, help="Sembol basi bar limiti (0 = hepsi)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", default=OUTPUT_FILE)
    args = parser.parse_args()

    if optuna is None:
        print("optuna kurulu degil; pip install optuna gerekli.")
        return

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        print(f"Top USDT futures (top {args.top}) taranir...")
        symbols = get_top_usdt_symbols(args.top)
    print(f"Semboller: {symbols} | Trial/sembol: {args.trials} | Gun: {args.days} | Bar limiti: {args.bars or 'hepsi'}")

    data: Dict[str, pd.DataFrame] = {}
    per_symbol: Dict[str, dict] = {}
    for sym in symbols:
        try:
            df = load_data(sym, days=args.days, bars=args.bars)
        except Exception as e:
            print(f"  {sym}: veri alinamadi ({e}), atlaniyor.")
            continue
        data[sym] = df
        print(f"  {sym}: {len(df)} bar yuklendi, optimize ediliyor...")
        best = optimize_symbol(df, args.trials, args.seed)
        per_symbol[sym] = {
            "best_params": best["best_params"],
            "best_score": round(best["best_score"], 4),
            "results": _fmt_results(best["results"]),
        }
        print(f"    score={best['best_score']:.2f} {best['results']['trades']} trade "
              f"wr=%{best['results']['win_rate'] * 100:.1f} np=%{best['results']['net_profit_pct']:.2f}")

    if not per_symbol:
        print("Hicbir sembol icin veri alinamadi.")
        return

    unified = None
    if len(per_symbol) > 1:
        avg = _average_params([s["best_params"] for s in per_symbol.values()])
        tested = {sym: _fmt_results(test_params_on(df, avg)) for sym, df in data.items()}
        unified = {
            "unified_best_params": {k: round(v, 4) if isinstance(v, float) else v for k, v in avg.items()},
            "tested_on": list(data.keys()),
            "results": tested,
        }

    report = {
        "strategy": "TTPTSL",
        "optimized_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "config": {"symbols": list(data.keys()), "trials_per_symbol": args.trials, "data_days": args.days},
        "per_symbol": per_symbol,
        "unified": unified,
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\nSonuc yazildi: {args.output}")

    if unified:
        print("\n=== UNIFIED (semboller arasi ortak) ===")
        print(json.dumps(unified["results"], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
