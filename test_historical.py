"""
Historical 15-day XAUUSD 1m backtest of Trend Strength indicator
Simulates multi-timeframe scoring logic in Python
"""

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

def score_ohlc(close, htf_open, htf_close, htf_high, htf_low, midline_mode=False):
    body_high = max(htf_close, htf_open)
    body_low  = min(htf_close, htf_open)
    midline   = body_low + (body_high - body_low) / 2
    if midline_mode:
        return 1 if close >= midline else -1
    if close >= htf_high:
        return 3
    if close <= htf_low:
        return -3
    if close >= body_high:
        return 2
    if close <= body_low:
        return -2
    return 1 if close >= midline else -1

print("=" * 70)
print("XAUUSD TREND STRENGTH INDICATOR - 15 DAY HISTORICAL TEST")
print("=" * 70)

# Fetch 1m data in chunks (Yahoo limit: ~7 days per request)
print("\nDownloading 15 days of XAUUSD 1-minute data...")
end = datetime.now()

# Download in 7-day chunks
chunks = []
chunk_end = end
for i in range(3):  # up to 21 days
    chunk_start = chunk_end - timedelta(days=7)
    try:
        chunk = yf.download("GC=F", start=chunk_start.strftime("%Y-%m-%d"),
                            end=chunk_end.strftime("%Y-%m-%d"), interval="1m", progress=False)
        if chunk is not None and not chunk.empty:
            if isinstance(chunk.columns, pd.MultiIndex):
                chunk.columns = [c[0] for c in chunk.columns]
            chunks.append(chunk)
            print(f"  Chunk {i+1}: {len(chunk)} bars ({chunk_start.date()} to {chunk_end.date()})")
    except Exception as e:
        print(f"  Chunk {i+1} failed: {e}")
    chunk_end = chunk_start

