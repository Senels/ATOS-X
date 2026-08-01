"""
Compare backtest_v2 trades with Pine Script entry conditions.
For each b2 trade bar, check if ALL Pine entry gates would pass.
"""
import json
import numpy as np
from optimize_ltm import load_data, backtest_v2


def check_entry_gates(df, p, trade_bars):
    """For each trade bar, check if Pine Script entry conditions pass."""
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

    GEO = {'Scalping':(2.5,0.2),'Balanced':(4.0,0.25),'Deep Trend':(6.0,0.3)}
    if p['band_preset']=='Custom': eb,es = p['base_mult'],p['band_step']
    else: eb,es = GEO[p['band_preset']]
    mults = [eb, eb*(1+es), eb*(1+2*es), eb*(1+3*es)]

    RSK = {'Conservative':(2.5,1.0,2.0,4.0),'Aggressive':(1.0,1.5,2.5,4.0),
           'Scalping':(0.8,0.8,1.5,2.0),'Balanced':(1.5,1.0,2.0,3.0)}
    if p['risk_preset']=='Custom': slm,tp1m,tp2m,tp3m = p['sl_mult'],p['tp1_mult'],p['tp2_mult'],p['tp3_mult']
    else: slm,tp1m,tp2m,tp3m = RSK[p['risk_preset']]

    gate_fails = {k:0 for k in ['signal','active_dir','entry_allowed','risk_le_zero','margin_exceeded']}
    first_fail_detail = []

    for bi in sorted(trade_bars)[:10]:
        i = bi
        src, hi, lo, op = c[i], h[i], l[i], o[i]

        # 1. Signal condition (long)
        raw_u = [src - trad[i]*mk for mk in mults]
        raw_l = [src + trad[i]*mk for mk in mults]

        # Simulate trend to check signal
        cur_trend = 1
        cur_ts = np.array([np.nan]*4)
        for j in range(1, i+1):
            if trad[j]==0 or np.isnan(trad[j]):
                continue
            src_j, hi_j, lo_j, op_j = c[j], h[j], l[j], o[j]
            u_j = [src_j - trad[j]*mk for mk in mults]
            l_j = [src_j + trad[j]*mk for mk in mults]
            if np.isnan(cur_ts[0]):
                cur_ts = np.array([u_j[k] if cur_trend==1 else l_j[k] for k in range(4)])
                continue
            fb = p['flip_band']-1
            fp = cur_ts[fb]
            dd = cur_trend==1 and not np.isnan(fp) and src_j < fp
            du = cur_trend==-1 and not np.isnan(fp) and src_j > fp
            if cur_trend==1:
                if dd: cur_trend=-1; cur_ts=np.array(l_j)
                else: cur_ts = np.maximum(u_j, cur_ts)
            else:
                if du: cur_trend=1; cur_ts=np.array(u_j)
                else: cur_ts = np.minimum(l_j, cur_ts)

        # Check signal at bar i (simplified: check long_rc)
        cur_trend_i = cur_trend
        lo_i, hi_i, op_i = lo, hi, op

        # Pending logic for this specific bar
        long_rc = False
        if cur_trend_i == 1 and not np.isnan(cur_ts[0]):
            td = 4 if lo_i <= cur_ts[3] else 3 if lo_i <= cur_ts[2] else 2 if lo_i <= cur_ts[1] else 1 if lo_i <= cur_ts[0] else 0
            pending = td > 0  # simplified: if touch on this bar
            long_rc = pending and cur_trend_i==1 and not np.isnan(cur_ts[0]) and src > cur_ts[0] and src > op_i

        has_signal = long_rc  # simplified signal check
        if not has_signal:
            gate_fails['signal'] += 1
            first_fail_detail.append((i, 'signal', ''))
            continue

        # 2. activeDir == 0 (always true since this is a new signal)
        # We're checking each trade bar independently

        # 3. entryAllowed
        is_warmed_up = i >= warmup
        session_ok = True  # user says 7/24
        trading_halted = False  # first trade
        entry_allowed = is_warmed_up and session_ok and not trading_halted
        if not entry_allowed:
            gate_fails['entry_allowed'] += 1
            first_fail_detail.append((i, 'entry_allowed', ''))
            continue

        # 4. riskLong > 0
        sl_wick = min(lo_i - rskd[i]*0.25, src - rskd[i]*0.5)
        sl_p = sl_wick
        risk_val = src - sl_p
        if risk_val <= 0:
            gate_fails['risk_le_zero'] += 1
            first_fail_detail.append((i, 'risk_le_zero', f'rskd={rskd[i]:.4f} src={src:.2f} lo={lo_i:.2f} sl={sl_p:.4f}'))
            continue

        # 5. calcPositionQty margin check
        risk_cap = 10000 * 2.0 / 100.0
        sl_pts = abs(src - sl_p)
        pv = 1.0
        rpc = sl_pts * pv
        risk_qty = max(1, np.floor(risk_cap / rpc)) if rpc > 0 else 1
        margin_qty = max(1, np.floor(10000 / src))
        qty = min(risk_qty, margin_qty)
        order_val = qty * src

        if order_val > 10000:
            gate_fails['margin_exceeded'] += 1
            first_fail_detail.append((i, 'margin_exceeded', f'qty={qty} risk_qty={risk_qty} margin_qty={margin_qty} ord_val={order_val:.0f}'))
            continue

        first_fail_detail.append((i, 'ALL_PASS', f'qty={qty} ord_val={order_val:.0f} risk={risk_val:.4f}'))

    return gate_fails, first_fail_detail


if __name__ == '__main__':
    with open('best_params.json') as f:
        data = json.load(f)
    p = data['full_config']

    df = load_data()
    trades = backtest_v2(df, p)
    trade_bars = [t['entry_bar'] for t in trades]

    print(f"Total backtest_v2 trades: {len(trades)}")
    print(f"Checking entry gates for each trade bar...\n")

    fails, details = check_entry_gates(df, p, trade_bars)

    print(f"Gate failure counts:")
    for k, v in fails.items():
        print(f"  {k}: {v}")

    print(f"\nFirst 10 bars detail:")
    for d in details:
        status = "✅" if d[1]=='ALL_PASS' else "❌"
        print(f"  {status} Bar {d[0]}: {d[1]} {d[2]}")
