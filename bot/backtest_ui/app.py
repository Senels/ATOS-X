"""
XAUUSDT Backtest Portal — Flask API
Provides: data fetching, backtest execution, optimization, scenario comparison.
"""
import json, os, sys, time, random
import numpy as np
import pandas as pd
import requests
from datetime import datetime, timedelta
from flask import Flask, jsonify, render_template, request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config import Config

app = Flask(__name__, template_folder=os.path.join(os.path.dirname(__file__), "templates"))
BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
HISTORY_DIR = os.path.join(BASE, Config.HISTORY_DIR)
BINANCE_FUTURES = "https://fapi.binance.com"

# ── Binance data ──

def fetch_klines_range(symbol, interval="1m", start_time=None, end_time=None, limit=1500, max_iters=20):
    """Fetch klines with optional start/end time. Returns list of bar dicts."""
    url = f"{BINANCE_FUTURES}/fapi/v1/klines"
    all_bars = []
    params = {"symbol": symbol, "interval": interval, "limit": min(limit, 1500)}
    if start_time:
        params["startTime"] = int(start_time)
    if end_time:
        params["endTime"] = int(end_time)

    for _ in range(max_iters):
        try:
            resp = requests.get(url, params=params, timeout=15)
            data = resp.json()
            if isinstance(data, dict) and "code" in data:
                return None
            if not data:
                break
            bars = [{
                "t": int(k[0]), "o": float(k[1]), "h": float(k[2]),
                "l": float(k[3]), "c": float(k[4]), "v": float(k[5]),
            } for k in data]
            all_bars.extend(bars)
            if len(data) < min(limit, 1500):
                break
            params["startTime"] = data[-1][0] + 1
        except Exception:
            break
    return all_bars if all_bars else None


def fetch_klines_df(symbol, interval="1m", limit=5000, start_date=None, end_date=None):
    """Fetch OHLCV data as DataFrame. Supports date range or limit-based fetch."""
    start_ts = None
    end_ts = None

    if start_date:
        dt = datetime.strptime(start_date, "%Y-%m-%d")
        start_ts = int(dt.timestamp() * 1000)
    if end_date:
        dt = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
        end_ts = int(dt.timestamp() * 1000)

    if start_ts:
        bars = fetch_klines_range(symbol, interval, start_time=start_ts, end_time=end_ts, limit=1500)
    else:
        bars = fetch_klines_range(symbol, interval, limit=limit)

    if not bars:
        return None

    df = pd.DataFrame(bars)
    df.columns = ["timestamp", "open", "high", "low", "close", "volume"]
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms")

    # Filter by exact range if both dates given
    if start_date and end_date and start_ts and end_ts:
        df = df[(df["timestamp"] >= start_ts) & (df["timestamp"] <= end_ts)]

    return df


# ── Range Detector (server-side) ──

