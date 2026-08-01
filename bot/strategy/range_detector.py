"""
Range Detector Strategy
Signature parameters: rd_length, rd_mult, rd_atr_len
Detects volatility-based ranges and generates break-out signals.
"""
import numpy as np
import pandas as pd

def range_detector(df, p):
    """
    p = {
        'rd_length': int,      # range lookback
        'rd_mult': float,      # ATR multiplier for bands
        'rd_atr_len': int,     # ATR length
        'rd_smooth': int,      # smoothing period (optional)
        'sl_atr': float,       # SL in ATR
        'tp_atr': float,       # TP in ATR
        'leverage': float,     # leverage
        'position_pct': float, # % of capital per trade
    }
    Returns list of trade dicts and equity curve.
    """
    o = df['open'].values
    h = df['high'].values
    l = df['low'].values
    c = df['close'].values
    v = df['volume'].values
    n = len(c)

    length = p.get('rd_length', 20)
    mult = p.get('rd_mult', 2.0)
    atr_len = p.get('rd_atr_len', 14)
    smooth = p.get('rd_smooth', 1)
    sl_atr = p.get('sl_atr', 1.5)
    tp_atr = p.get('tp_atr', 3.0)
    leverage = p.get('leverage', 1.0)
    pos_pct = p.get('position_pct', 10.0) / 100.0

    # ATR
    tr = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i-1]), abs(l[i] - c[i-1]))
    tr[0] = h[0] - l[0]

    atr = np.zeros(n)
    atr[0] = tr[0]
    for i in range(1, n):
        atr[i] = (atr[i-1] * (atr_len - 1) + tr[i]) / atr_len

    # Range: highest high / lowest low over length
    hh = np.zeros(n)
    ll = np.zeros(n)
    for i in range(n):
        start = max(0, i - length + 1)
        hh[i] = np.max(h[start:i+1])
        ll[i] = np.min(l[start:i+1])

    # Center line (smoothed)
    center = (hh + ll) / 2
    if smooth > 1:
        # Simple moving average
        kernel = np.ones(smooth) / smooth
        center = np.convolve(center, kernel, mode='same')
        center[:smooth] = center[smooth]

    # Bands
    upper = center + atr * mult
    lower = center - atr * mult

    # Range width as % of price
    range_width = (hh - ll) / center * 100

    warmup = max(length * 2, atr_len * 3, 60)

    # Trade state
    in_long = False
    in_short = False
    entry_price = 0.0
    entry_bar = 0
    sl_price = 0.0
    tp_price = 0.0
    capital = 10000.0
    peak = capital

    trades = []
    eq = [capital]

    for i in range(1, n):
        if i < warmup or atr[i] == 0 or np.isnan(atr[i]):
            eq.append(capital)
            continue

        # Signals: break-out of range bands
        long_sig = not in_long and not in_short and c[i] > upper[i] and c[i] > o[i]
        short_sig = not in_short and not in_long and c[i] < lower[i] and c[i] < o[i]

        # Check exits
        if in_long:
            if l[i] <= sl_price:
                exit_p = sl_price
                pnl = (exit_p - entry_price) / entry_price * leverage * capital * pos_pct
                capital += pnl
                trades.append({
                    'entry': entry_price, 'exit': exit_p, 'pnl': pnl,
                    'pnl_pct': (exit_p - entry_price) / entry_price * 100 * leverage,
                    'dir': 'LONG', 'entry_bar': int(entry_bar), 'exit_bar': i,
                    'win': exit_p >= tp_price or pnl > 0
                })
                in_long = False
            elif h[i] >= tp_price:
                exit_p = tp_price
                pnl = (exit_p - entry_price) / entry_price * leverage * capital * pos_pct
                capital += pnl
                trades.append({
                    'entry': entry_price, 'exit': exit_p, 'pnl': pnl,
                    'pnl_pct': (exit_p - entry_price) / entry_price * 100 * leverage,
                    'dir': 'LONG', 'entry_bar': int(entry_bar), 'exit_bar': i,
                    'win': True
                })
                in_long = False
            elif short_sig:
                exit_p = c[i]
                pnl = (exit_p - entry_price) / entry_price * leverage * capital * pos_pct
                capital += pnl
                trades.append({
                    'entry': entry_price, 'exit': exit_p, 'pnl': pnl,
                    'pnl_pct': (exit_p - entry_price) / entry_price * 100 * leverage,
                    'dir': 'LONG', 'entry_bar': int(entry_bar), 'exit_bar': i,
                    'win': pnl > 0
                })
                in_long = False

        if in_short:
            if h[i] >= sl_price:
                exit_p = sl_price
                pnl = (entry_price - exit_p) / entry_price * leverage * capital * pos_pct
                capital += pnl
                trades.append({
                    'entry': entry_price, 'exit': exit_p, 'pnl': pnl,
                    'pnl_pct': (entry_price - exit_p) / entry_price * 100 * leverage,
                    'dir': 'SHORT', 'entry_bar': int(entry_bar), 'exit_bar': i,
                    'win': exit_p <= tp_price or pnl > 0
                })
                in_short = False
            elif l[i] <= tp_price:
                exit_p = tp_price
                pnl = (entry_price - exit_p) / entry_price * leverage * capital * pos_pct
                capital += pnl
                trades.append({
                    'entry': entry_price, 'exit': exit_p, 'pnl': pnl,
                    'pnl_pct': (entry_price - exit_p) / entry_price * 100 * leverage,
                    'dir': 'SHORT', 'entry_bar': int(entry_bar), 'exit_bar': i,
                    'win': True
                })
                in_short = False
            elif long_sig:
                exit_p = c[i]
                pnl = (entry_price - exit_p) / entry_price * leverage * capital * pos_pct
                capital += pnl
                trades.append({
                    'entry': entry_price, 'exit': exit_p, 'pnl': pnl,
                    'pnl_pct': (entry_price - exit_p) / entry_price * 100 * leverage,
                    'dir': 'SHORT', 'entry_bar': int(entry_bar), 'exit_bar': i,
                    'win': pnl > 0
                })
                in_short = False

        # Entry
        if long_sig and not in_long and not in_short:
            entry_price = c[i]
            entry_bar = i
            risk = atr[i] * sl_atr
            sl_price = entry_price - risk
            tp_price = entry_price + atr[i] * tp_atr
            in_long = True

        if short_sig and not in_short and not in_long:
            entry_price = c[i]
            entry_bar = i
            risk = atr[i] * sl_atr
            sl_price = entry_price + risk
            tp_price = entry_price - atr[i] * tp_atr
            in_short = True

        peak = max(peak, capital)
        eq.append(capital)

    return trades, eq
