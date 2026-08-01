"""
Out-of-Sample Test — LTM Strategy
Tests best params on multiple 7d windows.
"""

import json
import numpy as np
import pandas as pd
import yfinance as yf
import warnings
warnings.filterwarnings("ignore")

# Reuse the optimizer's multi-chunk loader
from optimize_ltm import load_data

# ── Backtest engine (same as optimize_ltm.py) ──

def backtest_v2(df, p):
    o = df["open"].values
    h = df["high"].values
    l = df["low"].values
    c = df["close"].values
    v = df["volume"].values
    n = len(c)

    GEO = {"Scalping": (2.5, 0.2), "Balanced": (4.0, 0.25), "Deep Trend": (6.0, 0.3)}
    if p["band_preset"] == "Custom":
        eb, es = p["base_mult"], p["band_step"]
    else:
        eb, es = GEO[p["band_preset"]]
    m = [eb, eb * (1 + es), eb * (1 + 2 * es), eb * (1 + 3 * es)]

    RSK = {"Conservative": (2.5, 1.0, 2.0, 4.0), "Aggressive": (1.0, 1.5, 2.5, 4.0),
           "Scalping": (0.8, 0.8, 1.5, 2.0), "Balanced": (1.5, 1.0, 2.0, 3.0)}
    if p["risk_preset"] == "Custom":
        slm, tp1m, tp2m, tp3m = p["sl_mult"], p["tp1_mult"], p["tp2_mult"], p["tp3_mult"]
    else:
        slm, tp1m, tp2m, tp3m = RSK[p["risk_preset"]]
    lev_long = p.get("long_leverage", 1.0)
    lev_short = p.get("short_leverage", 1.0)

    def atr_series(period):
        tr = np.zeros(n)
        for i in range(1, n):
            tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
        atr = np.zeros(n)
        atr[0] = tr[0]
        for i in range(1, n):
            atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period if i >= period else np.mean(tr[1:i + 1])
        return atr

    trad = atr_series(p["atr_len"])
    rskd = atr_series(p["atr_len_risk"])
    warmup = max(p["atr_len"] * 3, 60)

    ema50 = np.zeros(n)
    ema50[0] = c[0]
    alpha = 2 / (50 + 1)
    for i in range(1, n):
        ema50[i] = (c[i] - ema50[i - 1]) * alpha + ema50[i - 1]

    volSma = np.zeros(n)
    cumVol = 0.0
    for i in range(n):
        cumVol += v[i] if not np.isnan(v[i]) else 0.0
        if i == 0:
            volSma[i] = v[0] if not np.isnan(v[0]) else 0.0
        elif i < 19:
            volSma[i] = np.nanmean(v[max(0, i - 19):i + 1])
        else:
            volSma[i] = (volSma[i - 1] * 19 + (v[i] if not np.isnan(v[i]) else 0.0)) / 20
    symbolHasVol = cumVol > 0

    trend = np.ones(n, dtype=int)
    ts = np.full((n, 4), np.nan)
    trend_start = np.zeros(n, dtype=int)
    bars_in_trend = np.zeros(n, dtype=int)

    cur_trend = 1
    cur_ts = np.array([np.nan, np.nan, np.nan, np.nan])
    cur_start = 0

    pend = {"long": 0, "short": 0, "ld": 0, "sd": 0}
    last_sig = -10000

    act = {"dir": 0, "entry": 0.0, "sl": 0.0, "tp1": 0.0, "tp2": 0.0, "tp3": 0.0,
           "bar": -100, "tp1r": False, "tp2r": False, "tp3r": False, "be": False}

    trades = []

    for i in range(n):
        if i == 0 or trad[i] == 0 or np.isnan(trad[i]):
            continue

        src, hi, lo, op = c[i], h[i], l[i], o[i]

        raw_u = [src - trad[i] * mk for mk in m]
        raw_l = [src + trad[i] * mk for mk in m]

        if np.isnan(cur_ts[0]):
            cur_ts = np.array([raw_u[j] if cur_trend == 1 else raw_l[j] for j in range(4)])
            continue

        fb = p["flip_band"] - 1
        fp = cur_ts[fb]
        do_flip_dn = cur_trend == 1 and not np.isnan(fp) and src < fp
        do_flip_up = cur_trend == -1 and not np.isnan(fp) and src > fp

        if cur_trend == 1:
            if do_flip_dn:
                cur_trend = -1
                cur_ts = np.array(raw_l)
                cur_start = i
            else:
                cur_ts = np.maximum(raw_u, cur_ts)
        else:
            if do_flip_up:
                cur_trend = 1
                cur_ts = np.array(raw_u)
                cur_start = i
            else:
                cur_ts = np.minimum(raw_l, cur_ts)

        trend[i] = cur_trend
        ts[i] = cur_ts
        trend_start[i] = cur_start
        bars_in_trend[i] = i - cur_start

        if i < warmup:
            continue

        prev_trend = trend[i - 1]
        flip_bar = trend[i] != prev_trend

        if flip_bar:
            for k in ["long", "short"]:
                pend[k] = 0
                pend[k + "d"] = 0

        conf_bull_flip = flip_bar and cur_trend == 1
        conf_bear_flip = flip_bar and cur_trend == -1

        for k in ["long", "short"]:
            pend[k] = max(pend[k] - 1, 0)
            if pend[k] == 0:
                pend[k + "d"] = 0

        if cur_trend == 1 and not np.isnan(cur_ts[0]):
            td = 4 if lo <= cur_ts[3] else 3 if lo <= cur_ts[2] else 2 if lo <= cur_ts[1] else 1 if lo <= cur_ts[0] else 0
            if td:
                pend["long"] = p["retest_window"]
                pend["ld"] = max(pend["ld"], td)
        if cur_trend == -1 and not np.isnan(cur_ts[0]):
            td = 4 if hi >= cur_ts[3] else 3 if hi >= cur_ts[2] else 2 if hi >= cur_ts[1] else 1 if hi >= cur_ts[0] else 0
            if td:
                pend["short"] = p["retest_window"]
                pend["sd"] = max(pend["sd"], td)

        long_rc = pend["long"] > 0 and cur_trend == 1 and not np.isnan(cur_ts[0]) and src > cur_ts[0] and src > op
        short_rc = pend["short"] > 0 and cur_trend == -1 and not np.isnan(cur_ts[0]) and src < cur_ts[0] and src < op

        rng = max(hi - lo, 1e-10)
        cl = (src - lo) / rng
        cs = (hi - src) / rng
        dp = {2: 25, 3: 18, 1: 15, 4: 10}
        de_l = dp.get(pend["ld"], 0)
        de_s = dp.get(pend["sd"], 0)
        ca_l = 20 if cl > 0.7 else (12 if cl > 0.5 else 5)
        ca_s = 20 if cs > 0.7 else (12 if cs > 0.5 else 5)
        ag = 15 if 10 <= bars_in_trend[i] <= 150 else (8 if bars_in_trend[i] < 10 else 5)

        if i > 0:
            biasDir = 0
            if not np.isnan(c[i - 1]) and not np.isnan(ema50[i - 1]):
                if c[i - 1] > ema50[i - 1]:
                    biasDir = 1
                elif c[i - 1] < ema50[i - 1]:
                    biasDir = -1
        else:
            biasDir = 0
        biasPtsL = 20 if biasDir == 1 else (10 if biasDir == 0 else 0)
        biasPtsS = 20 if biasDir == -1 else (10 if biasDir == 0 else 0)

        volBase = volSma[i - 1] if i > 0 else volSma[i]
        if volBase <= 0:
            volBase = volSma[i]
        rv = v[i] if not np.isnan(v[i]) else 0.0
        if symbolHasVol:
            vp = 20 if rv > volBase * 1.2 else (12 if rv > volBase else 5)
        else:
            vp = 12

        lsc = de_l + ca_l + ag + vp + biasPtsL
        ssc = de_s + ca_s + ag + vp + biasPtsS
        cdok = i - last_sig >= p["cooldown"]

        conf_l = long_rc and cdok and lsc >= p["min_score"]
        conf_s = short_rc and cdok and ssc >= p["min_score"]

        long_sig = conf_l or conf_bull_flip
        short_sig = conf_s or conf_bear_flip

        if conf_l or conf_s or conf_bull_flip or conf_bear_flip:
            last_sig = i

        sl_hit = tp1_hit = tp2_hit = tp3_hit = False
        if act["dir"] != 0 and i > act["bar"]:
            sl_hit = (act["dir"] == 1 and lo <= act["sl"]) or (act["dir"] == -1 and hi >= act["sl"])
            tp1_hit = (act["dir"] == 1 and hi >= act["tp1"]) or (act["dir"] == -1 and lo <= act["tp1"])
            tp2_hit = (act["dir"] == 1 and hi >= act["tp2"]) or (act["dir"] == -1 and lo <= act["tp2"])
            tp3_hit = (act["dir"] == 1 and hi >= act["tp3"]) or (act["dir"] == -1 and lo <= act["tp3"])

            if tp1_hit and not act["tp1r"] and not sl_hit:
                act["tp1r"] = True
                if p["be"] and not act["be"]:
                    act["sl"] = act["entry"]
                    act["be"] = True
            if tp2_hit and not act["tp2r"] and not sl_hit:
                act["tp2r"] = True
            if tp3_hit and not act["tp3r"] and not sl_hit:
                act["tp3r"] = True

            if sl_hit or tp3_hit:
                exit_p = act["sl"] if sl_hit else act["tp3"]
                lev = lev_long if act["dir"] == 1 else lev_short
                pnl_pct = (exit_p - act["entry"]) / act["entry"] * 100 * act["dir"] * lev
                trades.append({
                    "entry": act["entry"], "exit": exit_p, "pnl_pct": pnl_pct,
                    "win": act["tp1r"], "bars": i - act["bar"],
                    "dir": "L" if act["dir"] == 1 else "S",
                })
                act = {"dir": 0, "entry": 0.0, "sl": 0.0, "tp1": 0.0, "tp2": 0.0, "tp3": 0.0,
                       "bar": -100, "tp1r": False, "tp2r": False, "tp3r": False, "be": False}

        rev_s = (short_sig and act["dir"] == 1) or (long_sig and act["dir"] == -1)
        if rev_s:
            if act["dir"] != 0:
                lev = lev_long if act["dir"] == 1 else lev_short
                pnl_pct = (c[i] - act["entry"]) / act["entry"] * 100 * act["dir"] * lev
                trades.append({
                    "entry": act["entry"], "exit": c[i], "pnl_pct": pnl_pct,
                    "win": act["tp1r"], "bars": i - act["bar"],
                    "dir": "L" if act["dir"] == 1 else "S",
                })
            act = {"dir": 0, "entry": 0.0, "sl": 0.0, "tp1": 0.0, "tp2": 0.0, "tp3": 0.0,
                   "bar": -100, "tp1r": False, "tp2r": False, "tp3r": False, "be": False}

        if act["dir"] == 0 and long_sig:
            sl_dist = rskd[i] * slm
            sl_wick = min(lo - rskd[i] * 0.25, src - rskd[i] * 0.5)
            sl_p = sl_wick if p["sl_mode"] == "Wick-Anchored" else src - sl_dist
            risk = src - sl_p
            if risk > 0:
                act = {"dir": 1, "entry": src, "sl": sl_p,
                       "tp1": src + risk * tp1m, "tp2": src + risk * tp2m, "tp3": src + risk * tp3m,
                       "bar": i, "tp1r": False, "tp2r": False, "tp3r": False, "be": False}

        if act["dir"] == 0 and short_sig:
            sl_dist = rskd[i] * slm
            sl_wick = max(hi + rskd[i] * 0.25, src + rskd[i] * 0.5)
            sl_p = sl_wick if p["sl_mode"] == "Wick-Anchored" else src + sl_dist
            risk = sl_p - src
            if risk > 0:
                act = {"dir": -1, "entry": src, "sl": sl_p,
                       "tp1": src - risk * tp1m, "tp2": src - risk * tp2m, "tp3": src - risk * tp3m,
                       "bar": i, "tp1r": False, "tp2r": False, "tp3r": False, "be": False}

    return trades


