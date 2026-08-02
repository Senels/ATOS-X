"""ATOS X backtest motoru.

TradeBotV23.analyze()'den gelen `orders` DataFrame'ini alir ve gercekcil
sekilde simule eder:
  - Sinyal bari kapanista uretilir -> giris bir sonraki barin ACILISINDA olur
  - SL/TP bar ici (high/low) kontrol edilir; ayni bar hem SL hem TP gorurse
    kotumsem (SL once) kabul edilir
  - Taker fee + slippage
  - Risk bazli pozisyon boyutlandirma: risk % = RISK_PER_TRADE
  - Kaldirac siniri: notional <= equity * max_leverage
"""
import numpy as np
import pandas as pd
from typing import Any, Dict, List, Optional

_BARS_PER_YEAR = {
    "1m": 525600, "3m": 175200, "5m": 105120, "15m": 35040,
    "30m": 17520, "1h": 8760, "2h": 4380, "4h": 2190,
    "6h": 1460, "12h": 730, "1d": 365, "3d": 121, "1w": 52,
}


def bars_per_year(interval: str) -> int:
    return _BARS_PER_YEAR.get(str(interval).lower(), 2190)


class BacktestEngine:
    def __init__(
        self,
        initial_equity: float = 10000.0,
        risk_per_trade: float = 0.02,
        fee_rate: float = 0.0005,
        slippage: float = 0.0001,
        max_leverage: float = 10.0,
    ):
        self.initial_equity = float(initial_equity)
        self.risk_per_trade = float(risk_per_trade)
        self.fee_rate = float(fee_rate)
        self.slippage = float(slippage)
        self.max_leverage = float(max_leverage)

    # ------------------------------------------------------------------
    def run(self, df: pd.DataFrame, orders: pd.DataFrame, interval: str = "4h") -> Dict[str, Any]:
        if len(df) < 2:
            return {"error": "Yetersiz veri"}
        o = df["open"].to_numpy(float)
        h = df["high"].to_numpy(float)
        l = df["low"].to_numpy(float)
        c = df["close"].to_numpy(float)
        sig = orders["signal"].to_numpy(int)
        sl_arr = orders["sl"].to_numpy(float)
        tp_arr = orders["tp"].to_numpy(float)

        n = len(df)
        self.equity = self.initial_equity
        trades: List[Dict[str, Any]] = []
        eq_curve = np.empty(n)
        pos: Optional[Dict[str, Any]] = None
        bars_in_pos = 0

        for i in range(n):
            # 1) Bekleyen emir: bir onceki barin sinyali -> bu barin acilisi
            if i > 0 and sig[i - 1] != 0:
                side = int(sig[i - 1])
                slp = sl_arr[i - 1]
                tpp = tp_arr[i - 1]

                if pos is not None and pos["side"] != side:
                    self._close(pos, o[i], "flip", i, trades)
                    pos = None

                if pos is None and np.isfinite(slp):
                    px = o[i] * (1 + self.slippage * side)
                    if side == 1:
                        if px <= slp:
                            self._gap_trade(1, px, slp, tpp, "immediate_sl", i, trades)
                        elif px >= tpp:
                            self._gap_trade(1, px, slp, tpp, "immediate_tp", i, trades)
                        else:
                            pos = self._open(1, px, slp, tpp, i)
                    else:
                        if px >= slp:
                            self._gap_trade(-1, px, slp, tpp, "immediate_sl", i, trades)
                        elif px <= tpp:
                            self._gap_trade(-1, px, slp, tpp, "immediate_tp", i, trades)
                        else:
                            pos = self._open(-1, px, slp, tpp, i)
                    if pos is not None:
                        self.equity -= pos["entry_fee"]

            # 2) Acik pozisyon yonetimi (SL/TP bar ici)
            if pos is not None:
                bars_in_pos += 1
                side = pos["side"]
                if side == 1:
                    if l[i] <= pos["sl"]:
                        self._close(pos, pos["sl"], "stop_loss", i, trades)
                        pos = None
                    elif h[i] >= pos["tp"]:
                        self._close(pos, pos["tp"], "take_profit", i, trades)
                        pos = None
                else:
                    if h[i] >= pos["sl"]:
                        self._close(pos, pos["sl"], "stop_loss", i, trades)
                        pos = None
                    elif l[i] <= pos["tp"]:
                        self._close(pos, pos["tp"], "take_profit", i, trades)
                        pos = None

            # 3) Equity egrisi (acik pozisyonun gerceklesmemis PnL'i ile)
            if pos is not None:
                unreal = (c[i] - pos["entry"]) * pos["qty"] * pos["side"]
                eq_curve[i] = self.equity + unreal
            else:
                eq_curve[i] = self.equity

        # Test sonu: kalan pozisyonu son kapanisla kapat
        if pos is not None:
            self._close(pos, c[n - 1], "end_of_test", n - 1, trades)
            pos = None
        eq_curve[-1] = self.equity

        exposure = bars_in_pos / n * 100 if n else 0.0
        return self._metrics(df, eq_curve, trades, interval, exposure)

    # ------------------------------------------------------------------
    def position_size(self, entry: float, sl: float, equity: float) -> Dict[str, float]:
        """Risk bazli pozisyon boyutlandirma (backtest + canli ortak).

        qty = risk_amari / SL mesafesi; notional kaldirac siniriyla cappili.
        """
        sl_dist = abs(entry - sl)
        if sl_dist <= 0:
            sl_dist = entry * 0.02
        risk_amt = equity * self.risk_per_trade
        qty = risk_amt / sl_dist
        max_notional = equity * self.max_leverage
        if qty * entry > max_notional:
            qty = max_notional / entry
        return {
            "qty": qty,
            "entry_fee": qty * entry * self.fee_rate,
            "risk_amount": risk_amt,
            "sl_dist": sl_dist,
        }

    def _open(self, side: int, entry: float, sl: float, tp: float, bar: int) -> Dict[str, Any]:
        sizing = self.position_size(entry, sl, self.equity)
        return {
            "side": side, "entry": entry, "sl": sl, "tp": tp,
            "qty": sizing["qty"], "entry_fee": sizing["entry_fee"],
            "risk_amount": sizing["risk_amount"],
            "entry_bar": bar,
        }

    def _close(self, pos: Dict[str, Any], exit_px: float, reason: str, bar: int,
               trades: List[Dict[str, Any]]) -> None:
        pnl = (exit_px - pos["entry"]) * pos["qty"] * pos["side"]
        exit_fee = exit_px * pos["qty"] * self.fee_rate
        net = pnl - exit_fee - pos["entry_fee"]
        self.equity += pnl - exit_fee
        trades.append(self._trade_record(pos, exit_px, net, reason, bar))

    def _gap_trade(self, side: int, px: float, sl: float, tp: float, reason: str,
                   bar: int, trades: List[Dict[str, Any]]) -> None:
        """Acilis fiyati SL/TP'nin otesinde kaldi: giris aninda kapanir."""
        pos = self._open(side, px, sl, tp, bar)
        self.equity -= pos["entry_fee"]
        self._close(pos, px, reason, bar, trades)

    @staticmethod
    def _trade_record(pos: Dict[str, Any], exit_px: float, net: float, reason: str, bar: int) -> Dict[str, Any]:
        return {
            "bar": int(bar),
            "side": "LONG" if pos["side"] == 1 else "SHORT",
            "entry": round(float(pos["entry"]), 6),
            "exit": round(float(exit_px), 6),
            "sl": round(float(pos["sl"]), 6),
            "tp": round(float(pos["tp"]), 6),
            "qty": round(float(pos["qty"]), 6),
            "pnl": round(float(net), 2),
            "r_multiple": round(float(net / pos["risk_amount"]), 3) if pos["risk_amount"] else 0.0,
            "reason": reason,
        }

    # ------------------------------------------------------------------
    def _metrics(self, df: pd.DataFrame, eq_curve: np.ndarray, trades: List[Dict[str, Any]],
                 interval: str, exposure: float) -> Dict[str, Any]:
        n = len(eq_curve)
        final_equity = float(eq_curve[-1])
        net_profit = final_equity - self.initial_equity
        bph = float(df["close"].iloc[-1] / df["close"].iloc[0] - 1)

        wins = [t for t in trades if t["pnl"] > 0]
        losses = [t for t in trades if t["pnl"] < 0]
        gross_profit = sum(t["pnl"] for t in wins)
        gross_loss = abs(sum(t["pnl"] for t in losses))

        # Drawdown
        peak = np.maximum.accumulate(eq_curve)
        dd_pct = np.where(peak > 0, (eq_curve - peak) / peak, 0)
        max_dd = float(np.min(eq_curve - peak))
        max_dd_pct = float(np.min(dd_pct)) * 100

        # Periyodik getiriler
        rets = np.diff(eq_curve) / np.where(eq_curve[:-1] > 0, eq_curve[:-1], np.nan)
        rets = rets[np.isfinite(rets)]
        bpy = bars_per_year(interval)
        if len(rets) > 1:
            mu = rets.mean()
            sd = rets.std(ddof=1)
            sharpe = float(mu / sd * np.sqrt(bpy)) if sd > 0 else 0.0
            downside = rets[rets < 0]
            dd_sd = float(np.sqrt((downside ** 2).mean())) if len(downside) > 0 else 0.0
            sortino = float(mu / dd_sd * np.sqrt(bpy)) if dd_sd > 0 else 0.0
        else:
            sharpe, sortino = 0.0, 0.0

        return {
            "params": {
                "initial_equity": self.initial_equity,
                "risk_per_trade": self.risk_per_trade,
                "fee_rate": self.fee_rate,
                "slippage": self.slippage,
                "max_leverage": self.max_leverage,
            },
            "bars": n,
            "interval": interval,
            "initial_equity": self.initial_equity,
            "final_equity": round(final_equity, 2),
            "net_profit": round(net_profit, 2),
            "total_return_pct": round((final_equity / self.initial_equity - 1) * 100, 2),
            "buy_hold_return_pct": round(bph * 100, 2),
            "total_trades": len(trades),
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "win_rate": round(len(wins) / len(trades) * 100, 2) if trades else 0.0,
            "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else None,
            "avg_trade_pnl": round(np.mean([t["pnl"] for t in trades]), 2) if trades else 0.0,
            "avg_r_multiple": round(np.mean([t["r_multiple"] for t in trades]), 3) if trades else 0.0,
            "expectancy": round(np.mean([t["r_multiple"] for t in trades]), 3) if trades else 0.0,
            "max_drawdown": round(max_dd, 2),
            "max_drawdown_pct": round(max_dd_pct, 2),
            "sharpe": round(sharpe, 3),
            "sortino": round(sortino, 3),
            "exposure_pct": round(exposure, 2),
            "equity_curve": [round(float(x), 2) for x in eq_curve],
            "trades": trades,
        }