def backtest_range_detector(df, p):
    o = df["open"].values
    h = df["high"].values
    l = df["low"].values
    c = df["close"].values
    n = len(c)

    length = int(p.get("rd_length", 20))
    mult = float(p.get("rd_mult", 2.0))
    atr_len = int(p.get("rd_atr_len", 14))
    smooth = int(p.get("rd_smooth", 1))
    sl_atr = float(p.get("sl_atr", 1.5))
    tp_atr = float(p.get("tp_atr", 3.0))
    leverage = float(p.get("leverage", 1.0))
    pos_pct = float(p.get("position_pct", 10)) / 100.0
    start_capital = float(p.get("start_capital", 10000))

    tr = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i-1]), abs(l[i] - c[i-1]))
    tr[0] = h[0] - l[0]

    atr = np.zeros(n)
    atr[0] = tr[0]
    for i in range(1, n):
        atr[i] = (atr[i-1] * (atr_len - 1) + tr[i]) / atr_len

    hh = np.zeros(n)
    ll = np.zeros(n)
    for i in range(n):
        s = max(0, i - length + 1)
        hh[i] = np.max(h[s:i+1])
        ll[i] = np.min(l[s:i+1])

    center = (hh + ll) / 2
    if smooth > 1:
        kernel = np.ones(smooth) / smooth
        center = np.convolve(center, kernel, mode="same")

    upper = center + atr * mult
    lower = center - atr * mult
    warmup = max(length * 2, atr_len * 3, 60)

    capital = start_capital
    peak = capital
    in_long = in_short = False
    entry_price = entry_bar = sl_price = tp_price = 0.0
    trades = []
    eq = [capital]

    for i in range(1, n):
        if i < warmup or atr[i] == 0 or np.isnan(atr[i]):
            eq.append(capital)
            continue

        long_sig = not in_long and not in_short and bool(c[i] > upper[i]) and bool(c[i] > o[i])
        short_sig = not in_short and not in_long and bool(c[i] < lower[i]) and bool(c[i] < o[i])

        if in_long:
            exited = False
            if l[i] <= sl_price:
                exit_p = float(sl_price)
                pnl = (exit_p - entry_price) / entry_price * leverage * capital * pos_pct
                capital += pnl
                trades.append(dict(entry=float(entry_price), exit=exit_p, pnl=pnl, pnl_pct=(exit_p-entry_price)/entry_price*100*leverage, dir="LONG", entry_bar=int(entry_bar), exit_bar=i, win=bool(pnl > 0)))
                in_long = False; exited = True
            elif h[i] >= tp_price:
                exit_p = float(tp_price)
                pnl = (exit_p - entry_price) / entry_price * leverage * capital * pos_pct
                capital += pnl
                trades.append(dict(entry=float(entry_price), exit=exit_p, pnl=pnl, pnl_pct=(exit_p-entry_price)/entry_price*100*leverage, dir="LONG", entry_bar=int(entry_bar), exit_bar=i, win=True))
                in_long = False; exited = True
            elif short_sig:
                exit_p = float(c[i])
                pnl = (exit_p - entry_price) / entry_price * leverage * capital * pos_pct
                capital += pnl
                trades.append(dict(entry=float(entry_price), exit=exit_p, pnl=pnl, pnl_pct=(exit_p-entry_price)/entry_price*100*leverage, dir="LONG", entry_bar=int(entry_bar), exit_bar=i, win=bool(pnl > 0)))
                in_long = False; exited = True
            if exited:
                eq.append(capital); peak = max(peak, capital); continue

        if in_short:
            exited = False
            if h[i] >= sl_price:
                exit_p = float(sl_price)
                pnl = (entry_price - exit_p) / entry_price * leverage * capital * pos_pct
                capital += pnl
                trades.append(dict(entry=float(entry_price), exit=exit_p, pnl=pnl, pnl_pct=(entry_price-exit_p)/entry_price*100*leverage, dir="SHORT", entry_bar=int(entry_bar), exit_bar=i, win=bool(pnl > 0)))
                in_short = False; exited = True
            elif l[i] <= tp_price:
                exit_p = float(tp_price)
                pnl = (entry_price - exit_p) / entry_price * leverage * capital * pos_pct
                capital += pnl
                trades.append(dict(entry=float(entry_price), exit=exit_p, pnl=pnl, pnl_pct=(entry_price-exit_p)/entry_price*100*leverage, dir="SHORT", entry_bar=int(entry_bar), exit_bar=i, win=True))
                in_short = False; exited = True
            elif long_sig:
                exit_p = float(c[i])
                pnl = (entry_price - exit_p) / entry_price * leverage * capital * pos_pct
                capital += pnl
                trades.append(dict(entry=float(entry_price), exit=exit_p, pnl=pnl, pnl_pct=(entry_price-exit_p)/entry_price*100*leverage, dir="SHORT", entry_bar=int(entry_bar), exit_bar=i, win=bool(pnl > 0)))
                in_short = False; exited = True
            if exited:
                eq.append(capital); peak = max(peak, capital); continue

        if long_sig and not in_long and not in_short:
            entry_price = float(c[i]); entry_bar = i
            sl_price = entry_price - float(atr[i]) * sl_atr
            tp_price = entry_price + float(atr[i]) * tp_atr
            in_long = True
        elif short_sig and not in_short and not in_long:
            entry_price = float(c[i]); entry_bar = i
            sl_price = entry_price + float(atr[i]) * sl_atr
            tp_price = entry_price - float(atr[i]) * tp_atr
            in_short = True

        peak = max(peak, capital)
        eq.append(capital)

    return trades, eq


