import os, time, json, requests
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

SYMBOL = "XAUUSDT"
TIMEFRAME = "1m"
CAPITAL = 1000.0
LEVERAGE = 100
TRADING_STEPS = [0.02, 0.03, 0.04, 0.06, 0.09]
DCA_MAX_ADDS = 4
DCA_SL_PCT = 5.0
TP_PCT = 0.25
MAX_CONSECUTIVE_LOSSES = 6
DAILY_LOSS_LIMIT = 50.0
FEE_TAKER = 0.04
FUNDING_8H_RATE = 0.005
SR_LOOKBACK_3M = 10
BACKTEST_DAYS = 90
FUTURES_BASE = "https://testnet.binancefuture.com"

FETCHED_DF = None

def fetch_klines_range(start_str, end_str, limit=1500):
    all_rows = []
    start_ms = int(datetime.strptime(start_str, "%Y-%m-%d %H:%M:%S").timestamp() * 1000)
    end_ms = int(datetime.strptime(end_str, "%Y-%m-%d %H:%M:%S").timestamp() * 1000)
    while start_ms < end_ms:
        params = {"symbol": SYMBOL, "interval": TIMEFRAME, "limit": limit, "startTime": start_ms}
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

def rsi_func(data, period=14):
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

def find_sr_levels(highs, lows, lookback=SR_LOOKBACK_3M):
    supports = []; resistances = []
    n = len(highs)
    for i in range(max(2, n-lookback), n-2):
        if highs[i] > highs[i-1] and highs[i] > highs[i+1] and highs[i] > highs[i-2] and highs[i] > highs[i+2]:
            resistances.append(highs[i])
        if lows[i] < lows[i-1] and lows[i] < lows[i+1] and lows[i] < lows[i-2] and lows[i] < lows[i+2]:
            supports.append(lows[i])
    return supports, resistances

def next_support_down(price, supports, used):
    below = [s for s in supports if s < price and s not in used]
    return max(below) if below else None

def next_resistance_up(price, resistances, used):
    above = [r for r in resistances if r > price and r not in used]
    return min(above) if above else None

def get_step_margin(equity, step):
    step = min(step, len(TRADING_STEPS)-1)
    return equity * TRADING_STEPS[step]

