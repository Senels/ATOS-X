import pandas as pd
import numpy as np
from config import Config

def add_all_indicators(df):
    df = df.copy()
    close = df["close"].values
    high = df["high"].values
    low = df["low"].values
    volume = df["volume"].values

    df["rsi"] = _rsi(close, Config.RSI_LEN)
    df["macd"], df["macd_signal"], df["macd_hist"] = _macd(close)
    df["ema_fast"] = _ema(close, Config.EMA_FAST)
    df["ema_slow"] = _ema(close, Config.EMA_SLOW)
    df["vol_sma"] = _sma(volume, Config.VOL_SMA_LEN)
    df["atr"] = _atr(high, low, close, Config.ATR_LEN)
    df["vol_ratio"] = volume / df["vol_sma"]
    df["momentum_pct"] = _momentum(close, Config.MOMENTUM_LOOKBACK)
    return df

def _rsi(close, period=14):
    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)
    avg_gain = _sma(gain, period)
    avg_loss = _sma(loss, period)
    rs = np.divide(avg_gain, avg_loss, out=np.ones_like(avg_gain), where=avg_loss != 0)
    return 100 - (100 / (1 + rs))

def _ema(data, period):
    result = np.zeros_like(data)
    multiplier = 2 / (period + 1)
    result[0] = data[0]
    for i in range(1, len(data)):
        result[i] = (data[i] - result[i-1]) * multiplier + result[i-1]
    return result

def _sma(data, period):
    result = np.zeros_like(data)
    cumsum = np.cumsum(data)
    result[:period] = cumsum[:period] / np.arange(1, period + 1)
    result[period:] = (cumsum[period:] - cumsum[:-period]) / period
    return result

def _macd(close, fast=12, slow=26, signal=9):
    ema_fast = _ema(close, fast)
    ema_slow = _ema(close, slow)
    macd_line = ema_fast - ema_slow
    macd_signal = _ema(macd_line, signal)
    macd_hist = macd_line - macd_signal
    return macd_line, macd_signal, macd_hist

def _momentum(close, period=24):
    result = np.zeros_like(close)
    if len(close) > period:
        result[period:] = (close[period:] - close[:-period]) / close[:-period] * 100
    return result

def _atr(high, low, close, period=14):
    tr = np.maximum(high - low,
                    np.abs(high - np.roll(close, 1)),
                    np.abs(low - np.roll(close, 1)))
    tr[0] = high[0] - low[0]
    return _sma(tr, period)