def run_test(label, df_slice, params):
    print(f"\n  {'='*55}")
    print(f"  {label}")
    print(f"  {'='*55}")
    print(f"  Veri: {df_slice.index[0]} -> {df_slice.index[-1]}  ({len(df_slice)} bar)")

    trades = backtest_v2(df_slice, params)
    if not trades:
        print("  Hiç trade yok!")
        return

    wins = sum(1 for t in trades if t["win"])
    total = len(trades)
    wr = wins / total * 100
    net = sum(t["pnl_pct"] for t in trades)
    gross_p = sum(t["pnl_pct"] for t in trades if t["pnl_pct"] > 0) or 0.001
    gross_l = abs(sum(t["pnl_pct"] for t in trades if t["pnl_pct"] < 0)) or 0.001
    pf = gross_p / gross_l
    avg_win = gross_p / wins if wins > 0 else 0
    avg_loss = gross_l / (total - wins) if total - wins > 0 else 0
    longs = sum(1 for t in trades if t["dir"] == "L")
    shorts = sum(1 for t in trades if t["dir"] == "S")

    print(f"  Trade sayısı : {total}  (Long: {longs}, Short: {shorts})")
    print(f"  Win/Loss     : {wins} / {total - wins}")
    print(f"  Win Rate     : %{wr:.1f}")
    print(f"  Net Kar (1x) : %{net:.2f}")
    print(f"  Net Kar (20x): %{net*20:.2f}")
    print(f"  Profit Factor: {pf:.2f}")
    print(f"  Avg Win      : %{avg_win:.2f}")
    print(f"  Avg Loss     : %{avg_loss:.2f}")


