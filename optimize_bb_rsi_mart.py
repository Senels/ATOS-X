"""
BB + RSI Scalp Martingale Optimizer — XAUUSD 1m
Re-implements the Pine Script strategy in numpy,
then uses Optuna to find max-profit parameters.
"""

import optuna
import pandas as pd
import numpy as np
import yfinance as yf
import json
from functools import partial
from datetime import datetime, timedelta
import warnings

warnings.filterwarnings("ignore")

# ══════════════════════════════════════════════════════════
# 1. DATA
# ══════════════════════════════════════════════════════════

def load_data():
    print("Downloading XAUUSD 1m data (multi-week)...")
    today = datetime.utcnow().strftime("%Y-%m-%d")
    weeks = [
        ("2026-06-18", "2026-06-25"),
        ("2026-06-25", "2026-07-02"),
        ("2026-07-02", "2026-07-09"),
        ("2026-07-09", "2026-07-16"),
        ("2026-07-16", today),
    ]
    frames = []
    for s, e in weeks:
        try:
            df = yf.download("GC=F", interval="1m", start=s, end=e, progress=False)
            if df is not None and not df.empty:
                frames.append(df)
                print(f"   {s} -> {e}: {len(df)} bars")
        except Exception:
            pass
    if not frames:
        raise RuntimeError("Could not download XAUUSD data.")
    df = pd.concat(frames)
    df = df[~df.index.duplicated(keep="first")]
    df.sort_index(inplace=True)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0].lower() for c in df.columns]
    else:
        df.columns = [str(c).lower() for c in df.columns]
    print(f"   Total: {len(df)} bars  {df.index[0]} -> {df.index[-1]}")
    return df


# ══════════════════════════════════════════════════════════
# 2. BACKTEST ENGINE
# ══════════════════════════════════════════════════════════

