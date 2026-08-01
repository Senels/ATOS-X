"""Testnet smoke run: salt-okunur baglanti + pozisyon/bakiye kontrolu.
Gercek emir gondermez. Guvenli oldugunu dogrulamak icin:
  1. bot/.env icinde BINANCE_API_KEY/SECRET testnet anahtarlari olsun
  2. BINANCE_TESTNET=true
Calistirma:  python smoke_testnet.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from config import Config
from execution.trader import BinanceTrader


def diagnose_keys():
    """Anahtarin hangi Binance API'sinde gecerli oldugunu test eder (salt-okunur)."""
    import hashlib
    import hmac
    import time as _time
    import urllib.parse

    import requests

    ts = int(_time.time() * 1000)
    qs = urllib.parse.urlencode({"timestamp": ts, "recvWindow": 5000})
    sig = hmac.new(Config.BINANCE_API_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
    targets = [
        ("FUTURES TESTNET", "https://testnet.binancefuture.com"),
        ("FUTURES MAINNET", "https://fapi.binance.com"),
        ("SPOT TESTNET", "https://testnet.binance.vision"),
    ]
    for label, host in targets:
        path = "/fapi/v2/positionRisk" if "FUTURES" in label else "/api/v3/account"
        url = f"{host}{path}?{qs}&signature={sig}"
        try:
            r = requests.get(url, headers={"X-MBX-APIKEY": Config.BINANCE_API_KEY}, timeout=10)
            print(f"  {label:<20} -> {r.status_code} {r.text[:80]}")
        except Exception as e:
            print(f"  {label:<20} -> ERISIM YOK: {str(e)[:80]}")


def main():
    print(f"Testnet: {Config.BINANCE_TESTNET}")
    if not Config.BINANCE_API_KEY:
        print("ERROR: BINANCE_API_KEY yok (.env kontrol edin)")
        sys.exit(1)
    if "--diagnose" in sys.argv:
        print("Anahtar tanilamasi (salt-okunur):")
        diagnose_keys()
        return
    trader = BinanceTrader(testnet=Config.BINANCE_TESTNET)
    print("  -> sync_open_positions() testi...")
    positions = trader.sync_open_positions()
    if positions:
        for p in positions:
            print(f"  ACK POSITION: {p['symbol']} {p['side']} qty={p['qty']} entry={p['entry_price']}")
    else:
        print("  No open positions (beklenen: testnet temiz)")
    print("  -> account balance testi...")
    client = trader._get_client()
    bal = client.futures_account_balance()
    usdt = next((b for b in bal if b.get("asset") == "USDT"), None)
    if usdt:
        print(f"  USDT balance: {usdt.get('balance')} | available: {usdt.get('availableBalance')}")
    print("SMOKE OK")


if __name__ == "__main__":
    main()
