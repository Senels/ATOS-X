"""
Compare Python backtest_v2 with Pine Script logic bar-by-bar.
Pinpoint the first divergence.
"""
import json
import numpy as np
from optimize_ltm import load_data, backtest_v2


def extract_patterns(p):
    GEO = {'Scalping': (2.5, 0.2), 'Balanced': (4.0, 0.25), 'Deep Trend': (6.0, 0.3)}
    if p['band_preset'] == 'Custom':
        eb, es = p['base_mult'], p['band_step']
    else:
        eb, es = GEO[p['band_preset']]
    m = [eb, eb * (1 + es), eb * (1 + 2 * es), eb * (1 + 3 * es)]
    return m


def compute_atr(h, l, c, period):
    n = len(c)
    tr = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    atr = np.zeros(n)
    atr[0] = tr[0]
    for i in range(1, n):
        if i >= period:
            atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
        else:
            atr[i] = np.mean(tr[1:i + 1]) if i > 1 else tr[1]
    return atr


def backtest_v2_debug(df, p, log_until=None):
    """Same as backtest_v2 but with detailed logging up to N bars."""
    o = df['open'].values; h = df['high'].values; l = df['low'].values
    c = df['close'].values; v = df['volume'].values; n = len(c)
    m = extract_patterns(p)

    trad = compute_atr(h, l, c, p['atr_len'])
    rskd = compute_atr(h, l, c, p['atr_len_risk'])
    warmup = max(p['atr_len'] * 3, 60)

    ema50 = np.zeros(n)
    ema50[0] = c[0]
    alpha = 2 / 51
    for i in range(1, n):
        ema50[i] = (c[i] - ema50[i - 1]) * alpha + ema50[i - 1]

    volSma = np.zeros(n)
    for i in range(n):
        if i < 19:
            volSma[i] = np.nanmean(v[max(0, i - 19):i + 1])
        else:
            volSma[i] = (volSma[i - 1] * 19 + v[i]) / 20
    symbolHasVol = np.nansum(v) > 0

    RSK = {'Conservative': (2.5, 1.0, 2.0, 4.0), 'Aggressive': (1.0, 1.5, 2.5, 4.0),
           'Scalping': (0.8, 0.8, 1.5, 2.0), 'Balanced': (1.5, 1.0, 2.0, 3.0)}
    if p['risk_preset'] == 'Custom':
        slm, tp1m, tp2m, tp3m = p['sl_mult'], p['tp1_mult'], p['tp2_mult'], p['tp3_mult']
    else:
        slm, tp1m, tp2m, tp3m = RSK[p['risk_preset']]

    trend = np.ones(n, dtype=int)
    ts = np.full((n, 4), np.nan)
    trend_start = np.zeros(n, dtype=int)
    bars_in_trend = np.zeros(n, dtype=int)

    cur_trend = 1
    cur_ts = np.array([np.nan, np.nan, np.nan, np.nan])
    cur_start = 0
    pend = {'long': 0, 'short': 0, 'ld': 0, 'sd': 0}
    last_sig = -10000
    act = {'dir': 0, 'entry': 0.0, 'sl': 0.0, 'tp1': 0.0, 'tp2': 0.0, 'tp3': 0.0,
           'bar': -100, 'tp1r': False, 'tp2r': False, 'tp3r': False, 'be': False}
    trades = []

    limit = n if log_until is None else min(log_until, n)
    logs = []

    for i in range(limit):
        if i == 0 or trad[i] == 0 or np.isnan(trad[i]):
            if i > 0:
                trend[i] = trend[i - 1]
            continue

        src, hi, lo, op = c[i], h[i], l[i], o[i]

        raw_u = [src - trad[i] * mk for mk in m]
        raw_l = [src + trad[i] * mk for mk in m]

        if np.isnan(cur_ts[0]):
            cur_ts = np.array([raw_u[j] if cur_trend == 1 else raw_l[j] for j in range(4)])
            trend[i], ts[i] = cur_trend, cur_ts
            continue

        fb = p['flip_band'] - 1
        fp = cur_ts[fb]
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

        trend[i], ts[i], trend_start[i] = cur_trend, cur_ts, cur_start
        bars_in_trend[i] = i - cur_start

        if i < warmup:
            continue

        prev_trend = trend[i - 1]
        flip_bar = trend[i] != prev_trend

        if flip_bar:
            for k in ['long', 'short']:
                pend[k] = 0; pend[k + 'd'] = 0

        conf_bull_flip = flip_bar and cur_trend == 1
        conf_bear_flip = flip_bar and cur_trend == -1

        for k in ['long', 'short']:
            pend[k] = max(pend[k] - 1, 0)
            if pend[k] == 0:
                pend[k + 'd'] = 0

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
        ca_l = 20 if cl > 0.7 else (12 if cl > 0.5 else 5)
        ca_s = 20 if cs > 0.7 else (12 if cs > 0.5 else 5)
        ag = 15 if 10 <= bars_in_trend[i] <= 150 else (8 if bars_in_trend[i] < 10 else 5)

        biasDir = 0
        if i > 0 and not np.isnan(c[i - 1]) and not np.isnan(ema50[i - 1]):
            if c[i - 1] > ema50[i - 1]:
                biasDir = 1
            elif c[i - 1] < ema50[i - 1]:
                biasDir = -1
        biasPtsL = 20 if biasDir == 1 else (10 if biasDir == 0 else 0)
        biasPtsS = 20 if biasDir == -1 else (10 if biasDir == 0 else 0)

        volBase = volSma[i - 1] if i > 0 else volSma[i]
        if volBase <= 0:
            volBase = volSma[i]
        rv = v[i] if not np.isnan(v[i]) else 0.0
        vp = (20 if rv > volBase * 1.2 else (12 if rv > volBase else 5)) if symbolHasVol else 12

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

        # Log signals (before trade management)
        if long_sig or short_sig:
            logs.append({
                'bar': i, 'trend': cur_trend, 'flip_bar': flip_bar,
                'conf_l': conf_l, 'conf_s': conf_s,
                'conf_bull_flip': conf_bull_flip, 'conf_bear_flip': conf_bear_flip,
                'lsc': lsc, 'ssc': ssc, 'cdok': cdok, 'long_rc': long_rc, 'short_rc': short_rc,
                'active_dir': act['dir'], 'pend_l': pend['long'], 'pend_s': pend['short'],
                'long_sig': long_sig, 'short_sig': short_sig,
                'src': float(src), 'cur_ts0': float(cur_ts[0]),
                'src_gt_ts0': bool(src > cur_ts[0]) if not np.isnan(cur_ts[0]) else None,
                'src_gt_op': bool(src > op),
                'src_lt_ts0': bool(src < cur_ts[0]) if not np.isnan(cur_ts[0]) else None,
                'src_lt_op': bool(src < op),
                'ld': pend['ld'], 'sd': pend['sd'],
                'de_l': de_l, 'de_s': de_s, 'ca_l': ca_l, 'ca_s': ca_s,
                'ag': ag, 'vp': vp, 'biasL': biasPtsL, 'biasS': biasPtsS,
            })

        # Trade management (same as backtest_v2)
        sl_hit = tp1_hit = tp2_hit = tp3_hit = False
        if act['dir'] != 0 and i > act['bar']:
            sl_hit = (act['dir'] == 1 and lo <= act['sl']) or (act['dir'] == -1 and hi >= act['sl'])
            tp1_hit = (act['dir'] == 1 and hi >= act['tp1']) or (act['dir'] == -1 and lo <= act['tp1'])
            tp2_hit = (act['dir'] == 1 and hi >= act['tp2']) or (act['dir'] == -1 and lo <= act['tp2'])
            tp3_hit = (act['dir'] == 1 and hi >= act['tp3']) or (act['dir'] == -1 and lo <= act['tp3'])
            if tp1_hit and not act['tp1r'] and not sl_hit:
                act['tp1r'] = True
                if p['be'] and not act['be']:
                    act['sl'] = act['entry']; act['be'] = True
            if tp2_hit and not act['tp2r'] and not sl_hit:
                act['tp2r'] = True
            if tp3_hit and not act['tp3r'] and not sl_hit:
                act['tp3r'] = True
            if sl_hit or tp3_hit:
                act = {'dir': 0, 'entry': 0.0, 'sl': 0.0, 'tp1': 0.0, 'tp2': 0.0, 'tp3': 0.0,
                       'bar': -100, 'tp1r': False, 'tp2r': False, 'tp3r': False, 'be': False}

        rev_s = (short_sig and act['dir'] == 1) or (long_sig and act['dir'] == -1)
        if rev_s:
            act = {'dir': 0, 'entry': 0.0, 'sl': 0.0, 'tp1': 0.0, 'tp2': 0.0, 'tp3': 0.0,
                   'bar': -100, 'tp1r': False, 'tp2r': False, 'tp3r': False, 'be': False}

        if act['dir'] == 0 and long_sig:
            sl_dist = rskd[i] * slm
            sl_wick = min(lo - rskd[i] * 0.25, src - rskd[i] * 0.5)
            sl_p = sl_wick if p['sl_mode'] == 'Wick-Anchored' else src - sl_dist
            risk = src - sl_p
            if risk > 0:
                act = {'dir': 1, 'entry': src, 'sl': sl_p,
                       'tp1': src + risk * tp1m, 'tp2': src + risk * tp2m, 'tp3': src + risk * tp3m,
                       'bar': i, 'tp1r': False, 'tp2r': False, 'tp3r': False, 'be': False}

        if act['dir'] == 0 and short_sig:
            sl_dist = rskd[i] * slm
            sl_wick = max(hi + rskd[i] * 0.25, src + rskd[i] * 0.5)
            sl_p = sl_wick if p['sl_mode'] == 'Wick-Anchored' else src + sl_dist
            risk = sl_p - src
            if risk > 0:
                act = {'dir': -1, 'entry': src, 'sl': sl_p,
                       'tp1': src - risk * tp1m, 'tp2': src - risk * tp2m, 'tp3': src - risk * tp3m,
                       'bar': i, 'tp1r': False, 'tp2r': False, 'tp3r': False, 'be': False}

    return trades, logs


