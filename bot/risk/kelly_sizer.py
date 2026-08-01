import numpy as np
from config import Config

class KellySizer:
    def __init__(self):
        self.stats = {}

    def update(self, symbol, side, result_usd):
        key = f"{symbol}_{side}"
        if key not in self.stats:
            self.stats[key] = {"wins": [], "pnls": []}
        self.stats[key]["pnls"].append(result_usd)
        self.stats[key]["wins"].append(1 if result_usd > 0 else 0)
        if len(self.stats[key]["wins"]) > Config.KELLY_WINDOW * 2:
            self.stats[key]["wins"] = self.stats[key]["wins"][-Config.KELLY_WINDOW * 2:]
            self.stats[key]["pnls"] = self.stats[key]["pnls"][-Config.KELLY_WINDOW * 2:]

    def get_kelly(self, symbol, side):
        key = f"{symbol}_{side}"
        if key not in self.stats or len(self.stats[key]["wins"]) < 5:
            return 0.08
        wins = self.stats[key]["wins"]
        pnls = self.stats[key]["pnls"]
        p = np.mean(wins)
        avg_win = np.mean([x for x in pnls if x > 0]) if any(x > 0 for x in pnls) else 1
        avg_loss = abs(np.mean([x for x in pnls if x < 0])) if any(x < 0 for x in pnls) else 1
        b = avg_win / avg_loss if avg_loss > 0 else 1
        q = 1 - p
        kelly = (p * b - q) / b if b > 0 else 0
        kelly = max(0.01, min(kelly * Config.KELLY_FRACTIONAL, 0.20))
        return round(kelly, 4)
