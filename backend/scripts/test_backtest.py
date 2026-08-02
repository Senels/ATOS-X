"""BTCUSDT 4h uzerinde v23 + backtest uctan uca dogrulama."""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.backtest.engine import BacktestEngine
from app.data import loader
from app.strategy.tradebot_v23 import TradeBotV23


def main():
    t0 = time.time()
    df = loader.load_csv("BTCUSDT", "4h")
    print(f"Bars: {len(df)}  |  {df.index[0]} -> {df.index[-1]}")

    bot = TradeBotV23()
    result = bot.analyze(df)
    orders = result["orders"]
    n_sig = int((orders["signal"] != 0).sum())
    n_long = int((orders["signal"] == 1).sum())
    n_short = int((orders["signal"] == -1).sum())
    print(f"Sinyal bar: {n_sig} (long {n_long} / short {n_short})")

    engine = BacktestEngine(initial_equity=10000, risk_per_trade=0.02,
                            fee_rate=0.0005, slippage=0.0001, max_leverage=10)
    metrics = engine.run(df, orders, "4h")

    skip = {"equity_curve", "trades", "params"}
    print("\n=== METRIKLER ===")
    for k, v in metrics.items():
        if k in skip:
            continue
        print(f"  {k}: {v}")

    print(f"\n=== TRADES ({len(metrics['trades'])}) ===")
    for t in metrics["trades"][:8]:
        print(f"  bar {t['bar']:4d} {t['side']:5s} {t['reason']:12s} "
              f"pnl {t['pnl']:9.2f}  R {t['r_multiple']:6.2f}  "
              f"entry {t['entry']:.1f} exit {t['exit']:.1f}")

    print("\n=== CANLI SINYAL (son bar) ===")
    print(json.dumps(bot.generate_signal(df), indent=2))
    print(f"\nSure: {time.time() - t0:.2f}s")


if __name__ == "__main__":
    main()
