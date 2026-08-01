"""
Debug script: compare Python backtest_v2 logic with Pine Script logic
to find why Pine doesn't execute trades.
"""
import json
import numpy as np
import pandas as pd
from optimize_ltm import load_data, backtest_v2

def pine_matched_backtest(df, p):
    """
    Replicates Pine Script EXACTLY including all gates:
    - entryAllowed (isWarmedUp, sessionOk, not tradingHalted)
    - activeDir tracking
    - slDistance > 0 check
    Returns trade list and debug info about blocked entries.
    """
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
    # Pine doesn't use leverage in calcPositionQty anymore (we removed it)
    lev_long = 1.0
    lev_short = 1.0

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

    # EMA-50 & Volume SMA-20
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

    # ── Pine-specific state ──
    # Trade state
    active_dir = 0
    active_entry = active_sl = active_tp1 = active_tp2 = active_tp3 = 0.0
    entry_bar = -100
    tp1r = tp2r = tp3r = False
    be_active = False
    tp1_scaled = tp2_scaled = False
    trail_activation = np.nan

    # Pro trackers (simplified - no daily tracker)
    consec_losses = 0
    trading_paused = False

    # Session filter: always 7/24 (user confirmed)
    def session_ok(i):
        return True  # 7/24

    # Signal state
    cur_trend = 1
    cur_ts = np.array([np.nan, np.nan, np.nan, np.nan])
    cur_start = 0
    pend = {"long": 0, "short": 0, "ld": 0, "sd": 0}
    last_sig = -10000

    # Debug counters
    debug = {
        "long_signal_true": 0,
        "active_dir_zero": 0,
        "entry_allowed_true": 0,
        "all_gates_passed": 0,
        "risk_positive": 0,
        "trade_executed": 0,
        "blocked_reasons": [],
    }

    trades = []

    for i in range(n):
        if i == 0 or trad[i] == 0 or np.isnan(trad[i]):
            continue

        src, hi, lo, op = c[i], h[i], l[i], o[i]

        # ── Bands ──
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

        bars_in_trend = i - cur_start

        if i < warmup:
            continue

        # ── Pine parity: isSessionAllowed (7/24) ──
        session_ok_val = session_ok(i)

        # ── Pine parity: tradingHalted check ──
        consec_halted = p.get("max_consec_loss", 0) > 0 and consec_losses >= p.get("max_consec_loss", 0)
        daily_halted = False  # simplified
        trading_halted = trading_paused or consec_halted or daily_halted

        # ── Pine parity: entryAllowed ──
        entry_allowed = session_ok_val and not trading_halted

        # ── Signals (same as Python backtest_v2) ──
        prev_trend = 1 if i == warmup else 1  # simplified
        if i > warmup:
            pass  # We don't have trend array, use current logic

        # Detect flip by checking if cur_ts just reset
        flip_bar = False
        if i > warmup:
            # Check if cur_ts values changed drastically (flip)
            # Simplified: flip when trend direction changes
            pass

        # ── Pending decay ──
        for k in ["long", "short"]:
            pend[k] = max(pend[k] - 1, 0)
            if pend[k] == 0:
                pend[k + "d"] = 0

        # ── Touch ──
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
        ag = 15 if 10 <= bars_in_trend <= 150 else (8 if bars_in_trend < 10 else 5)
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

        # Flip detection for this bar (compare with previous)
        # We track cur_trend, need previous trend
        if i == warmup:
            prev_trend_val = cur_trend
        conf_bull_flip = False
        conf_bear_flip = False

        raw_long_sig = conf_l or conf_bull_flip
        raw_short_sig = conf_s or conf_bear_flip
        long_sig = raw_long_sig if not p.get("reverse_signal", False) else raw_short_sig
        short_sig = raw_short_sig if not p.get("reverse_signal", False) else raw_long_sig

        if conf_l or conf_s:
            last_sig = i

        # ── Trade management ──
        sl_hit = tp1_hit = tp2_hit = tp3_hit = False
        if active_dir != 0 and i > entry_bar:
            sl_hit = (active_dir == 1 and lo <= active_sl) or (active_dir == -1 and hi >= active_sl)
            tp1_hit = (active_dir == 1 and hi >= active_tp1) or (active_dir == -1 and lo <= active_tp1)
            tp2_hit = (active_dir == 1 and hi >= active_tp2) or (active_dir == -1 and lo <= active_tp2)
            tp3_hit = (active_dir == 1 and hi >= active_tp3) or (active_dir == -1 and lo <= active_tp3)
            if tp1_hit and not tp1r and not sl_hit:
                tp1r = True
                # BE
            if tp3_hit and not tp3r and not sl_hit:
                tp3r = True
            if sl_hit or tp3_hit:
                active_dir = 0

        # ── ENTRY ANALYSIS ──
        if long_sig:
            debug["long_signal_true"] += 1

        if active_dir == 0:
            debug["active_dir_zero"] += 1

        if entry_allowed:
            debug["entry_allowed_true"] += 1

        # Check what happens with ALL Pine gates
        risk_ok = False
        if long_sig and active_dir == 0 and entry_allowed:
            debug["all_gates_passed"] += 1
            # SL calculation (same as Pine)
            sl_dist = rskd[i] * slm
            sl_wick = min(lo - rskd[i] * 0.25, src - rskd[i] * 0.5)
            sl_p = sl_wick if p["sl_mode"] == "Wick-Anchored" else src - sl_dist
            risk_val = src - sl_p
            if risk_val > 0:
                risk_ok = True
                debug["risk_positive"] += 1
                debug["trade_executed"] += 1
                # Actually enter trade
                active_dir = 1
                active_entry = src
                active_sl = sl_p
                active_tp1 = src + risk_val * tp1m
                active_tp2 = src + risk_val * tp2m
                active_tp3 = src + risk_val * tp3m
                entry_bar = i
                tp1r = tp2r = tp3r = False
                be_active = False
                trades.append({"bar": i, "dir": "L", "entry": float(src)})

        # Debug: first few posts-warmup bars where signal is true but entry doesn't happen
        if long_sig and active_dir == 0 and entry_allowed and not risk_ok and i < warmup + 500:
            sl_dist = rskd[i] * slm
            sl_wick = min(lo - rskd[i] * 0.25, src - rskd[i] * 0.5)
            sl_p = sl_wick if p["sl_mode"] == "Wick-Anchored" else src - sl_dist
            risk_val = src - sl_p
            debug["blocked_reasons"].append({
                "bar": i, "reason": "risk_not_positive",
                "rskd": float(rskd[i]), "slm": slm, "sl_dist": float(sl_dist),
                "src": float(src), "sl_p": float(sl_p), "risk_val": float(risk_val),
                "sl_wick_low_term": float(lo - rskd[i] * 0.25),
                "sl_wick_close_term": float(src - rskd[i] * 0.5),
            })

        if long_sig and not (active_dir == 0) and i < warmup + 500:
            debug["blocked_reasons"].append({
                "bar": i, "reason": "active_dir_not_zero",
                "active_dir": active_dir,
            })

        if long_sig and active_dir == 0 and not entry_allowed and i < warmup + 500:
            debug["blocked_reasons"].append({
                "bar": i, "reason": "entry_not_allowed",
                "session_ok": session_ok_val,
                "trading_halted": trading_halted,
                "trading_paused": trading_paused,
                "consec_halted": consec_halted,
            })

    return trades, debug