def compute_summary(trades, eq, params, start_capital):
    if not trades:
        return dict(final_equity=start_capital, total_return_pct=0, total_trades=0, win_rate=0, wins=0, losses=0, total_pnl=0, profit_factor=0, avg_win=0, avg_loss=0, max_drawdown_pct=0, sharpe_ratio=0, sortino_ratio=0, avg_bars_held=0, total_commission=0, avg_trade_usd=0, avg_win_pct=0, avg_loss_pct=0, max_win=0, max_loss=0, max_win_pct=0, max_loss_pct=0, max_consec_wins=0, max_consec_losses=0, gross_profit=0, gross_loss=0)

    wins_t = [t for t in trades if t["win"]]
    losses_t = [t for t in trades if not t["win"]]
    total_pnl = sum(t["pnl"] for t in trades)
    gross_profit = sum(t["pnl"] for t in wins_t)
    gross_loss = abs(sum(t["pnl"] for t in losses_t))
    pf = gross_profit / gross_loss if gross_loss > 0 else gross_profit if gross_profit > 0 else 0
    final_eq = eq[-1] if eq else start_capital
    total_ret = (final_eq - start_capital) / start_capital * 100

    peak = np.maximum.accumulate(eq)
    dd = (peak - eq) / peak
    max_dd = float(np.max(dd) * 100)

    rets = np.diff(eq) / eq[:-1] if len(eq) > 1 else [0]
    sharpe = float(np.mean(rets) / np.std(rets) * np.sqrt(365)) if len(rets) > 0 and np.std(rets) > 0 else 0
    neg_rets = rets[rets < 0]
    sortino = float(np.mean(rets) / np.sqrt(np.mean(neg_rets**2)) * np.sqrt(365)) if len(neg_rets) > 0 and np.mean(neg_rets**2) > 0 else 0

    avg_bars = np.mean([(t["exit_bar"] - t["entry_bar"]) for t in trades]) if trades else 0
    avg_win = float(np.mean([t["pnl"] for t in wins_t])) if wins_t else 0
    avg_loss = float(np.mean([t["pnl"] for t in losses_t])) if losses_t else 0
    avg_win_pct = float(np.mean([t["pnl_pct"] for t in wins_t])) if wins_t else 0
    avg_loss_pct = float(np.mean([t["pnl_pct"] for t in losses_t])) if losses_t else 0
    max_win = float(max(t["pnl"] for t in wins_t)) if wins_t else 0
    max_loss = float(min(t["pnl"] for t in losses_t)) if losses_t else 0
    max_win_pct = float(max(t["pnl_pct"] for t in wins_t)) if wins_t else 0
    max_loss_pct = float(min(t["pnl_pct"] for t in losses_t)) if losses_t else 0

    consec_w = consec_l = max_cw = max_cl = 0
    for t in trades:
        if t["win"]: consec_w += 1; consec_l = 0; max_cw = max(max_cw, consec_w)
        else: consec_l += 1; consec_w = 0; max_cl = max(max_cl, consec_l)

    return dict(
        final_equity=round(final_eq, 2), total_return_pct=round(total_ret, 2),
        total_trades=len(trades), win_rate=round(len(wins_t)/len(trades)*100, 2) if trades else 0,
        wins=len(wins_t), losses=len(losses_t), total_pnl=round(total_pnl, 2),
        profit_factor=round(pf, 2), avg_win=round(avg_win, 2), avg_loss=round(avg_loss, 2),
        avg_win_pct=round(avg_win_pct, 2), avg_loss_pct=round(avg_loss_pct, 2),
        max_win=round(max_win, 2), max_loss=round(max_loss, 2),
        max_win_pct=round(max_win_pct, 2), max_loss_pct=round(max_loss_pct, 2),
        max_drawdown_pct=round(max_dd, 2), sharpe_ratio=round(sharpe, 2),
        sortino_ratio=round(sortino, 2), avg_bars_held=round(avg_bars, 2),
        total_commission=round(sum(t.get("commission", 0) for t in trades), 2),
        avg_trade_usd=round(total_pnl / len(trades), 2) if trades else 0,
        max_consec_wins=max_cw, max_consec_losses=max_cl,
        gross_profit=round(gross_profit, 2), gross_loss=round(gross_loss, 2),
    )


# ── Routes ──

@app.route("/")
def index():
    return render_template("backtest.html")


@app.route("/api/backtest/run", methods=["POST"])
def api_run_backtest():
    p = request.get_json() or {}
    symbol = p.get("symbol", "XAUUSDT")
    interval = p.get("timeframe", "1m")
    limit = int(p.get("data_count", 5000))
    start_date = p.get("start_date") or None
    end_date = p.get("end_date") or None

    df = fetch_klines_df(symbol, interval, limit, start_date, end_date)
    if df is None or len(df) < 100:
        return jsonify({"success": False, "error": f"Could not fetch data for {symbol}"})
    if start_date or end_date:
        limit = len(df)

    trades, eq = backtest_range_detector(df, p)

    scenarios = []
    for lev in [1, 3, 5, 10, 15, 20]:
        sp = dict(p)
        sp["leverage"] = lev
        st, seq = backtest_range_detector(df, sp)
        ss = compute_summary(st, seq, sp, float(p.get("start_capital", 10000)))
        ss["label"] = f"{lev}x"
        ss["position_pct"] = int(p.get("position_pct", 10))
        scenarios.append(ss)

    summary = compute_summary(trades, eq, p, float(p.get("start_capital", 10000)))
    summary["data_bars"] = len(df)

    return jsonify({
        "success": True,
        "trades": trades,
        "equity_curve": [round(e, 2) for e in eq],
        "summary": summary,
        "scenarios": scenarios,
        "params": p,
    })


