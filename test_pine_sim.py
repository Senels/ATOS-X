"""
Full Pine Script logic simulation with position sizing + margin cap.
Tests every gate in the entry chain.
"""
import json
import numpy as np
from optimize_ltm import load_data, backtest_v2


def simulate_pine(df, p):
    o = df['open'].values; h = df['high'].values; l = df['low'].values
    c = df['close'].values; v = df['volume'].values; n = len(c)

    def atr(period):
        tr = np.zeros(n)
        for i in range(1, n): tr[i] = max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1]))
        a = np.zeros(n)
        for i in range(1, n):
            a[i] = (a[i-1]*(period-1)+tr[i])/period if i >= period else np.mean(tr[1:i+1])
        return a

    trad = atr(p['atr_len'])
    rskd = atr(p['atr_len_risk'])
    warmup = max(p['atr_len'] * 3, 60)

    # EMA
    ema50 = np.zeros(n)
    ema50[0] = c[0]
    alpha = 2 / 51
    for i in range(1, n):
        ema50[i] = (c[i] - ema50[i - 1]) * alpha + ema50[i - 1]

    # Volume
    volSma = np.zeros(n)
    for i in range(n):
        volSma[i] = np.nanmean(v[max(0, i-19):i+1]) if i < 19 else (volSma[i-1] * 19 + v[i]) / 20
    symbolHasVol = np.nansum(v) > 0

    GEO = {'Scalping': (2.5, 0.2), 'Balanced': (4.0, 0.25), 'Deep Trend': (6.0, 0.3)}
    if p['band_preset'] == 'Custom':
        eb, es = p['base_mult'], p['band_step']
    else:
        eb, es = GEO[p['band_preset']]
    m = [eb, eb*(1+es), eb*(1+2*es), eb*(1+3*es)]

    RSK = {'Conservative': (2.5, 1.0, 2.0, 4.0), 'Aggressive': (1.0, 1.5, 2.5, 4.0),
           'Scalping': (0.8, 0.8, 1.5, 2.0), 'Balanced': (1.5, 1.0, 2.0, 3.0)}
    if p['risk_preset'] == 'Custom':
        slm, tp1m, tp2m, tp3m = p['sl_mult'], p['tp1_mult'], p['tp2_mult'], p['tp3_mult']
    else:
        slm, tp1m, tp2m, tp3m = RSK[p['risk_preset']]

    # State
    trend = np.ones(n, dtype=int)
    cur_ts = np.full(4, np.nan)
    cur_start = 0
    pend = {'long': 0, 'short': 0, 'ld': 0, 'sd': 0}
    last_sig = -10000
    act = {'dir': 0}
    active_dir = 0

    # Also track backtest_v2 to compare
    trades_b2 = backtest_v2(df, p)
    b2_first_bars = set(t['entry_bar'] for t in trades_b2)

    # Diagnostic
    results = []

    for i in range(n):
        if i == 0 or trad[i] == 0 or np.isnan(trad[i]):
            continue
        src, hi, lo, op = c[i], h[i], l[i], o[i]

        raw_u = [src - trad[i] * mk for mk in m]
        raw_l = [src + trad[i] * mk for mk in m]

        if np.isnan(cur_ts[0]):
            cur_trend = 1
            cur_ts = np.array([raw_u[j] for j in range(4)])
            continue

        fb = p['flip_band'] - 1
        fp = cur_ts[fb]
        cur_trend = int(trend[i-1])
        do_flip_dn = cur_trend == 1 and not np.isnan(fp) and src < fp
        do_flip_up = cur_trend == -1 and not np.isnan(fp) and src > fp

        if cur_trend == 1:
            if do_flip_dn:
                cur_trend = -1; cur_ts = np.array(raw_l); cur_start = i
            else:
                cur_ts = np.maximum(raw_u, cur_ts)
        else:
            if do_flip_up:
                cur_trend = 1; cur_ts = np.array(raw_u); cur_start = i
            else:
                cur_ts = np.minimum(raw_l, cur_ts)

        trend[i] = cur_trend
        bars_in_trend = i - cur_start

        if i < warmup:
            continue

        prev_trend = trend[i-1]
        flip_bar = trend[i] != prev_trend

        if flip_bar:
            for k in ['long', 'short']:
                pend[k] = 0; pend[k+'d'] = 0

        conf_bull_flip = flip_bar and cur_trend == 1
        conf_bear_flip = flip_bar and cur_trend == -1

        for k in ['long', 'short']:
            pend[k] = max(pend[k] - 1, 0)
            if pend[k] == 0:
                pend[k+'d'] = 0

        if cur_trend == 1 and not np.isnan(cur_ts[0]):
            td = 4 if lo <= cur_ts[3] else 3 if lo <= cur_ts[2] else 2 if lo <= cur_ts[1] else 1 if lo <= cur_ts[0] else 0
            if td:
                pend['long'] = p['retest_window']
                pend['ld'] = max(pend['ld'], td)
        if cur_trend == -1 and not np.isnan(cur_ts[0]):
            td = 4 if hi >= cur_ts[3] else 3 if hi >= cur_ts[2] else 2 if hi >= cur_ts[1] else 1 if hi >= cur_ts[0] else 0
            if td:
                pend['short'] = p['retest_window']
                pend['sd'] = max(pend['sd'], td)

        long_rc = pend['long'] > 0 and cur_trend == 1 and not np.isnan(cur_ts[0]) and src > cur_ts[0] and src > op
        short_rc = pend['short'] > 0 and cur_trend == -1 and not np.isnan(cur_ts[0]) and src < cur_ts[0] and src < op

        rng = max(hi - lo, 1e-10)
        cl = (src - lo) / rng; cs = (hi - src) / rng
        dp = {2: 25, 3: 18, 1: 15, 4: 10}
        de_l = dp.get(pend['ld'], 0); de_s = dp.get(pend['sd'], 0)
        ca_l = 20 if cl > 0.7 else 12 if cl > 0.5 else 5
        ca_s = 20 if cs > 0.7 else 12 if cs > 0.5 else 5
        ag = 15 if 10 <= bars_in_trend <= 150 else (8 if bars_in_trend < 10 else 5)

        if i > 0:
            biasDir = 0
            if not np.isnan(c[i-1]) and not np.isnan(ema50[i-1]):
                if c[i-1] > ema50[i-1]: biasDir = 1
                elif c[i-1] < ema50[i-1]: biasDir = -1
        else:
            biasDir = 0
        biasPtsL = 20 if biasDir == 1 else (10 if biasDir == 0 else 0)
        biasPtsS = 20 if biasDir == -1 else (10 if biasDir == 0 else 0)

        volBase = volSma[i-1] if i > 0 else volSma[i]
        if volBase <= 0: volBase = volSma[i]
        rv = v[i] if not np.isnan(v[i]) else 0.0
        vp = (20 if rv > volBase * 1.2 else 12 if rv > volBase else 5) if symbolHasVol else 12

        lsc = de_l + ca_l + ag + vp + biasPtsL
        ssc = de_s + ca_s + ag + vp + biasPtsS
        cdok = i - last_sig >= p['cooldown']

        conf_l = long_rc and cdok and lsc >= p['min_score']
        conf_s = short_rc and cdok and ssc >= p['min_score']

        raw_long_sig = conf_l or conf_bull_flip
        raw_short_sig = conf_s or conf_bear_flip
        long_sig = raw_long_sig if not p.get('reverse_signal', False) else raw_short_sig
        short_sig = raw_short_sig if not p.get('reverse_signal', False) else raw_long_sig

        if conf_l or conf_s or conf_bull_flip or conf_bear_flip:
            last_sig = i

        # Now test entry conditions EXACTLY like Pine Script
        if long_sig and active_dir == 0:
            # Calculate SL and risk (same as Pine)
            sl_dist = rskd[i] * slm
            sl_wick = min(lo - rskd[i] * 0.25, src - rskd[i] * 0.5)
            sl_p = sl_wick if p['sl_mode'] == 'Wick-Anchored' else src - sl_dist
            risk_val = src - sl_p

            # Pine calcPositionQty with margin cap
            risk_cap = 10000 * 2.0 / 100.0
            sl_pts = abs(src - sl_p)
            pv = 1.0  # nz(syminfo.pointvalue, 1)
            rpc = sl_pts * pv
            risk_qty = max(1, np.floor(risk_cap / rpc)) if rpc > 0 else 1
            margin_qty = max(1, np.floor(10000 / src))
            qty = min(risk_qty, margin_qty)
            order_val = qty * src

            entry_ok = risk_val > 0
            margin_ok = order_val <= 10000  # 1x margin check

            # Track if this same bar generates a trade in backtest_v2
            in_bt2 = i in b2_first_bars

            if i <= warmup + 1000:  # Focus on first 1000 bars
                results.append({
                    'bar': i, 'long_sig': long_sig, 'short_sig': short_sig,
                    'conf_l': conf_l, 'conf_s': conf_s,
                    'flip': conf_bull_flip or conf_bear_flip,
                    'active_dir': active_dir,
                    'risk_ok': entry_ok, 'margin_ok': margin_ok,
                    'risk_val': float(risk_val), 'qty': int(qty),
                    'order_val': float(order_val),
                    'equity': 10000,
                    'rskd': float(rskd[i]),
                    'in_b2': in_bt2,
                })

            if entry_ok and margin_ok and active_dir == 0:
                active_dir = 1  # Simulate entry

    return results


