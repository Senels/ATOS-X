import os, time, json, hmac, hashlib, requests, urllib.parse, threading
from datetime import datetime
from dotenv import load_dotenv
import pandas as pd
import numpy as np

load_dotenv()

# ─── CONFIG ────────────────────────────────────────────────────────────────
SYMBOL = "XAUUSDT"
TIMEFRAME = "1m"
CAPITAL = 1000.0       # USDT
LEVERAGE = 100
POS_PCT = 0.02         # 2% of equity per position
SL_PCT = 8.0           # stop loss % from entry
TP_PCT = 1.0           # take profit % from entry
NO_DCA = True          # single entry, no DCA pyramid
INDEPENDENT_LONG_SHORT = True  # long and short can coexist
MAX_CONCURRENT_POSITIONS = 2    # one long + one short
DAILY_LOSS_LIMIT = 50.0
MAX_CONSECUTIVE_LOSSES = 6
AI_CHECK_INTERVAL = 300    # seconds (5 min)
POSITION_CHECK_INTERVAL = 5  # seconds
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
AI_PROVIDER = os.getenv("AI_PROVIDER", "gemini").lower()  # gemini or deepseek
USE_TESTNET = os.getenv("USE_TESTNET", "true").lower() == "true"
LIVE_TRADING = os.getenv("LIVE_TRADING", "false").lower() == "true"

FUTURES_BASE = "https://fapi.binance.com"
if USE_TESTNET:
    FUTURES_BASE = "https://testnet.binancefuture.com"

LOG_FILE = "xauusdt_bot_log.json"

# ─── DATA FETCHING ─────────────────────────────────────────────────────────
def binance_request(method, endpoint, params=None, signed=False):
    url = FUTURES_BASE + endpoint
    headers = {"X-MBX-APIKEY": BINANCE_API_KEY} if BINANCE_API_KEY else {}
    if signed:
        params = params or {}
        query_str = urllib.parse.urlencode(params)
        params["signature"] = hmac.new(
            BINANCE_API_SECRET.encode(), query_str.encode(), hashlib.sha256
        ).hexdigest()
    if method == "GET":
        r = requests.get(url, params=params, headers=headers, timeout=10)
    elif method == "POST":
        r = requests.post(url, params=params, headers=headers, timeout=10)
    else:
        r = requests.delete(url, params=params, headers=headers, timeout=10)
    if r.status_code != 200:
        raise Exception(f"Binance API error {r.status_code}: {r.text}")
    return r.json()

def fetch_klines(limit=200):
    data = binance_request("GET", "/fapi/v1/klines", {
        "symbol": SYMBOL, "interval": TIMEFRAME, "limit": limit
    })
    rows = []
    for k in data:
        rows.append({
            "timestamp": int(k[0]), "open": float(k[1]), "high": float(k[2]),
            "low": float(k[3]), "close": float(k[4]), "volume": float(k[5])
        })
    return pd.DataFrame(rows)

def get_current_price():
    ticker = binance_request("GET", "/fapi/v1/ticker/price", {"symbol": SYMBOL})
    return float(ticker["price"])

def get_account_balance():
    if not LIVE_TRADING:
        return CAPITAL - position_manager.total_used_margin if hasattr(position_manager, 'total_used_margin') else CAPITAL
    acc = binance_request("GET", "/fapi/v2/account", signed=True)
    for a in acc.get("assets", []):
        if a["asset"] == "USDT":
            return float(a["walletBalance"])
    return CAPITAL

def get_position_info():
    if not LIVE_TRADING:
        return None
    positions = binance_request("GET", "/fapi/v2/positionRisk", {"symbol": SYMBOL}, signed=True)
    for p in positions:
        if p["symbol"] == SYMBOL:
            return p
    return None

def set_leverage():
    if LIVE_TRADING:
        binance_request("POST", "/fapi/v1/leverage",
                        {"symbol": SYMBOL, "leverage": LEVERAGE}, signed=True)

