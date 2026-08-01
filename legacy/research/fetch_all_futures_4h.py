import requests
import pandas as pd
import time
import os
import sys
from datetime import datetime
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BINANCE_FUTURES_BASE = "https://fapi.binance.com"
INTERVAL = sys.argv[1] if len(sys.argv) > 1 else "4h"
OUTPUT_DIR = f"futures_{INTERVAL}_data"
LIMIT = 1000

def _session():
    s = requests.Session()
    retries = Retry(total=3, backoff_factor=2, status_forcelist=[500, 502, 503, 504])
    s.mount("https://", HTTPAdapter(max_retries=retries))
    return s

def get_all_usdt_symbols():
    url = f"{BINANCE_FUTURES_BASE}/fapi/v1/exchangeInfo"
    resp = _session().get(url, timeout=15)
    data = resp.json()
    symbols = [s["symbol"] for s in data["symbols"]
               if s["symbol"].endswith("USDT") and s["status"] == "TRADING"]
    return sorted(symbols)

def fetch_klines(symbol, interval=INTERVAL, limit=LIMIT):
    url = f"{BINANCE_FUTURES_BASE}/fapi/v1/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    resp = _session().get(url, params=params, timeout=15)
    data = resp.json()
    rows = []
    for k in data:
        rows.append({
            "timestamp": int(k[0]),
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
            "close_time": int(k[6]),
            "quote_volume": float(k[7]),
            "trades": int(k[8])
        })
    return pd.DataFrame(rows)

def get_downloaded():
    downloaded = set()
    if not os.path.isdir(OUTPUT_DIR):
        return downloaded
    for f in os.listdir(OUTPUT_DIR):
        if f.endswith(f"_{INTERVAL}.csv"):
            sym = f.replace(f"_{INTERVAL}.csv", "")
            downloaded.add(sym)
    return downloaded

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("Fetching Binance Futures USDT pairs...")
    all_symbols = get_all_usdt_symbols()
    print(f"Total {len(all_symbols)} USDT pairs found.")

    downloaded = get_downloaded()
    symbols = [s for s in all_symbols if s not in downloaded]
    print(f"Already downloaded: {len(downloaded)}")
    print(f"Remaining: {len(symbols)}\n")

    if not symbols:
        print("All symbols already downloaded!")
        return

    results = []
    for i, sym in enumerate(symbols, 1):
        try:
            df = fetch_klines(sym)
            csv_path = os.path.join(OUTPUT_DIR, f"{sym}_{INTERVAL}.csv")
            df.to_csv(csv_path, index=False)
            print(f"[{i:3d}/{len(symbols)}] {sym} -> {len(df)} bars saved")
            results.append({"symbol": sym, "bars": len(df), "last_close": df["close"].iloc[-1], "status": "OK"})
        except Exception as e:
            print(f"[{i:3d}/{len(symbols)}] {sym} -> ERROR: {e}")
            results.append({"symbol": sym, "bars": 0, "last_close": 0, "status": f"ERROR"})
        time.sleep(0.15)

    # Merge with existing summary if present
    summary_path = os.path.join(OUTPUT_DIR, "_summary.csv")
    if os.path.exists(summary_path):
        existing = pd.read_csv(summary_path)
        new = pd.DataFrame(results)
        summary = pd.concat([existing, new], ignore_index=True)
    else:
        summary = pd.DataFrame(results)
    summary.to_csv(summary_path, index=False)

    ok_count = len(summary[summary["status"] == "OK"])
    total_bars = summary["bars"].sum()
    print(f"\n=== SUMMARY ===")
    print(f"Successful: {ok_count}/{len(all_symbols)}")
    print(f"Total bars: {total_bars}")
    print(f"Last updated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"Data saved in '{OUTPUT_DIR}' folder.")

if __name__ == "__main__":
    main()
