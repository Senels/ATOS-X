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


def main():
    print(f"Testnet: {Config.BINANCE_TESTNET}")
    if not Config.BINANCE_API_KEY:
        print("ERROR: BINANCE_API_KEY yok (.env kontrol edin)")
        sys.exit(1)
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
