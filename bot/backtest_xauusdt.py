import os, sys, time, json, requests
from datetime import datetime, timedelta
from bisect import bisect_right
import pandas as pd
import numpy as np

SYMBOL = "XAUUSDT"
TIMEFRAME = "1m"
CAPITAL = 1000.0
LEVERAGE = 100
POS_PCT = 0.02
SL_PCT = float(sys.argv[1]) if len(sys.argv) > 1 else 6.0
TP_PCT = float(sys.argv[2]) if len(sys.argv) > 2 else 3.0
BACKTEST_DAYS = int(sys.argv[3]) if len(sys.argv) > 3 else 90
DAYS_OFFSET = int(sys.argv[4]) if len(sys.argv) > 4 else 0  # days before now to start
MAX_CONSECUTIVE_LOSSES = 6
DAILY_LOSS_LIMIT = 50.0
FEE_TAKER = 0.04
FUNDING_8H_RATE = 0.005
FUTURES_BASE = "https://testnet.binancefuture.com"
DATA_CACHE = f"xauusdt_1m_{BACKTEST_DAYS}d_offset{DAYS_OFFSET}.csv"
H1_CACHE = DATA_CACHE.replace("_1m_", "_1h_")

# --- Feature Flags ---
USE_H1_TREND = False   # H1 trend filter - disabled for now
H1_FAST = 9
H1_SLOW = 21

USE_ATR_FILTER = False  # ATR volatility filter - disabled for now
ATR_PERIOD = 14
ATR_MULT = 1.2

USE_TRAILING = False    # Trailing stop - disabled for now
TRAIL_BE_ACTIVATE = 0.5
TRAIL_DIST = 0.5

USE_ADX = False         # ADX regime filter
ADX_PERIOD = 14
ADX_THRESHOLD = 20      # only trade when ADX > 20 (trending)

USE_MACD = True         # MACD confirmation (NEW)
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

# Tighter entry thresholds
RSI_OVERSOLD = 30       # was 35
RSI_OVERBOUGHT = 70     # was 65

# Martingale / DCA (disabled - tested, degrades performance)
MARTINGALE_ENABLED = False
ANTI_MART_MULT = 1.5
MAX_ANTI_MULT = 4.0
DCA_ENABLED = False
DCA_MAX_ADDS = 2

def fetch_klines_range(start_str, end_str, interval="1m", limit=1500):
    all_rows = []
    start_ms = int(datetime.strptime(start_str, "%Y-%m-%d %H:%M:%S").timestamp() * 1000)
    end_ms = int(datetime.strptime(end_str, "%Y-%m-%d %H:%M:%S").timestamp() * 1000)
    while start_ms < end_ms:
        params = {"symbol": SYMBOL, "interval": interval, "limit": limit, "startTime": start_ms}
        try:
            r = requests.get(f"{FUTURES_BASE}/fapi/v1/klines", params=params, timeout=15)
            data = r.json()
            if not data: break
            for k in data:
                ts = int(k[0])
                if ts > end_ms: break
                all_rows.append({"timestamp": ts, "open": float(k[1]), "high": float(k[2]),
                                 "low": float(k[3]), "close": float(k[4]), "volume": float(k[5])})
            start_ms = data[-1][0] + 1
            print(f"  Fetched {len(data)} bars, total: {len(all_rows)}")
        except Exception as e:
            print(f"  Fetch error: {e}"); break
    return pd.DataFrame(all_rows)

def ema(data, period):
    result = np.zeros_like(data)
    mult = 2 / (period + 1)
    result[0] = data[0]
    for i in range(1, len(data)):
        result[i] = (data[i] - result[i-1]) * mult + result[i-1]
    return result

def rsi(data, period=14):
    delta = np.diff(data, prepend=data[0])
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)
    avg_gain = np.zeros_like(gain); avg_loss = np.zeros_like(loss)
    avg_gain[0] = np.mean(gain[:period]) if len(gain) >= period else gain[0]
    avg_loss[0] = np.mean(loss[:period]) if len(loss) >= period else loss[0]
    for i in range(1, len(gain)):
        avg_gain[i] = (avg_gain[i-1] * (period-1) + gain[i]) / period
        avg_loss[i] = (avg_loss[i-1] * (period-1) + loss[i]) / period
    rs = np.divide(avg_gain, avg_loss, out=np.ones_like(avg_gain), where=avg_loss != 0)
    return 100 - (100 / (1 + rs))

def bollinger(data, period=20, std=2):
    ma = np.zeros_like(data); rs = np.zeros_like(data)
    for i in range(len(data)):
        if i < period: ma[i] = np.mean(data[:i+1]); rs[i] = np.std(data[:i+1])
        else: ma[i] = np.mean(data[i-period+1:i+1]); rs[i] = np.std(data[i-period+1:i+1])
    return ma + rs * std, ma, ma - rs * std

