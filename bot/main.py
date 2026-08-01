import time
import numpy as np
import schedule
from datetime import datetime
from config import Config
from data.fetcher import fetch_top_coins, fetch_klines
from data.indicators import add_all_indicators
from risk.kelly_sizer import KellySizer
from risk.margin_manager import MarginManager
from risk.martingale import MartingaleTracker
from execution.trader import BinanceTrader
from execution.trailing_stop import TrailingStopManager
from execution.position_tracker import PositionTracker
from storage.logger import TradeLogger
from ai.reflection import AIReflection

trader = BinanceTrader(testnet=Config.BINANCE_TESTNET)
mm = MarginManager()
kelly = KellySizer()
martingale = MartingaleTracker()
trailing = TrailingStopManager()
positions = PositionTracker()
logger = TradeLogger()
ai = AIReflection()
top_symbols = []

def update_top_list():
    global top_symbols
    try:
        print(f"[{datetime.now()}] Updating top coin list...")
        top_symbols = fetch_top_coins(top_n=Config.MOMENTUM_SCAN_COINS)
        print(f"  Top {len(top_symbols)} coins")
    except Exception as e:
        print(f"  Top list update failed: {e}")

def scan_and_trade():
    global top_symbols
    if not top_symbols:
        update_top_list()
    if not top_symbols:
        return
    print(f"[{datetime.now()}] Momentum scanning {len(top_symbols)} coins...")
    momentum = {}
    klines_data = {}
    for symbol in top_symbols:
        try:
            df = fetch_klines(symbol, limit=Config.MOMENTUM_LOOKBACK + 10)
            if df is None or len(df) < Config.MOMENTUM_LOOKBACK + 1:
                continue
            df = add_all_indicators(df)
            last = df.iloc[-1]
            mom = last.get("momentum_pct", 0)
            if np.isnan(mom) or np.isinf(mom):
                continue
            momentum[symbol] = mom
            klines_data[symbol] = (last, df)
        except Exception as e:
            pass
    if len(momentum) < Config.MOMENTUM_TOP_N * 2:
        print(f"  Only {len(momentum)} coins with valid momentum data")
        return
    sorted_mom = sorted(momentum.items(), key=lambda x: x[1], reverse=True)
    gainers = [s for s, m in sorted_mom[:Config.MOMENTUM_TOP_N] if m > Config.MIN_MOMENTUM_STRENGTH]
    losers = [s for s, m in sorted_mom[-Config.MOMENTUM_TOP_N:] if m < -Config.MIN_MOMENTUM_STRENGTH]
    losers.reverse()
    print(f"  Gainers: {gainers}")
    print(f"  Losers:  {losers}")
    open_count = len(positions.get_open_positions())
    max_new = Config.MAX_CONCURRENT_POSITIONS - open_count
    if max_new <= 0:
        return
    candidates = []
    for i in range(max(len(gainers), len(losers))):
        if i < len(gainers):
            candidates.append((gainers[i], "LONG"))
        if i < len(losers) and len(candidates) < max_new * 2:
            candidates.append((losers[i], "SHORT"))
    candidates = candidates[:max_new]
    for sym, side in candidates:
        if positions.is_open(sym, side):
            continue
        last, df = klines_data[sym]
        atr_pct = last["atr"] / last["close"] * 100 if last["close"] > 0 else 0
        if atr_pct > Config.MAX_ATR_PCT:
            print(f"  SKIP {sym}: ATR% {atr_pct:.1f} > max {Config.MAX_ATR_PCT}")
            continue
        vol_sma = last.get("vol_sma", last.get("volume", 0))
        avg_vol_usdt = vol_sma * last["close"]
        if avg_vol_usdt < Config.MIN_AVG_VOLUME_USDT:
            print(f"  SKIP {sym}: low avg volume ${avg_vol_usdt:.0f}")
            continue
        avail = mm.available_margin()
        if avail < 5:
            continue
        kelly_pct = kelly.get_kelly(sym, side)
        mg_mult = martingale.get_multiplier(sym, side)
        pos_margin = avail * kelly_pct * mg_mult
        pos_margin = min(pos_margin, avail * 0.3)
        stop_dist_pct = atr_pct * Config.TRAIL_DISTANCE_MULT
        loss_if_stopped = stop_dist_pct * Config.LEVERAGE / 100
        max_margin_for_risk = avail * 0.08 / loss_if_stopped if loss_if_stopped > 0 else avail
        pos_margin = min(pos_margin, max_margin_for_risk)
        if not mm.can_open(pos_margin):
            continue
        atr = last["atr"]
        price = last["close"]
        qty = pos_margin * Config.LEVERAGE / price
        if qty * price < 5:
            continue
        trader.set_leverage(sym)
        order = trader.market_open(sym, side, pos_margin)
        if order:
            pos_id = f"{sym}_{side}_{int(time.time()*1000)}"
            positions.open(pos_id, sym, side, price, qty, pos_margin, atr)
            mm.add_position(pos_id, pos_margin)
            sl_price = trailing.open_position(pos_id, price, atr, side)
            open_count += 1
            print(f"  ENTRY: {sym} {side} @ {price:.2f} | Margin: {pos_margin:.1f} | "
                  f"SL: {sl_price:.2f} | Momentum: %{momentum[sym]:.2f}")
    check_open_positions()

