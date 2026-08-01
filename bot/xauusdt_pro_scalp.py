import os, time, json, hmac, hashlib, requests, urllib.parse
from datetime import datetime
from dotenv import load_dotenv
import pandas as pd
import numpy as np

load_dotenv()

# ─── CONFIG ────────────────────────────────────────────────────────────────
SYMBOL = "XAUUSDT"
CAPITAL = 1000.0
LEVERAGE = 100
USE_TESTNET = os.getenv("USE_TESTNET", "true").lower() == "true"
LIVE_TRADING = os.getenv("LIVE_TRADING", "false").lower() == "true"
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")

FUTURES_BASE = "https://testnet.binancefuture.com" if USE_TESTNET else "https://fapi.binance.com"
LOG_FILE = "xauusdt_pro_bot_log.json"

# ─── INDICATOR PARAMS ───────────────────────────────────────────────────────
EMA50, EMA200 = 50, 200
SRSI_LEN, SRSI_K, SRSI_D = 5, 3, 3
SRSI_OS, SRSI_OB = 20, 80
MACD_FAST, MACD_SLOW, MACD_SIG = 12, 26, 9
CCI_LEN, CCI_OS, CCI_OB = 20, -100, 100
ATR_LEN = 14

TP_PCT, SL_PCT = 0.25, 0.15
MG_BASE_PCT, MG_MULT, MG_MAX = 0.5, 1.5, 4
MIN_SCORE = 4

DAILY_LOSS_LIMIT = 50.0
MAX_CONSECUTIVE_LOSSES = 6
POSITION_CHECK_INTERVAL = 5
HTF_UPDATE_INTERVAL = 300

# ─── BINANCE API ────────────────────────────────────────────────────────────
def binance_request(method, endpoint, params=None, signed=False):
    url = FUTURES_BASE + endpoint
    headers = {"X-MBX-APIKEY": BINANCE_API_KEY} if BINANCE_API_KEY else {}
    if signed:
        params = params or {}
        qs = urllib.parse.urlencode(params)
        params["signature"] = hmac.new(
            BINANCE_API_SECRET.encode(), qs.encode(), hashlib.sha256
        ).hexdigest()
    r = requests.request(method, url, params=params, headers=headers, timeout=10)
    if r.status_code != 200:
        raise Exception(f"API error {r.status_code}: {r.text}")
    return r.json()

def fetch_klines(tf="1m", limit=500):
    data = binance_request("GET", "/fapi/v1/klines",
        {"symbol": SYMBOL, "interval": tf, "limit": limit})
    rows = []
    for k in data:
        rows.append({"timestamp": int(k[0]), "open": float(k[1]),
                      "high": float(k[2]), "low": float(k[3]),
                      "close": float(k[4]), "volume": float(k[5])})
    return pd.DataFrame(rows)

def get_current_price():
    return float(binance_request("GET", "/fapi/v1/ticker/price", {"symbol": SYMBOL})["price"])

def set_leverage():
    if LIVE_TRADING:
        binance_request("POST", "/fapi/v1/leverage",
            {"symbol": SYMBOL, "leverage": LEVERAGE}, signed=True)

def set_isolated():
    if LIVE_TRADING:
        try:
            binance_request("POST", "/fapi/v1/marginType",
                {"symbol": SYMBOL, "marginType": "ISOLATED"}, signed=True)
        except Exception as e:
            if "No need to change" not in str(e):
                raise

def get_lot_step():
    info = binance_request("GET", "/fapi/v1/exchangeInfo")
    for s in info["symbols"]:
        if s["symbol"] == SYMBOL:
            pp = int(s.get("pricePrecision", 2))
            for f in s["filters"]:
                if f["filterType"] == "LOT_SIZE":
                    return float(f["stepSize"]), pp
    return 0.001, 2

def get_account_balance():
    if not LIVE_TRADING:
        return CAPITAL - position_manager.total_used_margin if hasattr(position_manager, 'total_used_margin') else CAPITAL
    acc = binance_request("GET", "/fapi/v2/account", signed=True)
    for a in acc.get("assets", []):
        if a["asset"] == "USDT":
            return float(a["walletBalance"])
    return CAPITAL

