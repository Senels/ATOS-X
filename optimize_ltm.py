"""
LTM Strategy Optimizer — XAUUSD 1m
Re-implements the core Pine Script strategy logic in Python,
then uses Optuna to find max-profit parameters.
"""

import optuna
import pandas as pd
import numpy as np
import yfinance as yf
import json
from functools import partial
from datetime import datetime
import warnings

warnings.filterwarnings("ignore")

# ══════════════════════════════════════════════════════════
# 1. DATA
# ══════════════════════════════════════════════════════════

def load_data():
    print("Downloading XAUUSD 1m data (multi-week)...")
    from datetime import datetime, timedelta
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
# 2. BACKTEST ENGINE  (faithful reimplementation)
# ══════════════════════════════════════════════════════════

def backtest(df, p):
    """
    p = parameter dict (see objective() for all keys)
    Returns list of trade dicts and equity curve.
    """
    o = df["open"].values
    h = df["high"].values
    l = df["low"].values
    c = df["close"].values
    v = df["volume"].values
    n = len(c)

    # ── Band geometry ──
    PRESETS = {"Scalping": (2.5, 0.2), "Balanced": (4.0, 0.25), "Deep Trend": (6.0, 0.3)}
    if p["band_preset"] == "Custom":
        eb, es = p["base_mult"], p["band_step"]
    else:
        eb, es = PRESETS[p["band_preset"]]
    m1 = eb
    m2 = eb * (1.0 + es)
    m3 = eb * (1.0 + 2.0 * es)
    m4 = eb * (1.0 + 3.0 * es)

    # ── Risk ──
    RISK_P = {
        "Conservative": (2.5, 1.0, 2.0, 4.0),
        "Aggressive": (1.0, 1.5, 2.5, 4.0),
        "Scalping": (0.8, 0.8, 1.5, 2.0),
        "Balanced": (1.5, 1.0, 2.0, 3.0),
    }
    if p["risk_preset"] == "Custom":
        slm, tp1m, tp2m, tp3m = p["sl_mult"], p["tp1_mult"], p["tp2_mult"], p["tp3_mult"]
    else:
        slm, tp1m, tp2m, tp3m = RISK_P[p["risk_preset"]]

    # ── ATR ──
    def _atr(length):
        tr = np.zeros(n)
        for i in range(1, n):
            tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
        atr = np.zeros(n)
        for i in range(n):
            if i < length:
                atr[i] = np.mean(tr[max(0, i - length + 1) : i + 1])
            else:
                atr[i] = (atr[i - 1] * (length - 1) + tr[i]) / length
        return atr

    trail_atr = _atr(p["atr_len"])
    risk_atr = _atr(p["atr_len_risk"])
    warmup = max(p["atr_len"] * 3, 60)

    # ── Running state ──
    cur_trend = 1
    cur_ts1 = cur_ts2 = cur_ts3 = cur_ts4 = np.nan
    cur_trend_start = 0

    pending_long = pending_short = 0
    pending_long_depth = pending_short_depth = 0
    last_sig_bar = -10000

    active_dir = 0
    active_entry = active_sl = active_tp1 = active_tp2 = active_tp3 = 0.0
    entry_bar = -100
    tp1_reached = tp2_reached = tp3_reached = False
    be_active = False

    trades = []
    in_trade = False

    for i in range(1, n):
        if trail_atr[i] == 0 or np.isnan(trail_atr[i]):
            continue
        src = c[i]
        hi, lo, op = h[i], l[i], o[i]

        # Band raw values
        u1 = src - trail_atr[i] * m1
        u2 = src - trail_atr[i] * m2
        u3 = src - trail_atr[i] * m3
        u4 = src - trail_atr[i] * m4
        l1 = src + trail_atr[i] * m1
        l2 = src + trail_atr[i] * m2
        l3 = src + trail_atr[i] * m3
        l4 = src + trail_atr[i] * m4

        if np.isnan(cur_ts1):
            cur_ts1 = u1 if cur_trend == 1 else l1
            cur_ts2 = u2 if cur_trend == 1 else l2
            cur_ts3 = u3 if cur_trend == 1 else l3
            cur_ts4 = u4 if cur_trend == 1 else l4
            continue

        # Flip
        flip_prev = {2: cur_ts2, 3: cur_ts3, 4: cur_ts4}[p["flip_band"]]
        if cur_trend == 1 and not np.isnan(flip_prev) and src < flip_prev:
            cur_trend = -1
            cur_ts1, cur_ts2, cur_ts3, cur_ts4 = l1, l2, l3, l4
            cur_trend_start = i
        elif cur_trend == -1 and not np.isnan(flip_prev) and src > flip_prev:
            cur_trend = 1
            cur_ts1, cur_ts2, cur_ts3, cur_ts4 = u1, u2, u3, u4
            cur_trend_start = i
        elif cur_trend == 1:
            cur_ts1, cur_ts2, cur_ts3, cur_ts4 = max(u1, cur_ts1), max(u2, cur_ts2), max(u3, cur_ts3), max(u4, cur_ts4)
        else:
            cur_ts1, cur_ts2, cur_ts3, cur_ts4 = min(l1, cur_ts1), min(l2, cur_ts2), min(l3, cur_ts3), min(l4, cur_ts4)

        if i < warmup:
            continue

        flip_bar = i > 1  # simplified: check if just flipped (above it's handled per-bar)
        # Actually detect if trend changed from previous iteration
        # We track prev_trend across iterations
        if i == 1:
            prev_trend = 1
        # We'll trust the current state

        bars_in_trend = i - cur_trend_start

        # ── Pending / Touch ──
        pending_long = max(pending_long - 1, 0)
        pending_short = max(pending_short - 1, 0)
        if pending_long == 0:    pending_long_depth = 0
        if pending_short == 0:   pending_short_depth = 0

        if cur_trend == 1 and not np.isnan(cur_ts1):
            td = 4 if lo <= cur_ts4 else 3 if lo <= cur_ts3 else 2 if lo <= cur_ts2 else 1 if lo <= cur_ts1 else 0
            if td > 0:
                pending_long = p["retest_window"]
                pending_long_depth = max(pending_long_depth, td)
        if cur_trend == -1 and not np.isnan(cur_ts1):
            td = 4 if hi >= cur_ts4 else 3 if hi >= cur_ts3 else 2 if hi >= cur_ts2 else 1 if hi >= cur_ts1 else 0
            if td > 0:
                pending_short = p["retest_window"]
                pending_short_depth = max(pending_short_depth, td)

        # Reclaim
        long_reclaim = pending_long > 0 and cur_trend == 1 and not np.isnan(cur_ts1) and src > cur_ts1 and src > op
        short_reclaim = pending_short > 0 and cur_trend == -1 and not np.isnan(cur_ts1) and src < cur_ts1 and src < op

        # Score
        rng = hi - lo
        cl = (src - lo) / rng if rng > 0 else 0.5
        cs = (hi - src) / rng if rng > 0 else 0.5

        def _dp(d): return {2: 25, 3: 18, 1: 15, 4: 10}.get(d, 0)
        depth_l = _dp(pending_long_depth)
        depth_s = _dp(pending_short_depth)
        candle_l = 20 if cl > 0.7 else (12 if cl > 0.5 else 5)
        candle_s = 20 if cs > 0.7 else (12 if cs > 0.5 else 5)
        age_p = 15 if 10 <= bars_in_trend <= 150 else (8 if bars_in_trend < 10 else 5)
        vol_p = 20 if v[i] > 0 else 12

        long_score = depth_l + candle_l + age_p + vol_p
        short_score = depth_s + candle_s + age_p + vol_p

        cooldown_ok = i - last_sig_bar >= p["cooldown"]
        confirmed_long = long_reclaim and cooldown_ok and long_score >= p["min_score"]
        confirmed_short = short_reclaim and cooldown_ok and short_score >= p["min_score"]

        # Simple flip detection (compare trend[i-1] vs trend[i])
        # Since we update cur_trend in place, we need to track changes
        # Use a simple heuristic: if the band values just reset, it was a flip
        # Better: track trend from previous iteration
        if i == 1:
            prev_trend_local = 1
        else:
            prev_trend_local = prev_trend_local if 'prev_trend_local' in dir() else 1

        # Actually let me re-derive flip_bar properly
        # We need to know if this bar is the first bar of a new trend
        # Check if the close crossed the previous bar's flip band
        # Simplified: if cur_trend changed from last iteration
        if i > 1:
            # We track the trend value per iteration
            pass

        # For simplicity, mark flip when the band values were freshly assigned
        # (which happens at the flip condition above)
        # We'll detect it by checking if the current bar has the "new" trend
        # Let me redo this more carefully.

    # Actually, let me redo the backtest with proper per-bar trend tracking
    return trades  # placeholder

