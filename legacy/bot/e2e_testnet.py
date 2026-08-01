"""Uctan uca TESTNET testi: giris -> exchange-side SL/TP -> trail replace -> kapanis -> temizlik.

Testnet'te gercek emir gonderir (kucuk tutar). Her durumda sonunda temizlik yapar:
kalan pozisyon kapatilir, bekleyen emirler iptal edilir.
"""

import sys
import time

from config import Config
from execution.trader import BinanceTrader

trader = BinanceTrader(testnet=Config.BINANCE_TESTNET)
client = trader._get_client()

CLEANUP_SYMBOL = None
RESULTS = []


def step(name, ok, detail=""):
    print(f"  {'[OK]  ' if ok else '[FAIL]'} {name}" + (f" | {detail}" if detail else ""))
    RESULTS.append((name, ok, detail))
    return ok


def round_to_tick(price, tick):
    if not tick:
        return price
    d = len(tick.split(".")[1].rstrip("0")) if "." in tick else 0
    return round(price // float(tick) * float(tick), d)


def pick_symbol():
    info = client.futures_exchange_info()
    stable = ("USDC", "FDUSD", "TUSD", "DAI", "USDP", "AEUR", "EUR", "BUSD")
    for s in info["symbols"]:
        if s.get("contractType") != "PERPETUAL":
            continue
        if s.get("quoteAsset") != "USDT":
            continue
        if s.get("status") != "TRADING":
            continue
        if s["baseAsset"].endswith(stable) or s["baseAsset"].startswith("1000") is False and s["baseAsset"].upper() in ("USDT",):
            continue
        filters = {f["filterType"]: f for f in s["filters"]}
        if float(filters.get("MIN_NOTIONAL", {}).get("notional", 5)) > 8:
            continue
        try:
            price = float(client.futures_symbol_ticker(symbol=s["symbol"])["price"])
        except Exception:
            continue
        step_size = float(filters["LOT_SIZE"]["stepSize"])
        tick = filters["PRICE_FILTER"]["tickSize"]
        if 0 < price < 2 and step_size <= 0.01:
            return s["symbol"], price, tick
    return None, None, None


DONE = False


def cleanup():
    if DONE:
        return
    try:
        if CLEANUP_SYMBOL:
            client.futures_cancel_all_open_orders(symbol=CLEANUP_SYMBOL)
            client.futures_cancel_all_algo_open_orders(symbol=CLEANUP_SYMBOL)
            print("  [CLEANUP] bekleyen emirler iptal edildi")
    except Exception as e:
        print(f"  [CLEANUP] emir iptali basarisiz: {e}")
    try:
        for p in trader.sync_open_positions():
            trader.market_close(p["symbol"], p["side"], p["qty"])
            print(f"  [CLEANUP] {p['symbol']} {p['side']} pozisyonu kapatildi")
    except Exception as e:
        print(f"  [CLEANUP] pozisyon kapatma basarisiz: {e}")


def main():
    global CLEANUP_SYMBOL, DONE
    print("== E2E TESTNET: Giris -> Exchange SL/TP -> Trail -> Kapanis ==")

    existing = trader.sync_open_positions()
    if existing:
        print(f"  NOT: testten once acik pozisyon(lar) var: {existing}")

    symbol, price, tick = pick_symbol()
    if not symbol:
        print("  FAIL: uygun sembol bulunamadi")
        sys.exit(1)
    CLEANUP_SYMBOL = symbol
    print(f"  Sembol: {symbol} @ {price}")

    if not step("set_leverage(5x)", bool(trader.set_leverage(symbol, 5))):
        sys.exit(1)

    order = trader.market_open(symbol, "LONG", 10.0)
    if not step("market_open LONG (10 USDT)", bool(order)):
        cleanup()
        sys.exit(1)

    time.sleep(1)
    pos = None
    for attempt in range(5):
        positions = trader.sync_open_positions()
        pos = next((p for p in positions if p["symbol"] == symbol), None)
        if pos:
            break
        time.sleep(1.5)
    if not step("pozisyon borsada gorunuyor", bool(pos), str(pos)):
        cleanup()
        sys.exit(1)
    qty = pos["qty"]
    entry = pos["entry_price"]

    sl_price = round_to_tick(entry * 0.99, tick)
    tp_price = round_to_tick(entry * 1.01, tick)
    stops = trader.set_tp_sl(symbol, "LONG", entry, sl_price, tp_price)
    if not step("set_tp_sl emirleri olustu", bool(stops and stops["sl"] and stops["tp"]), str(stops)):
        cleanup()
        sys.exit(1)

    open_orders = trader.get_open_algo_orders(symbol=symbol)
    stops_on_exchange = [o for o in open_orders if o["orderType"] in ("STOP_MARKET", "TAKE_PROFIT_MARKET")]
    all_close_pos = len(stops_on_exchange) == 2 and all(bool(o.get("closePosition")) for o in stops_on_exchange)
    step("exchange-side SL/TP (closePosition=true)", all_close_pos,
         f"{len(stops_on_exchange)} emir: " + ", ".join(f"{o['orderType']}@{o['triggerPrice']}" for o in stops_on_exchange))

    new_sl = round_to_tick(entry * 0.995, tick)
    replaced = trader.replace_stop(symbol, "LONG", stops["sl"], new_sl, entry)
    if not step("replace_stop (trail sim.)", bool(replaced and replaced.get("sl")), str(replaced)):
        cleanup()
        sys.exit(1)

    open_orders = trader.get_open_algo_orders(symbol=symbol)
    sl_orders = [o for o in open_orders if o["orderType"] == "STOP_MARKET"]
    sl_updated = len(sl_orders) == 1 and abs(float(sl_orders[0]["triggerPrice"]) - new_sl) < 1e-12
    step("trail sonrasi SL guncellendi", sl_updated,
         f"beklenen={new_sl} gercek={sl_orders[0]['triggerPrice'] if sl_orders else '-'}")

    close_order = trader.market_close(symbol, "LONG", qty)
    if not step("market_close (reduceOnly)", bool(close_order), str(close_order)):
        cleanup()
        sys.exit(1)

    time.sleep(1)
    flat = False
    for attempt in range(10):
        positions = trader.sync_open_positions()
        flat = not any(p["symbol"] == symbol for p in positions)
        if flat:
            break
        time.sleep(1.5)
    step("pozisyon kapandi", flat)

    trader.cancel_order(symbol, replaced["sl"], algo=True)
    trader.cancel_order(symbol, stops["tp"], algo=True)
    open_orders = trader.get_open_algo_orders(symbol=symbol)
    step("bekleyen emir kalmadi", len(open_orders) == 0, f"{len(open_orders)} emir kaldi")

    failed = [r for r in RESULTS if not r[1]]
    print()
    print(f"== SONUC: {len(RESULTS) - len(failed)}/{len(RESULTS)} adim gecti ==")
    DONE = True
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    try:
        main()
    finally:
        cleanup()