def check_open_positions():
    for pos_id, pos in list(positions.get_open_positions().items()):
        try:
            df = fetch_klines(pos["symbol"], limit=2)
            if df is None or len(df) < 2:
                continue
            cp = df.iloc[-1]["close"]
            result = trailing.update_price(pos_id, cp)
            if result and result[0] == "hit":
                stop_price = result[1]
                pnl = positions.close(pos_id, stop_price, "trailing_stop")
                mm.total_equity += pnl
                mm.remove_position(pos_id)
                logger.log_trade(pos["symbol"], pos["side"],
                                 pos["entry_price"], stop_price, pos["qty"],
                                 pos["margin_used"], pnl, pnl / pos["margin_used"],
                                 "trailing_stop", None, int(time.time() * 1000))
                kelly.update(pos["symbol"], pos["side"], pnl)
                if pnl > 0:
                    martingale.on_win(pos["symbol"], pos["side"])
                else:
                    martingale.on_loss(pos["symbol"], pos["side"])
                trailing.close_position(pos_id)
                print(f"  EXIT: {pos_id} @ {stop_price:.2f} | PnL: {pnl:.1f} USDT")
        except Exception as e:
            print(f"  Position check error {pos_id}: {e}")

def run_ai_reflection():
    trades = logger.get_all()
    eq = [Config.INITIAL_CAPITAL]
    cumulative = Config.INITIAL_CAPITAL
    for t in trades:
        cumulative += t["pnl"]
        eq.append(cumulative)
    reflection = ai.analyze(trades, eq, None)
    if reflection:
        print(f"\n=== AI REFLECTION [{datetime.now()}] ===")
        print(f"Rating: {reflection['rating']} | Trades: {reflection['trade_count']} | "
              f"Win: %{reflection['win_rate']*100:.0f} | Sharpe: {reflection['sharpe']:.2f}")
        for s in reflection["suggestions"]:
            print(f"  >> {s}")

if __name__ == "__main__":
    print("Starting Liquidity Orchestrator Bot (Momentum Strategy)...")
    print(f"Capital: {Config.INITIAL_CAPITAL} USDT | Leverage: {Config.LEVERAGE}x | "
          f"MaxPos: {Config.MAX_CONCURRENT_POSITIONS} | Top {Config.MOMENTUM_TOP_N} Gainers/Losers")
    update_top_list()
    schedule.every(Config.TOP_N_UPDATE_INTERVAL).seconds.do(update_top_list)
    schedule.every(Config.TIMEFRAME_MINUTES).minutes.do(scan_and_trade)
    schedule.every(60).seconds.do(check_open_positions)
    schedule.every(6).hours.do(run_ai_reflection)
    print(f"Bot running. Momentum scan every {Config.TIMEFRAME_MINUTES}min.")
    print(f"Positions checked every 60s. Top {Config.MOMENTUM_SCAN_COINS} updated hourly.")
    while True:
        schedule.run_pending()
        time.sleep(10)