def atr(high, low, close, period=14):
    tr = np.zeros_like(close)
    tr[0] = high[0] - low[0]
    for i in range(1, len(close)):
        tr[i] = max(high[i]-low[i], abs(high[i]-close[i-1]), abs(low[i]-close[i-1]))
    return ema(tr, period)

def adx(high, low, close, period=14):
    tr = np.zeros_like(close)
    plus_dm = np.zeros_like(close)
    minus_dm = np.zeros_like(close)
    tr[0] = high[0] - low[0]
    for i in range(1, len(close)):
        tr[i] = max(high[i]-low[i], abs(high[i]-close[i-1]), abs(low[i]-close[i-1]))
        up_move = high[i] - high[i-1]
        down_move = low[i-1] - low[i]
        plus_dm[i] = up_move if up_move > down_move and up_move > 0 else 0
        minus_dm[i] = down_move if down_move > up_move and down_move > 0 else 0
    atr_val = ema(tr, period)
    plus_di = 100 * ema(plus_dm, period) / np.where(atr_val == 0, 1, atr_val)
    minus_di = 100 * ema(minus_dm, period) / np.where(atr_val == 0, 1, atr_val)
    dx = 100 * np.abs(plus_di - minus_di) / np.where(plus_di + minus_di == 0, 1, plus_di + minus_di)
    return ema(dx, period)

def macd(data, fast=12, slow=26, signal=9):
    ema_f = ema(data, fast)
    ema_s = ema(data, slow)
    macd_line = ema_f - ema_s
    signal_line = ema(macd_line, signal)
    return macd_line, signal_line

def load_h1_trend():
    """Fetch H1 data and return array of trend direction (1=up, -1=down, 0=flat) aligned to M1 timestamps"""
    cache = H1_CACHE
    if os.path.exists(cache):
        h1 = pd.read_csv(cache, dtype={"timestamp": "int64"})
    else:
        end = datetime.now() - timedelta(days=DAYS_OFFSET)
        start = end - timedelta(days=BACKTEST_DAYS)
        h1 = fetch_klines_range(start.strftime("%Y-%m-%d %H:%M:%S"),
                                end.strftime("%Y-%m-%d %H:%M:%S"), interval="1h")
        if len(h1) > 10:
            h1.to_csv(cache, index=False)
    h1_close = h1["close"].values
    h1_ema_f = ema(h1_close, H1_FAST)
    h1_ema_s = ema(h1_close, H1_SLOW)
    h1_ts = h1["timestamp"].values
    return h1_ts, h1_ema_f, h1_ema_s

def get_h1_trend_idx(m1_ts, h1_ts, h1_ema_f, h1_ema_s):
    """For each m1 timestamp, return the matching h1 trend index"""
    idx = bisect_right(h1_ts, m1_ts) - 1
    if idx < 0 or idx >= len(h1_ema_f):
        return 0
    if h1_ema_f[idx] > h1_ema_s[idx]: return 1
    if h1_ema_f[idx] < h1_ema_s[idx]: return -1
    return 0

def calc_fib_levels(high, low, lookback=21):
    """Find swing highs/lows, compute Fibonacci DCA levels (no lookahead)."""
    n = len(high)
    fib_long = np.full((n, 3), np.nan)
    fib_short = np.full((n, 3), np.nan)
    is_sh = np.zeros(n, dtype=bool)
    is_sl = np.zeros(n, dtype=bool)
    for i in range(lookback, n - lookback):
        if high[i] == max(high[i-lookback:i+lookback+1]):
            is_sh[i] = True
        if low[i] == min(low[i-lookback:i+lookback+1]):
            is_sl[i] = True
    last_sh = last_sl = np.nan
    for i in range(n):
        cb = i - lookback
        if cb >= lookback:
            if is_sh[cb]: last_sh = high[cb]
            if is_sl[cb]: last_sl = low[cb]
        if not (np.isnan(last_sh) or np.isnan(last_sl)):
            hi = max(last_sh, last_sl); lo = min(last_sh, last_sl); d = hi - lo
            if d > 0:
                fib_long[i] = [lo - d*0.272, lo - d*0.414, lo - d*0.618]
                fib_short[i] = [hi + d*0.272, hi + d*0.414, hi + d*0.618]
    return fib_long, fib_short