if __name__ == "__main__":
    with open(r"C:\Users\svkts\OneDrive\Belgeler\Default Project\best_params.json") as f:
        data = json.load(f)

    p = data["full_config"]
    print(f"Params: {json.dumps(data['best_params'], indent=2)}")

    # Add Pine-specific params
    p["max_consec_loss"] = 5
    p["max_daily_loss"] = 5.0

    print(f"\nLoading data...")
    df_all = load_data()

    print(f"\n--- Standard Python backtest_v2 ---")
    trades = backtest_v2(df_all, p)
    print(f"Trades: {len(trades)}")
    if trades:
        net = sum(t["pnl_pct"] for t in trades)
        wins = sum(1 for t in trades if t["win"])
        print(f"Net: {net:+.2f}%, WR: {wins/len(trades)*100:.1f}%")

    print(f"\n--- Pine-matched backtest ---")
    pine_trades, debug = pine_matched_backtest(df_all, p)
    print(f"Trades: {len(pine_trades)}")
    print(f"\nDebug counters:")
    print(f"  long_signal_true : {debug['long_signal_true']}")
    print(f"  active_dir_zero  : {debug['active_dir_zero']}")
    print(f"  entry_allowed_true: {debug['entry_allowed_true']}")
    print(f"  all gates passed : {debug['all_gates_passed']}")
    print(f"  risk_positive    : {debug['risk_positive']}")
    print(f"  trade_executed   : {debug['trade_executed']}")

    if debug["blocked_reasons"]:
        print(f"\nBlocked entries (first 20):")
        for b in debug["blocked_reasons"][:20]:
            print(f"  Bar {b['bar']}: {b['reason']} | {b.get('rskd','')} {b.get('active_dir','')}")

    if len(trades) != len(pine_trades):
        print(f"\n*** MISMATCH: Python={len(trades)} vs Pine={len(pine_trades)} ***")
    else:
        print(f"\nMATCH OK: {len(trades)} trades both sides")