if not chunks:
    print("No data received from yfinance. Trying ccxt fallback...")
    try:
        import ccxt
        exchange = ccxt.binance()
        ohlcv = exchange.fetch_ohlcv("XAUUSD", "1m", limit=21600)
        df = pd.DataFrame(ohlcv, columns=['timestamp','Open','High','Low','Close','Volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        chunks = [df]
    except Exception as e2:
        print(f"ccxt fallback also failed: {e2}")
        exit(1)

df = pd.concat(chunks).sort_index()
df = df[~df.index.duplicated(keep='first')]

print(f"\nTotal downloaded: {len(df)} bars")
print(f"Date range: {df.index[0]} to {df.index[-1]}")

# Keep only last 15 calendar days
cutoff = df.index[-1] - timedelta(days=15)
df = df[df.index >= cutoff]
print(f"Using last 15 days: {len(df)} bars ({df.index[0]} to {df.index[-1]})")

# =========================================
# HIGHER TIMEFRAME RESAMPLING
# =========================================
tf_configs = [
    ("1m",   "1T",   True),    # TF1 row 7 (top)
    ("5m",   "5T",   True),    # TF2 row 6
    ("15m",  "15T",  True),    # TF3 row 5
    ("1h",   "1h",   True),    # TF4 row 4
    ("4h",   "4h",   True),    # TF5 row 3
    ("1D",   "1D",   True),    # TF6 row 2
    ("1W",   "1W",   True),    # TF7 row 1 (bottom)
]

def make_htf_scores(df_1m):
    """Compute HTF scores for each 1m bar"""
    scores = {}
    for name, rule, enabled in tf_configs:
        if not enabled:
            continue
        if rule == "1T":
            # 1m - use current bar
            s_ohlc = []
            s_mid  = []
            for idx, row in df_1m.iterrows():
                s_ohlc.append(score_ohlc(row['Close'], row['Open'], row['Close'], row['High'], row['Low'], False))
                s_mid.append(score_ohlc(row['Close'], row['Open'], row['Close'], row['High'], row['Low'], True))
            scores[f'{name}_ohlc'] = s_ohlc
            scores[f'{name}_mid']  = s_mid
        else:
            # Resample: use the **previous completed** HTF bar
            resampled = df_1m.resample(rule, label='right', closed='right').agg({
                'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last'
            }).dropna()

            s_ohlc = []
            s_mid  = []
            prev_htf = None
            prev_idx = None
            htf_iter = iter(resampled.iterrows())

            try:
                prev_idx, prev_htf = next(htf_iter)
            except StopIteration:
                prev_htf = None

            for idx, row in df_1m.iterrows():
                # Check if we've crossed into a new HTF period
                while prev_htf is not None and idx >= prev_idx:
                    try:
                        prev_idx, prev_htf = next(htf_iter)
                    except StopIteration:
                        prev_htf = None
                        prev_idx = None
                        break

                if prev_htf is not None:
                    o, h, l, c = prev_htf['Open'], prev_htf['High'], prev_htf['Low'], prev_htf['Close']
                else:
                    o, h, l, c = row['Open'], row['High'], row['Low'], row['Close']

                s_ohlc.append(score_ohlc(row['Close'], o, c, h, l, False))
                s_mid.append(score_ohlc(row['Close'], o, c, h, l, True))

            scores[f'{name}_ohlc'] = s_ohlc
            scores[f'{name}_mid']  = s_mid

    return pd.DataFrame(scores, index=df_1m.index)

scores_df = make_htf_scores(df)

# =========================================
# STATISTICS
# =========================================
print(f"\n{'='*70}")
print("TREND SCORE DISTRIBUTION (OHLC+Midline Mode)")
print(f"{'='*70}")

for name, _, enabled in tf_configs:
    if not enabled:
        continue
    col = f'{name}_ohlc'
    if col not in scores_df.columns:
        continue
    vals = scores_df[col]
    total = len(vals)
    dist = vals.value_counts().sort_index()
    pct_positive = (vals > 0).sum() / total * 100
    pct_negative = (vals < 0).sum() / total * 100
    pct_neutral  = (vals == 0).sum() / total * 100
    print(f"\n  {name:>4} ({total:>6} bars):")
    print(f"       Bullish: {pct_positive:>5.1f}%  Bearish: {pct_negative:>5.1f}%  Neutral: {pct_neutral:>5.1f}%")
    for score_val in [3, 2, 1, -1, -2, -3]:
        cnt = (vals == score_val).sum()
        if cnt > 0:
            print(f"       Score {score_val:>2}: {cnt:>6} bars ({cnt/total*100:>5.1f}%)")

print(f"\n{'='*70}")
print("TREND SCORE DISTRIBUTION (Above/Below Midline Mode)")
print(f"{'='*70}")

for name, _, enabled in tf_configs:
    if not enabled:
        continue
    col = f'{name}_mid'
    if col not in scores_df.columns:
        continue
    vals = scores_df[col]
    total = len(vals)
    pct_bullish = (vals == 1).sum() / total * 100
    pct_bearish = (vals == -1).sum() / total * 100
    print(f"  {name:>4}: Bullish {pct_bullish:>5.1f}%  Bearish {pct_bearish:>5.1f}%")

# =========================================
# EXTERNAL SIGNAL ANALYSIS
# =========================================
print(f"\n{'='*70}")
print("EXTERNAL TREND SIGNAL ANALYSIS")
print(f"{'='*70}")

for threshold in [1, 2, 3]:
    signals = []
    for idx in scores_df.index:
        ok_bull = True
        ok_bear = True
        for name, _, enabled in tf_configs:
            if not enabled:
                continue
            s = scores_df.at[idx, f'{name}_ohlc']
            if s < threshold:
                ok_bull = False
            if s > -threshold:
                ok_bear = False
        if ok_bull:
            signals.append(1)
        elif ok_bear:
            signals.append(-1)
        else:
            signals.append(0)

    bullish_pct = sum(1 for s in signals if s == 1) / len(signals) * 100
    bearish_pct = sum(1 for s in signals if s == -1) / len(signals) * 100
    neutral_pct = sum(1 for s in signals if s == 0) / len(signals) * 100

    # Count signal transitions
    transitions = 0
    for i in range(1, len(signals)):
        if signals[i] != signals[i-1]:
            transitions += 1

    print(f"\n  Threshold={threshold}:")
    print(f"       Bullish: {bullish_pct:>5.1f}%  Bearish: {bearish_pct:>5.1f}%  Neutral: {neutral_pct:>5.1f}%")
    print(f"       Signal changes: {transitions} ({transitions/len(signals)*100:.1f}% of bars)")

# =========================================
# CONSECUTIVE TREND ANALYSIS
# =========================================
print(f"\n{'='*70}")
print("CONSECUTIVE BULLISH/BEARISH RUNS (Midline Mode)")
print(f"{'='*70}")

for name, _, enabled in tf_configs:
    if not enabled:
        continue
    col = f'{name}_mid'
    vals = scores_df[col].values
    max_run = 0
    current_run = 0
    current_dir = 0
    runs = {1: [], -1: []}

    for v in vals:
        if v != 0:
            if v == current_dir:
                current_run += 1
            else:
                if current_run > 0 and current_dir != 0:
                    runs[current_dir].append(current_run)
                current_dir = v
                current_run = 1
        else:
            if current_run > 0 and current_dir != 0:
                runs[current_dir].append(current_run)
            current_run = 0
            current_dir = 0
    if current_run > 0 and current_dir != 0:
        runs[current_dir].append(current_run)

    avg_bull = np.mean(runs[1]) if runs[1] else 0
    avg_bear = np.mean(runs[-1]) if runs[-1] else 0
    max_bull = max(runs[1]) if runs[1] else 0
    max_bear = max(runs[-1]) if runs[-1] else 0
    print(f"  {name:>4}: Bull runs avg={avg_bull:>5.1f} max={max_bull:>4}  Bear runs avg={avg_bear:>5.1f} max={max_bear:>4}")

# =========================================
# MULTI-TIMEFRAME ALIGNMENT
# =========================================
print(f"\n{'='*70}")
print("MULTI-TIMEFRAME ALIGNMENT (All TFs agree - Midline Mode)")
print(f"{'='*70}")

all_enabled_names = [n for n, _, e in tf_configs if e]
total_bars = len(scores_df)

all_bullish = sum(1 for idx in scores_df.index 
                  if all(scores_df.at[idx, f'{n}_mid'] == 1 for n in all_enabled_names))
all_bearish = sum(1 for idx in scores_df.index 
                  if all(scores_df.at[idx, f'{n}_mid'] == -1 for n in all_enabled_names))
mixed = total_bars - all_bullish - all_bearish

print(f"  All 7 TFs Bullish: {all_bullish:>6} bars ({all_bullish/total_bars*100:>5.1f}%)")
print(f"  All 7 TFs Bearish: {all_bearish:>6} bars ({all_bearish/total_bars*100:>5.1f}%)")
print(f"  Mixed/Transition:  {mixed:>6} bars ({mixed/total_bars*100:>5.1f}%)")

# =========================================
# RECENT TREND SUMMARY (last 50 bars)
# =========================================
print(f"\n{'='*70}")
print("LAST 50 BARS - TREND STRENGTH HEATMAP")
print("Legend: +3 +2 +1 0 -1 -2 -3  |  Columns: 1m 5m 15m 1h 4h 1D 1W")
print(f"{'='*70}")

def to_char(v):
    if v == 3:  return '3'
    if v == 2:  return '2'
    if v == 1:  return '1'
    if v == 0:  return '.'
    if v == -1: return '1'
    if v == -2: return '2'
    if v == -3: return '3'

recent = scores_df.tail(50)
print(f"\n  {'Time':>12}  {'1m':>2} {'5m':>2} {'15m':>2} {'1h':>2} {'4h':>2} {'1D':>2} {'1W':>2}")
print(f"  {'-'*40}")
for idx, row in recent.iterrows():
    time_str = idx.strftime('%m/%d %H:%M')
    mid_vals = [row[f'{n}_mid'] for n, _, e in tf_configs if e]
    ohlc_vals = [row[f'{n}_ohlc'] for n, _, e in tf_configs if e]
    mid_str = ' '.join(f'{v:>2}' for v in mid_vals)
    ohlc_str = ' '.join(f'{v:>2}' for v in ohlc_vals)
    print(f"  {time_str}  M:{mid_str}  O:{ohlc_str}")

print(f"\n{'='*70}")
print("WEIGHTED SCORING SIMULATION (Yeni Yontem)")
print("Weights: 1m=1, 5m=2, 15m=4, 1h=8, 4h=16, 1D=24, 1W=32")
print(f"{'='*70}")

weights = [1, 2, 4, 8, 16, 24, 32]
tf_names_short = ["1m","5m","15m","1h","4h","1D","1W"]
thresholds_w = [0.1, 0.2, 0.3, 0.4, 0.5]

totalW = sum(weights)
weighted_sigs = {}

for th in thresholds_w:
    bullish = 0; bearish = 0; neutral = 0
    for idx in scores_df.index:
        wsum = 0
        for i in range(7):
            wsum += scores_df.at[idx, f'{tf_names_short[i]}_mid'] * weights[i]
        avg = wsum / totalW
        if avg >= th:
            bullish += 1
        elif avg <= -th:
            bearish += 1
        else:
            neutral += 1
    total = bullish + bearish + neutral
    print(f"\n  Threshold={th:.1f}:")
    print(f"       Bullish: {bullish/total*100:>5.1f}%  Bearish: {bearish/total*100:>5.1f}%  Neutral: {neutral/total*100:>5.1f}%")
    print(f"       -> Bullish + Bearish = {(bullish+bearish)/total*100:.1f}% (All TFs Aligned eski yontem: %10.7)")
