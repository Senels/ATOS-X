"""Deterministic OOS trading simulation for model signals.

Research/paper layer only: no exchange API calls and no live orders.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Iterable

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class BacktestConfig:
    initial_equity: float = 1000.0
    fee_rate: float = 0.0005
    slippage_bps: float = 2.0
    funding_rate: float = 0.0
    risk_per_trade: float = 0.01
    stop_loss_pct: float = 0.02
    take_profit_pct: float = 0.04


@dataclass(frozen=True)
class Trade:
    entry_time: str
    exit_time: str
    side: int
    entry: float
    exit: float
    qty: float
    gross_pnl: float
    fees: float
    funding: float
    net_pnl: float


def _execution_price(price: float, side: int, slippage_bps: float, entry: bool) -> float:
    slip = slippage_bps / 10000.0
    # Conservative adverse fill for both entry and exit.
    direction = side if entry else -side
    return price * (1.0 + direction * slip)


def run_oos_backtest(frame: pd.DataFrame, signals: Iterable[int], config: BacktestConfig | None = None) -> dict:
    """Run one-position-at-a-time OOS simulation.

    Signals: -1 short, 0 flat/no-entry, +1 long. Positions are entered on the
    signal bar close and closed on stop, target, or an opposite signal.
    """
    cfg = config or BacktestConfig()
    required = {"close"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Eksik kolonlar: {sorted(missing)}")
    df = frame.copy().sort_index()
    sig = np.asarray(list(signals), dtype=np.int8)
    if len(sig) != len(df):
        raise ValueError("signal ve dataframe uzunluklari esit olmali")

    equity = float(cfg.initial_equity)
    peak = equity
    max_dd = 0.0
    position = None
    trades: list[Trade] = []

    for i, (ts, row) in enumerate(df.iterrows()):
        price = float(row["close"])
        signal = int(sig[i])

        if position is None and signal in (-1, 1):
            side = signal
            entry = _execution_price(price, side, cfg.slippage_bps, True)
            risk_cash = equity * cfg.risk_per_trade
            qty = risk_cash / max(entry * cfg.stop_loss_pct, 1e-12)
            position = {"side": side, "entry": entry, "qty": qty, "time": ts}
            continue

        if position is None:
            continue

        side = position["side"]
        entry = position["entry"]
        stop = entry * (1.0 - side * cfg.stop_loss_pct)
        target = entry * (1.0 + side * cfg.take_profit_pct)
        high = float(row.get("high", price))
        low = float(row.get("low", price))

        exit_price = None
        reason = None
        if side == 1:
            if low <= stop:
                exit_price, reason = stop, "stop"
            elif high >= target:
                exit_price, reason = target, "target"
        else:
            if high >= stop:
                exit_price, reason = stop, "stop"
            elif low <= target:
                exit_price, reason = target, "target"

        if exit_price is None and signal == -side:
            exit_price, reason = price, "reverse"

        if exit_price is None:
            continue

        exit_fill = _execution_price(float(exit_price), side, cfg.slippage_bps, False)
        qty = float(position["qty"])
        gross = (exit_fill - entry) * qty * side
        fees = (abs(entry * qty) + abs(exit_fill * qty)) * cfg.fee_rate
        funding = abs(entry * qty) * cfg.funding_rate
        net = gross - fees - funding
        equity += net
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / max(peak, 1e-12))
        trades.append(Trade(str(position["time"]), str(ts), side, entry, exit_fill, qty, gross, fees, funding, net))
        position = None

    pnls = np.array([t.net_pnl for t in trades], dtype=float)
    wins = pnls[pnls > 0]
    losses = pnls[pnls < 0]
    gross_profit = float(wins.sum()) if len(wins) else 0.0
    gross_loss = float(-losses.sum()) if len(losses) else 0.0
    pf = gross_profit / gross_loss if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)
    expectancy = float(pnls.mean()) if len(pnls) else 0.0
    std = float(pnls.std(ddof=1)) if len(pnls) > 1 else 0.0
    sharpe = float(np.sqrt(len(pnls)) * expectancy / std) if std > 0 else 0.0
    downside = pnls[pnls < 0]
    downside_std = float(downside.std(ddof=1)) if len(downside) > 1 else 0.0
    sortino = float(np.sqrt(len(pnls)) * expectancy / downside_std) if downside_std > 0 else 0.0
    return {
        "config": asdict(cfg),
        "initial_equity": cfg.initial_equity,
        "final_equity": equity,
        "net_pnl": equity - cfg.initial_equity,
        "return_pct": (equity / cfg.initial_equity - 1.0) * 100.0,
        "trades": len(trades),
        "win_rate": float((pnls > 0).mean()) if len(pnls) else 0.0,
        "profit_factor": pf,
        "expectancy": expectancy,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown_pct": max_dd * 100.0,
        "fees": float(sum(t.fees for t in trades)),
        "funding": float(sum(t.funding for t in trades)),
        "trade_log": [asdict(t) for t in trades],
    }
