import numpy as np
import json
import os
from datetime import datetime
from config import Config

class AIReflection:
    def __init__(self):
        self.reflections = []
        self.load()

    def load(self):
        path = os.path.join(Config.LOG_DIR, "reflections.json")
        if os.path.exists(path):
            with open(path) as f:
                self.reflections = json.load(f)

    def save(self):
        path = os.path.join(Config.LOG_DIR, "reflections.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.reflections[-50:], f, indent=2)

    def analyze(self, trade_history, equity_curve, current_config):
        if len(trade_history) < 5:
            return None
        recent = trade_history[-20:]
        wins = [t for t in recent if t["pnl"] > 0]
        losses = [t for t in recent if t["pnl"] < 0]
        win_rate = len(wins) / len(recent) if recent else 0
        avg_win = np.mean([t["pnl"] for t in wins]) if wins else 0
        avg_loss = abs(np.mean([t["pnl"] for t in losses])) if losses else 0
        profit_factor = abs(avg_win * len(wins) / (avg_loss * len(losses))) if avg_loss * len(losses) > 0 else 0
        sharpe = 0
        if len(equity_curve) > 1:
            returns = np.diff(equity_curve) / equity_curve[:-1]
            sharpe = np.mean(returns) / np.std(returns) * np.sqrt(365) if np.std(returns) > 0 else 0
        max_dd = 0
        if len(equity_curve) > 1:
            peak = np.maximum.accumulate(equity_curve)
            dd = (peak - equity_curve) / peak
            max_dd = np.max(dd) if len(dd) > 0 else 0

        suggestions = []
        if win_rate < 0.35:
            suggestions.append(f"Kazanma orani dusuk (%{win_rate*100:.0f}). Score esigi artirilabilir veya RSI araligi daraltilabilir.")
        if profit_factor < 1.2:
            suggestions.append(f"Profit factor dusuk ({profit_factor:.2f}). SL/TP orani veya trailing stop mesafesi gozden gecirilmeli.")
        if max_dd > 0.20:
            suggestions.append(f"Max drawdown %%{max_dd*100:.0f}. Pozisyon boyutu kucultulmeli veya Kelly orani azaltilmali.")
        if sharpe < 0.5 and sharpe > 0:
            suggestions.append(f"Sharpe dusuk ({sharpe:.2f}). Daha fazla cesitlendirme veya daha yuksek score esigi onerilir.")

        reflection = {
            "timestamp": datetime.now().isoformat(),
            "trade_count": len(recent),
            "win_rate": round(win_rate, 4),
            "profit_factor": round(profit_factor, 4),
            "sharpe": round(sharpe, 4),
            "max_drawdown": round(max_dd, 4),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "total_pnl": round(sum(t["pnl"] for t in recent), 2),
            "suggestions": suggestions,
            "rating": "GOOD" if sharpe > 1.0 and win_rate > 0.45 else ("NEUTRAL" if sharpe > 0.3 else "NEEDS_IMPROVEMENT")
        }
        self.reflections.append(reflection)
        self.save()
        return reflection