@app.route("/api/backtest/optimize", methods=["POST"])
def api_optimize():
    p = request.get_json() or {}
    symbol = p.get("symbol", "XAUUSDT")
    interval = p.get("timeframe", "1m")
    limit = int(p.get("data_count", 5000))
    start_date = p.get("start_date") or None
    end_date = p.get("end_date") or None

    df = fetch_klines_df(symbol, interval, limit, start_date, end_date)
    if df is None or len(df) < 100:
        return jsonify({"success": False, "error": "Could not fetch data"})

    rd_lengths = [10, 20, 30, 50]
    rd_mults = [1.5, 2.0, 2.5, 3.0]
    rd_atr_lens = [7, 14, 21]
    sl_atrs = [1.0, 1.5, 2.0, 2.5]
    tp_atrs = [2.0, 3.0, 4.0, 5.0]

    best_score = -9999
    best_params = None
    best_trades = None
    best_eq = None

    for rl in rd_lengths:
        for rm in rd_mults:
            for ra in rd_atr_lens:
                for sl in sl_atrs:
                    for tp in tp_atrs:
                        sp = dict(p)
                        sp.update(rd_length=rl, rd_mult=rm, rd_atr_len=ra, sl_atr=sl, tp_atr=tp)
                        tr, eq_curve = backtest_range_detector(df, sp)
                        if not tr:
                            continue
                        wins = sum(1 for t in tr if t["win"])
                        total = len(tr)
                        wr = wins / total if total > 0 else 0
                        net_pnl = sum(t["pnl"] for t in tr)
                        score = net_pnl * (wr ** 0.5)
                        if score > best_score:
                            best_score = score
                            best_params = sp
                            best_trades = tr
                            best_eq = eq_curve

    if best_params is None:
        return jsonify({"success": False, "error": "No valid parameter combination found"})

    bp = {k: v for k, v in best_params.items() if k in ("rd_length", "rd_mult", "rd_atr_len", "sl_atr", "tp_atr")}
    summary = compute_summary(best_trades, best_eq, best_params, float(p.get("start_capital", 10000)))

    return jsonify({
        "success": True,
        "best_params": bp,
        "best_score": round(best_score, 2),
        "trades": best_trades,
        "equity_curve": [round(e, 2) for e in best_eq] if best_eq else [],
        "summary": summary,
        "params": best_params,
    })


@app.route("/api/data/fetch", methods=["POST"])
def api_fetch_data():
    p = request.get_json() or {}
    symbol = p.get("symbol", "XAUUSDT")
    interval = p.get("timeframe", "1m")
    limit = int(p.get("limit", 5000))

    df = fetch_klines_df(symbol, interval, limit)
    if df is None:
        return jsonify({"success": False, "error": f"Could not fetch {symbol} data"})

    return jsonify({"success": True, "symbol": symbol, "bars": len(df)})


@app.route("/api/data/ohlcv")
def api_ohlcv():
    symbol = request.args.get("symbol", "XAUUSDT")
    interval = request.args.get("timeframe", "1m")
    limit = int(request.args.get("limit", 500))
    start_date = request.args.get("start_date") or None
    end_date = request.args.get("end_date") or None

    if start_date:
        dt = datetime.strptime(start_date, "%Y-%m-%d")
        start_ts = int(dt.timestamp() * 1000)
        end_ts = None
        if end_date:
            dt = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
            end_ts = int(dt.timestamp() * 1000)
        bars = fetch_klines_range(symbol, interval, start_time=start_ts, end_time=end_ts, limit=limit)
        if bars and len(bars) > limit:
            bars = bars[-limit:]
    else:
        bars = fetch_klines_range(symbol, interval, limit=limit)

    if not bars:
        return jsonify({"success": False, "error": f"No data for {symbol}"})

    return jsonify({"success": True, "bars": bars})


@app.route("/api/coins")
def api_coins():
    coins = ["XAUUSDT", "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT",
             "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT",
             "BNBUSDT", "MATICUSDT", "UNIUSDT", "LTCUSDT", "ATOMUSDT"]
    return jsonify(coins)


if __name__ == "__main__":
    os.makedirs(HISTORY_DIR, exist_ok=True)
    print("XAUUSDT Backtest Portal starting...")
    print(f"  Data dir: {HISTORY_DIR}")
    app.run(debug=True, port=5000)
