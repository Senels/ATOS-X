"""Download Binance Global USD-M Futures klines for research/backtesting.

Uses Binance's public REST market-data endpoints only. No API key and no orders.
The downloader writes one CSV per symbol/timeframe and resumes from existing data.
"""
from __future__ import annotations

import argparse
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

BASE_URL = "https://fapi.binance.com"
KLINES = "/fapi/v1/klines"
EXCHANGE_INFO = "/fapi/v1/exchangeInfo"
INTERVAL_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
    "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000,
    "4h": 14_400_000, "6h": 21_600_000, "8h": 28_800_000,
    "12h": 43_200_000, "1d": 86_400_000,
}
COLUMNS = ["timestamp", "open", "high", "low", "close", "volume", "close_time",
           "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore"]


def _get(session: requests.Session, path: str, params: dict, retries: int = 5):
    for attempt in range(retries):
        try:
            response = session.get(BASE_URL + path, params=params, timeout=30)
            if response.status_code == 429:
                time.sleep(min(60, 2 ** attempt))
                continue
            response.raise_for_status()
            return response.json()
        except requests.RequestException:
            if attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError("Binance request failed")


def futures_symbols(session: requests.Session) -> list[str]:
    info = _get(session, EXCHANGE_INFO, {})
    return sorted(
        s["symbol"] for s in info["symbols"]
        if s.get("status") == "TRADING"
        and s.get("contractType") == "PERPETUAL"
        and s.get("quoteAsset") == "USDT"
    )


def download_symbol(session: requests.Session, symbol: str, interval: str,
                    start_ms: int, end_ms: int, out: Path) -> int:
    step = INTERVAL_MS[interval]
    cursor = start_ms
    rows: list[list] = []
    if out.exists():
        old = pd.read_csv(out)
        if not old.empty and "timestamp" in old.columns:
            cursor = max(cursor, int(old["timestamp"].max()) + step)
            rows = old.values.tolist()

    while cursor < end_ms:
        data = _get(session, KLINES, {
            "symbol": symbol, "interval": interval,
            "startTime": cursor, "endTime": end_ms, "limit": 1500,
        })
        if not data:
            break
        rows.extend(data)
        last = int(data[-1][0])
        next_cursor = last + step
        if next_cursor <= cursor:
            raise RuntimeError(f"Kline cursor stalled for {symbol} {interval}")
        cursor = next_cursor
        if len(data) < 1500:
            break
        time.sleep(0.08)

    if not rows:
        return 0
    df = pd.DataFrame(rows, columns=COLUMNS).drop_duplicates(subset=["timestamp"]).sort_values("timestamp")
    df = df[df["timestamp"].between(start_ms, end_ms - 1)]
    numeric = ["open", "high", "low", "close", "volume", "quote_volume", "trades", "taker_buy_base", "taker_buy_quote"]
    for col in numeric:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    return len(df)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", choices=sorted(INTERVAL_MS), default="4h")
    parser.add_argument("--years", type=float, default=5.0)
    parser.add_argument("--symbols", nargs="*", default=None)
    parser.add_argument("--max-symbols", type=int, default=0)
    parser.add_argument("--data-dir", default="backend/app/data/archive")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    start = now - timedelta(days=365.25 * args.years)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(now.timestamp() * 1000)

    with requests.Session() as session:
        symbols = args.symbols or futures_symbols(session)
        if args.max_symbols > 0:
            symbols = symbols[:args.max_symbols]
        print(f"Binance Global USD-M Futures: {len(symbols)} symbols, {args.interval}, {args.years:g} years")
        for i, symbol in enumerate(symbols, 1):
            path = Path(args.data_dir) / f"{symbol}_{args.interval}.csv"
            count = download_symbol(session, symbol, args.interval, start_ms, end_ms, path)
            print(f"[{i}/{len(symbols)}] {symbol}: {count} rows -> {path}")


if __name__ == "__main__":
    main()
