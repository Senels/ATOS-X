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
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from app.strategy.tradebot_v23 import atr

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
        max_drawdown_pct: float = 0.0,
        max_consecutive_losses: int = 0,
        max_daily_loss_pct: float = 0.0,
        min_equity: float = 0.0,
        trailing_activate_pct: float = 0.0,
        trailing_sl_pct: float = 0.0,
        trailing_min_move_pct: float = 0.0,
        breakeven_activate_pct: float = 0.0,
        max_position_age_hours: float = 0.0,
        min_signal_strength: float = 0.0,
        vol_sizing_enabled: bool = False,
        vol_mult_hi: float = 1.5,
        vol_mult_lo: float = 0.6,
        vol_mult_factor: float = 0.5,
    ):
        self.initial_equity = float(initial_equity)
        self.risk_per_trade = float(risk_per_trade)
        self.fee_rate = float(fee_rate)
        self.slippage = float(slippage)
        self.max_leverage = float(max_leverage)
        self.max_drawdown_pct = float(max_drawdown_pct)
        self.max_consecutive_losses = int(max_consecutive_losses)
        self.max_daily_loss_pct = float(max_daily_loss_pct)
        self.min_equity = float(min_equity)
        self.trailing_activate_pct = float(trailing_activate_pct)
        self.trailing_sl_pct = float(trailing_sl_pct)
        self.trailing_min_move_pct = float(trailing_min_move_pct)
        self.breakeven_activate_pct = float(breakeven_activate_pct)
        self.max_position_age_hours = float(max_position_age_hours)
        self.min_signal_strength = float(min_signal_strength)
        self.vol_sizing_enabled = bool(vol_sizing_enabled)
        self.vol_mult_hi = float(vol_mult_hi)
        self.vol_mult_lo = float(vol_mult_lo)
        self.vol_mult_factor = float(vol_mult_factor)

    # ------------------------------------------------------------------
    def _can_enter(self) -> bool:
        """Risk korumalari aktifse yeni giris engellenir (canli ile ayni)."""
        if self.halted_dd or self.halted_eq:
            return False
        if self.max_consecutive_losses > 0 and self.consec >= self.max_consecutive_losses:
            return False
        return True

    def _strength_ok(self, strength: float) -> bool:
        """Minimum sinyal gucu esigi (canli _strength_gate ile ayni)."""
        if self.min_signal_strength <= 0:
            return True
        return float(strength) >= self.min_signal_strength

    def _apply_intra_risk(self, pos: Dict[str, Any], close: float, high: float, low: float):
        """Breakeven + trailing SL yonetimi (canli check_positions ile ayni)."""
        entry = pos["entry"]
        profit_pct = (close - entry) / entry * 100 if pos["side"] == 1 \
            else (entry - close) / entry * 100
        if self.breakeven_activate_pct > 0 and profit_pct >= self.breakeven_activate_pct:
            if pos["side"] == 1 and pos["sl"] < entry:
                pos["sl"] = entry
            elif pos["side"] == -1 and pos["sl"] > entry:
                pos["sl"] = entry
        if self.trailing_activate_pct > 0 and self.trailing_sl_pct > 0 \
                and profit_pct >= self.trailing_activate_pct:
            if pos["side"] == 1:
                new_sl = close * (1 - self.trailing_sl_pct / 100.0)
            else:
                new_sl = close * (1 + self.trailing_sl_pct / 100.0)
            better = new_sl > pos["sl"] if pos["side"] == 1 else new_sl < pos["sl"]
            if better:
                if self.trailing_min_move_pct > 0:
                    move_pct = abs(new_sl - pos["sl"]) / entry * 100
                    if move_pct < self.trailing_min_move_pct:
                        new_sl = pos["sl"]
                pos["sl"] = new_sl
    def run(self, df: pd.DataFrame, orders: pd.DataFrame, interval: str = "4h",
            ai_blocks: Optional[np.ndarray] = None) -> Dict[str, Any]:
        """Bar tabanli backtest.

        `ai_blocks`: opsiyonel bool dizisi — True ise o bardaki sinyal
        yok sayilir (AI kapisi simulasyonu). Sinyal dizisiyle ayni hizada
        olmalidir; giris `ai_blocks[i - 1]` ile engellenir.
        """
        if len(df) < 2:
            return {"error": "Yetersiz veri"}
        o = df["open"].to_numpy(float)
        h = df["high"].to_numpy(float)
        l = df["low"].to_numpy(float)
        c = df["close"].to_numpy(float)
        sig = orders["signal"].to_numpy(int)
        sl_arr = orders["sl"].to_numpy(float)
        tp_arr = orders["tp"].to_numpy(float)
        strength_arr = orders["strength"].to_numpy(float) \
            if "strength" in orders.columns else np.full(len(df), np.inf)
        # Strateji-yonetimli mod: orders "exit" kolonu iceriyorsa SL/TP ve
        # cikis direktiflerini her bar icin strateji belirler (v23 yolu
        # degismez).
        managed = "exit" in orders.columns
        exit_arr = orders["exit"].to_numpy() if managed else None
        exit_qty_arr = orders["exit_qty_pct"].to_numpy(float) \
            if managed and "exit_qty_pct" in orders.columns else None
        exit_price_arr = orders["exit_price"].to_numpy(float) \
            if managed and "exit_price" in orders.columns else None

        n = len(df)
        self.equity = self.initial_equity
        trades: List[Dict[str, Any]] = []
        eq_curve = np.empty(n)
        pos: Optional[Dict[str, Any]] = None
        bars_in_pos = 0
        self.halted_dd = False
        self.halted_eq = False
        self.consec = 0
        peak_bt = self.initial_equity
        hours_per_bar = 24.0 * 365 / max(bars_per_year(interval), 1)
        atr_series = atr(df, 14)
        atr_arr = atr_series.to_numpy(dtype=float)
        atr_mean_arr = atr_series.rolling(20).mean().to_numpy(dtype=float)

        for i in range(n):
            # 1) Bekleyen emir: bir onceki barin sinyali -> bu barin acilisi
            if i > 0 and sig[i - 1] != 0:
                side = int(sig[i - 1])
                slp = sl_arr[i - 1]
                tpp = tp_arr[i - 1]
                atr_ratio = None
                if self.vol_sizing_enabled:
                    am = atr_mean_arr[i - 1]
                    if np.isfinite(am) and am > 0:
                        atr_ratio = float(atr_arr[i - 1] / am)

                if pos is not None and pos["side"] != side:
                    self._close(pos, o[i], "flip", i, trades)
                    pos = None

                if pos is None and np.isfinite(slp) and self._can_enter() \
                        and self._strength_ok(strength_arr[i - 1]) \
                        and (ai_blocks is None or not ai_blocks[i - 1]):
                    px = o[i] * (1 + self.slippage * side)
                    if side == 1:
                        if px <= slp:
                            self._gap_trade(1, px, slp, tpp, "immediate_sl", i, trades,
                                            atr_ratio=atr_ratio)
                        elif px >= tpp:
                            self._gap_trade(1, px, slp, tpp, "immediate_tp", i, trades,
                                            atr_ratio=atr_ratio)
                        else:
                            pos = self._open(1, px, slp, tpp, i, atr_ratio=atr_ratio)
                    else:
                        if px >= slp:
                            self._gap_trade(-1, px, slp, tpp, "immediate_sl", i, trades,
                                            atr_ratio=atr_ratio)
                        elif px <= tpp:
                            self._gap_trade(-1, px, slp, tpp, "immediate_tp", i, trades,
                                            atr_ratio=atr_ratio)
                        else:
                            pos = self._open(-1, px, slp, tpp, i, atr_ratio=atr_ratio)
                    if pos is not None:
                        self.equity -= pos["entry_fee"]

            # 2) Acik pozisyon yonetimi (SL/TP bar ici)
            if pos is not None:
                bars_in_pos += 1
                side = pos["side"]
                if managed and exit_arr is not None:
                    # Strateji-yonetimli: per-bar SL/TP + cikis direktifleri
                    sl_i = sl_arr[i]
                    tp_i = tp_arr[i]
                    if np.isfinite(sl_i):
                        pos["sl"] = float(sl_i)
                    if np.isfinite(tp_i):
                        pos["tp"] = float(tp_i)
                    ex = exit_arr[i]
                    if ex and str(ex):
                        ep = float(exit_price_arr[i]) if (exit_price_arr is not None
                                                          and np.isfinite(exit_price_arr[i])) else None
                        qfrac = float(exit_qty_arr[i]) if exit_qty_arr is not None else 1.0
                        if ex == "sl":
                            self._close(pos, ep if ep is not None else pos["sl"], "stop_loss", i, trades)
                            pos = None
                        elif ex == "reversal":
                            self._close(pos, ep if ep is not None else c[i], "reversal", i, trades)
                            pos = None
                        elif ex == "trail_tp":
                            self._close(pos, ep if ep is not None else c[i], "trail_tp", i, trades)
                            pos = None
                        elif ex == "tp_partial":
                            if qfrac >= 1.0 - 1e-9:
                                self._close(pos, ep if ep is not None else pos["tp"], "take_profit", i, trades)
                                pos = None
                            else:
                                self._partial_close(pos, ep if ep is not None else pos["tp"],
                                                    qfrac, "take_profit", i, trades)
                else:
                    # Miras statik SL/TP yolu
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

            # 2b) Time-stop: max acik kalma suresi asilinca kapat
            if pos is not None and not managed and self.max_position_age_hours > 0:
                if (i - pos["entry_bar"]) * hours_per_bar >= self.max_position_age_hours:
                    self._close(pos, c[i], "time_stop", i, trades)
                    pos = None

            # 2c) Breakeven + trailing SL (strateji-yonetimli modda strateji yapar)
            if pos is not None and not managed:
                self._apply_intra_risk(pos, c[i], h[i], l[i])

            # 3) Equity egrisi (acik pozisyonun gerceklesmemis PnL'i ile)
            if pos is not None:
                unreal = (c[i] - pos["entry"]) * pos["qty"] * pos["side"]
                eq_curve[i] = self.equity + unreal
            else:
                eq_curve[i] = self.equity

            # Risk koruma bayraklari (drawdown + equity taban)
            if self.max_drawdown_pct > 0:
                peak_bt = max(peak_bt, eq_curve[i])
                dd_now = (peak_bt - eq_curve[i]) / peak_bt * 100 if peak_bt > 0 else 0.0
                if dd_now >= self.max_drawdown_pct:
                    self.halted_dd = True
                elif self.halted_dd and dd_now <= self.max_drawdown_pct * 0.5:
                    self.halted_dd = False
            if self.min_equity > 0:
                self.halted_eq = eq_curve[i] < self.min_equity

        # Test sonu: kalan pozisyonu son kapanisla kapat
        if pos is not None:
            self._close(pos, c[n - 1], "end_of_test", n - 1, trades)
            pos = None
        eq_curve[-1] = self.equity

        exposure = bars_in_pos / n * 100 if n else 0.0
        return self._metrics(df, eq_curve, trades, interval, exposure)

    # ------------------------------------------------------------------
    def position_size(self, entry: float, sl: float, equity: float,
                      atr_ratio: Optional[float] = None) -> Dict[str, float]:
        """Risk bazli pozisyon boyutlandirma (backtest + canli ortak).

        qty = risk_amari / SL mesafesi; notional kaldirac siniriyla cappili.
        `atr_ratio` = sinyal bari ATR% / 20 bar ortalama ATR%. Rejim yuksekse
        (`> vol_mult_hi`) risk `vol_mult_factor` ile kucultulur; dusukse
        normal risk korunur (asiri kucuk pozisyon istemeyiz).
        """
        sl_dist = abs(entry - sl)
        if sl_dist <= 0:
            sl_dist = entry * 0.02
        risk_amt = equity * self.risk_per_trade
        if self.vol_sizing_enabled and atr_ratio is not None \
                and atr_ratio > self.vol_mult_hi:
            risk_amt *= self.vol_mult_factor
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

    def _open(self, side: int, entry: float, sl: float, tp: float, bar: int,
              atr_ratio: Optional[float] = None) -> Dict[str, Any]:
        sizing = self.position_size(entry, sl, self.equity, atr_ratio=atr_ratio)
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
        self.consec = self.consec + 1 if net < 0 else 0
        trades.append(self._trade_record(pos, exit_px, net, reason, bar))

    def _partial_close(self, pos: Dict[str, Any], exit_px: float, frac: float, reason: str,
                       bar: int, trades: List[Dict[str, Any]]) -> None:
        """Pozisyonun `frac` oranindaki kismini kapatir; kalan pozisyonda kalir."""
        exit_qty = pos["qty"] * frac
        side = pos["side"]
        pnl = (exit_px - pos["entry"]) * exit_qty * side
        exit_fee = exit_px * exit_qty * self.fee_rate
        entry_fee_part = pos["entry_fee"] * frac
        net = pnl - exit_fee - entry_fee_part
        self.equity += pnl - exit_fee
        self.consec = self.consec + 1 if net < 0 else 0
        rec = dict(pos, qty=exit_qty)
        trades.append(self._trade_record(rec, exit_px, net, reason, bar))
        pos["qty"] -= exit_qty
        pos["entry_fee"] -= entry_fee_part

    def _gap_trade(self, side: int, px: float, sl: float, tp: float, reason: str,
                   bar: int, trades: List[Dict[str, Any]],
                   atr_ratio: Optional[float] = None) -> None:
        """Acilis fiyati SL/TP'nin otesinde kaldi: giris aninda kapanir."""
        pos = self._open(side, px, sl, tp, bar, atr_ratio=atr_ratio)
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
                "max_drawdown_pct": self.max_drawdown_pct,
                "max_consecutive_losses": self.max_consecutive_losses,
                "max_daily_loss_pct": self.max_daily_loss_pct,
                "min_equity": self.min_equity,
                "trailing_activate_pct": self.trailing_activate_pct,
                "trailing_sl_pct": self.trailing_sl_pct,
                "trailing_min_move_pct": self.trailing_min_move_pct,
                "breakeven_activate_pct": self.breakeven_activate_pct,
                "max_position_age_hours": self.max_position_age_hours,
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
