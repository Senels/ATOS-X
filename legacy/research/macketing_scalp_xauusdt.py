#!/usr/bin/env python3
"""
Macketings 1min Scalping [Smart Exit + Time] - Python backtest
Converted from Pine Script v6
Symbol: XAUUSDT (Binance Futures)
Timeframe: 1m
Period: Last ~3 months
"""

import json
import urllib3
import pandas as pd
import numpy as np
from backtesting import Backtest, Strategy
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ─── PARAMETERS ──────────────────────────────────────────
LEN80 = 80
LEN90 = 90
LEN340 = 340
LEN500 = 500
LEN_SAFETY = 325
COOLDOWN_BARS = 100
USE_TIME_FILTER = True
TRADING_SESSION = "0900-2359"
SL_PERC = 0.002
BE_TRIGGER_PERC = 0.003
BE_PROFIT_PERC = 0.002
TP_PERC = 0.015
USE_SMART_EXIT = True
SMART_EXIT_MIN_PROFIT = 0.003


# ─── HELPERS ─────────────────────────────────────────────
def is_time_ok(timestamp, session_str):
    start_str, end_str = session_str.split('-')
    start_mins = int(start_str[:2]) * 60 + int(start_str[2:])
    end_mins = int(end_str[:2]) * 60 + int(end_str[2:])
    cur_mins = timestamp.hour * 60 + timestamp.minute
    if start_mins <= end_mins:
        return start_mins <= cur_mins <= end_mins
    return cur_mins >= start_mins or cur_mins <= end_mins