def set_isolated_mode():
    if LIVE_TRADING:
        try:
            binance_request("POST", "/fapi/v1/marginType",
                           {"symbol": SYMBOL, "marginType": "ISOLATED"}, signed=True)
        except Exception as e:
            if "No need to change margin type" not in str(e):
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

# ─── INDICATORS ────────────────────────────────────────────────────────────
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
    avg_gain = np.zeros_like(gain)
    avg_loss = np.zeros_like(loss)
    avg_gain[0] = np.mean(gain[:period]) if len(gain) >= period else gain[0]
    avg_loss[0] = np.mean(loss[:period]) if len(loss) >= period else loss[0]
    for i in range(1, len(gain)):
        avg_gain[i] = (avg_gain[i-1] * (period - 1) + gain[i]) / period
        avg_loss[i] = (avg_loss[i-1] * (period - 1) + loss[i]) / period
    rs = np.divide(avg_gain, avg_loss, out=np.ones_like(avg_gain), where=avg_loss != 0)
    return 100 - (100 / (1 + rs))

def bollinger(data, period=20, std=2):
    ma = np.zeros_like(data)
    for i in range(len(data)):
        if i < period:
            ma[i] = np.mean(data[:i+1])
        else:
            ma[i] = np.mean(data[i-period+1:i+1])
    rolling_std = np.zeros_like(data)
    for i in range(len(data)):
        if i < period:
            rolling_std[i] = np.std(data[:i+1])
        else:
            rolling_std[i] = np.std(data[i-period+1:i+1])
    upper = ma + rolling_std * std
    lower = ma - rolling_std * std
    return upper, ma, lower

def atr(high, low, close, period=14):
    tr = np.maximum(high - low,
                    np.abs(high - np.roll(close, 1)),
                    np.abs(low - np.roll(close, 1)))
    tr[0] = high[0] - low[0]
    return ema(tr, period)

# ─── SIGNAL GENERATION ─────────────────────────────────────────────────────
def generate_signals(df):
    close = df["close"].values
    high = df["high"].values
    low = df["low"].values
    ema9 = ema(close, 9)
    ema21 = ema(close, 21)
    rsi_val = rsi(close, 14)
    bb_upper, bb_mid, bb_lower = bollinger(close, 20, 2)
    atr_val = atr(high, low, close, 14)

    df["ema9"] = ema9
    df["ema21"] = ema21
    df["rsi"] = rsi_val
    df["bb_upper"] = bb_upper
    df["bb_lower"] = bb_lower
    df["bb_mid"] = bb_mid
    df["atr"] = atr_val

    last = df.iloc[-1]
    prev = df.iloc[-2]

    long_score = 0
    short_score = 0

    # EMA trend
    if last["ema9"] > last["ema21"]:
        long_score += 1
    else:
        short_score += 1

    # RSI
    if last["rsi"] < 35:
        long_score += 1
    elif last["rsi"] > 65:
        short_score += 1

    # Bollinger
    if last["close"] <= last["bb_lower"] * 1.001:
        long_score += 1
    elif last["close"] >= last["bb_upper"] * 0.999:
        short_score += 1

    # Candle direction
    if last["close"] > last["open"]:
        long_score += 1
    elif last["close"] < last["open"]:
        short_score += 1

    signal = None
    if long_score >= 3:
        signal = "LONG"
    elif short_score >= 3:
        signal = "SHORT"

    bb_width_pct = (last["bb_upper"] - last["bb_lower"]) / last["bb_mid"] * 100
    volatility_pct = last["atr"] / last["close"] * 100

    return signal, last, long_score, short_score, bb_width_pct, volatility_pct

# ─── AI SENTIMENT (Gemini / DeepSeek) ──────────────────────────────────────
_ai_sentiment = None
_ai_last_update = 0
_ai_lock = threading.Lock()
_has_real_ai_key = False
if AI_PROVIDER == "deepseek":
    _has_real_ai_key = bool(DEEPSEEK_API_KEY) and DEEPSEEK_API_KEY != "your_deepseek_api_key_here"
else:
    _has_real_ai_key = bool(GEMINI_API_KEY) and GEMINI_API_KEY != "your_gemini_api_key_here"