def backtest(df, p):
    """
    p = parameter dict
    Returns (trades list, equity_curve list)
    """
    o = df["open"].values
    h = df["high"].values
    l = df["low"].values
    c = df["close"].values
    n = len(c)

    rsi_len   = p["rsi_len"]
    rsi_os    = p["rsi_os"]
    rsi_ob    = p["rsi_ob"]
    bb_len    = p["bb_len"]
    bb_mult   = p["bb_mult"]
    atr_len   = p["atr_len"]
    tp_mult   = p["tp_mult"]
    sl_mult   = p["sl_mult"]
    base_qty  = p["base_qty"]
    qty_mult  = p["qty_mult"]
    max_levels = int(p.get("max_levels", 5))

    # Position sizes per level (L1..L5)
    qty_L = [base_qty * (qty_mult ** i) for i in range(max_levels)]
    qty_S = qty_L  # same for short

    # ── RMA (Wilder's smoothing, matches Pine ta.rma) ──
    def rma_series(arr, period):
        alpha = 1.0 / period
        res = np.full(n, np.nan)
        for i in range(n):
            if i == 0:
                res[i] = arr[i] if not np.isnan(arr[i]) else 0.0
            elif np.isnan(res[i-1]):
                res[i] = arr[i] if not np.isnan(arr[i]) else 0.0
            else:
                res[i] = arr[i] * alpha + res[i-1] * (1.0 - alpha)
        return res

    # ── RSI ──
    diff = np.zeros(n)
    diff[0] = 0.0
    for i in range(1, n):
        diff[i] = c[i] - c[i-1]
    gain = np.where(diff > 0, diff, 0.0)
    loss = np.where(diff < 0, -diff, 0.0)
    avg_gain = rma_series(gain, rsi_len)
    avg_loss = rma_series(loss, rsi_len)
    vrsi = np.full(n, np.nan)
    for i in range(n):
        if avg_loss[i] == 0 or np.isnan(avg_loss[i]):
            rs = 100.0 if avg_gain[i] > 0 else 50.0
        else:
            rs = avg_gain[i] / avg_loss[i]
        vrsi[i] = 100.0 - (100.0 / (1.0 + rs))

    # ── Bollinger Bands (SMA + Std) ──
    bb_mid = np.full(n, np.nan)
    bb_upper = np.full(n, np.nan)
    bb_lower = np.full(n, np.nan)
    for i in range(n):
        if i < bb_len - 1:
            window = c[max(0, i - bb_len + 1):i + 1]
            m = np.nanmean(window)
            s = np.nanstd(window, ddof=0)
        else:
            window = c[i - bb_len + 1:i + 1]
            m = np.nanmean(window)
            s = np.nanstd(window, ddof=0)
        bb_mid[i] = m
        bb_upper[i] = m + bb_mult * s
        bb_lower[i] = m - bb_mult * s

    # ── ATR ──
    tr = np.zeros(n)
    tr[0] = h[0] - l[0] if not np.isnan(h[0]) and not np.isnan(l[0]) else 0.0
    for i in range(1, n):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i-1]), abs(l[i] - c[i-1]))
    atr = rma_series(tr, atr_len)

    warmup = max(rsi_len * 3, bb_len * 2, atr_len * 2, 50)

    # ── State ──
    long_level = -1   # -1 = no active long, 0..4 = level index
    long_entry = 0.0
    long_tp = 0.0
    long_sl = 0.0
    long_losses = 0

    short_level = -1
    short_entry = 0.0
    short_tp = 0.0
    short_sl = 0.0
    short_losses = 0

    capital = 10000.0
    trades = []
    equity_curve = []

    for i in range(1, n):
        if i < warmup:
            continue
        if np.isnan(vrsi[i]) or np.isnan(vrsi[i-1]) or np.isnan(bb_lower[i]) or np.isnan(bb_lower[i-1]):
            continue
        if atr[i] == 0 or np.isnan(atr[i]):
            continue

        # ── Signals ──
        long_signal = (
            vrsi[i] > rsi_os and vrsi[i-1] <= rsi_os and
            c[i] > bb_lower[i] and c[i-1] <= bb_lower[i]
        )
        short_signal = (
            vrsi[i] < rsi_ob and vrsi[i-1] >= rsi_ob and
            c[i] < bb_upper[i] and c[i-1] >= bb_upper[i]
        )

        # ── LONG state machine ──
        if long_level >= 0:
            if h[i] >= long_tp:
                qty = qty_L[long_level]
                pnl = qty * (long_tp - long_entry)
                capital += pnl
                trades.append({
                    "dir": "L", "level": long_level + 1, "qty": qty,
                    "entry": long_entry, "exit": long_tp,
                    "pnl": pnl, "win": True, "bar": i
                })
                long_level = -1
                long_losses = 0
            elif l[i] <= long_sl:
                qty = qty_L[long_level]
                pnl = qty * (long_sl - long_entry)
                capital += pnl
                trades.append({
                    "dir": "L", "level": long_level + 1, "qty": qty,
                    "entry": long_entry, "exit": long_sl,
                    "pnl": pnl, "win": False, "bar": i
                })
                long_level = -1
                long_losses += 1
                if long_losses >= max_levels:
                    long_losses = 0

        if long_level == -1 and long_signal and short_level == -1:
            lvl = min(long_losses, max_levels - 1)
            long_level = lvl
            long_entry = c[i]
            long_tp = c[i] + atr[i] * tp_mult
            long_sl = c[i] - atr[i] * sl_mult

        # ── SHORT state machine ──
        if short_level >= 0:
            if l[i] <= short_tp:
                qty = qty_S[short_level]
                pnl = qty * (short_entry - short_tp)
                capital += pnl
                trades.append({
                    "dir": "S", "level": short_level + 1, "qty": qty,
                    "entry": short_entry, "exit": short_tp,
                    "pnl": pnl, "win": True, "bar": i
                })
                short_level = -1
                short_losses = 0
            elif h[i] >= short_sl:
                qty = qty_S[short_level]
                pnl = qty * (short_entry - short_sl)
                capital += pnl
                trades.append({
                    "dir": "S", "level": short_level + 1, "qty": qty,
                    "entry": short_entry, "exit": short_sl,
                    "pnl": pnl, "win": False, "bar": i
                })
                short_level = -1
                short_losses += 1
                if short_losses >= max_levels:
                    short_losses = 0

        if short_level == -1 and short_signal and long_level == -1:
            lvl = min(short_losses, max_levels - 1)
            short_level = lvl
            short_entry = c[i]
            short_tp = c[i] - atr[i] * tp_mult
            short_sl = c[i] + atr[i] * sl_mult

        if i % 100 == 0:
            equity_curve.append({"bar": i, "equity": capital})

    return trades, equity_curve


# ══════════════════════════════════════════════════════════
# 3. OPTIMIZATION
# ══════════════════════════════════════════════════════════