def run_backtest(df):
    print(f"\nRunning backtest on {len(df)} bars ({BACKTEST_DAYS} days)...")
    ft = []
    if USE_H1_TREND: ft.append("H1 trend")
    if USE_ATR_FILTER: ft.append("ATR filter")
    if USE_TRAILING: ft.append("trailing")
    if MARTINGALE_ENABLED: ft.append(f"anti-mart {ANTI_MART_MULT}x")
    if DCA_ENABLED: ft.append(f"DCA/{DCA_MAX_ADDS}")
    print(f"Capital: ${CAPITAL} | Leverage: {LEVERAGE}x | Pos: {POS_PCT*100}% of equity")
    print(f"TP: {TP_PCT}% | SL: {SL_PCT}% | {', '.join(ft)}")
    print(f"Independent LONG/SHORT | Taker Fee: {FEE_TAKER}% | Funding: {FUNDING_8H_RATE}%/8h")

    close = df["close"].values; high = df["high"].values
    low = df["low"].values; open_p = df["open"].values
    timestamps = df["timestamp"].values

    ema9 = ema(close, 9); ema21 = ema(close, 21)
    rsi_val = rsi(close, 14)
    bb_upper, bb_mid, bb_lower = bollinger(close, 20, 2)

    # ATR filter
    atr_val = atr(high, low, close, ATR_PERIOD) if USE_ATR_FILTER else None
    if USE_ATR_FILTER:
        atr_median = np.median(atr_val[ATR_PERIOD:])
        atr_threshold = atr_median * ATR_MULT
        print(f"  ATR filter: median={atr_median:.3f}, threshold={atr_threshold:.3f}")

    # ADX regime filter
    adx_val = adx(high, low, close, ADX_PERIOD) if USE_ADX else None
    if USE_ADX:
        print(f"  ADX filter: threshold > {ADX_THRESHOLD}")

    # MACD
    macd_line, macd_signal = macd(close, MACD_FAST, MACD_SLOW, MACD_SIGNAL) if USE_MACD else (None, None)
    if USE_MACD:
        print(f"  MACD filter: enabled")

    # H1 trend
    h1_ts = h1_ema_f = h1_ema_s = None
    if USE_H1_TREND:
        h1_ts, h1_ema_f, h1_ema_s = load_h1_trend()
        print(f"  H1 trend filter: loaded {len(h1_ts)} bars")

    # Fibonacci DCA levels (1m swing highs/lows)
    fib_long = fib_short = None
    if DCA_ENABLED:
        fib_long, fib_short = calc_fib_levels(high, low, 21)
        print(f"  DCA: {DCA_MAX_ADDS} adds max, {ANTI_MART_MULT}x anti-martingale, 1m Fib levels")

    equity = CAPITAL; trades = []
    long_pos = None; short_pos = None
    consecutive_losses = 0; consecutive_wins = 0; daily_pnl = 0.0; last_trade_day = None
    total_signals = 0; total_dca_adds = 0; skipped_atr = 0; skipped_h1 = 0; skipped_adx = 0

    def calc_fees(price, qty):
        return price * qty * FEE_TAKER / 100

    def open_position(side, price, idx, dt_val, mart_step=0):
        nonlocal total_signals
        if MARTINGALE_ENABLED:
            mart_mult = min(ANTI_MART_MULT ** consecutive_wins, MAX_ANTI_MULT)
        else:
            mart_mult = 1.0
        margin = equity * POS_PCT * mart_mult
        if margin < 1: return None
        qty = margin * LEVERAGE / price
        if qty * price < 5: return None
        total_signals += 1
        tp = price * (1 + TP_PCT/100) if side == "LONG" else price * (1 - TP_PCT/100)
        sl = price * (1 - SL_PCT/100) if side == "LONG" else price * (1 + SL_PCT/100)
        p = {"side": side, "avg_entry": price, "qty": qty, "margin": margin,
             "tp": tp, "sl": sl, "entry_idx": idx,
             "entry_fees": calc_fees(price, qty),
             "entry_time": dt_val.strftime("%m-%d %H:%M"),
             "add_count": 0}
        if USE_TRAILING:
            p["best_price"] = price
            p["breakeven"] = False
        return p

    def close_trade(p, exit_price, reason, idx, dt_val):
        nonlocal equity, daily_pnl, consecutive_losses, consecutive_wins
        gross = (exit_price - p["avg_entry"]) * p["qty"] if p["side"] == "LONG" else (p["avg_entry"] - exit_price) * p["qty"]
        entry_fees = p["entry_fees"]
        exit_fee = calc_fees(exit_price, p["qty"])
        hours_open = (timestamps[idx] - timestamps[p["entry_idx"]]) / 3600000
        funding_intervals = max(0, int(hours_open / 8))
        avg_notional = p["avg_entry"] * p["qty"]
        funding_fee = avg_notional * FUNDING_8H_RATE / 100 * funding_intervals
        total_fees = entry_fees + exit_fee + funding_fee
        net_pnl = gross - total_fees
        equity += net_pnl; daily_pnl += net_pnl
        if net_pnl > 0:
            consecutive_losses = 0
            consecutive_wins += 1
        else:
            consecutive_losses += 1
            consecutive_wins = 0
        trades.append({
            "time": dt_val.strftime("%m-%d %H:%M"), "side": p["side"],
            "entry": round(p["avg_entry"],2), "exit": round(exit_price,2),
            "qty": round(p["qty"],3), "margin": round(p["margin"],2),
            "gross": round(gross,2), "fees": round(total_fees,2),
            "pnl": round(net_pnl,2), "reason": reason,
            "bars": idx - p["entry_idx"], "funding": round(funding_fee,2),
            "equity": round(equity,2),
            "adds": p.get("add_count", 0)
        })
        return net_pnl

    for i in range(50, len(df)):
        ts = timestamps[i]; dt = datetime.fromtimestamp(ts / 1000)
        today = dt.day
        if today != last_trade_day: daily_pnl = 0.0; last_trade_day = today
        if equity <= 0 or daily_pnl <= -DAILY_LOSS_LIMIT or consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
            continue

        # --- ATR volatility filter ---
        atr_ok = True
        if USE_ATR_FILTER and i >= ATR_PERIOD:
            if atr_val[i] > atr_threshold:
                atr_ok = False
                skipped_atr += 1

        # --- ADX regime filter ---
        adx_ok = True
        if USE_ADX and i >= ADX_PERIOD * 2:
            if adx_val[i] < ADX_THRESHOLD:
                adx_ok = False
                skipped_adx += 1

        # --- H1 trend check ---
        h1_dir = 0
        if USE_H1_TREND and h1_ts is not None:
            h1_dir = get_h1_trend_idx(ts, h1_ts, h1_ema_f, h1_ema_s)

        # --- LONG signal ---
        if long_pos is None and consecutive_losses < MAX_CONSECUTIVE_LOSSES:
            allow_long = True
            if USE_H1_TREND and h1_dir < 0:
                allow_long = False
                skipped_h1 += 1
            if not atr_ok: allow_long = False
            if not adx_ok: allow_long = False
            if allow_long and ema9[i] > ema21[i]:
                long_score = 0
                if rsi_val[i] < RSI_OVERSOLD: long_score += 1
                if close[i] <= bb_lower[i] * 1.001: long_score += 1
                if close[i] > open_p[i]: long_score += 1
                if USE_MACD and macd_line[i] > macd_signal[i]: long_score += 1
                min_score = 3 if USE_MACD else 2
                if long_score >= min_score:
                    pos = open_position("LONG", close[i], i, dt, consecutive_losses)
                    if pos:
                        long_pos = pos
                        print(f"  OPEN LONG @ ${close[i]:.2f} | SL: ${pos['sl']:.2f} | TP: ${pos['tp']:.2f}")

        # --- SHORT signal ---
        if short_pos is None and consecutive_losses < MAX_CONSECUTIVE_LOSSES:
            allow_short = True
            if USE_H1_TREND and h1_dir > 0:
                allow_short = False
                skipped_h1 += 1
            if not atr_ok: allow_short = False
            if not adx_ok: allow_short = False
            if allow_short and ema9[i] < ema21[i]:
                short_score = 0
                if rsi_val[i] > RSI_OVERBOUGHT: short_score += 1
                if close[i] >= bb_upper[i] * 0.999: short_score += 1
                if close[i] < open_p[i]: short_score += 1
                if USE_MACD and macd_line[i] < macd_signal[i]: short_score += 1
                min_score = 3 if USE_MACD else 2
                if short_score >= min_score:
                    pos = open_position("SHORT", close[i], i, dt, consecutive_losses)
                    if pos:
                        short_pos = pos
                        print(f"  OPEN SHORT @ ${close[i]:.2f} | SL: ${pos['sl']:.2f} | TP: ${pos['tp']:.2f}")

        # --- Trailing / partial TP logic ---
        if long_pos is not None and USE_TRAILING:
            p = long_pos
            p["best_price"] = max(p["best_price"], high[i])
            profit_pct = (p["best_price"] - p["avg_entry"]) / p["avg_entry"] * 100
            if profit_pct >= TRAIL_BE_ACTIVATE and not p["breakeven"]:
                be_price = p["avg_entry"] * 1.001
                if be_price > p["sl"]:
                    p["sl"] = be_price
                    p["breakeven"] = True
                    print(f"  BREAKEVEN LONG SL -> ${be_price:.2f}")
            if profit_pct >= TRAIL_BE_ACTIVATE:
                new_sl = p["best_price"] * (1 - TRAIL_DIST / 100)
                if new_sl > p["sl"]:
                    p["sl"] = new_sl

        if short_pos is not None and USE_TRAILING:
            p = short_pos
            p["best_price"] = min(p["best_price"], low[i])
            profit_pct = (p["avg_entry"] - p["best_price"]) / p["avg_entry"] * 100
            if profit_pct >= TRAIL_BE_ACTIVATE and not p["breakeven"]:
                be_price = p["avg_entry"] * 0.999
                if be_price < p["sl"]:
                    p["sl"] = be_price
                    p["breakeven"] = True
                    print(f"  BREAKEVEN SHORT SL -> ${be_price:.2f}")
            if profit_pct >= TRAIL_BE_ACTIVATE:
                new_sl = p["best_price"] * (1 + TRAIL_DIST / 100)
                if new_sl < p["sl"]:
                    p["sl"] = new_sl

        # --- DCA LONG: add at Fibonacci extension levels ---
        if DCA_ENABLED and long_pos is not None and long_pos["add_count"] < DCA_MAX_ADDS and fib_long is not None:
            lvl = fib_long[i, long_pos["add_count"]]
            if not np.isnan(lvl) and low[i] <= lvl * 1.001:
                total_dca_adds += 1
                dca_step = long_pos["add_count"] + 1
                am = min(ANTI_MART_MULT ** (consecutive_wins + dca_step), MAX_ANTI_MULT) if MARTINGALE_ENABLED else 1.0
                add_margin = equity * POS_PCT * am
                add_qty = add_margin * LEVERAGE / close[i]
                if add_qty * close[i] >= 5:
                    old_notional = long_pos["avg_entry"] * long_pos["qty"]
                    long_pos["qty"] += add_qty
                    long_pos["margin"] += add_margin
                    long_pos["avg_entry"] = (old_notional + close[i] * add_qty) / long_pos["qty"]
                    long_pos["sl"] = long_pos["avg_entry"] * (1 - SL_PCT/100)
                    long_pos["tp"] = long_pos["avg_entry"] * (1 + TP_PCT/100)
                    long_pos["entry_fees"] += calc_fees(close[i], add_qty)
                    long_pos["add_count"] += 1
                    print(f"  DCA ADD LONG #{long_pos['add_count']} @ ${close[i]:.2f} (Fib ext) | Avg: ${long_pos['avg_entry']:.2f} | SL: ${long_pos['sl']:.2f}")

        # --- DCA SHORT: add at Fibonacci extension levels ---
        if DCA_ENABLED and short_pos is not None and short_pos["add_count"] < DCA_MAX_ADDS and fib_short is not None:
            lvl = fib_short[i, short_pos["add_count"]]
            if not np.isnan(lvl) and high[i] >= lvl * 0.999:
                total_dca_adds += 1
                dca_step = short_pos["add_count"] + 1
                am = min(ANTI_MART_MULT ** (consecutive_wins + dca_step), MAX_ANTI_MULT) if MARTINGALE_ENABLED else 1.0
                add_margin = equity * POS_PCT * am
                add_qty = add_margin * LEVERAGE / close[i]
                if add_qty * close[i] >= 5:
                    old_notional = short_pos["avg_entry"] * short_pos["qty"]
                    short_pos["qty"] += add_qty
                    short_pos["margin"] += add_margin
                    short_pos["avg_entry"] = (old_notional + close[i] * add_qty) / short_pos["qty"]
                    short_pos["sl"] = short_pos["avg_entry"] * (1 + SL_PCT/100)
                    short_pos["tp"] = short_pos["avg_entry"] * (1 - TP_PCT/100)
                    short_pos["entry_fees"] += calc_fees(close[i], add_qty)
                    short_pos["add_count"] += 1
                    print(f"  DCA ADD SHORT #{short_pos['add_count']} @ ${close[i]:.2f} (Fib ext) | Avg: ${short_pos['avg_entry']:.2f} | SL: ${short_pos['sl']:.2f}")

        # --- Check SL/TP for LONG ---
        if long_pos is not None:
            p = long_pos; hit = False; exit_price = None; reason = ""
            if high[i] >= p["tp"]: exit_price = p["tp"]; reason = "take_profit"; hit = True
            if not hit and low[i] <= p["sl"]: exit_price = p["sl"]; reason = "stop_loss"; hit = True
            if hit:
                pnl = close_trade(p, exit_price, reason, i, dt)
                sign = "+" if pnl >= 0 else ""
                adds_str = f" adds={p['add_count']}" if p["add_count"] > 0 else ""
                print(f"  CLOSE LONG @ ${exit_price:.2f} | Net: {sign}${pnl:.2f} | {reason}{adds_str} | Eq: ${equity:.2f}")
                long_pos = None

        # --- Check SL/TP for SHORT ---
        if short_pos is not None:
            p = short_pos; hit = False; exit_price = None; reason = ""
            if low[i] <= p["tp"]: exit_price = p["tp"]; reason = "take_profit"; hit = True
            if not hit and high[i] >= p["sl"]: exit_price = p["sl"]; reason = "stop_loss"; hit = True
            if hit:
                pnl = close_trade(p, exit_price, reason, i, dt)
                sign = "+" if pnl >= 0 else ""
                adds_str = f" adds={p['add_count']}" if p["add_count"] > 0 else ""
                print(f"  CLOSE SHORT @ ${exit_price:.2f} | Net: {sign}${pnl:.2f} | {reason}{adds_str} | Eq: ${equity:.2f}")
                short_pos = None

    if USE_ATR_FILTER: print(f"  [ATR filter skipped {skipped_atr} signals]")
    if USE_H1_TREND: print(f"  [H1 trend filter skipped {skipped_h1} signals]")
    if USE_ADX: print(f"  [ADX filter skipped {skipped_adx} signals]")
    if DCA_ENABLED: print(f"  [DCA adds: {total_dca_adds}]")
    return trades, equity