# ─── STRATEGY ────────────────────────────────────────────
class MacketingScalping(Strategy):
    len80 = LEN80
    len90 = LEN90
    len340 = LEN340
    len500 = LEN500
    len_safety = LEN_SAFETY
    cooldown_bars = COOLDOWN_BARS
    use_time_filter = USE_TIME_FILTER
    trading_session = TRADING_SESSION
    sl_perc = SL_PERC
    be_trigger_perc = BE_TRIGGER_PERC
    be_profit_perc = BE_PROFIT_PERC
    tp_perc = TP_PERC
    use_smart_exit = USE_SMART_EXIT
    smart_exit_min_profit = SMART_EXIT_MIN_PROFIT

    def init(self):
        close = self.data.Close.df.squeeze()

        self.ema80 = self.I(lambda: close.ewm(span=self.len80, adjust=False).mean(), name='EMA80')
        self.ema90 = self.I(lambda: close.ewm(span=self.len90, adjust=False).mean(), name='EMA90')
        self.ema340 = self.I(lambda: close.ewm(span=self.len340, adjust=False).mean(), name='EMA340')
        self.ema500 = self.I(lambda: close.ewm(span=self.len500, adjust=False).mean(), name='EMA500')
        self.sma_safety = self.I(lambda: close.rolling(window=self.len_safety).mean(), name='SMA_Safety')

        self.last_exit_idx = -self.cooldown_bars - 1
        self.stop_price = None
        self.tp_price = None
        self.entry_price = None

    def next(self):
        i = len(self.data) - 1
        min_bars = max(self.len80, self.len90, self.len340, self.len500, self.len_safety) + 5
        if i < min_bars:
            return

        close = self.data.Close[-1]
        high = self.data.High[-1]
        low = self.data.Low[-1]

        ema80 = self.ema80[-1]
        ema90 = self.ema90[-1]
        ema340 = self.ema340[-1]
        ema500 = self.ema500[-1]
        sma_safety = self.sma_safety[-1]

        mid_band_low = min(ema80, ema90)
        mid_band_high = max(ema80, ema90)

        prev_close = self.data.Close[-2]
        prev_ema80 = self.ema80[-2]
        prev_ema90 = self.ema90[-2]
        prev_ema340 = self.ema340[-2]
        prev_ema500 = self.ema500[-2]
        prev_mid_low = min(prev_ema80, prev_ema90)
        prev_mid_high = max(prev_ema80, prev_ema90)

        # Cooldown
        is_cooldown_ok = (i - self.last_exit_idx) >= self.cooldown_bars

        # Time filter
        time_ok = not self.use_time_filter or is_time_ok(self.data.index[-1], self.trading_session)

        # Entry conditions
        retest_in_band = (mid_band_low <= close <= mid_band_high)
        retest_or_breakout_long = retest_in_band or (prev_close <= prev_mid_high and close > mid_band_high)
        retest_or_breakout_short = retest_in_band or (prev_close >= prev_mid_low and close < mid_band_low)

        long_conf = close > prev_close
        short_conf = close < prev_close

        long_trend_ok = (ema340 > ema500) and (mid_band_low > ema500)
        short_trend_ok = (ema340 < ema500) and (mid_band_high < ema500)

        long_entry = long_trend_ok and retest_or_breakout_long and long_conf and (close > sma_safety) and is_cooldown_ok and time_ok
        short_entry = short_trend_ok and retest_or_breakout_short and short_conf and (close < sma_safety) and is_cooldown_ok and time_ok

        # Entry
        if self.position.size == 0:
            if long_entry:
                self.buy()
                self.entry_price = close
                self.stop_price = close * (1 - self.sl_perc)
                self.tp_price = close * (1 + self.tp_perc)
            elif short_entry:
                self.sell()
                self.entry_price = close
                self.stop_price = close * (1 + self.sl_perc)
                self.tp_price = close * (1 - self.tp_perc)



        # LONG EXITS
        if self.position.is_long:
            # Breakeven
            if close >= self.entry_price * (1 + self.be_trigger_perc):
                new_sl = self.entry_price * (1 + self.be_profit_perc)
                if new_sl > self.stop_price:
                    self.stop_price = new_sl

            # Smart band exit
            if self.use_smart_exit and close >= self.entry_price * (1 + self.smart_exit_min_profit):
                if prev_close >= prev_mid_low and close < mid_band_low:
                    self.position.close()
                    self._reset(i)
                    return

            # Trend change exit
            if not np.isnan(prev_ema340) and not np.isnan(prev_ema500):
                if prev_ema340 >= prev_ema500 and ema340 < ema500:
                    self.position.close()
                    self._reset(i)
                    return

            # Stop loss
            if self.stop_price is not None and low <= self.stop_price:
                self.position.close()
                self._reset(i)
                return

            # Take profit
            if self.tp_price is not None and high >= self.tp_price:
                self.position.close()
                self._reset(i)
                return

        # SHORT EXITS
        if self.position.is_short:
            # Breakeven
            if close <= self.entry_price * (1 - self.be_trigger_perc):
                new_sl = self.entry_price * (1 - self.be_profit_perc)
                if new_sl < self.stop_price:
                    self.stop_price = new_sl

            # Smart band exit
            if self.use_smart_exit and close <= self.entry_price * (1 - self.smart_exit_min_profit):
                if prev_close <= prev_mid_high and close > mid_band_high:
                    self.position.close()
                    self._reset(i)
                    return

            # Trend change exit
            if not np.isnan(prev_ema340) and not np.isnan(prev_ema500):
                if prev_ema340 <= prev_ema500 and ema340 > ema500:
                    self.position.close()
                    self._reset(i)
                    return

            # Stop loss
            if self.stop_price is not None and high >= self.stop_price:
                self.position.close()
                self._reset(i)
                return

            # Take profit
            if self.tp_price is not None and low <= self.tp_price:
                self.position.close()
                self._reset(i)
                return

    def _reset(self, exit_idx):
        self.last_exit_idx = exit_idx
        self.stop_price = None
        self.tp_price = None
        self.entry_price = None


# ─── DATA FETCH ──────────────────────────────────────────
CACHE_PATH = "C:\\Users\\svkts\\OneDrive\\Belgeler\\Default Project\\xauusdt_1m_3mo.csv"

