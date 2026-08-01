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

def sync_exchange_positions():
    """Restart sonrasi borsadaki acik pozisyonlari geri yukle:
    state, trailing stop ve exchange-side SL emri yeniden kurulur."""
    try:
        open_positions = trader.sync_open_positions()
    except Exception as e:
        print(f"  [SYNC] Position sync failed: {e}")
        return
    if not open_positions:
        print("  [SYNC] No open positions on exchange")
        return
    print(f"  [SYNC] Found {len(open_positions)} open position(s) on exchange:")
    for p in open_positions:
        sym, side = p["symbol"], p["side"]
        if positions.is_open(sym, side):
            print(f"    {sym} {side} already tracked, skipping")
            continue
        try:
            df = add_all_indicators(fetch_klines(sym, limit=Config.ATR_LEN + 60))
            atr = float(df.iloc[-1]["atr"])
        except Exception:
            atr = 0.0
        pos_id = f"{sym}_{side}_restored_{int(time.time()*1000)}"
        entry = p["entry_price"]
        qty = p["qty"]
        est_margin = qty * entry / Config.LEVERAGE if Config.LEVERAGE > 0 else qty * entry
        positions.open(pos_id, sym, side, entry, qty, est_margin, atr)
        mm.add_position(pos_id, est_margin)
        sl_price = trailing.open_position(pos_id, entry, atr, side)
        tp_price = None
        if Config.TP_ATR_MULT > 0:
            tp_price = entry + atr * Config.TP_ATR_MULT if side == "LONG" else entry - atr * Config.TP_ATR_MULT
        stops = trader.set_tp_sl(sym, side, entry, sl_price, tp_price)
        if stops:
            trailing.set_orders(pos_id, sl_order_id=stops.get("sl"), tp_order_id=stops.get("tp"))
        print(f"    RESTORED {sym} {side} qty={qty:.4f} entry={entry:.4f} sl={sl_price:.4f}")

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
            tp_price = None
            if Config.TP_ATR_MULT > 0:
                tp_price = price + atr * Config.TP_ATR_MULT if side == "LONG" else price - atr * Config.TP_ATR_MULT
            stops = trader.set_tp_sl(sym, side, price, sl_price, tp_price)
            if stops:
                trailing.set_orders(pos_id, sl_order_id=stops.get("sl"), tp_order_id=stops.get("tp"))
            else:
                print(f"  !! ALERT {sym}: exchange SL/TP could not be placed - position unprotected!")
            open_count += 1
            print(f"  ENTRY: {sym} {side} @ {price:.2f} | Margin: {pos_margin:.1f} | "
                  f"SL: {sl_price:.2f} | TP: {tp_price if tp_price else '-'} | Momentum: %{momentum[sym]:.2f}")
    check_open_positions()

def _cancel_position_orders(sym, pos_id):
    """Kapanan pozisyonun bekleyen SL/TP emirlerini iptal eder (sonraki giriste tetiklenmesin)."""
    sl_id = trailing.get_sl_order_id(pos_id)
    tp_id = trailing.get_tp_order_id(pos_id)
    if sl_id:
        trader.cancel_order(sym, sl_id)
    if tp_id:
        trader.cancel_order(sym, tp_id)

def _settle_position(pos_id, exit_price, reason):
    """Exchange close basarili olduktan sonra lokal state'i kapatir (sadece bir kez)."""
    pos = positions.positions.get(pos_id)
    if pos is None or pos["status"] != "open":
        return
    pnl = positions.close(pos_id, exit_price, reason)
    mm.remove_position(pos_id)
    logger.log_trade(pos["symbol"], pos["side"],
                     pos["entry_price"], exit_price, pos["qty"],
                     pos["margin_used"], pnl, pnl / pos["margin_used"],
                     reason, None, int(time.time() * 1000))
    kelly.update(pos["symbol"], pos["side"], pnl)
    if pnl > 0:
        martingale.on_win(pos["symbol"], pos["side"])
    else:
        martingale.on_loss(pos["symbol"], pos["side"])
    trailing.close_position(pos_id)
    print(f"  EXIT: {pos_id} @ {exit_price:.2f} | PnL: {pnl:.1f} USDT | reason: {reason}")

def _exit_position(pos_id, exit_price, reason):
    """Gerçek kapanis emri gonderir; basarisizsa exit_pending ile retry."""
    pos = positions.positions.get(pos_id)
    if pos is None:
        return
    order = trader.market_close(pos["symbol"], pos["side"], pos["qty"])
    if order:
        _cancel_position_orders(pos["symbol"], pos_id)
        _settle_position(pos_id, exit_price, reason)
    else:
        pos["status"] = "exit_pending"
        print(f"  !! ALERT {pos_id}: close order failed, will retry (exit_pending)")

def check_open_positions():
    for pos_id, pos in list(positions.positions.items()):
        if pos["status"] == "exit_pending":
            _exit_position(pos_id, pos.get("exit_price", 0) or 0, "trailing_stop")
            continue
        if pos["status"] != "open":
            continue
        try:
            df = fetch_klines(pos["symbol"], limit=2)
            if df is None or len(df) < 2:
                continue
            cp = df.iloc[-1]["close"]
            result = trailing.update_price(pos_id, cp)
            if result:
                event, stop_price = result
                if event in ("trail_activated", "trail_updated"):
                    if Config.UPDATE_EXCHANGE_STOP:
                        new_stop = trailing.get_stop(pos_id)
                        old_sl = trailing.get_sl_order_id(pos_id)
                        if new_stop:
                            replaced = trader.replace_stop(
                                pos["symbol"], pos["side"], old_sl, new_stop, pos["entry_price"])
                            if replaced and replaced.get("sl"):
                                trailing.set_orders(pos_id, sl_order_id=replaced["sl"])
                elif event == "hit":
                    pos["exit_price"] = stop_price
                    _exit_position(pos_id, stop_price, "trailing_stop")
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
    print("Syncing open positions from exchange...")
    sync_exchange_positions()
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
