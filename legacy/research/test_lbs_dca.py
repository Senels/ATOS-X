"""
Legend BUY SELL DCA — Python Backtest (dinamik TP)
MACD + ADX sinyali + ATR bazli DCA averaging + weighted-average TP
"""

import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime
import warnings
warnings.filterwarnings("ignore")

def load_data():
    print("Downloading XAUUSD 1m data...")
    today = datetime.utcnow().strftime("%Y-%m-%d")
    weeks = [
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
        raise RuntimeError("Could not download data.")
    df = pd.concat(frames)
    df = df[~df.index.duplicated(keep="first")]
    df.sort_index(inplace=True)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0].lower() for c in df.columns]
    else:
        df.columns = [str(c).lower() for c in df.columns]
    print(f"   Total: {len(df)} bars  {df.index[0]} -> {df.index[-1]}")
    return df

def rma(arr, period):
    n = len(arr)
    result = np.full(n, np.nan)
    alpha = 1.0 / period
    for i in range(n):
        if i == 0:
            result[i] = arr[i] if not np.isnan(arr[i]) else 0
        elif np.isnan(result[i-1]):
            result[i] = arr[i] if not np.isnan(arr[i]) else 0
        else:
            result[i] = arr[i] * alpha + result[i-1] * (1 - alpha)
    return result

def ema(arr, period):
    result = np.full_like(arr, np.nan, dtype=np.float64)
    result[0] = arr[0]
    alpha = 2.0 / (period + 1)
    for i in range(1, len(arr)):
        result[i] = arr[i] * alpha + result[i-1] * (1 - alpha)
    return result

def calc_pnl(active_dir, avg_entry, exit_price, total_qty, equity):
    if active_dir == 1:
        dollar_pnl = (exit_price - avg_entry) * total_qty
    else:
        dollar_pnl = (avg_entry - exit_price) * total_qty
    pnl_pct = dollar_pnl / equity * 100 if equity > 0 else 0
    return dollar_pnl, pnl_pct

def entry_qty(equity, price, risk_pct, tp_dist):
    rc = equity * risk_pct / 100.0
    rpc = max(tp_dist, 0.1)
    rpc = 0.1 if (np.isnan(rpc) or rpc <= 0) else rpc
    rq = int(rc / rpc)
    mq = int(equity / max(price, 0.1))
    return float(max(min(rq, max(mq, 1)), 1))