def objective(trial, df):
    p = {}

    p["rsi_len"]    = trial.suggest_int("rsi_len", 3, 20)
    p["rsi_os"]     = trial.suggest_int("rsi_os", 20, 40)
    p["rsi_ob"]     = 100 - p["rsi_os"]  # symmetrical around 50
    p["bb_len"]     = trial.suggest_int("bb_len", 10, 50)
    p["bb_mult"]    = trial.suggest_float("bb_mult", 1.5, 3.5, step=0.1)
    p["atr_len"]    = trial.suggest_int("atr_len", 5, 30)
    p["tp_mult"]    = trial.suggest_float("tp_mult", 0.3, 3.0, step=0.1)
    p["sl_mult"]    = trial.suggest_float("sl_mult", 0.3, 2.5, step=0.1)
    p["base_qty"]   = trial.suggest_float("base_qty", 0.005, 0.05, step=0.005)
    p["qty_mult"]   = trial.suggest_float("qty_mult", 1.2, 3.0, step=0.1)
    p["max_levels"] = 5

    trades, _ = backtest(df, p)

    if len(trades) == 0:
        return -9999.0

    wins = sum(1 for t in trades if t["win"])
    total = len(trades)
    wr = wins / total
    gross_profit = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gross_loss   = abs(sum(t["pnl"] for t in trades if t["pnl"] < 0)) or 0.001
    net_pnl      = sum(t["pnl"] for t in trades)
    pf           = gross_profit / gross_loss

    # Score: maximize net PnL with quality bonuses
    score = net_pnl * (wr ** 0.3) * (pf ** 0.4)

    if total < 15:
        score *= 0.2
    if total > 600:
        score *= 0.7
    if wr < 0.35:
        score *= 0.4
    if pf < 1.05:
        score *= 0.3

    # Balance penalty: penalize if long/short ratio > 3:1
    longs = sum(1 for t in trades if t["dir"] == "L")
    shorts = sum(1 for t in trades if t["dir"] == "S")
    if longs > 0 and shorts > 0:
        ratio = max(longs, shorts) / min(longs, shorts)
        if ratio > 3.0:
            score *= max(0.1, 1.0 - (ratio - 3.0) * 0.05)
    elif longs == 0 or shorts == 0:
        score *= 0.01

    return score


# ══════════════════════════════════════════════════════════
# 4. RUN
# ══════════════════════════════════════════════════════════

def run():
    df = load_data()
    print("Optimizing... (1000 trials)\n")

    study = optuna.create_study(
        direction="maximize",
        sampler=optuna.samplers.TPESampler(seed=42)
    )
    study.optimize(partial(objective, df=df), n_trials=1000, show_progress_bar=True)

    best = study.best_params
    best["max_levels"] = 5

    # Map optimized base_qty/qty_mult to individual level sizes
    base = best["base_qty"]
    mult = best["qty_mult"]
    qty_levels = [round(base * (mult ** i), 4) for i in range(5)]
    qty_levels_str = ", ".join([f"L{i+1}={q}" for i, q in enumerate(qty_levels)])

    # Backtest with best params for detailed results
    trades, eq = backtest(df, best)

    wins    = sum(1 for t in trades if t["win"])
    total   = len(trades)
    wr      = wins / total * 100 if total > 0 else 0
    net     = sum(t["pnl"] for t in trades)
    gross_p = sum(t["pnl"] for t in trades if t["pnl"] > 0) or 0.001
    gross_l = abs(sum(t["pnl"] for t in trades if t["pnl"] < 0)) or 0.001
    pf      = gross_p / gross_l

    # Level breakdown
    by_level = {}
    for t in trades:
        key = t["level"]
        by_level.setdefault(key, {"total": 0, "wins": 0, "pnl": 0.0})
        by_level[key]["total"] += 1
        by_level[key]["wins"]  += 1 if t["win"] else 0
        by_level[key]["pnl"]   += t["pnl"]

    print()
    print("=" * 66)
    print("  BEST PARAMETERS — BB + RSI Scalp Martingale")
    print("=" * 66)
    for k, v in study.best_params.items():
        print(f"     {k:15s} = {v}")
    print()
    print(f"     Level Quantities: {qty_levels_str}")

    print()
    print("  " + "-" * 62)
    print("  BACKTEST RESULTS")
    print("  " + "-" * 62)
    print(f"     Total Trades  : {total}")
    print(f"     Wins / Losses : {wins} / {total - wins}")
    print(f"     Win Rate      : {wr:.1f}%")
    print(f"     Net PnL       : ${net:+.2f}")
    print(f"     Profit Factor : {pf:.2f}")
    print()
    print("  " + "-" * 62)
    print("  PERFORMANCE BY MARTINGALE LEVEL")
    print("  " + "-" * 62)
    for lvl in sorted(by_level.keys()):
        d = by_level[lvl]
        lwr = d["wins"] / d["total"] * 100 if d["total"] > 0 else 0
        print(f"     L{lvl}  |  {d['total']:3d} trades  |  WR={lwr:5.1f}%  |  PnL=${d['pnl']:+8.2f}")
    print("=" * 66)

    # Save
    out = {
        "best_params": study.best_params,
        "level_quantities": {f"L{i+1}": q for i, q in enumerate(qty_levels)},
        "results": {
            "trades": total,
            "wins": wins,
            "losses": total - wins,
            "win_rate_pct": round(wr, 1),
            "net_pnl_usd": round(net, 2),
            "profit_factor": round(pf, 2),
        },
        "level_breakdown": {
            str(lvl): d for lvl, d in by_level.items()
        },
    }
    path = r"C:\Users\svkts\OneDrive\Belgeler\Default Project\best_params_bb_rsi.json"
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nSaved to {path}")


if __name__ == "__main__":
    run()