###############################################################################
# Pass 1: Find all trades, identify the specific June 5 losing one
###############################################################################
def find_target_trade(df):
    close = df["close"].values; high = df["high"].values
    low = df["low"].values; open_p = df["open"].values
    timestamps = df["timestamp"].values

    ema9 = ema(close, 9); ema21 = ema(close, 21)
    rsi_val = rsi_func(close, 14)
    bb_upper, bb_mid, bb_lower = bollinger(close, 20, 2)

    equity = CAPITAL
    consecutive_losses = 0; daily_pnl = 0.0; last_trade_day = None
    all_supports = []; all_resistances = []

    def calc_fees(price, qty):
        return price * qty * FEE_TAKER / 100

    def start_position(side, price, idx, dt_val):
        margin = get_step_margin(equity, 0)
        if margin < 1: return None
        qty = margin * LEVERAGE / price
        if qty * price < 5: return None
        tp = price * (1 + TP_PCT/100) if side == "LONG" else price * (1 - TP_PCT/100)
        sl = price * (1 - DCA_SL_PCT/100) if side == "LONG" else price * (1 + DCA_SL_PCT/100)
        return {"side": side, "avg_entry": price, "last_entry": price,
                "qty": qty, "total_margin": margin, "add_count": 0,
                "tp": tp, "sl": sl, "entry_idx": idx,
                "entry_fees": calc_fees(price, qty),
                "entry_time": dt_val.strftime("%m-%d %H:%M"),
                "used_sr": [], "add_log": []}

    all_trades = []

    def close_trade(p, exit_price, reason, idx, dt_val):
        nonlocal equity, daily_pnl, consecutive_losses
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
        if net_pnl > 0: consecutive_losses = 0
        else: consecutive_losses += 1
        all_trades.append({
            "time": dt_val.strftime("%m-%d %H:%M"), "side": p["side"],
            "avg_entry": round(p["avg_entry"],2), "exit": round(exit_price,2),
            "adds": p["add_count"], "pnl": round(net_pnl,2), "reason": reason,
            "bars": idx - p["entry_idx"], "entry_idx": p["entry_idx"],
            "exit_idx": idx, "add_log": p.get("add_log", []),
            "entry_time": p["entry_time"]
        })

    def check_dca(p, pending, side, i, dt):
        if pending is None: return None
        triggered = False
        if side == "LONG" and low[i] <= pending["add_price"]:
            triggered = True
        elif side == "SHORT" and high[i] >= pending["add_price"]:
            triggered = True
        if not triggered:
            return pending
        add_margin = get_step_margin(equity, pending["step"])
        if add_margin < 1: return None
        qty_add = add_margin * LEVERAGE / pending["add_price"]
        if qty_add * pending["add_price"] < 5: return None
        total_qty = p["qty"] + qty_add
        total_margin = p["total_margin"] + add_margin
        p["avg_entry"] = (p["avg_entry"] * p["qty"] + pending["add_price"] * qty_add) / total_qty
        p["last_entry"] = pending["add_price"]
        p["qty"] = total_qty
        p["total_margin"] = total_margin
        p["add_count"] += 1
        p["used_sr"].append(pending["add_price"])
        p["entry_fees"] += calc_fees(pending["add_price"], qty_add)
        p["tp"] = p["avg_entry"] * (1 + TP_PCT/100) if side == "LONG" else p["avg_entry"] * (1 - TP_PCT/100)
        p["sl"] = pending["add_price"] * (1 - DCA_SL_PCT/100) if side == "LONG" else pending["add_price"] * (1 + DCA_SL_PCT/100)
        p["add_log"].append({
            "add_num": pending["step"], "price": pending["add_price"],
            "idx": i, "time": dt.strftime("%m-%d %H:%M"),
            "avg_entry": p["avg_entry"], "sl": p["sl"], "tp": p["tp"]
        })
        return None

    def get_next_add(p, side):
        if p["add_count"] >= DCA_MAX_ADDS: return None
        step = p["add_count"] + 1
        fallback_pct = 0.005
        if side == "LONG":
            search_below = p["last_entry"] * 0.995
            ap = next_support_down(search_below, all_supports, p["used_sr"])
            if ap is None: ap = p["last_entry"] * (1 - fallback_pct)
        else:
            search_above = p["last_entry"] * 1.005
            ap = next_resistance_up(search_above, all_resistances, p["used_sr"])
            if ap is None: ap = p["last_entry"] * (1 + fallback_pct)
        return {"add_price": ap, "step": step}

    long_pos = None; long_pending = None
    short_pos = None; short_pending = None

    for i in range(50, len(df)):
        ts = timestamps[i]; dt = datetime.fromtimestamp(ts / 1000)
        today = dt.day
        if today != last_trade_day: daily_pnl = 0.0; last_trade_day = today
        if equity <= 0 or daily_pnl <= -DAILY_LOSS_LIMIT or consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
            continue

        if i % 3 == 0 and i >= 3:
            idx_3m_start = max(0, i - 3 * SR_LOOKBACK_3M)
            sr_high = high[idx_3m_start:i+1:3]
            sr_low = low[idx_3m_start:i+1:3]
            all_supports, all_resistances = find_sr_levels(sr_high, sr_low)

        if long_pending is not None and long_pos is not None:
            long_pending = check_dca(long_pos, long_pending, "LONG", i, dt)
            if long_pending is None and long_pos["add_count"] < DCA_MAX_ADDS:
                long_pending = get_next_add(long_pos, "LONG")

        if short_pending is not None and short_pos is not None:
            short_pending = check_dca(short_pos, short_pending, "SHORT", i, dt)
            if short_pending is None and short_pos["add_count"] < DCA_MAX_ADDS:
                short_pending = get_next_add(short_pos, "SHORT")

        if long_pos is None and consecutive_losses < MAX_CONSECUTIVE_LOSSES:
            long_score = 0
            if ema9[i] > ema21[i]: long_score += 1
            if rsi_val[i] < 35: long_score += 1
            if close[i] <= bb_lower[i] * 1.001: long_score += 1
            if close[i] > open_p[i]: long_score += 1
            if long_score >= 3:
                pos = start_position("LONG", close[i], i, dt)
                if pos:
                    long_pos = pos
                    ap = next_support_down(close[i] * 0.995, all_supports, [])
                    if ap is None: ap = close[i] * 0.995
                    long_pending = {"add_price": ap, "step": 1}

        if short_pos is None and consecutive_losses < MAX_CONSECUTIVE_LOSSES:
            short_score = 0
            if ema9[i] < ema21[i]: short_score += 1
            if rsi_val[i] > 65: short_score += 1
            if close[i] >= bb_upper[i] * 0.999: short_score += 1
            if close[i] < open_p[i]: short_score += 1
            if short_score >= 3:
                pos = start_position("SHORT", close[i], i, dt)
                if pos:
                    short_pos = pos
                    ap = next_resistance_up(close[i] * 1.005, all_resistances, [])
                    if ap is None: ap = close[i] * 1.005
                    short_pending = {"add_price": ap, "step": 1}

        if long_pos is not None:
            p = long_pos; hit = False; exit_price = None; reason = ""
            if high[i] >= p["tp"]: exit_price = p["tp"]; reason = "take_profit"; hit = True
            if not hit and low[i] <= p["sl"]: exit_price = p["sl"]; reason = "dca_stop"; hit = True
            if hit:
                close_trade(p, exit_price, reason, i, dt)
                long_pos = None; long_pending = None

        if short_pos is not None:
            p = short_pos; hit = False; exit_price = None; reason = ""
            if low[i] <= p["tp"]: exit_price = p["tp"]; reason = "take_profit"; hit = True
            if not hit and high[i] >= p["sl"]: exit_price = p["sl"]; reason = "dca_stop"; hit = True
            if hit:
                close_trade(p, exit_price, reason, i, dt)
                short_pos = None; short_pending = None

    # Find the specific trade
    target = None
    for t in all_trades:
        # Check June 5 - look at entry_time or time (exit time)
        entry_month_day = t.get("entry_time", "")[:5]
        exit_month_day = t["time"][:5]
        if (entry_month_day == "06-05" or exit_month_day == "06-05") and t["side"] == "LONG" and t["adds"] == 4 and t["reason"] == "dca_stop":
            target = t
            break

    # If not found strictly on June 5, try broader search
    if target is None:
        for t in all_trades:
            if t["side"] == "LONG" and t["adds"] == 4 and t["reason"] == "dca_stop":
                target = t
                break

    return target, all_trades, all_supports, all_resistances


