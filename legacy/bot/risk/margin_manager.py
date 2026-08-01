from config import Config

class MarginManager:
    def __init__(self, initial_capital=Config.INITIAL_CAPITAL):
        self.total_equity = initial_capital
        self.used_margin = 0.0
        self.positions = {}

    def update_equity(self, equity):
        self.total_equity = equity

    def add_position(self, pos_id, margin_used):
        self.positions[pos_id] = margin_used
        self.used_margin = sum(self.positions.values())

    def remove_position(self, pos_id):
        if pos_id in self.positions:
            del self.positions[pos_id]
        self.used_margin = sum(self.positions.values())

    def can_open(self, required_margin):
        if self.total_equity <= 0:
            return False
        current_ratio = self.used_margin / self.total_equity
        new_ratio = (self.used_margin + required_margin) / self.total_equity
        return new_ratio <= Config.MARGIN_MAX_RATIO

    def available_margin(self):
        max_margin = self.total_equity * Config.MARGIN_MAX_RATIO
        return max(0, max_margin - self.used_margin)

    def position_count(self):
        return len(self.positions)

    def margin_usage_ratio(self):
        if self.total_equity <= 0:
            return 0
        return self.used_margin / self.total_equity