def analyze(trades, final_equity):
    print(f"\n{'='*70}")
    print(f"  BACKTEST RESULTS - {SYMBOL} {TIMEFRAME} ({BACKTEST_DAYS} days)")
    print(f"{'='*70}")
    if not trades: print("No trades."); return
    df = pd.DataFrame(trades)
    wins = df[df["pnl"]>0]; losses = df[df["pnl"]<0]
    total_trades = len(df); n_wins = len(wins); n_losses = len(losses)
    wr = n_wins/total_trades*100 if total_trades else 0
    net_pnl = df["pnl"].sum(); gross_pnl = df["gross"].sum()
    total_fees = df["fees"].sum()
    roi = (final_equity-CAPITAL)/CAPITAL*100
    aw = wins["pnl"].mean() if n_wins else 0
    al = losses["pnl"].abs().mean() if n_losses else 0
    pf = abs(wins["pnl"].sum()/losses["pnl"].sum()) if n_losses and losses["pnl"].sum()!=0 else float('inf')
    cum_eq = CAPITAL + df["pnl"].cumsum()
    peak = CAPITAL; mdd_val = 0
    for v in cum_eq:
        if v > peak: peak = v
        dd = (peak-v)/peak*100
        if dd > mdd_val: mdd_val = dd
    avg_hours = df["bars"].mean() / 60

    print(f"\n  [1] PERFORMANCE SUMMARY")
    print(f"  ------------------------------------------------------------------")
    print(f"  Total Trades     : {total_trades:5d}     Win Rate: {wr:6.2f}%")
    print(f"  Winning Trades   : {n_wins:5d}     Avg Win: ${aw:<7.2f}")
    print(f"  Losing Trades    : {n_losses:5d}     Avg Loss: -${al:<6.2f}")
    print(f"  Gross PnL        : ${gross_pnl:<8.2f}  Fees: ${total_fees:<7.2f}")
    print(f"  Net PnL          : ${net_pnl:<8.2f}  (fees {total_fees/gross_pnl*100:.1f}% of gross)")
    print(f"  ROI              : {roi:7.2f}%      Profit Factor: {pf:<8.2f}")
    print(f"  Final Equity     : ${final_equity:<8.2f}  Max DD: {mdd_val:6.2f}%")
    print(f"  Avg Bars Held    : {df['bars'].mean():7.1f}  (~{avg_hours:.1f} hours)")

    print(f"\n  [2] FEE BREAKDOWN")
    print(f"  ------------------------------------------------------------------")
    print(f"  Total Fees       : ${total_fees:<8.2f}")
    print(f"  Avg Fee/Trade    : ${total_fees/total_trades:<8.2f}")
    print(f"  Fee as % of Gross : {total_fees/gross_pnl*100:6.2f}%")
    print(f"  Fee as % of Capital: {total_fees/CAPITAL*100:6.2f}%")
    print(f"  Funding Included  : Yes (estimated ${df['funding'].sum():.2f} total)")

    print(f"\n  [3] SIDE ANALYSIS")
    print(f"  ------------------------------------------------------------------")
    for side in ["LONG","SHORT"]:
        sub = df[df["side"]==side]
        if len(sub)==0: continue
        sw = len(sub[sub["pnl"]>0])
        sr = sw/len(sub)*100
        print(f"  {side:6s} | Trades:{len(sub):3d} | Win:{sr:5.1f}% | Net:${sub['pnl'].sum():>8.2f} | Avg:${sub['pnl'].mean():>7.2f} | Fees:${sub['fees'].sum():>7.2f}")

    print(f"\n  [4] EXIT REASON ANALYSIS")
    print(f"  ------------------------------------------------------------------")
    print(f"  {'Reason':<15s} | {'Trades':>7} | {'%':>5} | {'Win%':>6} | {'Net PnL':>9} | {'Avg':>8} | {'Avg Fee':>8}")
    sep = f"  {'-'*15}-+-{'-'*7}-+-{'-'*5}-+-{'-'*6}-+-{'-'*9}-+-{'-'*8}-+-{'-'*8}"
    print(sep)
    for r in ["take_profit","stop_loss"]:
        sub = df[df["reason"]==r]
        if len(sub)==0: continue
        sw = len(sub[sub["pnl"]>0])
        sr = sw/len(sub)*100
        print(f"  {r:<15s} | {len(sub):7d} | {len(sub)/total_trades*100:5.1f} | {sr:6.1f} | ${sub['pnl'].sum():>8.2f} | ${sub['pnl'].mean():>7.2f} | ${sub['fees'].mean():>7.2f}")

    min_day = df["time"].min()[:5]; max_day = df["time"].max()[:5]
    df["date"] = df["time"].str[:5]
    df["hour"] = df["time"].str[6:8].astype(int)
    daily = df.groupby("date").agg(trades=("pnl","count"), pnl=("pnl","sum")).sort_index()
    best_day = daily.loc[daily["pnl"].idxmax()] if len(daily) else None
    worst_day = daily.loc[daily["pnl"].idxmin()] if len(daily) else None
    avg_daily_pnl = daily["pnl"].mean()
    print(f"\n  [5] TIME ANALYSIS ({min_day} to {max_day})")
    print(f"  ------------------------------------------------------------------")
    print(f"  Avg Daily PnL     : ${avg_daily_pnl:.2f}")
    if best_day is not None:
        print(f"  Best Day          : {best_day.name} (${best_day.pnl:.2f}, {int(best_day.trades)} trades)")
    if worst_day is not None:
        print(f"  Worst Day         : {worst_day.name} (${worst_day.pnl:.2f}, {int(worst_day.trades)} trades)")
    print(f"  Trades in Top 3h  : {df[df['hour'].isin([0,1,2,3])].shape[0]} trades")
    print(f"  Trades in EU sess.: {df[df['hour'].isin([8,9,10,11,12,13,14,15,16])].shape[0]} trades")
    print(f"  Trades in US sess.: {df[df['hour'].isin([13,14,15,16,17,18,19,20,21])].shape[0]} trades")
    print(f"  Active Days       : {len(daily)}")

    streak = 0; max_cw = 0; max_cl = 0; cur = 0
    for _, row in df.iterrows():
        if row["pnl"] > 0:
            if cur > 0: cur += 1
            else: cur = 1
        else:
            if cur < 0: cur -= 1
            else: cur = -1
        max_cw = max(max_cw, cur)
        max_cl = min(max_cl, cur)
    daily_rets = daily["pnl"] / CAPITAL
    sharpe = np.sqrt(365) * daily_rets.mean() / daily_rets.std() if len(daily_rets) > 1 and daily_rets.std() > 0 else 0
    calmar = roi / mdd_val if mdd_val > 0 else float('inf')

    n_ad = df["adds"].sum()
    if MARTINGALE_ENABLED or DCA_ENABLED:
        print(f"\n  [6] DCA / ANTI-MARTINGALE")
        print(f"  ------------------------------------------------------------------")
        print(f"  Anti-mart Mult    : {ANTI_MART_MULT}x")
        print(f"  Max Cap           : {MAX_ANTI_MULT}x")
        print(f"  Max adds/trade    : {DCA_MAX_ADDS}")
        print(f"  Total DCA adds    : {int(n_ad)}")
        print(f"  Trades with DCA   : {len(df[df['adds']>0])}")
        print(f"\n  [7] RISK METRICS")
    else:
        print(f"\n  [6] RISK METRICS")
    print(f"  ------------------------------------------------------------------")
    print(f"  Sharpe Ratio (d)  : {sharpe:.2f}")
    print(f"  Calmar Ratio      : {calmar:.2f}")
    print(f"  Max Drawdown      : {mdd_val:.2f}%")
    print(f"  Best Streak (wins): {max_cw}")
    print(f"  Worst Streak (loss): {abs(max_cl)}")
    print(f"  Win/Loss Ratio    : {(aw/al) if al > 0 else float('inf'):.2f}")
    print(f"  Avg Risk per Trade: ${df['margin'].mean():.2f} margin")

    print(f"\n  [8] PnL DISTRIBUTION")
    print(f"  ------------------------------------------------------------------")
    bins = [-999, -50, -20, -10, -5, 0, 2, 5, 10, 20, 50, 999]
    labels = ["<-$50","-$50-20","-$20-10","-$10-5","-$5-0","$0-2","$2-5","$5-10","$10-20","$20-50",">$50"]
    df["pnl_bin"] = pd.cut(df["pnl"], bins=bins, labels=labels, right=False)
    dist = df["pnl_bin"].value_counts().sort_index()
    for lb in labels:
        count = dist.get(lb, 0)
        bar = "#" * count
        print(f"  {lb:>8s} | {count:3d} {bar}")

    print(f"\n  [9] TOP 10 TRADES (by |PnL|)")
    print(f"  ------------------------------------------------------------------")
    top = df.reindex(df["pnl"].abs().sort_values(ascending=False).index).head(10)
    for _, t in top.iterrows():
        s = "+" if t["pnl"] >= 0 else ""
        print(f"  {t['time']} | {t['side']:4s} | ${t['entry']:.2f}->${t['exit']:.2f} | "
              f"${t['gross']:.2f}->{s}${t['pnl']:.2f} | fees:${t['fees']:.2f} | {t['reason']} | {t['bars']}b")