# ─── INDICATORS ────────────────────────────────────────────────────────────
def ema(data, period):
    result = np.zeros_like(data, dtype=float)
    mult = 2 / (period + 1)
    result[0] = data[0]
    for i in range(1, len(data)):
        result[i] = (data[i] - result[i-1]) * mult + result[i-1]
    return result

def sma(data, period):
    result = np.zeros_like(data, dtype=float)
    for i in range(len(data)):
        result[i] = np.mean(data[max(0, i-period+1):i+1])
    return result

def rsi(data, period=14):
    delta = np.diff(data, prepend=data[0])
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)
    avg_gain = sma(gain, period)
    avg_loss = sma(loss, period)
    rs = np.divide(avg_gain, avg_loss, out=np.ones_like(avg_gain), where=avg_loss != 0)
    return 100 - (100 / (1 + rs))

def stoch_rsi(close, length=5, k_smooth=3, d_smooth=3):
    r = rsi(close, length)
    n = len(r)
    ll = np.array([np.min(r[max(0, i-length+1):i+1]) for i in range(n)], dtype=float)
    hh = np.array([np.max(r[max(0, i-length+1):i+1]) for i in range(n)], dtype=float)
    stoch = np.divide(r - ll, np.maximum(hh - ll, 0.001)) * 100
    k = sma(stoch, k_smooth)
    d = sma(k, d_smooth)
    return k, d

def macd(data, fast=12, slow=26, sig=9):
    ef = ema(data, fast)
    es = ema(data, slow)
    line = ef - es
    signal = ema(line, sig)
    hist = line - signal
    return line, signal, hist

def cci(high, low, close, period=20):
    tp = (high + low + close) / 3
    tp_sma = sma(tp, period)
    mad = np.array([np.mean(np.abs(tp[max(0,i-period+1):i+1] - tp_sma[i])) for i in range(len(tp))])
    mad = np.maximum(mad, 0.001)
    return (tp - tp_sma) / (0.015 * mad)

def atr(high, low, close, period=14):
    tr = np.maximum(high - low,
                    np.abs(high - np.roll(close, 1)),
                    np.abs(low - np.roll(close, 1)))
    tr[0] = high[0] - low[0]
    return sma(tr, period)

def daily_fib_pivot(df_daily):
    last = df_daily.iloc[-2] if len(df_daily) > 1 else df_daily.iloc[-1]
    dh = last["high"]; dl = last["low"]; dc = last["close"]
    pp = (dh + dl + dc) / 3
    r1 = pp * 2 - dl; r2 = pp + (dh - dl)
    s1 = pp * 2 - dh; s2 = pp - (dh - dl)
    return pp, r1, r2, s1, s2

# ─── SIGNAL (SCORING 4/5) ─────────────────────────────────────────────────
last_htf_update = 0
cached_trend = None

def get_htf_trend():
    global last_htf_update, cached_trend
    now = time.time()
    if now - last_htf_update < HTF_UPDATE_INTERVAL and cached_trend is not None:
        return cached_trend
    df5 = fetch_klines("5m", 250)
    if df5 is None or len(df5) < 200:
        return cached_trend
    c5 = df5["close"].values
    e50 = ema(c5, EMA50)[-1]
    e200 = ema(c5, EMA200)[-1]
    cached_trend = e50 > e200
    last_htf_update = now
    return cached_trend

def calc_score(close_i, high_i, low_i, trend_up, s1, r1, srsi_kv_i, srsi_dv_i, macd_h_i, macd_l_i, macd_s_i, cci_i):
    sl, ss = 0, 0
    if trend_up: sl += 1
    else: ss += 1
    if close_i <= s1: sl += 1
    if close_i >= r1: ss += 1
    if srsi_kv_i < SRSI_OS and srsi_dv_i < SRSI_OS: sl += 1
    if srsi_kv_i > SRSI_OB and srsi_dv_i > SRSI_OB: ss += 1
    if macd_h_i > 0 and macd_l_i > macd_s_i: sl += 1
    if macd_h_i < 0 and macd_l_i < macd_s_i: ss += 1
    if cci_i < CCI_OS: sl += 1
    if cci_i > CCI_OB: ss += 1
    return sl, ss