def _build_prompt():
    try:
        df = fetch_klines(60)
        close = df["close"].values
        ema9 = ema(close, 9)[-1]
        ema21 = ema(close, 21)[-1]
        rsi_val = rsi(close, 14)[-1]
        bb_u, bb_m, bb_l = bollinger(close, 20, 2)
        last_price = close[-1]
        price_change_1h = (close[-1] - close[0]) / close[0] * 100
        return (
            f"XAUUSDT (Gold) 1min chart analysis:\n"
            f"Price: ${last_price:.2f}\n"
            f"EMA9: ${ema9:.2f}, EMA21: ${ema21:.2f}\n"
            f"RSI(14): {rsi_val:.1f}\n"
            f"BB Upper: ${bb_u[-1]:.2f}, Lower: ${bb_l[-1]:.2f}\n"
            f"1h Change: {price_change_1h:.2f}%\n\n"
            "Respond with ONLY one word: BULLISH, BEARISH, or NEUTRAL.\n"
            "Base your analysis on technical indicators above."
        )
    except:
        return None

def _parse_sentiment(text):
    text = text.strip().upper()
    if "BULLISH" in text:
        return "BULLISH"
    if "BEARISH" in text:
        return "BEARISH"
    return "NEUTRAL"

def _call_gemini(prompt):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    resp = requests.post(url, json=payload, timeout=15)
    data = resp.json()
    candidates = data.get("candidates", [])
    if not candidates:
        return None
    return candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")

def _call_deepseek(prompt):
    url = "https://api.deepseek.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 10,
        "temperature": 0.1
    }
    resp = requests.post(url, json=payload, headers=headers, timeout=15)
    data = resp.json()
    return data.get("choices", [{}])[0].get("message", {}).get("content", "")

def update_ai_sentiment():
    global _ai_sentiment, _ai_last_update
    now = time.time()
    if now - _ai_last_update < AI_CHECK_INTERVAL:
        return
    if not _has_real_ai_key:
        return

    prompt = _build_prompt()
    if not prompt:
        _ai_sentiment = "NEUTRAL"
        return

    try:
        if AI_PROVIDER == "deepseek":
            text = _call_deepseek(prompt)
        else:
            text = _call_gemini(prompt)

        if text is None:
            _ai_sentiment = "NEUTRAL"
            return

        _ai_sentiment = _parse_sentiment(text)
        _ai_last_update = now
        print(f"[{datetime.now().strftime('%H:%M:%S')}] AI Sentiment ({AI_PROVIDER}): {_ai_sentiment}")
    except Exception as e:
        print(f"  AI ({AI_PROVIDER}) error: {e}")

def get_ai_sentiment():
    with _ai_lock:
        if _ai_sentiment is None:
            return "NEUTRAL"
        return _ai_sentiment

# ─── POSITION MANAGER (DCA / Cumulative) ──────────────────────────────────