# ══════════════════════════════════════════════════════════
# Let me rewrite the backtest more carefully
# ══════════════════════════════════════════════════════════

def backtest_v2(df, p):
    """Full faithful backtest returning list of trade dicts."""
    o = df["open"].values
    h = df["high"].values
    l = df["low"].values
    c = df["close"].values
    v = df["volume"].values
    n = len(c)

    # Band geometry
    GEO = {"Scalping": (2.5, 0.2), "Balanced": (4.0, 0.25), "Deep Trend": (6.0, 0.3)}
    if p["band_preset"] == "Custom":
        eb, es = p["base_mult"], p["band_step"]
    else:
        eb, es = GEO[p["band_preset"]]
    m = [eb, eb * (1 + es), eb * (1 + 2 * es), eb * (1 + 3 * es)]

    # Risk
    RSK = {"Conservative": (2.5, 1.0, 2.0, 4.0), "Aggressive": (1.0, 1.5, 2.5, 4.0),
           "Scalping": (0.8, 0.8, 1.5, 2.0), "Balanced": (1.5, 1.0, 2.0, 3.0)}
    if p["risk_preset"] == "Custom":
        slm, tp1m, tp2m, tp3m = p["sl_mult"], p["tp1_mult"], p["tp2_mult"], p["tp3_mult"]
    else:
        slm, tp1m, tp2m, tp3m = RSK[p["risk_preset"]]
    lev_long = p.get("long_leverage", 1.0)
    lev_short = p.get("short_leverage", 1.0)

    # ATR
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

    # ── HTF bias (EMA-50) & Volume SMA-20 (Pine parity) ──
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

    # ── State arrays ──
    trend = np.ones(n, dtype=int)
    ts = np.full((n, 4), np.nan)
    trend_start = np.zeros(n, dtype=int)
    bars_in_trend = np.zeros(n, dtype=int)

    cur_trend = 1
    cur_ts = np.array([np.nan, np.nan, np.nan, np.nan])
    cur_start = 0

    # Signal / trade state
    pend = {"long": 0, "short": 0, "ld": 0, "sd": 0}
    last_sig = -10000

    act = {"dir": 0, "entry": 0.0, "sl": 0.0, "tp1": 0.0, "tp2": 0.0, "tp3": 0.0,
           "bar": -100, "tp1r": False, "tp2r": False, "tp3r": False, "be": False}

    trades = []

    for i in range(n):
        if i == 0 or trad[i] == 0 or np.isnan(trad[i]):
            continue

        src, hi, lo, op = c[i], h[i], l[i], o[i]

        # Raw bands
        raw_u = [src - trad[i] * mk for mk in m]
        raw_l = [src + trad[i] * mk for mk in m]

        # Initialize
        if np.isnan(cur_ts[0]):
            cur_ts = np.array([raw_u[j] if cur_trend == 1 else raw_l[j] for j in range(4)])
            trend[i] = cur_trend
            ts[i] = cur_ts
            continue

        # Flip
        fb = p["flip_band"] - 1  # 0-indexed
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

        # Reset pending on flip (Pine parity)
        if flip_bar:
            for k in ["long", "short"]:
                pend[k] = 0
                pend[k + "d"] = 0

        # ── Signals ──
        conf_bull_flip = flip_bar and cur_trend == 1
        conf_bear_flip = flip_bar and cur_trend == -1

        # Pending decay
        for k in ["long", "short"]:
            pend[k] = max(pend[k] - 1, 0)
            if pend[k] == 0:
                pend[k + "d"] = 0

        # Touch
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

        # Score
        rng = max(hi - lo, 1e-10)
        cl = (src - lo) / rng
        cs = (hi - src) / rng
        dp = {2: 25, 3: 18, 1: 15, 4: 10}
        de_l = dp.get(pend["ld"], 0)
        de_s = dp.get(pend["sd"], 0)
        ca_l = 20 if cl > 0.7 else (12 if cl > 0.5 else 5)
        ca_s = 20 if cs > 0.7 else (12 if cs > 0.5 else 5)
        ag = 15 if 10 <= bars_in_trend[i] <= 150 else (8 if bars_in_trend[i] < 10 else 5)

        # HTF bias (Pine parity: EMA-50 comparison, prev bar)
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

        # Volume scoring (Pine parity)
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

        raw_long_sig = conf_l or conf_bull_flip
        raw_short_sig = conf_s or conf_bear_flip
        long_sig = raw_long_sig if not p.get("reverse_signal", False) else raw_short_sig
        short_sig = raw_short_sig if not p.get("reverse_signal", False) else raw_long_sig

        # Update last signal
        if conf_l or conf_s or conf_bull_flip or conf_bear_flip:
            last_sig = i

        # ── Trade management ──
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
                    "entry_bar": int(act["bar"]), "exit_bar": i,
                })
                act = {"dir": 0, "entry": 0.0, "sl": 0.0, "tp1": 0.0, "tp2": 0.0, "tp3": 0.0,
                       "bar": -100, "tp1r": False, "tp2r": False, "tp3r": False, "be": False}

        # Reversal (Pine parity: uses long_sig/short_sig, includes flips)
        rev_s = (short_sig and act["dir"] == 1) or (long_sig and act["dir"] == -1)
        if rev_s:
            if act["dir"] != 0:
                lev = lev_long if act["dir"] == 1 else lev_short
                pnl_pct = (c[i] - act["entry"]) / act["entry"] * 100 * act["dir"] * lev
                trades.append({
                    "entry": act["entry"], "exit": c[i], "pnl_pct": pnl_pct,
                    "win": act["tp1r"], "bars": i - act["bar"],
                    "dir": "L" if act["dir"] == 1 else "S",
                    "entry_bar": int(act["bar"]), "exit_bar": i,
                })
            act = {"dir": 0, "entry": 0.0, "sl": 0.0, "tp1": 0.0, "tp2": 0.0, "tp3": 0.0,
                   "bar": -100, "tp1r": False, "tp2r": False, "tp3r": False, "be": False}

        wick_lo_m = p.get("sl_wick_lo_mult", 0.75)
        wick_cl_m = p.get("sl_wick_close_mult", 1.5)

        # Entry
        if act["dir"] == 0 and long_sig:
            sl_dist = rskd[i] * slm
            sl_wick = min(lo - rskd[i] * wick_lo_m, src - rskd[i] * wick_cl_m)
            sl_p = sl_wick if p["sl_mode"] == "Wick-Anchored" else src - sl_dist
            risk = src - sl_p
            if risk > 0:
                act = {"dir": 1, "entry": src, "sl": sl_p,
                       "tp1": src + risk * tp1m, "tp2": src + risk * tp2m, "tp3": src + risk * tp3m,
                       "bar": i, "tp1r": False, "tp2r": False, "tp3r": False, "be": False}

        if act["dir"] == 0 and short_sig:
            sl_dist = rskd[i] * slm
            sl_wick = max(hi + rskd[i] * wick_lo_m, src + rskd[i] * wick_cl_m)
            sl_p = sl_wick if p["sl_mode"] == "Wick-Anchored" else src + sl_dist
            risk = sl_p - src
            if risk > 0:
                act = {"dir": -1, "entry": src, "sl": sl_p,
                       "tp1": src - risk * tp1m, "tp2": src - risk * tp2m, "tp3": src - risk * tp3m,
                       "bar": i, "tp1r": False, "tp2r": False, "tp3r": False, "be": False}

    return trades