if __name__ == '__main__':
    with open('best_params.json') as f:
        data = json.load(f)
    p = data['full_config']

    df = load_data()
    results = simulate_pine(df, p)

    print(f"\nSignal bars (first 1000 bars after warmup):")
    print(f"{'Bar':>5} {'Type':>6} {'ActDir':>6} {'RiskOK':>6} {'MarginOK':>8} {'Qty':>4} {'OrdVal':>10} {'InB2':>5}")
    print("-" * 60)
    entry_count = 0
    in_b2_count = 0
    for r in results:
        print(f"{r['bar']:5d} {'L' if r['long_sig'] else 'S':>6} {r['active_dir']:6d} {str(r['risk_ok']):>6} {str(r['margin_ok']):>8} {r['qty']:4d} {r['order_val']:10.0f} {str(r['in_b2']):>5}")
        if r['risk_ok'] and r['margin_ok']:
            entry_count += 1
        if r['in_b2']:
            in_b2_count += 1

    print(f"\nTrades that would execute (risk_ok + margin_ok + active_dir==0): {entry_count}")
    print(f"Backtest_v2 trades in same range: {in_b2_count}")

    # Also check: how many b2 trades in this range?
    trades_b2 = backtest_v2(df, p)
    wu = max(p['atr_len'] * 3, 60)
    b2_in_range = [t for t in trades_b2 if t['entry_bar'] <= 1000 + wu]
    print(f"Total backtest_v2 trades: {len(trades_b2)}")
    print(f"Backtest_v2 trades in first 1000 bars after warmup: {len(b2_in_range)}")

    b2_first_bars_in_range = set(t['entry_bar'] for t in trades_b2 if t['entry_bar'] <= 1000 + wu)
    our_bars = set(r['bar'] for r in results if r['risk_ok'] and r['margin_ok'])
    missing = b2_first_bars_in_range - our_bars
    if missing:
        print(f"\nB2 trades NOT in our simulation: {sorted(missing)}")
        for b in sorted(missing):
            t = [t for t in trades_b2 if t['entry_bar'] == b]
            if t:
                print(f"  Bar {b}: dir={t[0]['dir']}")