def load_data():
    if os.path.exists(DATA_CACHE):
        print(f"Loading cached data from {DATA_CACHE} ({BACKTEST_DAYS}d offset {DAYS_OFFSET})...")
        df = pd.read_csv(DATA_CACHE, dtype={"timestamp": "int64"})
        df["timestamp"] = df["timestamp"].astype(np.int64)
        return df
    end = datetime.now() - timedelta(days=DAYS_OFFSET)
    start = end - timedelta(days=BACKTEST_DAYS)
    print(f"Fetching {SYMBOL} {TIMEFRAME} data from {start.strftime('%Y-%m-%d %H:%M:%S')} to {end.strftime('%Y-%m-%d %H:%M:%S')}...")
    df = fetch_klines_range(start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S"))
    if len(df) > 100:
        df.to_csv(DATA_CACHE, index=False)
        print(f"Cached to {DATA_CACHE} ({len(df)} bars)")
    return df

def run_with_params(sl=None, tp=None, days=None, offset=None, h1=None, atr=None, trail=None, adx=None, macd=None):
    """Run backtest with custom parameters, returns (trades_df, final_equity)"""
    global SL_PCT, TP_PCT, BACKTEST_DAYS, DAYS_OFFSET
    global USE_H1_TREND, USE_ATR_FILTER, USE_TRAILING, USE_ADX, USE_MACD
    global DATA_CACHE, H1_CACHE
    if sl is not None: SL_PCT = sl
    if tp is not None: TP_PCT = tp
    if days is not None: BACKTEST_DAYS = days
    if offset is not None: DAYS_OFFSET = offset
    if h1 is not None: USE_H1_TREND = h1
    if atr is not None: USE_ATR_FILTER = atr
    if trail is not None: USE_TRAILING = trail
    if adx is not None: USE_ADX = adx
    if macd is not None: USE_MACD = macd
    DATA_CACHE = f"xauusdt_1m_{BACKTEST_DAYS}d_offset{DAYS_OFFSET}.csv"
    H1_CACHE = DATA_CACHE.replace("_1m_", "_1h_")
    df = load_data()
    if len(df) < 100: return None, None
    trades, final_equity = run_backtest(df)
    return trades, final_equity

def parse_result(trades, final_equity):
    if not trades: return None
    df_t = pd.DataFrame(trades)
    net = df_t["pnl"].sum()
    wr = len(df_t[df_t["pnl"]>0])/len(df_t)*100
    dd_peak = CAPITAL; mdd = 0
    for v in CAPITAL + df_t["pnl"].cumsum():
        if v > dd_peak: dd_peak = v
        dd = (dd_peak-v)/dd_peak*100
        if dd > mdd: mdd = dd
    return {"sl": SL_PCT, "tp": TP_PCT, "trades": len(df_t), "wr": round(wr,1),
            "net": round(net,2), "dd": round(mdd,1), "eq": round(final_equity,2),
            "roi": round((final_equity-CAPITAL)/CAPITAL*100, 2)}

if __name__ == "__main__":
    df = load_data()
    if len(df) < 100: print(f"Not enough data: {len(df)}"); exit()
    trades, final_equity = run_backtest(df)
    analyze(trades, final_equity)
    r = parse_result(trades, final_equity)
    if r:
        print(f"\nRESULT: SL={r['sl']} TP={r['tp']} trades={r['trades']} WR={r['wr']}% net=${r['net']} DD={r['dd']}% eq=${r['eq']}")