if __name__ == "__main__":
    with open(r"C:\Users\svkts\OneDrive\Belgeler\Default Project\best_params.json") as f:
        data = json.load(f)

    p = data["full_config"]
    print(f"\n  Parametreler: {json.dumps(data['best_params'], indent=2)}")

    # Load all data at once (reuses optimizer's multi-chunk download)
    print(f"\n  Yukleniyor...")
    df_all = load_data()

    periods = [
        ("Egitim-chunk-1 (06-18..06-25)", "2026-06-18", "2026-06-25"),
        ("Egitim-chunk-2 (06-25..07-02)", "2026-06-25", "2026-07-02"),
        ("Egitim-chunk-3 (07-02..07-09)", "2026-07-02", "2026-07-09"),
        ("Egitim-chunk-4 (07-09..07-16)", "2026-07-09", "2026-07-16"),
        ("Egitim-chunk-5 (07-16..07-18)", "2026-07-16", "2026-07-18"),
        ("OOS -2a (06-04..06-11)", "2026-06-04", "2026-06-11"),
        ("OOS -1a (06-11..06-18)", "2026-06-11", "2026-06-18"),
    ]

    print(f"\n\n  {'='*50}")
    print(f"  A - EGITIM ICI TESTLER")
    print(f"  {'='*50}")
    for label, start, end in periods[:5]:
        df_slice = df_all.loc[start:end].copy()
        run_test(label, df_slice, p)

    print(f"\n\n  {'='*50}")
    print(f"  B - GERCEK OOS (egitim oncesi)")
    print(f"  {'='*50}")
    for label, start, end in periods[5:]:
        df_slice = df_all.loc[start:end].copy()
        run_test(label, df_slice, p)

    # 1x baseline comparison
    p1x = dict(p)
    p1x["long_leverage"] = 1.0
    p1x["short_leverage"] = 1.0

    print(f"\n\n  -- 1x KALDIRAC KARSILASTIRMASI --")
    for label, start, end in periods:
        df_slice = df_all.loc[start:end].copy()
        t = backtest_v2(df_slice, p1x)
        net1x = sum(ti["pnl_pct"] for ti in t) if t else 0
        wr1x = (sum(1 for ti in t if ti["win"]) / len(t) * 100) if t else 0
        print(f"  {label:20s}  Trade: {len(t):3d}  WR: %{wr1x:5.1f}  Net: %{net1x:+.2f}")