if __name__ == '__main__':
    with open('best_params.json') as f:
        data = json.load(f)
    p = data['full_config']
    print(f"Parameters:")
    print(f"  band_preset={p['band_preset']} base_mult={p['base_mult']} band_step={p['band_step']}")
    print(f"  atr_len={p['atr_len']} flip_band={p['flip_band']}")
    print(f"  min_score={p['min_score']} retest_window={p['retest_window']} cooldown={p['cooldown']}")
    print(f"  risk_preset={p['risk_preset']} sl_mode={p['sl_mode']} atr_len_risk={p['atr_len_risk']}")

    df = load_data()
    print(f"Data: {len(df)} bars")

    # Get standard backtest first
    trades_std = backtest_v2(df, p)
    print(f"\nStandard backtest: {len(trades_std)} trades")

    if trades_std:
        first_trade = trades_std[0]
        print(f"\nFirst trade: bar {first_trade['entry_bar']}, dir={first_trade['dir']}, entry={first_trade['entry']:.2f}")

        # Run debug backtest up to first trade bar + 20
        log_end = first_trade['entry_bar'] + 20
        _, logs = backtest_v2_debug(df, p, log_until=log_end)

        # Show log entries around the first trade
        entry_bar = first_trade['entry_bar']
        print(f"\nSignal logs around first trade (bar {entry_bar}):")
        nearby = [l for l in logs if abs(l['bar'] - entry_bar) <= 5]
        for log in nearby:
            print(f"  Bar {log['bar']:5d}: trend={log['trend']:+d} flip={log['flip_bar']} "
                  f"act_dir={log['active_dir']:+d} "
                  f"long_sig={log['long_sig']} short_sig={log['short_sig']} "
                  f"conf_l={log['conf_l']} conf_s={log['conf_s']} "
                  f"cf_bull={log['conf_bull_flip']} cf_bear={log['conf_bear_flip']} "
                  f"lsc={log['lsc']} ssc={log['ssc']} cdok={log['cdok']} "
                  f"long_rc={log['long_rc']} short_rc={log['short_rc']} "
                  f"src>ts0={log['src_gt_ts0']} src>op={log['src_gt_op']} "
                  f"pend_l={log['pend_l']} pend_s={log['pend_s']} "
                  f"de_l={log['de_l']} ca_l={log['ca_l']} ag={log['ag']} vp={log['vp']} biasL={log['biasL']} "
                  f"de_s={log['de_s']} ca_s={log['ca_s']} biasS={log['biasS']}")

        if len(nearby) == 0:
            print(f"  (no signals found within 5 bars of entry)")

        # Check all log entries that have long_sig=true
        sig_bars = [l for l in logs if l['long_sig'] or l['short_sig']]
        print(f"\nTotal signal bars within first {log_end}: {len(sig_bars)}")
        for sig in sig_bars[:10]:
            print(f"  Bar {sig['bar']}: dir={sig['trend']:+d} flip={sig['flip_bar']} "
                  f"long={sig['long_sig']} short={sig['short_sig']} active_dir={sig['active_dir']}")