###############################################################################
# Pass 2: Detailed replay of the specific trade
###############################################################################
def replay_trade(df, target_trade):
    close = df["close"].values; high = df["high"].values
    low = df["low"].values; open_p = df["open"].values
    timestamps = df["timestamp"].values

    ema9 = ema(close, 9); ema21 = ema(close, 21)
    rsi_val = rsi_func(close, 14)
    bb_upper, bb_mid, bb_lower = bollinger(close, 20, 2)

    entry_i = target_trade["entry_idx"]
    exit_i = target_trade["exit_idx"]

    out_lines = []
    def out(s=""):
        out_lines.append(s)
        print(s)

    out("=" * 80)
    out("  DETAILED ANALYSIS: LONG TRADE ON JUNE 5 (adds:4, reason:dca_stop)")
    out("=" * 80)

    dt_entry = datetime.fromtimestamp(timestamps[entry_i] / 1000)
    dt_exit = datetime.fromtimestamp(timestamps[exit_i] / 1000)

    out(f"\n  TRADE SUMMARY")
    out(f"  Entry         : {target_trade['entry_time']}  (idx {entry_i}, {dt_entry.strftime('%Y-%m-%d %H:%M:%S')})")
    out(f"  Exit          : {target_trade['time']}  (idx {exit_i}, {dt_exit.strftime('%Y-%m-%d %H:%M:%S')})")
    out(f"  Side          : LONG")
    out(f"  Bars Held     : {target_trade['bars']}")
    out(f"  Avg Entry     : ${target_trade['avg_entry']:.2f}")
    out(f"  Exit Price    : ${target_trade['exit']:.2f}")
    out(f"  Add Count     : {target_trade['adds']}")
    out(f"  Net PnL       : ${target_trade['pnl']:.2f}")
    out(f"  Reason        : {target_trade['reason']}")

    # 1. S/R Levels at entry
    out(f"\n{'='*80}")
    out("  1. S/R LEVELS DETECTED IN 3m DATA AROUND ENTRY TIME")
    out(f"{'='*80}")

    idx_3m_start = max(0, entry_i - 3 * SR_LOOKBACK_3M)
    sr_high = high[idx_3m_start:entry_i+1:3]
    sr_low = low[idx_3m_start:entry_i+1:3]
    supports, resistances = find_sr_levels(sr_high, sr_low)
    out(f"\n  3m lookback: indices {idx_3m_start} to {entry_i} "
        f"({datetime.fromtimestamp(timestamps[idx_3m_start]/1000).strftime('%m-%d %H:%M')} to "
        f"{dt_entry.strftime('%m-%d %H:%M')})")
    out(f"  Supports found ({len(supports)}): ${', $'.join(f'{s:.2f}' for s in sorted(supports))}" if supports else "  Supports found: None")
    out(f"  Resistances found ({len(resistances)}): ${', $'.join(f'{r:.2f}' for r in sorted(resistances))}" if resistances else "  Resistances found: None")

    # Also get S/R at each add point from stored data
    # Let me recalculate S/R at each add point
    out(f"\n  S/R levels progressive over the trade:")
    for i in range(entry_i, exit_i + 1, 3):
        if i < 3: continue
        idx_start = max(0, i - 3 * SR_LOOKBACK_3M)
        sr_h = high[idx_start:i+1:3]
        sr_l = low[idx_start:i+1:3]
        s, r = find_sr_levels(sr_h, sr_l)
        dt_i = datetime.fromtimestamp(timestamps[i] / 1000)
        if i == entry_i or i % 300 < 3:  # sample every ~5 hours
            out(f"    {dt_i.strftime('%m-%d %H:%M')}: supports={len(s)} resistances={len(r)}")

    # 2. Entry candle
    out(f"\n{'='*80}")
    out("  2. ENTRY CANDLE DETAILS")
    out(f"{'='*80}")
    out(f"\n  Timestamp    : {dt_entry.strftime('%Y-%m-%d %H:%M:%S')}")
    out(f"  Open         : ${open_p[entry_i]:.2f}")
    out(f"  High         : ${high[entry_i]:.2f}")
    out(f"  Low          : ${low[entry_i]:.2f}")
    out(f"  Close        : ${close[entry_i]:.2f}")
    out(f"  Volume       : {df['volume'].values[entry_i]:.2f}")
    out(f"\n  INDICATORS AT ENTRY:")
    out(f"  EMA9         : ${ema9[entry_i]:.2f}")
    out(f"  EMA21        : ${ema21[entry_i]:.2f}")
    out(f"  EMA9 > EMA21 : {ema9[entry_i] > ema21[entry_i]}  (score += 1)")
    out(f"  RSI(14)      : {rsi_val[entry_i]:.2f}")
    out(f"  RSI < 35     : {rsi_val[entry_i] < 35}  (score += 1)")
    out(f"  BB Upper     : ${bb_upper[entry_i]:.2f}")
    out(f"  BB Mid       : ${bb_mid[entry_i]:.2f}")
    out(f"  BB Lower     : ${bb_lower[entry_i]:.2f}")
    out(f"  Close <= BBL*1.001 : {close[entry_i] <= bb_lower[entry_i] * 1.001}  (score += 1)")
    out(f"  Close > Open : {close[entry_i] > open_p[entry_i]}  (score += 1)")
    long_score = sum([ema9[entry_i] > ema21[entry_i], rsi_val[entry_i] < 35,
                      close[entry_i] <= bb_lower[entry_i] * 1.001, close[entry_i] > open_p[entry_i]])
    out(f"  LONG SCORE   : {long_score}/4 >= 3 -> ENTRY")

    # 3. DCA adds
    out(f"\n{'='*80}")
    out("  3. DCA ADD LEVELS AND TRIGGER DETAILS")
    out(f"{'='*80}")

    add_log = target_trade.get("add_log", [])
    if not add_log:
        # Try to reconstruct by replaying
        out("  No add log recorded during pass 1. Replaying adds...")
        pass

    if add_log:
        for add in add_log:
            ai = add["idx"]
            add_dt = datetime.fromtimestamp(timestamps[ai] / 1000)
            out(f"\n  --- ADD #{add['add_num']} @ ${add['price']:.2f} ---")
            out(f"  Trigger Time : {add_dt.strftime('%Y-%m-%d %H:%M:%S')}")
            out(f"  Trigger Bar  : {ai - entry_i} bars after entry")
            out(f"  Candle: O=${open_p[ai]:.2f} H=${high[ai]:.2f} L=${low[ai]:.2f} C=${close[ai]:.2f}")
            out(f"  Price action : low={low[ai]:.2f} <= add_price={add['price']:.2f}? {low[ai] <= add['price']}")
            # Get S/R at this point
            idx_sr = max(0, ai - 3 * SR_LOOKBACK_3M)
            sr_h = high[idx_sr:ai+1:3]
            sr_l = low[idx_sr:ai+1:3]
            srs, srr = find_sr_levels(sr_h, sr_l)
            out(f"  Active S supports: ${', $'.join(f'{x:.2f}' for x in sorted(srs))}" if srs else "  Active S supports: None")
            out(f"  Active R resistances: ${', $'.join(f'{x:.2f}' for x in sorted(srr))}" if srr else "  Active R resistances: None")

            # After this add
            out(f"  New avg entry: ${add['avg_entry']:.2f}")
            out(f"  New SL       : ${add['sl']:.2f}")
            out(f"  New TP       : ${add['tp']:.2f}")
    else:
        out("  No DCA adds were triggered.")

    # 4. Hourly price chart
    out(f"\n{'='*80}")
    out("  4. PRICE CHART (every ~50 bars) FROM ENTRY TO EXIT")
    out(f"{'='*80}")
    out(f"  Period: {dt_entry.strftime('%m-%d %H:%M')} to {dt_exit.strftime('%m-%d %H:%M')} ({target_trade['bars']} bars)")
    out(f"\n  {'Bar':>6} {'Time':>12} {'Open':>8} {'High':>8} {'Low':>8} {'Close':>8} {'EMA9':>8} {'EMA21':>8} {'RSI':>6}")
    out(f"  {'-'*6} {'-'*12} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*6}")

    step = max(1, (exit_i - entry_i) // 50)
    for idx in range(entry_i, exit_i + 1, step):
        dt_i = datetime.fromtimestamp(timestamps[idx] / 1000)
        out(f"  {idx-entry_i:>5d} {dt_i.strftime('%m-%d %H:%M'):>12} ${open_p[idx]:>6.2f} ${high[idx]:>6.2f} ${low[idx]:>6.2f} ${close[idx]:>6.2f} ${ema9[idx]:>6.2f} ${ema21[idx]:>6.2f} {rsi_val[idx]:>5.1f}")
    if (exit_i - entry_i) % step != 0:
        dt_i = datetime.fromtimestamp(timestamps[exit_i] / 1000)
        out(f"  {exit_i-entry_i:>5d} {dt_i.strftime('%m-%d %H:%M'):>12} ${open_p[exit_i]:>6.2f} ${high[exit_i]:>6.2f} ${low[exit_i]:>6.2f} ${close[exit_i]:>6.2f} ${ema9[exit_i]:>6.2f} ${ema21[exit_i]:>6.2f} {rsi_val[exit_i]:>5.1f}")

    # Mark add points
    if add_log:
        out(f"\n  Add point markers:")
        for add in add_log:
            ai = add["idx"]
            add_dt = datetime.fromtimestamp(timestamps[ai] / 1000)
            out(f"    ADD #{add['add_num']} @ ${add['price']:.2f} at bar {ai-entry_i} ({add_dt.strftime('%m-%d %H:%M')})")

    # 5. Short position status
    out(f"\n{'='*80}")
    out("  5. SHORT POSITION STATUS DURING THIS LONG TRADE")
    out(f"{'='*80}")

    # Re-scan for short entries in a window around this trade
    scan_start = max(50, entry_i - 200)
    scan_end = min(len(df), exit_i + 200)
    short_events = []
    local_short_pos = None
    local_short_pending = None
    local_supports = []
    local_resistances = []

    def local_next_resistance_up(price, resistances, used):
        above = [r for r in resistances if r > price and r not in used]
        return min(above) if above else None

    def local_get_next_add(p, side):
        if p["add_count"] >= DCA_MAX_ADDS: return None
        step = p["add_count"] + 1
        fallback_pct = 0.005
        if side == "LONG":
            search_below = p["last_entry"] * 0.995
            ap = next_support_down(search_below, local_supports, p["used_sr"])
            if ap is None: ap = p["last_entry"] * (1 - fallback_pct)
        else:
            search_above = p["last_entry"] * 1.005
            ap = local_next_resistance_up(search_above, local_resistances, p["used_sr"])
            if ap is None: ap = p["last_entry"] * (1 + fallback_pct)
        return {"add_price": ap, "step": step}

    for i in range(scan_start, min(scan_end, exit_i + 1)):
        dt_i = datetime.fromtimestamp(timestamps[i] / 1000)

        if i % 3 == 0 and i >= 3:
            idx_sr = max(0, i - 3 * SR_LOOKBACK_3M)
            sr_h = high[idx_sr:i+1:3]
            sr_l = low[idx_sr:i+1:3]
            local_supports, local_resistances = find_sr_levels(sr_h, sr_l)

        if local_short_pending is not None and local_short_pos is not None:
            if high[i] >= local_short_pending["add_price"]:
                # simplified DCA trigger
                local_short_pos["add_count"] += 1
                local_short_pos["last_entry"] = local_short_pending["add_price"]
                local_short_pos["used_sr"].append(local_short_pending["add_price"])
                local_short_pos["sl"] = local_short_pending["add_price"] * (1 + DCA_SL_PCT/100)
                local_short_pos["tp"] = local_short_pos["avg_entry"] * (1 - TP_PCT/100)
                local_short_pending = local_get_next_add(local_short_pos, "SHORT")
            else:
                pass  # keep pending

        if local_short_pos is None:
            short_score = 0
            if ema9[i] < ema21[i]: short_score += 1
            if rsi_val[i] > 65: short_score += 1
            if close[i] >= bb_upper[i] * 0.999: short_score += 1
            if close[i] < open_p[i]: short_score += 1
            if short_score >= 3:
                local_short_pos = {
                    "side": "SHORT", "avg_entry": close[i], "last_entry": close[i],
                    "qty": 0, "total_margin": 0, "add_count": 0,
                    "tp": close[i] * (1 - TP_PCT/100),
                    "sl": close[i] * (1 + DCA_SL_PCT/100),
                    "entry_idx": i, "entry_fees": 0,
                    "entry_time": dt_i.strftime("%m-%d %H:%M"), "used_sr": []
                }
                ap = local_next_resistance_up(close[i] * 1.005, local_resistances, [])
                if ap is None: ap = close[i] * 1.005
                local_short_pending = {"add_price": ap, "step": 1}
                short_events.append(("OPEN", i, close[i], dt_i.strftime("%m-%d %H:%M")))

        if local_short_pos is not None:
            hit = False
            if low[i] <= local_short_pos["tp"]: hit = True
            if not hit and high[i] >= local_short_pos["sl"]: hit = True
            if hit:
                short_events.append(("CLOSE", i, close[i], dt_i.strftime("%m-%d %H:%M")))
                local_short_pos = None
                local_short_pending = None

    active_during_trade = any(entry_i <= e[1] <= exit_i for e in short_events if e[0] == "OPEN")
    if active_during_trade:
        out("  YES - SHORT position was active during this LONG trade.")
    else:
        out("  NO - No SHORT position was active during this LONG trade.")

    out(f"\n  SHORT events in range ({datetime.fromtimestamp(timestamps[scan_start]/1000).strftime('%m-%d %H:%M')} to {datetime.fromtimestamp(timestamps[min(scan_end, len(df)-1)]/1000).strftime('%m-%d %H:%M')}):")
    for evt in short_events:
        marker = " *** DURING LONG ***" if entry_i <= evt[1] <= exit_i else ""
        out(f"    {evt[3]} | {evt[0]:5s} SHORT @ ${evt[2]:.2f}{marker}")

    # 6. Indicator values at each add (detailed)
    out(f"\n{'='*80}")
    out("  6. ALL INDICATOR VALUES AT EACH ADD POINT")
    out(f"{'='*80}")

    if add_log:
        for add in add_log:
            ai = add["idx"]
            out(f"\n  --- ADD #{add['add_num']} @ ${add['price']:.2f} ({add['time']}) ---")
            out(f"  Candle    : O=${open_p[ai]:.2f} H=${high[ai]:.2f} L=${low[ai]:.2f} C=${close[ai]:.2f}")
            out(f"  EMA9      : ${ema9[ai]:.2f}")
            out(f"  EMA21     : ${ema21[ai]:.2f}")
            out(f"  RSI(14)   : {rsi_val[ai]:.2f}")
            out(f"  BB Lower  : ${bb_lower[ai]:.2f}")
            out(f"  BB Mid    : ${bb_mid[ai]:.2f}")
            out(f"  BB Upper  : ${bb_upper[ai]:.2f}")
            out(f"  Volume    : {df['volume'].values[ai]:.2f}")
            # S/R at this point
            idx_sr = max(0, ai - 3 * SR_LOOKBACK_3M)
            sr_h = high[idx_sr:ai+1:3]
            sr_l = low[idx_sr:ai+1:3]
            srs, srr = find_sr_levels(sr_h, sr_l)
            out(f"  S/R Supports  : ${', $'.join(f'{x:.2f}' for x in sorted(srs))}" if srs else "  S/R Supports  : None")
            out(f"  S/R Resistances: ${', $'.join(f'{x:.2f}' for x in sorted(srr))}" if srr else "  S/R Resistances: None")
            # Entry signal check at this bar
            add_score = sum([ema9[ai] > ema21[ai], rsi_val[ai] < 35,
                             close[ai] <= bb_lower[ai] * 1.001, close[ai] > open_p[ai]])
            out(f"  LONG signal at this bar? score={add_score}/4")
    else:
        out("  No adds to analyze.")

    out(f"\n{'='*80}")
    out("  END OF ANALYSIS")
    out(f"{'='*80}")

    with open(r"C:\Users\svkts\OneDrive\Belgeler\Default Project\bot\analysis_jun5.txt", "w") as f:
        f.write("\n".join(out_lines))

    print(f"\nAnalysis saved to analysis_jun5.txt")


if __name__ == "__main__":
    end = datetime.now()
    start = end - timedelta(days=BACKTEST_DAYS)
    print(f"Fetching {SYMBOL} {TIMEFRAME} data from {start.strftime('%Y-%m-%d %H:%M:%S')}...")
    df = fetch_klines_range(start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S"))
    if len(df) < 100:
        print(f"Not enough data: {len(df)}")
        exit()
    FETCHED_DF = df

    print(f"\n--- Pass 1: Finding target trade ---")
    target_trade, all_trades, all_supports, all_resistances = find_target_trade(df)

    if target_trade is None:
        print("\n[ERROR] Target trade not found (LONG, adds=4, dca_stop on or near June 5)")
        print("Listing all dca_stop LONG trades:")
        for t in all_trades:
            if t["side"] == "LONG" and t["reason"] == "dca_stop":
                print(f"  {t['entry_time']} -> {t['time']} adds:{t['adds']} pnl:{t['pnl']}")
        print("\nAll LONG trades on June 5:")
        for t in all_trades:
            if t["side"] == "LONG" and ("06-05" in t.get("entry_time","") or "06-05" in t.get("time","")):
                print(f"  entry:{t['entry_time']} exit:{t['time']} adds:{t['adds']} reason:{t['reason']} pnl:{t['pnl']}")
        exit()

    print(f"\nFound target trade: entry={target_trade['entry_time']} exit={target_trade['time']} adds={target_trade['adds']} reason={target_trade['reason']} pnl=${target_trade['pnl']:.2f}")

    print(f"\n--- Pass 2: Detailed replay ---")
    replay_trade(df, target_trade)
