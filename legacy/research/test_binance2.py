import ccxt
import traceback

try:
    binance = ccxt.binance({"timeout": 30000})
    print("Binance exchange created")
    markets = binance.load_markets()
    print(f"Markets loaded: {len(markets)} pairs")
    symbol = "XAUUSDT"
    if symbol in markets:
        print(f"{symbol} mevcut!")
        ohlcv = binance.fetch_ohlcv(symbol, "1m", limit=3)
        print(f"OHLCV: {len(ohlcv)} bars")
    else:
        print(f"{symbol} bulunamadi")
        # find similar
        for s in markets:
            if "GOLD" in s or "XAU" in s:
                print(f"  Found: {s}")
except Exception as e:
    print(f"Error: {e}")
    traceback.print_exc()
