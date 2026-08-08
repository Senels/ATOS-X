from decimal import Decimal


class RiskManager:
    def __init__(self):
        self.daily_pnl = Decimal("0")
        self.consecutive_losses = 0
        self.max_daily_loss_pct = Decimal("-3")
        self.max_consecutive_losses = 3
        self.max_allocation_per_symbol = Decimal("0.20")
    def can_open(self, symbol: str, portfolio_value: Decimal, proposed_qty_value: Decimal, open_positions: dict) -> tuple[bool, str]:
        if portfolio_value>0 and proposed_qty_value / portfolio_value > self.max_allocation_per_symbol:
            return False, f"Konsantrasyon aşıldı: {symbol} > %20"
        if self.daily_pnl <= self.max_daily_loss_pct:
            return False, f"Günlük max zarar: {self.daily_pnl}%"
        if self.consecutive_losses >= self.max_consecutive_losses:
            return False, f"{self.consecutive_losses} üst üste stop - kill switch"
        if symbol in open_positions:
            return False, f"{symbol} zaten açık"
        return True, "OK"
    def on_trade_close(self, pnl: Decimal):
        self.daily_pnl += pnl
        self.consecutive_losses = self.consecutive_losses+1 if pnl<0 else 0