# ══════════════════════════════════════════════════════════
# 3. OPTIMIZATION
# ══════════════════════════════════════════════════════════

def objective(trial, df):
    p = {}

    p["band_preset"] = trial.suggest_categorical("band_preset", ["Scalping", "Balanced", "Deep Trend", "Custom"])
    if p["band_preset"] == "Custom":
        p["base_mult"] = trial.suggest_float("base_mult", 1.0, 12.0, step=0.25)
        p["band_step"] = trial.suggest_float("band_step", 0.05, 0.6, step=0.025)
    else:
        p["base_mult"] = {"Scalping": 2.5, "Balanced": 4.0, "Deep Trend": 6.0}[p["band_preset"]]
        p["band_step"] = {"Scalping": 0.2, "Balanced": 0.25, "Deep Trend": 0.30}[p["band_preset"]]

    p["atr_len"] = trial.suggest_int("atr_len", 3, 120)
    p["flip_band"] = trial.suggest_int("flip_band", 1, 4)
    p["min_score"] = trial.suggest_int("min_score", 10, 100, step=5)
    p["retest_window"] = trial.suggest_int("retest_window", 1, 50)
    p["cooldown"] = trial.suggest_int("cooldown", 0, 30)
    p["risk_preset"] = trial.suggest_categorical("risk_preset", ["Conservative", "Balanced", "Aggressive", "Scalping", "Custom"])

    if p["risk_preset"] == "Custom":
        p["sl_mult"] = trial.suggest_float("sl_mult", 0.3, 5.0, step=0.1)
        p["tp1_mult"] = trial.suggest_float("tp1_mult", 0.3, 6.0, step=0.1)
        p["tp2_mult"] = trial.suggest_float("tp2_mult", 0.5, 10.0, step=0.1)
        p["tp3_mult"] = trial.suggest_float("tp3_mult", 1.0, 15.0, step=0.1)
    else:
        p["sl_mult"] = p["tp1_mult"] = p["tp2_mult"] = p["tp3_mult"] = 0

    p["sl_mode"] = trial.suggest_categorical("sl_mode", ["Wick-Anchored", "ATR"])
    p["atr_len_risk"] = trial.suggest_int("atr_len_risk", 3, 80)

    if p["sl_mode"] == "Wick-Anchored":
        p["sl_wick_lo_mult"] = trial.suggest_float("sl_wick_lo_mult", 0.10, 3.0, step=0.05)
        p["sl_wick_close_mult"] = trial.suggest_float("sl_wick_close_mult", 0.25, 5.0, step=0.05)
    else:
        p["sl_wick_lo_mult"] = 0.75
        p["sl_wick_close_mult"] = 1.5
    p["be"] = True
    p["reverse_signal"] = False
    p["long_leverage"] = trial.suggest_float("long_leverage", 1.0, 20.0, step=0.5)
    p["short_leverage"] = trial.suggest_float("short_leverage", 1.0, 20.0, step=0.5)

    trades = backtest_v2(df, p)

    if len(trades) == 0:
        return -9999.0

    wins = sum(1 for t in trades if t["win"])
    total = len(trades)
    wr = wins / total
    gross_profit = sum(t["pnl_pct"] for t in trades if t["pnl_pct"] > 0)
    gross_loss = abs(sum(t["pnl_pct"] for t in trades if t["pnl_pct"] < 0))
    net = sum(t["pnl_pct"] for t in trades)
    pf = gross_profit / gross_loss if gross_loss > 0 else gross_profit

    # Score: maximize net profit, bonus for consistency
    score = net * (wr ** 0.3) * (pf ** 0.4)
    # Penalize too few trades
    if total < 10:
        score *= 0.2
    # Penalize excessive trades (noise trading)
    if total > 400:
        score *= 0.7
    # Mild penalty for very low WR
    if wr < 0.40:
        score *= 0.5
    # Small penalty for very high leverage (overfitting guard)
    if p.get("long_leverage", 1) > 15 or p.get("short_leverage", 1) > 15:
        score *= 0.95

    return score