def generate_signals(df, df_daily):
    close = df["close"].values
    high = df["high"].values
    low = df["low"].values

    srsi_kv, srsi_dv = stoch_rsi(close, SRSI_LEN, SRSI_K, SRSI_D)
    macd_l, macd_s, macd_h = macd(close, MACD_FAST, MACD_SLOW, MACD_SIG)
    cci_v = cci(high, low, close, CCI_LEN)
    atr_v = atr(high, low, close, ATR_LEN)
    trend_up = get_htf_trend()
    pp, r1, r2, s1, s2 = daily_fib_pivot(df_daily)

    sl, ss = calc_score(close[-1], high[-1], low[-1],
                         trend_up if trend_up is not None else True,
                         s1, r1,
                         srsi_kv[-1], srsi_dv[-1],
                         macd_h[-1], macd_l[-1], macd_s[-1],
                         cci_v[-1])

    long_signal = sl >= MIN_SCORE
    short_signal = ss >= MIN_SCORE

    info = {
        "price": close[-1], "trend_up": trend_up,
        "srsi_k": srsi_kv[-1], "srsi_d": srsi_dv[-1],
        "macd_l": macd_l[-1], "macd_s": macd_s[-1], "macd_h": macd_h[-1],
        "cci": cci_v[-1], "atr": atr_v[-1],
        "s1": s1, "s2": s2, "r1": r1, "r2": r2,
        "score_l": sl, "score_s": ss
    }
    return long_signal, short_signal, info