def backtest(df, p):
    o = df["open"].values
    h = df["high"].values
    l = df["low"].values
    c = df["close"].values
    n = len(df)

    # MACD
    fast_ma = ema(c, p["fast_len"])
    slow_ma = ema(c, p["slow_len"])
    macd = fast_ma - slow_ma
    signal = ema(macd, p["signal_len"])

    # True Range
    tr = np.full(n, np.nan)
    tr[0] = h[0] - l[0]
    for i in range(1, n):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i-1]), abs(l[i] - c[i-1]))

    # ADX
    tr_rma = rma(tr, p["di_len"])
    up_chg = np.zeros(n)
    down_chg = np.zeros(n)
    for i in range(1, n):
        up_chg[i] = max(h[i] - h[i-1], 0)
        down_chg[i] = max(l[i-1] - l[i], 0)
    plus_dm = np.where((up_chg > down_chg) & (up_chg > 0), up_chg, 0)
    minus_dm = np.where((down_chg > up_chg) & (down_chg > 0), down_chg, 0)
    plus_dm_rma = rma(plus_dm, p["di_len"])
    minus_dm_rma = rma(minus_dm, p["di_len"])
    plus = np.full(n, np.nan)
    minus = np.full(n, np.nan)
    for i in range(n):
        if tr_rma[i] > 0 and not np.isnan(tr_rma[i]):
            plus[i] = 100 * plus_dm_rma[i] / tr_rma[i]
            minus[i] = 100 * minus_dm_rma[i] / tr_rma[i]
    sum_di = plus + minus
    sum_di = np.where(sum_di == 0, 1, sum_di)
    dx = 100 * np.abs(plus - minus) / sum_di
    adx_val = rma(np.nan_to_num(dx, nan=0), p["adx_len"])

    # ATR
    atr = rma(tr, p["atr_len"])

    # MACD cross signals
    macd_cross_up = np.full(n, False)
    macd_cross_down = np.full(n, False)
    for i in range(1, n):
        if not np.isnan(macd[i]) and not np.isnan(signal[i]) and not np.isnan(macd[i-1]) and not np.isnan(signal[i-1]):
            if macd[i] < 0 and macd[i] > signal[i] and macd[i-1] <= signal[i-1]:
                macd_cross_up[i] = True
            if macd[i] > 0 and macd[i] < signal[i] and macd[i-1] >= signal[i-1]:
                macd_cross_down[i] = True

    bullish = (adx_val > p["adx_threshold"]) & macd_cross_up
    bearish = (adx_val > p["adx_threshold"]) & macd_cross_down

    # STATE
    active_dir = 0
    active_entry = 0.0
    active_tp = 0.0
    dca_level = 1
    dca_base_entry = 0.0
    entry_base_qty = 0.0
    entry_bar = -999
    tp_reached = False
    cum_qty = 0.0
    cum_cost = 0.0
    equity = 10000.0
    trades = []
    warmup = max(p["atr_len"], p["adx_len"], p["fast_len"], p["slow_len"], p["signal_len"]) * 3

    for i in range(warmup, n):
        risk_atr = atr[i] if not np.isnan(atr[i]) else 0
        tp_dist = risk_atr * p["tp_base_mult"]
        tp_target = tp_dist * p["tp_mult"]

        # TP check
        if active_dir != 0 and not tp_reached and i > entry_bar:
            if active_dir == 1 and h[i] >= active_tp:
                tp_reached = True
            elif active_dir == -1 and l[i] <= active_tp:
                tp_reached = True

        # Close on TP
        if tp_reached and active_dir != 0:
            avg_entry = cum_cost / cum_qty if cum_qty > 0 else active_entry
            dollar_pnl, pnl_pct = calc_pnl(active_dir, avg_entry, active_tp, cum_qty, equity)
            equity += dollar_pnl
            trades.append({
                "entry": active_entry, "exit": active_tp, "pnl_pct": pnl_pct,
                "win": True, "bars": i - entry_bar, "dir": "L" if active_dir == 1 else "S",
                "reason": "tp", "dca_level": dca_level, "avg_entry": avg_entry, "total_qty": cum_qty,
            })
            active_dir = 0; tp_reached = False; dca_level = 1
            cum_qty = 0; cum_cost = 0

        # DCA triggers
        if active_dir != 0 and dca_base_entry != 0 and not tp_reached and i > entry_bar and risk_atr > 0:
            spacing = risk_atr * p["dca_spacing"]
            for lvl in range(2, p.get("dca_max_level", 5) + 1):
                if lvl > dca_level:
                    if active_dir == 1:
                        if l[i] <= dca_base_entry - (lvl - 1) * spacing:
                            dca_level = lvl
                            new_qty = entry_base_qty * (p.get("dca_multiplier", 2.0) ** (lvl - 1))
                            cum_qty += new_qty
                            cum_cost += new_qty * c[i]
                            avg_entry = cum_cost / cum_qty
                            active_tp = avg_entry + tp_target
                    else:
                        if h[i] >= dca_base_entry + (lvl - 1) * spacing:
                            dca_level = lvl
                            new_qty = entry_base_qty * (p.get("dca_multiplier", 2.0) ** (lvl - 1))
                            cum_qty += new_qty
                            cum_cost += new_qty * c[i]
                            avg_entry = cum_cost / cum_qty
                            active_tp = avg_entry - tp_target

        # Reversal
        rev = (active_dir == 1 and bearish[i]) or (active_dir == -1 and bullish[i])
        if rev and active_dir != 0:
            avg_entry = cum_cost / cum_qty if cum_qty > 0 else active_entry
            dollar_pnl, pnl_pct = calc_pnl(active_dir, avg_entry, c[i], cum_qty, equity)
            equity += dollar_pnl
            won = (active_dir == 1 and c[i] >= avg_entry) or (active_dir == -1 and c[i] <= avg_entry)
            trades.append({
                "entry": active_entry, "exit": c[i], "pnl_pct": pnl_pct,
                "win": won, "bars": i - entry_bar, "dir": "L" if active_dir == 1 else "S",
                "reason": "rev", "dca_level": dca_level, "avg_entry": avg_entry, "total_qty": cum_qty,
            })
            active_dir = 0; tp_reached = False; dca_level = 1
            cum_qty = 0; cum_cost = 0

            if bearish[i]:
                active_dir = -1; active_entry = c[i]; active_tp = c[i] - tp_target
                dca_base_entry = c[i]; dca_level = 1
                entry_base_qty = entry_qty(equity, c[i], p["risk_pct"], tp_dist)
                cum_qty = entry_base_qty; cum_cost = entry_base_qty * c[i]
                entry_bar = i
            elif bullish[i]:
                active_dir = 1; active_entry = c[i]; active_tp = c[i] + tp_target
                dca_base_entry = c[i]; dca_level = 1
                entry_base_qty = entry_qty(equity, c[i], p["risk_pct"], tp_dist)
                cum_qty = entry_base_qty; cum_cost = entry_base_qty * c[i]
                entry_bar = i

        # Fresh entry
        if active_dir == 0:
            if bullish[i]:
                active_dir = 1; active_entry = c[i]; active_tp = c[i] + tp_target
                dca_base_entry = c[i]; dca_level = 1
                entry_base_qty = entry_qty(equity, c[i], p["risk_pct"], tp_dist)
                cum_qty = entry_base_qty; cum_cost = entry_base_qty * c[i]
                entry_bar = i
            elif bearish[i]:
                active_dir = -1; active_entry = c[i]; active_tp = c[i] - tp_target
                dca_base_entry = c[i]; dca_level = 1
                entry_base_qty = entry_qty(equity, c[i], p["risk_pct"], tp_dist)
                cum_qty = entry_base_qty; cum_cost = entry_base_qty * c[i]
                entry_bar = i

    return trades

