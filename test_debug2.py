"""
Deep comparison: backtest_v2 vs Pine parity.
Track every signal type and find root cause of divergence.
"""
import json
import numpy as np
from collections import defaultdict
from optimize_ltm import load_data, backtest_v2


def analyze_trend_and_signals(df, p):
    """Replicate backtest_v2 signal detection and compare with Pine parity."""
    o = df['open'].values; h = df['high'].values; l = df['low'].values
    c = df['close'].values; v = df['volume'].values; n = len(c)

    GEO = {'Scalping':(2.5,0.2),'Balanced':(4.0,0.25),'Deep Trend':(6.0,0.3)}
    if p['band_preset'] == 'Custom':
        eb, es = p['base_mult'], p['band_step']
    else:
        eb, es = GEO[p['band_preset']]
    m = [eb, eb*(1+es), eb*(1+2*es), eb*(1+3*es)]

    def atr_series(period):
        tr = np.zeros(n)
        for i in range(1, n):
            tr[i] = max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1]))
        atr = np.zeros(n); atr[0] = tr[0]
        for i in range(1, n):
            if i >= period:
                atr[i] = (atr[i-1]*(period-1) + tr[i]) / period
            else:
                atr[i] = np.mean(tr[1:i+1]) if i > 1 else tr[1]
        return atr

    trad = atr_series(p['atr_len'])
    rskd = atr_series(p['atr_len_risk'])
    warmup = max(p['atr_len'] * 3, 60)

    ema50 = np.zeros(n)
    ema50[0] = c[0]
    alpha = 2 / 51
    for i in range(1, n):
        ema50[i] = (c[i] - ema50[i-1]) * alpha + ema50[i-1]

    volSma = np.zeros(n)
    for i in range(n):
        if i < 19:
            volSma[i] = np.nanmean(v[max(0, i-19):i+1])
        else:
            volSma[i] = (volSma[i-1] * 19 + v[i]) / 20
    cumVol = np.nansum(v)
    symbolHasVol = cumVol > 0

    trend = np.full(n, np.nan)
    trend[0] = 1
    cur_ts_arr = np.full((n, 4), np.nan)
    cur_start_arr = np.zeros(n, dtype=int)

    signal_log = []
    signal_by_type = defaultdict(list)

    for i in range(n):
        if i == 0 or trad[i] == 0 or np.isnan(trad[i]):
            if i > 0:
                trend[i] = trend[i-1]
            continue

        src, hi, lo = c[i], h[i], l[i]
        raw_u = [src - trad[i] * mk for mk in m]
        raw_l = [src + trad[i] * mk for mk in m]

        if np.isnan(cur_ts_arr[i-1][0]):
            cur_trend = trend[i-1] if not np.isnan(trend[i-1]) else 1
            cur_ts_arr[i] = np.array([raw_u[j] if cur_trend == 1 else raw_l[j] for j in range(4)])
            trend[i] = cur_trend
            cur_start_arr[i] = i
            continue

        fb = p['flip_band'] - 1
        fp = cur_ts_arr[i-1][fb]
        prev_trend = int(trend[i-1])

        do_flip_dn = prev_trend == 1 and not np.isnan(fp) and src < fp
        do_flip_up = prev_trend == -1 and not np.isnan(fp) and src > fp

        if prev_trend == 1:
            if do_flip_dn:
                trend[i] = -1
                cur_ts_arr[i] = np.array(raw_l)
                cur_start_arr[i] = i
            else:
                trend[i] = 1
                cur_ts_arr[i] = np.maximum(raw_u, cur_ts_arr[i-1])
                cur_start_arr[i] = cur_start_arr[i-1]
        else:
            if do_flip_up:
                trend[i] = 1
                cur_ts_arr[i] = np.array(raw_u)
                cur_start_arr[i] = i
            else:
                trend[i] = -1
                cur_ts_arr[i] = np.minimum(raw_l, cur_ts_arr[i-1])
                cur_start_arr[i] = cur_start_arr[i-1]

        if i < warmup:
            continue

        cur_trend = int(trend[i])
        cur_ts = cur_ts_arr[i]
        bars_in_trend = i - cur_start_arr[i]
        flip_bar = cur_trend != prev_trend

        # Signal detection (exact backtest_v2 logic)
        pend = {'long': 0, 'short': 0, 'ld': 0, 'sd': 0}

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
        cl = (src - lo) / rng
        cs = (hi - src) / rng
        dp = {2: 25, 3: 18, 1: 15, 4: 10}
        de_l = dp.get(pend['ld'], 0)
        de_s = dp.get(pend['sd'], 0)
        ca_l = 20 if cl > 0.7 else (12 if cl > 0.5 else 5)
        ca_s = 20 if cs > 0.7 else (12 if cs > 0.5 else 5)
        ag = 15 if 10 <= bars_in_trend <= 150 else (8 if bars_in_trend < 10 else 5)

        biasDir = 0
        if i > 0 and not np.isnan(c[i-1]) and not np.isnan(ema50[i-1]):
            if c[i-1] > ema50[i-1]:
                biasDir = 1
            elif c[i-1] < ema50[i-1]:
                biasDir = -1
        biasPtsL = 20 if biasDir == 1 else (10 if biasDir == 0 else 0)
        biasPtsS = 20 if biasDir == -1 else (10 if biasDir == 0 else 0)

        volBase = volSma[i-1] if i > 0 else volSma[i]
        if volBase <= 0:
            volBase = volSma[i]
        rv = v[i] if not np.isnan(v[i]) else 0.0
        if symbolHasVol:
            vp = 20 if rv > volBase * 1.2 else (12 if rv > volBase else 5)
        else:
            vp = 12

        lsc = de_l + ca_l + ag + vp + biasPtsL
        ssc = de_s + ca_s + ag + vp + biasPtsS
        cdok = True  # simplified for now

        # Flip signals (backtest_v2 logic at ~line 480)
        conf_bull_flip = flip_bar and cur_trend == 1 and bars_in_trend <= 3
        conf_bear_flip = flip_bar and cur_trend == -1 and bars_in_trend <= 3

        # Long-legged flip handling (bars_in_trend > 3)
        # In backtest_v2, these use a different path
        if flip_bar and bars_in_trend > 3:
            conf_bull_flip = cur_trend == 1 and long_rc
            conf_bear_flip = cur_trend == -1 and short_rc

        if conf_l := (long_rc and cdok and lsc >= p['min_score']):
            signal_by_type['reclaim_long'].append(i)
        if conf_s := (short_rc and cdok and ssc >= p['min_score']):
            signal_by_type['reclaim_short'].append(i)
        if conf_bull_flip:
            signal_by_type['bull_flip'].append(i)
        if conf_bear_flip:
            signal_by_type['bear_flip'].append(i)

    print(f"Signal breakdown:")
    for k, v in signal_by_type.items():
        print(f"  {k}: {len(v)}")
    print(f"  Total unique bars: {len(set(sum(signal_by_type.values(), [])))}")


if __name__ == '__main__':
    with open('best_params.json') as f:
        data = json.load(f)
    p = data['full_config']
    print(f"Params: {json.dumps(data['best_params'], indent=2)}")

    df = load_data()
    print(f"Data: {len(df)} bars, {df.index[0]} -> {df.index[-1]}")

    analyze_trend_and_signals(df, p)

    trades = backtest_v2(df, p)
    print(f"\nStandard backtest: {len(trades)} trades")
    dirs = [t['dir'] for t in trades]
    print(f"  L={sum(1 for d in dirs if d=='L')}, S={sum(1 for d in dirs if d=='S')}")
    for t in trades[:5]:
        print(f"  Bar {t['bar']} {t['dir']}: PnL={t['pnl_pct']:+.2f}%")
