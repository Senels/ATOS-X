import pandas as pd
import numpy as np
import requests
import time
import json
import os
from datetime import datetime, timedelta
from config import Config

BINANCE_FUTURES_BASE = "https://fapi.binance.com"

def fetch_top_coins(min_volume=Config.MIN_VOLUME_USDT, top_n=Config.TOP_N_COINS):
    url = f"{BINANCE_FUTURES_BASE}/fapi/v1/ticker/24hr"
    resp = requests.get(url, timeout=10)
    data = resp.json()
    usdt_pairs = [d for d in data if d["symbol"].endswith("USDT")]
    sorted_pairs = sorted(usdt_pairs, key=lambda x: float(x["quoteVolume"]), reverse=True)
    top = [p["symbol"] for p in sorted_pairs if float(p["quoteVolume"]) >= min_volume][:top_n]
    return top

def fetch_klines(symbol, interval=Config.TIMEFRAME, limit=1000):
    url = f"{BINANCE_FUTURES_BASE}/fapi/v1/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    resp = requests.get(url, params=params, timeout=15)
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

def load_csv_data(symbols, interval=Config.TIMEFRAME):
    data = {}
    interval_min = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "2h": 120, "4h": 240, "1d": 1440}
    suffix = interval_min.get(interval, 240)
    for sym in symbols:
        csv_path = os.path.join(Config.HISTORY_DIR, f"{sym}_{suffix}.csv")
        if os.path.exists(csv_path):
            df = pd.read_csv(csv_path)
            df["symbol"] = sym
            data[sym] = df
            print(f"  {sym}: {len(df)} bars (CSV)")
        else:
            print(f"  {sym}: CSV not found")
    return data

def fetch_all_top_klines(symbols=None, interval=Config.TIMEFRAME, limit=1000):
    if symbols is None:
        symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
                    "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT",
                    "MATICUSDT", "UNIUSDT", "SHIBUSDT", "LTCUSDT", "ATOMUSDT",
                    "ETCUSDT", "XLMUSDT", "FILUSDT", "TRXUSDT", "APTUSDT"]
    csv_data = load_csv_data(symbols, interval)
    if csv_data:
        return csv_data
    cache_file = os.path.join(Config.HISTORY_DIR, f"klines_cache_{interval}.json")
    data = {}
    for sym in symbols:
        try:
            df = fetch_klines(sym, interval, limit)
            df["symbol"] = sym
            data[sym] = df
            print(f"  {sym}: {len(df)} bars")
        except Exception as e:
            print(f"  {sym}: FAILED - {e}")
    return data

def _regime_returns(n, base_vol=0.015):
    """Generate returns with regime switching (trend/range) and momentum."""
    regimes = np.zeros(n, dtype=int)
    n_regimes = max(3, n // 200)
    for i in range(n_regimes):
        start = i * n // n_regimes
        end = (i + 1) * n // n_regimes
        regimes[start:end] = np.random.choice([0, 1, 2], p=[0.3, 0.4, 0.3])
    returns = np.zeros(n)
    r_prev = 0.0
    for i in range(n):
        if regimes[i] == 0:
            mu = np.random.uniform(-0.0008, 0.0008)
            sig = base_vol * np.random.uniform(0.5, 1.0)
        elif regimes[i] == 1:
            mu = np.random.uniform(-0.0003, 0.0005)
            sig = base_vol * np.random.uniform(0.8, 1.2)
        else:
            mu = np.random.uniform(-0.0015, 0.0015)
            sig = base_vol * np.random.uniform(1.5, 3.0)
        ar = 0.08 * r_prev
        r = np.random.normal(mu + ar, sig)
        returns[i] = r
        r_prev = r
    return returns

def generate_synthetic_data(symbols=None, n_bars=1000):
    if symbols is None:
        symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
                    "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT",
                    "MATICUSDT", "UNIUSDT", "SHIBUSDT", "LTCUSDT", "ATOMUSDT",
                    "ETCUSDT", "XLMUSDT", "FILUSDT", "TRXUSDT", "APTUSDT",
                    "AAVEUSDT", "ALGOUSDT", "ARBUSDT", "AXSUSDT", "CHZUSDT",
                    "CROUSDT", "EOSUSDT", "FETUSDT", "FTMUSDT", "GRTUSDT",
                    "HBARUSDT", "ICPUSDT", "INJUSDT", "IOTAUSDT", "KAVAUSDT",
                    "KSMUSDT", "MANAUSDT", "MKRUSDT", "NEARUSDT", "OPUSDT",
                    "PEPEUSDT", "QNTUSDT", "RUNEUSDT", "SANDUSDT", "SEIUSDT",
                    "STORJUSDT", "SUSHIUSDT", "THETAUSDT", "TIAUSDT", "WIFUSDT"]
    np.random.seed(42)
    data = {}
    base_prices = {"BTCUSDT": 65000, "ETHUSDT": 3400, "BNBUSDT": 580, "SOLUSDT": 140,
                   "XRPUSDT": 0.62, "DOGEUSDT": 0.12, "ADAUSDT": 0.45, "AVAXUSDT": 35,
                   "DOTUSDT": 7.2, "LINKUSDT": 14.5, "MATICUSDT": 0.72, "UNIUSDT": 7.8,
                   "SHIBUSDT": 0.000025, "LTCUSDT": 85, "ATOMUSDT": 9.5,
                   "ETCUSDT": 28, "XLMUSDT": 0.11, "FILUSDT": 5.8, "TRXUSDT": 0.11, "APTUSDT": 9.2}
    common_factor = _regime_returns(n_bars + 50, 0.010)
    for sym in symbols:
        price = base_prices.get(sym, 100)
        n = n_bars + 50
        beta = np.random.uniform(0.3, 1.2)
        idiomatic = _regime_returns(n, np.random.uniform(0.005, 0.020))
        returns = beta * common_factor + (1 - beta * 0.5) * idiomatic
        c = price * np.exp(np.cumsum(returns))
        o = np.concatenate([[c[0]], c[:-1]])
        h = np.maximum(o, c) * (1 + np.abs(returns) * np.random.uniform(0.5, 1.0, n))
        l = np.minimum(o, c) * (1 - np.abs(returns) * np.random.uniform(0.5, 1.0, n))
        h = np.maximum(h, c * 1.001)
        l = np.minimum(l, c * 0.999)
        v = np.random.lognormal(14, 1.5, n) * (1 + 0.5 * np.sin(np.arange(n) * 0.01))
        v = v * (1 + 5 * np.abs(returns))
        ts = pd.date_range(end=pd.Timestamp.now(), periods=n, freq="4h").astype(np.int64) // 10**6
        df = pd.DataFrame({
            "timestamp": ts, "open": o, "high": h, "low": l, "close": c,
            "volume": v, "close_time": ts, "quote_volume": c * v,
            "trades": np.random.randint(1000, 50000, n)
        })
        df["symbol"] = sym
        data[sym] = df
        print(f"  {sym}: {len(df)} bars (synthetic)")
    return data
