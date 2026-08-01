import pandas as pd
import numpy as np
import time
import json
import os
from datetime import datetime
from config import Config
from data.fetcher import fetch_all_top_klines, generate_synthetic_data
from data.indicators import add_all_indicators
from strategy.scorer import calculate_scores
from risk.kelly_sizer import KellySizer
from risk.margin_manager import MarginManager
from risk.martingale import MartingaleTracker
from execution.trailing_stop import TrailingStopManager
from execution.position_tracker import PositionTracker
from storage.logger import TradeLogger
from ai.reflection import AIReflection

class BacktestEngine:
    def __init__(self):
        self.mm = MarginManager()
        self.kelly = KellySizer()
        self.martingale = MartingaleTracker()
        self.trailing = TrailingStopManager()
        self.positions = PositionTracker()
        self.logger = TradeLogger()
        self.ai = AIReflection()
        self.equity_curve = [Config.INITIAL_CAPITAL]

    def run(self, symbols=None):
        self.logger.reset()
        if symbols is None:
            symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
                       "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT",
                       "MATICUSDT", "UNIUSDT", "SHIBUSDT", "LTCUSDT", "ATOMUSDT",
                       "ETCUSDT", "XLMUSDT", "FILUSDT", "TRXUSDT", "APTUSDT"]
        print(f"Loading {len(symbols)} coins from CSVs...")
        raw = fetch_all_top_klines(symbols)
        if not raw:
            print("CSV load failed, generating synthetic data...")
            raw = generate_synthetic_data(symbols)
        print("Calculating indicators...")
        df_dict = {}
        for sym, df in raw.items():
            df_dict[sym] = add_all_indicators(df)

        print("Running backtest...")
        min_len = min(len(df) for df in df_dict.values())
        start_bar = max(Config.MOMENTUM_LOOKBACK + 1, 200)
        total_bars = min_len
        start_time = time.time()

        for bar_idx in range(start_bar, total_bars):
            if bar_idx % 100 == 0:
                elapsed = time.time() - start_time
                eta = (elapsed / (bar_idx - start_bar + 1)) * (total_bars - bar_idx) if bar_idx > start_bar else 0
                print(f"  Bar {bar_idx}/{total_bars} | Eq: {self.mm.total_equity:.0f} USDT | "
                      f"Pos: {self.positions.total_open_margin():.1f} margin | "
                      f"Elapsed: {elapsed:.0f}s ETA: {eta:.0f}s")

            current_data = {}
            for sym in df_dict:
                row = df_dict[sym].iloc[bar_idx]
                current_data[sym] = row.to_dict() if hasattr(row, 'to_dict') else row

            self._check_trailing_stops(current_data, bar_idx)
            self._check_new_signals(current_data, bar_idx, df_dict)

            equity = self.mm.total_equity - self.mm.used_margin
            for pos_id, pos in self.positions.get_open_positions().items():
                sym = pos["symbol"]
                if sym in current_data:
                    self.positions.update_upnl(pos_id, current_data[sym]["close"])
                    equity += pos["margin_used"] + pos.get("unrealized_pnl", 0)
            self.equity_curve.append(equity)

        print("\n=== BACKTEST COMPLETE ===")
        self._report()

    def _check_trailing_stops(self, current_data, bar_idx):
        for pos_id, pos in list(self.positions.get_open_positions().items()):
            sym = pos["symbol"]
            if sym not in current_data:
                continue
            cp = current_data[sym]["close"]
            result = self.trailing.update_price(pos_id, cp)
            if result and result[0] == "hit":
                stop_price = result[1]
                ts = current_data[sym]["timestamp"]
                pnl, pos_data = self.positions.close(pos_id, stop_price, "trailing_stop", exit_time=ts, exit_bar=bar_idx)
                self.mm.total_equity += pnl
                self.logger.log_trade(
                    pos["symbol"], pos["side"], pos["entry_price"], stop_price,
                    pos["qty"], pos["margin_used"], pnl,
                    pnl / pos["margin_used"], "trailing_stop",
                    pos_data["entry_time"], ts, bars_held=pos_data.get("bars_held", 0)
                )
                self.mm.remove_position(pos_id)
                self.kelly.update(pos["symbol"], pos["side"], pnl)
                self.martingale.on_win(pos["symbol"], pos["side"]) if pnl > 0 else \
                    self.martingale.on_loss(pos["symbol"], pos["side"])
                self.trailing.close_position(pos_id)

    def _check_new_signals(self, current_data, bar_idx, df_dict):
        combined = {}
        for sym, row_dict in current_data.items():
            mom = row_dict.get("momentum_pct", 0)
            if np.isnan(mom) or np.isinf(mom):
                continue
            if mom < Config.MIN_MOMENTUM_STRENGTH:
                continue
            if mom > 80:
                continue
            atr_pct = row_dict["atr"] / row_dict["close"] * 100 if row_dict["close"] > 0 else 0
            if atr_pct > Config.MAX_ATR_PCT:
                continue
            vol = row_dict.get("volume", 0)
            vol_sma = row_dict.get("vol_sma", vol)
            avg_vol = vol_sma * row_dict["close"]
            if avg_vol < Config.MIN_AVG_VOLUME_USDT:
                continue

            price = row_dict["close"]
            ema50 = row_dict.get("ema_fast", price)

            if price < ema50:
                continue

            macd = row_dict.get("macd", 0)
            macd_signal = row_dict.get("macd_signal", 0)
            macd_hist = row_dict.get("macd_hist", 0)

            if macd <= macd_signal:
                continue
            if macd_hist <= 0:
                continue

            rsi = row_dict.get("rsi", 50)
            if rsi > 75 or rsi < 35:
                continue

            score = mom
            if 40 < rsi < 60:
                score += 1.0
            elif 60 < rsi < 70:
                score += 0.5

            vol_ratio = row_dict.get("vol_ratio", 1.0)
            if vol_ratio > Config.VOL_MULTIPLIER:
                score += 1.0

            combined[sym] = {
                "score": score,
                "momentum": mom,
                "price": price,
                "atr_pct": atr_pct,
            }

        if not combined:
            return

        sorted_by_score = sorted(combined.items(), key=lambda x: x[1]["score"], reverse=True)
        candidates = [(s, "LONG") for s, d in sorted_by_score
                     if d["score"] >= Config.SCORE_ENTRY_THRESHOLD][:Config.MOMENTUM_TOP_N]

        open_count = len(self.positions.get_open_positions())
        max_new = Config.MAX_CONCURRENT_POSITIONS - open_count
        candidates = candidates[:max_new]

        for sym, side in candidates:
            if self.positions.is_open(sym, side):
                continue
            avail = self.mm.available_margin()
            if avail < Config.MIN_POSITION_MARGIN:
                continue
            kelly_pct = self.kelly.get_kelly(sym, side)
            mg_mult = self.martingale.get_multiplier(sym, side)
            pos_margin = avail * kelly_pct * mg_mult
            pos_margin = min(pos_margin, avail * Config.MAX_MARGIN_PCT_PER_POSITION)
            atr = current_data[sym]["atr"]
            price = current_data[sym]["close"]
            stop_dist_pct = (atr / price) * Config.TRAIL_DISTANCE_MULT if price > 0 else 0
            loss_pct_of_margin = stop_dist_pct * Config.LEVERAGE
            if loss_pct_of_margin > 0:
                risk_cap = avail * Config.RISK_PER_TRADE_PCT / loss_pct_of_margin
                pos_margin = min(pos_margin, risk_cap)
            pos_margin = max(pos_margin, Config.MIN_POSITION_MARGIN)
            if not self.mm.can_open(pos_margin):
                continue
            qty = pos_margin * Config.LEVERAGE / price
            if qty * price < 10:
                continue
            pos_id = f"{sym}_{side}_{bar_idx}"
            self.positions.open(pos_id, sym, side, price, qty, pos_margin, atr,
                                entry_time=current_data[sym]["timestamp"], entry_bar=bar_idx)
            self.mm.add_position(pos_id, pos_margin)
            self.trailing.open_position(pos_id, price, atr, side)
            open_count += 1

    def _report(self):
        trades = self.logger.get_all()
        eq = self.equity_curve
        final_eq = eq[-1] if eq else Config.INITIAL_CAPITAL
        total_return = (final_eq - Config.INITIAL_CAPITAL) / Config.INITIAL_CAPITAL * 100
        print(f"\nInitial Capital: {Config.INITIAL_CAPITAL:.0f} USDT")
        print(f"Final Equity:    {final_eq:.0f} USDT")
        print(f"Total Return:    %{total_return:.2f}")
        print(f"Total Trades:    {len(trades)}")
        if trades:
            wins = [t for t in trades if t["pnl"] > 0]
            losses = [t for t in trades if t["pnl"] < 0]
            wr = len(wins) / len(trades) * 100
            total_pnl = sum(t["pnl"] for t in trades)
            pf = abs(sum(t["pnl"] for t in wins) / sum(t["pnl"] for t in losses)) if sum(t["pnl"] for t in losses) < 0 else 0
            print(f"Win Rate:        %{wr:.1f} ({len(wins)}W / {len(losses)}L)")
            print(f"Total PnL:       {total_pnl:.0f} USDT")
            print(f"Profit Factor:   {pf:.2f}")
            avg_w = np.mean([t["pnl"] for t in wins]) if wins else 0
            avg_l = np.mean([t["pnl"] for t in losses]) if losses else 0
            print(f"Avg Win:         {avg_w:.1f} USDT")
            print(f"Avg Loss:        {avg_l:.1f} USDT")

            peak = np.maximum.accumulate(eq)
            dd = (peak - eq) / peak
            max_dd = np.max(dd) * 100
            print(f"Max Drawdown:    %{max_dd:.2f}")

            if len(eq) > 1:
                returns = np.diff(eq) / eq[:-1]
                sharpe = np.mean(returns) / np.std(returns) * np.sqrt(365) if np.std(returns) > 0 else 0
                print(f"Sharpe Ratio:    {sharpe:.2f}")

        reflection = self.ai.analyze(trades, eq, None)
        if reflection:
            print(f"\n=== AI REFLECTION ===")
            print(f"Rating: {reflection['rating']}")
            print(f"Sharpe: {reflection['sharpe']}")
            print(f"Win Rate: %{reflection['win_rate']*100:.1f}")
            print(f"Max DD: %{reflection['max_drawdown']*100:.1f}")
            if reflection["suggestions"]:
                print(f"Suggestions:")
                for s in reflection["suggestions"]:
                    print(f"  - {s}")

        self.save_results(trades, eq, reflection)

    def save_results(self, trades, eq, reflection=None):
        result = {
            "config": {
                "leverage": Config.LEVERAGE,
                "initial_capital": Config.INITIAL_CAPITAL,
                "max_concurrent": Config.MAX_CONCURRENT_POSITIONS,
                "kelly_fractional": Config.KELLY_FRACTIONAL,
                "trail_distance_mult": Config.TRAIL_DISTANCE_MULT,
                "trail_activation_mult": Config.TRAIL_ACTIVATION_MULT,
                "mg_multiplier": Config.MG_MULTIPLIER,
                "mg_max_level": Config.MG_MAX_LEVEL,
                "momentum_lookback": Config.MOMENTUM_LOOKBACK,
                "momentum_top_n": Config.MOMENTUM_TOP_N,
                "min_avg_volume_usdt": Config.MIN_AVG_VOLUME_USDT,
                "max_atr_pct": Config.MAX_ATR_PCT,
            },
            "summary": {},
            "equity_curve": [round(e, 2) for e in eq],
            "trades": trades,
            "reflection": reflection,
            "timestamp": datetime.now().isoformat()
        }
        if trades:
            wins = [t for t in trades if t["pnl"] > 0]
            losses = [t for t in trades if t["pnl"] < 0]
            total_pnl = sum(t["pnl"] for t in trades)
            pf = abs(sum(t["pnl"] for t in wins) / sum(t["pnl"] for t in losses)) if losses and sum(t["pnl"] for t in losses) < 0 else 0
            peak = np.maximum.accumulate(eq)
            dd = (peak - eq) / peak
            max_dd = np.max(dd) * 100
            returns = np.diff(eq) / eq[:-1] if len(eq) > 1 else [0]
            sharpe = float(np.mean(returns) / np.std(returns) * np.sqrt(365)) if len(returns) > 0 and np.std(returns) > 0 else 0
            result["summary"] = {
                "final_equity": round(eq[-1], 2) if eq else Config.INITIAL_CAPITAL,
                "total_return_pct": round((eq[-1] - Config.INITIAL_CAPITAL) / Config.INITIAL_CAPITAL * 100, 2) if eq else 0,
                "total_trades": len(trades),
                "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else 0,
                "wins": len(wins),
                "losses": len(losses),
                "total_pnl": round(total_pnl, 2),
                "profit_factor": round(pf, 2),
                "avg_win": round(np.mean([t["pnl"] for t in wins]), 2) if wins else 0,
                "avg_loss": round(np.mean([t["pnl"] for t in losses]), 2) if losses else 0,
                "max_drawdown_pct": round(max_dd, 2),
                "sharpe_ratio": round(sharpe, 2),
                "avg_win_pct": round(np.mean([t["pnl_pct"]*100 for t in wins]), 2) if wins else 0,
                "avg_loss_pct": round(np.mean([t["pnl_pct"]*100 for t in losses]), 2) if losses else 0,
                "max_win_pct": round(max(t["pnl_pct"]*100 for t in wins), 2) if wins else 0,
                "max_loss_pct": round(min(t["pnl_pct"]*100 for t in losses), 2) if losses else 0,
                "max_win": round(max(t["pnl"] for t in wins), 2) if wins else 0,
                "max_loss": round(min(t["pnl"] for t in losses), 2) if losses else 0,
            }
        out_dir = os.path.join(Config.LOG_DIR, "backtest_results")
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, "latest.json")
        with open(path, "w") as f:
            json.dump(result, f, indent=2)
        print(f"\nResults saved to {path}")

if __name__ == "__main__":
    bt = BacktestEngine()
    bt.run()