def fetch_data(force_refresh=False):
    # Try loading from cache
    if not force_refresh:
        try:
            df = pd.read_csv(CACHE_PATH, index_col=0, parse_dates=True)
            print(f"  Loaded {len(df)} bars from cache")
            return df
        except (FileNotFoundError, pd.errors.EmptyDataError):
            pass

    # Fetch fresh data via Binance Futures API
    http = urllib3.PoolManager(cert_reqs="CERT_NONE", assert_hostname=False)
    symbol = "XAUUSDT"
    interval = "1m"
    limit = 1000
    start_ts = int((datetime.now() - timedelta(days=90)).timestamp() * 1000)

    all_candles = []
    current_start = start_ts

    while True:
        params = {"symbol": symbol, "interval": interval, "startTime": current_start, "limit": limit}
        url = "https://fapi.binance.com/fapi/v1/klines?" + "&".join(f"{k}={v}" for k, v in params.items())
        try:
            r = http.request("GET", url, timeout=30)
            candles = json.loads(r.data.decode("utf-8"))
        except Exception as e:
            print(f"\n  Fetch error: {e}")
            break
        if not candles:
            break
        all_candles.extend(candles)
        current_start = candles[-1][0] + 60000
        if len(candles) < limit:
            break
        print(f"  Fetched {len(all_candles)} bars...", end="\r")

    print(f"\n  Total: {len(all_candles)} bars")

    df = pd.DataFrame(all_candles, columns=[
        "Timestamp", "Open", "High", "Low", "Close", "Volume",
        "CloseTime", "QuoteVol", "Trades", "TakerBuyBase", "TakerBuyQuote", "Ignore"
    ])
    df["Timestamp"] = pd.to_datetime(df["Timestamp"], unit="ms")
    df.set_index("Timestamp", inplace=True)
    df = df[["Open", "High", "Low", "Close", "Volume"]].astype(float)
    df = df[~df.index.duplicated(keep="first")]
    df.sort_index(inplace=True)
    df.to_csv(CACHE_PATH)
    return df


# ─── MAIN ────────────────────────────────────────────────
if __name__ == '__main__':
    print("=" * 55)
    print("  Macketings 1min Scalping - XAUUSDT")
    print("=" * 55)

    print("\nFetching XAUUSDT 1m data from Binance Futures...")
    data = fetch_data()
    print(f"  From {data.index[0]}  to  {data.index[-1]}")
    print(f"  Shape: {data.shape}")

    bt = Backtest(data, MacketingScalping, cash=10_000, commission=0.0002)
    results = bt.run()

    print("\n" + "-" * 55)
    print("  BACKTEST RESULTS")
    print("-" * 55)
    summary = {}
    for key, val in results.items():
        if key in ('_trades', '_equity_curve', '_strategy'):
            continue
        summary[key] = val

    for key in ['Start', 'End', 'Duration', 'Exposure Time [%]',
                'Equity Final [$]', 'Equity Peak [$]', 'Return [%]', 'Buy & Hold Return [%]',
                'Max. Drawdown [%]', 'Max. Drawdown Duration',
                'Total Trades', 'Win Rate [%]', 'Best Trade [%]', 'Worst Trade [%]',
                'Avg Trade [%]', 'Max Trade Duration', 'Avg Trade Duration',
                'Profit Factor', 'Expectancy [%]', 'Sharpe Ratio', 'Calmar Ratio']:
        if key in summary:
            print(f"  {key:25s}: {str(summary[key]):>25s}")

    trades = results['_trades']
    if trades is not None and len(trades) > 0:
        print("\n" + "-" * 55)
        print(f"  TRADES ({len(trades)})")
        print("-" * 55)
        cols = ['Size', 'EntryPrice', 'ExitPrice', 'PnL', 'ReturnPct',
                'EntryTime', 'ExitTime', 'Duration']
        avail = [c for c in cols if c in trades.columns]
        pd.set_option('display.max_rows', 30)
        pd.set_option('display.width', 160)
        print(trades[avail].head(25).to_string())

        csv_path = "C:\\Users\\svkts\\OneDrive\\Belgeler\\Default Project\\xauusdt_trades.csv"
        trades.to_csv(csv_path)
        print(f"\n  All trades saved: {csv_path}")

    try:
        bt.plot(filename='macketing_xauusdt_1m.html', open_browser=False)
        print("\n  Chart: macketing_xauusdt_1m.html")
    except Exception as e:
        print(f"\n  Plot error: {e}")
