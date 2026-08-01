from config import Config

class MartingaleTracker:
    def __init__(self):
        self.levels = {}

    def get_level(self, symbol, side):
        key = f"{symbol}_{side}"
        return self.levels.get(key, 0)

    def on_loss(self, symbol, side):
        key = f"{symbol}_{side}"
        current = self.levels.get(key, 0)
        self.levels[key] = min(current + 1, Config.MG_MAX_LEVEL)

    def on_win(self, symbol, side):
        key = f"{symbol}_{side}"
        self.levels[key] = 0

    def get_multiplier(self, symbol, side):
        level = self.get_level(symbol, side)
        return Config.MG_BASE_MULT * (Config.MG_MULTIPLIER ** level)

    def reset_all(self):
        self.levels = {}