class PositionManager:
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

    def reset_daily_if_needed(self):
        today = datetime.now().day
        if today != self.last_trade_day:
            self.daily_pnl = 0.0
            self.last_trade_day = today

    def can_trade(self):
        self.reset_daily_if_needed()
        if self.consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
            return False
        if self.daily_pnl <= -DAILY_LOSS_LIMIT:
            return False
        return True

    def calc_qty(self, margin, price):
        step_size, _ = get_lot_step()
        qty = margin * LEVERAGE / price
        raw_precision = len(str(step_size).split(".")[1].rstrip("0")) if "." in str(step_size) else 0
        return round(qty // step_size * step_size, raw_precision)

    def open_first(self, side, entry_price):
        margin = MARTINGALE_STEPS[0]
        qty = self.calc_qty(margin, entry_price)
        if qty * entry_price < 5:
            return False

        if LIVE_TRADING:
            try:
                set_leverage(); set_isolated_mode()
                side_b = "BUY" if side == "LONG" else "SELL"
                order = binance_request("POST", "/fapi/v1/order", {
                    "symbol": SYMBOL, "side": side_b, "type": "MARKET", "quantity": qty
                }, signed=True)
                fill = float(order.get("avgPrice", entry_price))
            except Exception as e:
                print(f"  ORDER ERROR: {e}"); return False
        else:
            fill = entry_price

        tp = fill * (1 + TP_PCT/100) if side == "LONG" else fill * (1 - TP_PCT/100)
        sl = fill * (1 - DCA_SL_PCT/100) if side == "LONG" else fill * (1 + DCA_SL_PCT/100)

        self.current_position = {
            "side": side, "avg_entry": fill, "last_entry": fill,
            "qty": qty, "total_margin": margin, "add_count": 0,
            "tp": tp, "sl": sl, "open_time": time.time()
        }
        self.total_used_margin += margin
        print(f"  ENTRY {side} @ ${fill:.2f} | Qty: {qty} | Margin: ${margin} | "
              f"TP: ${tp:.2f} | SL: ${sl:.2f} (5% below last)")
        return True

    def add_to_position(self, add_price, add_margin):
        p = self.current_position
        if p is None or p["add_count"] >= DCA_MAX_ADDS:
            return False

        qty_add = self.calc_qty(add_margin, add_price)
        if qty_add * add_price < 5:
            return False

        if LIVE_TRADING:
            try:
                side_b = "BUY" if p["side"] == "LONG" else "SELL"
                order = binance_request("POST", "/fapi/v1/order", {
                    "symbol": SYMBOL, "side": side_b, "type": "MARKET", "quantity": qty_add
                }, signed=True)
                fill = float(order.get("avgPrice", add_price))
            except Exception as e:
                print(f"  ADD ORDER ERROR: {e}"); return False
        else:
            fill = add_price

        total_qty = p["qty"] + qty_add
        total_margin = p["total_margin"] + add_margin
        p["avg_entry"] = (p["avg_entry"] * p["qty"] + fill * qty_add) / total_qty
        p["last_entry"] = fill
        p["qty"] = total_qty
        p["total_margin"] = total_margin
        p["add_count"] += 1

        p["tp"] = p["avg_entry"] * (1 + TP_PCT/100) if p["side"] == "LONG" else p["avg_entry"] * (1 - TP_PCT/100)
        p["sl"] = fill * (1 - DCA_SL_PCT/100) if p["side"] == "LONG" else fill * (1 + DCA_SL_PCT/100)

        self.total_used_margin += add_margin
        print(f"  ADD {p['side']} #{p['add_count']} @ ${fill:.2f} | +{qty_add} qty | "
              f"Avg: ${p['avg_entry']:.2f} | Total Margin: ${total_margin} | "
              f"TP: ${p['tp']:.2f} | SL: ${p['sl']:.2f}")
        return True

    def check_position(self, current_price):
        if self.current_position is None:
            return None

        p = self.current_position

        if p["side"] == "LONG":
            if current_price >= p["tp"]:
                return self.close_position(current_price, "take_profit")
            if current_price <= p["sl"]:
                return self.close_position(current_price, "dca_stop")
        else:
            if current_price <= p["tp"]:
                return self.close_position(current_price, "take_profit")
            if current_price >= p["sl"]:
                return self.close_position(current_price, "dca_stop")

        return None

    def close_position(self, exit_price, reason):
        p = self.current_position
        if p is None: return None

        if p["side"] == "LONG":
            pnl = (exit_price - p["avg_entry"]) * p["qty"]
        else:
            pnl = (p["avg_entry"] - exit_price) * p["qty"]

        self.total_pnl += pnl
        self.daily_pnl += pnl
        self.trade_count += 1

        trade_record = {
            "id": self.trade_count,
            "time": datetime.now().isoformat(),
            "side": p["side"], "avg_entry": round(p["avg_entry"],2),
            "last_entry": round(p["last_entry"],2), "exit": exit_price,
            "qty": p["qty"], "total_margin": p["total_margin"],
            "adds": p["add_count"], "leverage": LEVERAGE,
            "pnl": round(pnl, 2), "reason": reason
        }
        self.trade_log.append(trade_record)

        if pnl > 0:
            self.consecutive_losses = 0
        else:
            self.consecutive_losses += 1

        sign = "+" if pnl >= 0 else ""
        print(f"  CLOSE {p['side']} @ ${exit_price:.2f} | PnL: {sign}${pnl:.2f} | "
              f"{reason.upper()} | Adds: {p['add_count']} | "
              f"ConsLoss: {self.consecutive_losses} | Daily: ${self.daily_pnl:.2f}")

        self.total_used_margin -= p["total_margin"]
        self.current_position = None
        self.save_log()
        return trade_record

position_manager = PositionManager()

# ─── DCA ADD LEVEL TRACKING ─────────────────────────────────────────────────
pending_dca = None  # {"side": str, "add_price": float, "step": int}

def check_existing_positions():
    if LIVE_TRADING:
        pos = get_position_info()
        if pos and float(pos["positionAmt"]) != 0:
            entry = float(pos["entryPrice"])
            amt = abs(float(pos["positionAmt"]))
            side = "LONG" if float(pos["positionAmt"]) > 0 else "SHORT"
            upnl = float(pos["unRealizedProfit"])
            if position_manager.current_position is None:
                position_manager.current_position = {
                    "side": side, "avg_entry": entry, "last_entry": entry,
                    "qty": amt, "total_margin": abs(float(pos.get("isolatedWallet", entry*amt/LEVERAGE))),
                    "add_count": 0,
                    "tp": entry * (1+TP_PCT/100) if side=="LONG" else entry*(1-TP_PCT/100),
                    "sl": entry*(1-DCA_SL_PCT/100) if side=="LONG" else entry*(1+DCA_SL_PCT/100),
                    "open_time": time.time()
                }
                position_manager.total_used_margin += position_manager.current_position["total_margin"]
                print(f"  SYNCED existing {side} position @ ${entry:.2f} | UPnL: ${upnl:.2f}")

def trade_loop():
    global pending_dca
    print(f"\n{'='*60}")
    print(f" XAUUSDT DCA SCALP BOT (No Trailing)")
    print(f" Capital: ${CAPITAL} | Leverage: {LEVERAGE}x | Timeframe: {TIMEFRAME}")
    print(f" TP: {TP_PCT}% | DCA SL: {DCA_SL_PCT}% below last add | Adds: {DCA_MAX_ADDS} x{DCA_ADD_DIST_PCT}%")
    print(f" Daily Loss Limit: ${DAILY_LOSS_LIMIT} | Max Cons Losses: {MAX_CONSECUTIVE_LOSSES}")
    print(f" Mode: {'LIVE' if LIVE_TRADING else 'PAPER'} Trading")
    print(f" Testnet: {USE_TESTNET}")
    ai_name = AI_PROVIDER.upper() if _has_real_ai_key else "DISABLED"
    print(f" AI Provider:    {ai_name}")
    print(f"{'='*60}\n")

    if LIVE_TRADING:
        if not BINANCE_API_KEY or not BINANCE_API_SECRET:
            print("ERROR: LIVE trading requires BINANCE_API_KEY and BINANCE_API_SECRET in .env")
            return
        set_leverage()
        set_isolated_mode()
        check_existing_positions()

    last_bar_time = 0
    ai_thread_running = False

    def ai_updater():
        nonlocal ai_thread_running
        ai_thread_running = True
        while True:
            update_ai_sentiment()
            time.sleep(AI_CHECK_INTERVAL)

    if _has_real_ai_key:
        t = threading.Thread(target=ai_updater, daemon=True)
        t.start()

    heartbeat_interval = 60
    last_heartbeat = time.time()

    while True:
        try:
            df = fetch_klines(200)
            if df is None or len(df) < 50:
                time.sleep(5)
                continue

            current_price = df.iloc[-1]["close"]
            current_bar_time = df.iloc[-1]["timestamp"]

            now = time.time()
            if now - last_heartbeat > heartbeat_interval:
                last_heartbeat = now
                bal = CAPITAL - position_manager.total_used_margin
                p = position_manager.current_position
                if p:
                    pos_status = f"ACTIVE {p['side']} Avg:${p['avg_entry']:.2f} Last:${p['last_entry']:.2f} Adds:{p['add_count']}"
                else:
                    pos_status = "NONE"
                dca_info = f"DCA: pending @ ${pending_dca['add_price']:.2f}" if pending_dca else "DCA: none"
                print(f"[HB] ${current_price:.2f} | {pos_status} | {dca_info} | "
                      f"Bal: ${bal:.1f} | Daily: ${position_manager.daily_pnl:.2f} | "
                      f"Total: ${position_manager.total_pnl:.2f} | ConsLoss: {position_manager.consecutive_losses}")

            # Check for pending DCA add
            if pending_dca is not None and position_manager.current_position is not None:
                p = position_manager.current_position
                if ((pending_dca["side"] == "LONG" and current_price <= pending_dca["add_price"]) or
                    (pending_dca["side"] == "SHORT" and current_price >= pending_dca["add_price"])):
                    margin_add = MARTINGALE_STEPS[min(pending_dca["step"], len(MARTINGALE_STEPS)-1)]
                    print(f"  DCA TRIGGERED step {pending_dca['step']} @ ${current_price:.2f}")
                    position_manager.add_to_position(current_price, margin_add)
                    # Queue next add level
                    p = position_manager.current_position
                    if p["add_count"] < DCA_MAX_ADDS:
                        next_step = p["add_count"] + 1
                        if p["side"] == "LONG":
                            add_px = p["last_entry"] * (1 - DCA_ADD_DIST_PCT/100)
                        else:
                            add_px = p["last_entry"] * (1 + DCA_ADD_DIST_PCT/100)
                        pending_dca = {"side": p["side"], "add_price": add_px, "step": next_step}
                        print(f"  NEXT DCA @ ${add_px:.2f}")
                    else:
                        pending_dca = None

            # Check open position
            result = position_manager.check_position(current_price)
            if result:
                pending_dca = None  # position closed, clear DCA queue

            # New signal only on new bar (only if no position)
            if current_bar_time != last_bar_time and position_manager.current_position is None:
                last_bar_time = current_bar_time
                signal, last, long_s, short_s, bb_w, vol = generate_signals(df)

                ai_sent = get_ai_sentiment()

                if signal == "LONG" and ai_sent == "BEARISH":
                    signal = None
                elif signal == "SHORT" and ai_sent == "BULLISH":
                    signal = None

                print(f"[{datetime.now().strftime('%H:%M:%S')}] Bar | ${last['close']:.2f} | "
                      f"RSI:{last['rsi']:.1f} EMA9:{last['ema9']:.2f} EMA21:{last['ema21']:.2f} | "
                      f"Signal:{signal or 'NONE'} (L:{long_s}/S:{short_s}) | AI:{ai_sent}")

                if signal and position_manager.can_trade():
                    if position_manager.open_first(signal, last["close"]):
                        p = position_manager.current_position
                        add_px = p["last_entry"] * (1 - DCA_ADD_DIST_PCT/100) if signal == "LONG" else p["last_entry"] * (1 + DCA_ADD_DIST_PCT/100)
                        pending_dca = {"side": signal, "add_price": add_px, "step": 1}
                        print(f"  NEXT DCA @ ${add_px:.2f} ({DCA_ADD_DIST_PCT}% from entry)")

            time.sleep(POSITION_CHECK_INTERVAL)

        except KeyboardInterrupt:
            print("\nBot stopped by user.")
            break
        except Exception as e:
            print(f"  Loop error: {e}")
            time.sleep(10)

    # Print final summary
    print(f"\n{'='*60}")
    print(f" BOT SUMMARY")
    print(f" Total Trades: {position_manager.trade_count}")
    print(f" Total PnL: ${position_manager.total_pnl:.2f}")
    print(f" Consecutive Losses: {position_manager.consecutive_losses}")
    print(f" Daily PnL: ${position_manager.daily_pnl:.2f}")
    print(f"{'='*60}")

# ─── MAIN ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    try:
        trade_loop()
    except KeyboardInterrupt:
        print("\nBot stopped.")
