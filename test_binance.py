import ccxt
import pandas as pd
from datetime import datetime, timedelta

binance = ccxt.binance()
symbol = "XAUUSDT"

try:
    markets = binance.load_markets()
    if symbol in markets:
        print(f"{symbol} mevcut")
        now = datetime.utcnow()
        since_dt = now - timedelta(days=30)
        since = int(since_dt.timestamp() * 1000)
        ohlcv = binance.fetch_ohlcv(symbol, "1m", since=since, limit=5)
        if ohlcv:
            print(f"Veri alindi: {len(ohlcv)} bar")
            t0 = pd.to_datetime(ohlcv[0][0], unit="ms")
            t1 = pd.to_datetime(ohlcv[-1][0], unit="ms")
            print(f"Ilk bar: {t0}")
            print(f"Son bar: {t1}")
            print(f"Fiyat: {ohlcv[0][1]} - {ohlcv[0][4]}")
    else:
        print(f"{symbol} bulunamadi. Mevcut XAU/USD pairs:")
        for s in markets:
            if "XAU" in s or "GOLD" in s:
                print(f"  {s}")
except Exception as e:
    print(f"Hata: {e}")