# ═══════════════ RUN ═══════════════

df = load_data()

p = {
    "fast_len": 5,
    "slow_len": 13,
    "signal_len": 9,
    "adx_len": 14,
    "di_len": 10,
    "adx_threshold": 35.0,
    "risk_pct": 1.0,
    "atr_len": 10,
    "tp_base_mult": 1.5,
    "tp_mult": 1.5,
    "dca_spacing": 3.0,
    "dca_max_level": 3,
    "dca_multiplier": 2.0,
    "leverage": 18.0,
}

trades = backtest(df, p)

if not trades:
    print("\nHic trade sinyali olusmadi!")
    exit()

wins = sum(1 for t in trades if t.get("win", False))
losses = len(trades) - wins
total_pnl = sum(t["pnl_pct"] for t in trades)

gross_profit = sum(t["pnl_pct"] for t in trades if t["pnl_pct"] > 0)
gross_loss = abs(sum(t["pnl_pct"] for t in trades if t["pnl_pct"] < 0))
pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")

avg_pnl = total_pnl / len(trades) if trades else 0

# Consecutive losses
consec = 0
max_consec = 0
for t in trades:
    if not t.get("win", False):
        consec += 1
        max_consec = max(max_consec, consec)
    else:
        consec = 0

print(f"\n{'='*60}")
print(f"LEGEND BUY SELL DCA — Dinamik TP Backtest")
print(f"{'='*60}")
print(f"Konfig: ADX>{p['adx_threshold']} DCA={p['dca_max_level']} spacing={p['dca_spacing']}xATR")
print(f"        TP={p['tp_base_mult']}x{p['tp_mult']}={p['tp_base_mult']*p['tp_mult']}xATR (weighted-avg)")
print(f"        Multiplier={p['dca_multiplier']}x  Risk={p['risk_pct']}%")
print(f"{'='*60}")
print(f"Toplam Trade: {len(trades)}")
print(f"Kazanan: {wins}  Kaybeden: {losses}")
print(f"Win Rate: {wins/len(trades)*100:.1f}%")
print(f"Net PnL: {total_pnl:+.2f}%")
print(f"Ortalama PnL/Trade: {avg_pnl:+.2f}%")
print(f"Profit Factor: {pf:.2f}")
print(f"Gross Profit: {gross_profit:+.2f}%")
print(f"Gross Loss: {gross_loss:.2f}%")
print(f"Max Ardil Kayip: {max_consec}")
print(f"{'='*60}")

dca_counts = {}
for t in trades:
    lvl = t.get("dca_level", 1)
    dca_counts[lvl] = dca_counts.get(lvl, 0) + 1
print(f"\nDCA Level Dagilimi:")
for lvl in sorted(dca_counts.keys()):
    print(f"  Level {lvl}: {dca_counts[lvl]} trade ({dca_counts[lvl]/len(trades)*100:.1f}%)")

reasons = {}
for t in trades:
    r = t.get("reason", "tp")
    reasons[r] = reasons.get(r, 0) + 1
print(f"\nKapanis Nedeni:")
for r, cnt in sorted(reasons.items()):
    print(f"  {r}: {cnt} trade ({cnt/len(trades)*100:.1f}%)")

print(f"\nWorst 5 trades:")
for t in sorted(trades, key=lambda x: x["pnl_pct"])[:5]:
    print(f"  {t['dir']} PnL={t['pnl_pct']:+7.2f}% DCA={t['dca_level']} {t['reason']} avg={t.get('avg_entry',0):.2f}")

print(f"\nBest 5 trades:")
for t in sorted(trades, key=lambda x: x["pnl_pct"], reverse=True)[:5]:
    print(f"  {t['dir']} PnL={t['pnl_pct']:+7.2f}% DCA={t['dca_level']} {t['reason']} avg={t.get('avg_entry',0):.2f}")

print(f"\nSon 10 Trade:")
for t in trades[-10:]:
    print(f"  {t['dir']} {'W' if t['win'] else 'L'} PnL={t['pnl_pct']:+7.2f}% bars={t['bars']} DCA={t['dca_level']} {t['reason']}")
