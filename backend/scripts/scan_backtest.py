"""Toplu backtest taramasi: arsivdeki sembolleri v23 + BacktestEngine ile tarar.

Kullanim:
  python scripts/scan_backtest.py --symbols 50 --sort net_profit --top 15 --out report.csv
  python scripts/scan_backtest.py --min-trades 10 --sort profit_factor
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from app.backtest.engine import BacktestEngine
from app.data import loader
from app.data.loader import is_stablecoin_symbol
from app.strategy import settings as strat_settings
from app.strategy.tradebot_v23 import TradeBotV23

SUMMARY_COLS = [
    "symbol", "bars", "interval", "total_trades", "win_rate",
    "profit_factor", "total_return_pct", "buy_hold_return_pct",
    "net_profit", "max_drawdown_pct", "sharpe", "sortino", "exposure_pct",
]

METRIC_ALIASES = {
    "return": "total_return_pct",
    "net": "net_profit",
    "pf": "profit_factor",
    "sharpe": "sharpe",
    "win": "win_rate",
}


def _sort_key(name: str):
    return METRIC_ALIASES.get(name, name)


def main():
    parser = argparse.ArgumentParser(description="Toplu backtest taramasi")
    parser.add_argument("--symbols", type=int, default=30, help="Taranacak sembol sayisi (0 = tumu)")
    parser.add_argument("--limit", type=int, default=1000, help="Bar sayisi")
    parser.add_argument("--interval", default="4h")
    parser.add_argument("--sort", default="net_profit", help="Siralama olcutu")
    parser.add_argument("--top", type=int, default=10, help="Konsola yazilacak satir sayisi")
    parser.add_argument("--min-trades", type=int, default=1, help="Min trade esigi")
    parser.add_argument("--out", default="", help="CSV/JSON cikti dosyasi (bos = yok)")
    args = parser.parse_args()

    all_symbols = [s for s in loader.list_symbols(args.interval) if not is_stablecoin_symbol(s)]
    if args.symbols and args.symbols > 0:
        symbols = all_symbols[: args.symbols]
    else:
        symbols = all_symbols

    strat_settings.load()
    settings = strat_settings.get_settings()
    engine_kwargs = {
        "initial_equity": settings["initial_equity"],
        "risk_per_trade": settings["risk_per_trade"],
        "fee_rate": settings["fee_rate"],
        "slippage": 0.0001,
        "max_leverage": settings["max_leverage"],
    }

    t0 = time.time()
    bot = TradeBotV23(settings)
    rows = []
    errors = 0
    for i, symbol in enumerate(symbols, 1):
        try:
            df = loader.load_csv(symbol, args.interval, limit=args.limit)
            orders = bot.analyze(df)["orders"]
            engine = BacktestEngine(**engine_kwargs)
            metrics = engine.run(df, orders, args.interval)
            if metrics.get("total_trades", 0) < args.min_trades:
                continue
            rows.append({"symbol": symbol, **{k: metrics.get(k) for k in SUMMARY_COLS if k != "symbol"}})
        except Exception as e:
            errors += 1
        if i % 100 == 0:
            print(f"[{i}/{len(symbols)}] {len(rows)} basarili, {errors} hata")

    df = pd.DataFrame(rows)
    if df.empty:
        print("Sonuc yok.")
        return

    sort_key = _sort_key(args.sort)
    df = df.sort_values(sort_key, ascending=False, na_position="last")

    print(f"\nTaranan: {len(symbols)} sembol | Basarili: {len(df)} | Hata: {errors} | Sure: {time.time() - t0:.1f}s")
    print(f"Karli sembol: {(df['net_profit'] > 0).sum()} / {len(df)}")
    print(f"Ortalama getiri: {df['total_return_pct'].mean():.2f}%  Medyan PF: {df['profit_factor'].median():.2f}"
          if df['profit_factor'].notna().any() else "PF verisi yok")
    print(f"Ortalama win rate: {df['win_rate'].mean():.1f}%  Ortalama trade: {df['total_trades'].mean():.0f}")

    show = ["symbol", "total_trades", "win_rate", "profit_factor", "total_return_pct", "net_profit", "sharpe"]
    with pd.option_context("display.max_rows", args.top, "display.width", 160):
        print("\n=== TOP ===")
        print(df[show].head(args.top).to_string(index=False))
        if len(df) > args.top:
            print("\n=== BOTTOM ===")
            print(df[show].tail(min(args.top, len(df))).to_string(index=False))

    if args.out:
        if args.out.endswith(".json"):
            payload = {
                "interval": args.interval,
                "limit": args.limit,
                "engine": engine_kwargs,
                "results": df.to_dict(orient="records"),
            }
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
        else:
            df.to_csv(args.out, index=False)
        print(f"\nKaydedildi: {args.out}")


if __name__ == "__main__":
    main()
