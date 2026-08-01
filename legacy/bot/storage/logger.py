import json
import os
from datetime import datetime
from config import Config

class TradeLogger:
    def __init__(self):
        self.trades = []
        self.load()

    def reset(self):
        self.trades = []
        self.save()

    def load(self):
        path = os.path.join(Config.LOG_DIR, "trades.json")
        if os.path.exists(path):
            with open(path) as f:
                self.trades = json.load(f)

    def save(self):
        path = os.path.join(Config.LOG_DIR, "trades.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.trades, f, indent=2)

    def log_trade(self, symbol, side, entry_price, exit_price, qty,
                  margin_used, pnl, pnl_pct, reason, entry_time, exit_time,
                  bars_held=0, commission=0.0):
        trade = {
            "symbol": symbol,
            "side": side,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "qty": qty,
            "margin_used": margin_used,
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 4),
            "reason": reason,
            "entry_time": entry_time,
            "exit_time": exit_time,
            "bars_held": bars_held,
            "commission": round(commission, 2),
        }
        self.trades.append(trade)
        self.save()
        return trade

    def get_all(self):
        return self.trades

    def get_recent(self, n=20):
        return self.trades[-n:]

    def summary(self):
        if not self.trades:
            return "No trades yet"
        total_pnl = sum(t["pnl"] for t in self.trades)
        wins = [t for t in self.trades if t["pnl"] > 0]
        losses = [t for t in self.trades if t["pnl"] < 0]
        wr = len(wins) / len(self.trades) * 100 if self.trades else 0
        return {
            "total_trades": len(self.trades),
            "total_pnl": round(total_pnl, 2),
            "win_rate": round(wr, 1),
            "wins": len(wins),
            "losses": len(losses),
            "avg_win": round(sum(t["pnl"] for t in wins) / len(wins), 2) if wins else 0,
            "avg_loss": round(sum(t["pnl"] for t in losses) / len(losses), 2) if losses else 0
        }