# ─── POSITION MANAGER (Martingale) ─────────────────────────────────────────
class ProPositionManager:
    def __init__(self):
        self.total_used_margin = 0.0
        self.current_position = None
        self.consecutive_losses = 0
        self.daily_pnl = 0.0
        self.trade_count = 0
        self.total_pnl = 0.0
        self.last_trade_day = datetime.now().day
        self.trade_log = []
        self.load_log()

    def load_log(self):
        if os.path.exists(LOG_FILE):
            try:
                with open(LOG_FILE) as f:
                    data = json.load(f)
                    self.trade_log = data.get("trades", [])
                    self.total_pnl = data.get("total_pnl", 0)
                    self.consecutive_losses = data.get("consecutive_losses", 0)
                    self.daily_pnl = data.get("daily_pnl", 0)
            except: pass

    def save_log(self):
        with open(LOG_FILE, "w") as f:
            json.dump({
                "trades": self.trade_log[-500:],
                "total_pnl": round(self.total_pnl, 2),
                "consecutive_losses": self.consecutive_losses,
                "daily_pnl": round(self.daily_pnl, 2)
            }, f, indent=2)

    def reset_daily(self):
        today = datetime.now().day
        if today != self.last_trade_day:
            self.daily_pnl = 0.0
            self.last_trade_day = today

    def can_trade(self):
        self.reset_daily()
        if self.consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
            return False
        if self.daily_pnl <= -DAILY_LOSS_LIMIT:
            return False
        return True

    def calc_qty(self, margin, price):
        step_size, _ = get_lot_step()
        qty = margin * LEVERAGE / price
        raw = len(str(step_size).split(".")[1].rstrip("0")) if "." in str(step_size) else 0
        return round(qty // step_size * step_size, raw)

    def mg_size(self, lvl):
        return MG_BASE_PCT / 100 * (MG_MULT ** (lvl - 1))

    def open_first(self, side, entry_price):
        margin = self.mg_size(1) * CAPITAL
        qty = self.calc_qty(margin, entry_price)
        if qty * entry_price < 5:
            return False

        if LIVE_TRADING:
            try:
                set_leverage(); set_isolated()
                side_b = "BUY" if side == "LONG" else "SELL"
                order = binance_request("POST", "/fapi/v1/order", {
                    "symbol": SYMBOL, "side": side_b, "type": "MARKET", "quantity": qty
                }, signed=True)
                fill = float(order.get("avgPrice", entry_price))
            except Exception as e:
                print(f"  ORDER ERROR: {e}"); return False
        else:
            fill = entry_price

        self.current_position = {
            "side": side,
            "entries": [{"price": fill, "qty": qty}],
            "mg_lvl": 1,
            "avg_entry": fill,
            "total_qty": qty,
            "total_margin": margin,
            "last_entry": fill,
            "open_time": time.time()
        }
        self.total_used_margin += margin
        print(f"  ENTRY {side} MG1 @ ${fill:.2f} | Qty: {qty} | Margin: ${margin:.2f}")
        return True

    def add_level(self, add_price):
        p = self.current_position
        if p is None or p["mg_lvl"] >= MG_MAX:
            return False
        lvl = p["mg_lvl"] + 1
        margin = self.mg_size(lvl) * CAPITAL
        qty = self.calc_qty(margin, add_price)
        if qty * add_price < 5:
            return False

        if LIVE_TRADING:
            try:
                side_b = "BUY" if p["side"] == "LONG" else "SELL"
                order = binance_request("POST", "/fapi/v1/order", {
                    "symbol": SYMBOL, "side": side_b, "type": "MARKET", "quantity": qty
                }, signed=True)
                fill = float(order.get("avgPrice", add_price))
            except Exception as e:
                print(f"  ADD ERROR: {e}"); return False
        else:
            fill = add_price

        p["entries"].append({"price": fill, "qty": qty})
        p["mg_lvl"] = lvl
        p["total_qty"] += qty
        p["total_margin"] += margin
        p["last_entry"] = fill
        total_cost = sum(e["price"] * e["qty"] for e in p["entries"])
        p["avg_entry"] = total_cost / p["total_qty"]
        self.total_used_margin += margin
        print(f"  ADD {p['side']} MG{lvl} @ ${fill:.2f} | +{qty} | Avg: ${p['avg_entry']:.2f}")
        return True

    def check_position(self, current_price):
        p = self.current_position
        if p is None:
            return None
        tp = p["avg_entry"] * (1 + TP_PCT / 100) if p["side"] == "LONG" else p["avg_entry"] * (1 - TP_PCT / 100)
        sl = p["last_entry"] * (1 - SL_PCT / 100) if p["side"] == "LONG" else p["last_entry"] * (1 + SL_PCT / 100)
        if p["side"] == "LONG":
            if current_price >= tp:
                return self.close_position(current_price, "take_profit")
            if current_price <= sl:
                return self.close_position(current_price, "stop_loss")
        else:
            if current_price <= tp:
                return self.close_position(current_price, "take_profit")
            if current_price >= sl:
                return self.close_position(current_price, "stop_loss")
        return None

    def close_position(self, exit_price, reason):
        p = self.current_position
        if p is None:
            return None
        if p["side"] == "LONG":
            pnl = (exit_price - p["avg_entry"]) * p["total_qty"]
        else:
            pnl = (p["avg_entry"] - exit_price) * p["total_qty"]
        self.total_pnl += pnl
        self.daily_pnl += pnl
        self.trade_count += 1
        trade_record = {
            "id": self.trade_count,
            "time": datetime.now().isoformat(),
            "side": p["side"], "avg_entry": round(p["avg_entry"], 2),
            "exit": exit_price, "qty": p["total_qty"],
            "total_margin": p["total_margin"],
            "mg_levels": p["mg_lvl"], "leverage": LEVERAGE,
            "pnl": round(pnl, 2), "reason": reason
        }
        self.trade_log.append(trade_record)
        if pnl > 0:
            self.consecutive_losses = 0
        else:
            self.consecutive_losses += 1
        sign = "+" if pnl >= 0 else ""
        print(f"  CLOSE {p['side']} @ ${exit_price:.2f} | PnL: {sign}${pnl:.2f} | "
              f"{reason.upper()} | MG:{p['mg_lvl']} | "
              f"ConsLoss:{self.consecutive_losses} | Daily:${self.daily_pnl:.2f}")
        self.total_used_margin -= p["total_margin"]
        self.current_position = None
        self.save_log()
        return trade_record

position_manager = ProPositionManager()

# ─── MAIN LOOP ─────────────────────────────────────────────────────────────
def trade_loop():
    print(f"\n{'='*60}")
    print(f" XAUUSDT PRO SCALP v2 (Skor {MIN_SCORE}/5)")
    print(f" Capital: ${CAPITAL} | Leverage: {LEVERAGE}x")
    print(f" TP: {TP_PCT}% | SL: {SL_PCT}% | MG: {MG_MAX} levels x{MG_MULT}")
    print(f" Indicators: Trend(5m) + Pivot + StochRSI + MACD + CCI")
    print(f" Min Score: {MIN_SCORE}/5 | Daily Loss: ${DAILY_LOSS_LIMIT}")
    print(f" Mode: {'LIVE' if LIVE_TRADING else 'PAPER'} | Testnet: {USE_TESTNET}")
    print(f"{'='*60}\n")

    if LIVE_TRADING:
        if not BINANCE_API_KEY or not BINANCE_API_SECRET:
            print("ERROR: LIVE requires BINANCE_API_KEY/ SECRET in .env"); return
        set_leverage(); set_isolated()

    last_bar_time = 0
    heartbeat = 60
    last_hb = time.time()

    while True:
        try:
            df = fetch_klines("1m", 300)
            df_daily = fetch_klines("1d", 10)
            if df is None or len(df) < 100 or df_daily is None or len(df_daily) < 3:
                time.sleep(5); continue

            cp = df.iloc[-1]["close"]
            bt = df.iloc[-1]["timestamp"]
            now = time.time()

            if now - last_hb > heartbeat:
                last_hb = now
                bal = CAPITAL - position_manager.total_used_margin
                p = position_manager.current_position
                pos_s = f"ACTIVE {p['side']} MG{p['mg_lvl']} Avg:${p['avg_entry']:.2f}" if p else "NONE"
                print(f"[HB] ${cp:.2f} | {pos_s} | Bal:${bal:.1f} | "
                      f"Daily:${position_manager.daily_pnl:.2f} | Total:${position_manager.total_pnl:.2f}")

            position_manager.check_position(cp)

            if bt != last_bar_time:
                last_bar_time = bt
                long_sig, short_sig, info = generate_signals(df, df_daily)

                if position_manager.current_position is not None:
                    p = position_manager.current_position
                    if (p["side"] == "LONG" and long_sig) or (p["side"] == "SHORT" and short_sig):
                        position_manager.add_level(cp)

                if position_manager.current_position is None:
                    sig = "LONG" if long_sig else "SHORT" if short_sig else None
                    trend_s = "BULL" if info["trend_up"] else "BEAR" if info["trend_up"] is not None else "N/A"
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] ${cp:.2f} | "
                          f"Trend:{trend_s} | StochRSI:{info['srsi_k']:.1f} | "
                          f"CCI:{info['cci']:.0f} | MACD:{'BULL' if info['macd_h']>0 else 'BEAR'} | "
                          f"Score L:{info['score_l']}/S:{info['score_s']} | "
                          f"Signal:{sig or 'NONE'}")

                    if sig and position_manager.can_trade():
                        position_manager.open_first(sig, cp)

            time.sleep(POSITION_CHECK_INTERVAL)

        except KeyboardInterrupt:
            print("\nBot stopped by user."); break
        except Exception as e:
            print(f"  Loop error: {e}")
            time.sleep(10)

    print(f"\n{'='*60}\n BOT SUMMARY\n Trades: {position_manager.trade_count}\n "
          f"Total PnL: ${position_manager.total_pnl:.2f}\n{'='*60}")

if __name__ == "__main__":
    trade_loop()