# ══════════════════════════════════════════════════════════
# 4. RUN
# ══════════════════════════════════════════════════════════

def run():
    df = load_data()
    print("Optimizing... (1000 trials, deep analysis - this will take several minutes)\n")

    study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=42))
    study.optimize(partial(objective, df=df), n_trials=1000, show_progress_bar=True)

    best = study.best_params
    # Fill in preset-dependent params for display
    GEO = {"Scalping": (2.5, 0.2), "Balanced": (4.0, 0.25), "Deep Trend": (6.0, 0.3)}
    if best["band_preset"] != "Custom":
        best["base_mult"], best["band_step"] = GEO[best["band_preset"]]
    if best["risk_preset"] != "Custom":
        RSK = {"Conservative": (2.5, 1.0, 2.0, 4.0), "Aggressive": (1.0, 1.5, 2.5, 4.0),
               "Scalping": (0.8, 0.8, 1.5, 2.0), "Balanced": (1.5, 1.0, 2.0, 3.0)}
        best["sl_mult"], best["tp1_mult"], best["tp2_mult"], best["tp3_mult"] = RSK[best["risk_preset"]]

    # Ensure all required keys exist
    best["be"] = True
    if best.get("sl_mult") is None:
        best["sl_mult"] = 1.5
        best["tp1_mult"] = 1.0
        best["tp2_mult"] = 2.0
        best["tp3_mult"] = 3.0
    if best.get("sl_wick_lo_mult") is None:
        best["sl_wick_lo_mult"] = 0.75
        best["sl_wick_close_mult"] = 1.5

    # Backtest with best params for detailed results
    trades = backtest_v2(df, best)
    wins = sum(1 for t in trades if t["win"])
    total = len(trades)
    wr = wins / total * 100 if total > 0 else 0
    net = sum(t["pnl_pct"] for t in trades)
    gross_p = sum(t["pnl_pct"] for t in trades if t["pnl_pct"] > 0) or 0.001
    gross_l = abs(sum(t["pnl_pct"] for t in trades if t["pnl_pct"] < 0)) or 0.001
    pf = gross_p / gross_l
    avg_win = gross_p / wins if wins > 0 else 0
    avg_loss = gross_l / (total - wins) if total - wins > 0 else 0

    print()
    print("=" * 62)
    print("  BEST PARAMETERS - XAUUSD 1m")
    print("=" * 62)
    for k, v in study.best_params.items():
        print(f"     {k:20s} = {v}")
    print(f"\n     {'band_preset':20s} = {best.get('band_preset','')}")
    print(f"     {'base_mult':20s} = {best.get('base_mult','')}")
    print(f"     {'band_step':20s} = {best.get('band_step','')}")
    print(f"     {'risk_preset':20s} = {best.get('risk_preset','')}")
    print(f"     {'sl_mult':20s} = {best.get('sl_mult','')}")
    print(f"     {'tp1_mult':20s} = {best.get('tp1_mult','')}")
    print(f"     {'tp2_mult':20s} = {best.get('tp2_mult','')}")
    print(f"     {'tp3_mult':20s} = {best.get('tp3_mult','')}")
    print(f"     {'sl_wick_lo_mult':20s} = {best.get('sl_wick_lo_mult','')}")
    print(f"     {'sl_wick_close_mult':20s} = {best.get('sl_wick_close_mult','')}")
    print(f"     {'long_leverage':20s} = {best.get('long_leverage', 1.0)}")
    print(f"     {'short_leverage':20s} = {best.get('short_leverage', 1.0)}")

    print()
    print("  " + "-" * 60)
    print("  BACKTEST RESULTS")
    print("  " + "-" * 60)
    print(f"     Total Trades : {total}")
    print(f"     Wins / Losses: {wins} / {total - wins}")
    print(f"     Win Rate     : {wr:.1f}%")
    print(f"     Net Profit   : {net:+.2f}%")
    print(f"     Profit Factor: {pf:.2f}")
    print(f"     Avg Win      : {avg_win:+.2f}%")
    print(f"     Avg Loss     : {avg_loss:+.2f}%")
    print("=" * 62)

    # Save
    out = {"best_params": study.best_params, "full_config": best,
           "results": {"trades": total, "wins": wins, "losses": total - wins,
                       "win_rate_pct": round(wr, 1), "net_profit_pct": round(net, 2),
                       "profit_factor": round(pf, 2)}}
    path = r"C:\Users\svkts\OneDrive\Belgeler\Default Project\best_params.json"
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nSaved to {path}")


if __name__ == "__main__":
    run()
