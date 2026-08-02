"""Grid search optimizasyonu: en iyi strateji parametrelerini bulur.

Kullanim:
  python scripts/optimize.py --symbols 10 --objective combined --workers 6 --apply
  python scripts/optimize.py --confirmations rqk,ema --top 5
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from app.optimization.search import DEFAULT_GRID, GridSearch, best_settings_to_file
from app.data import loader
from app.data.loader import is_stablecoin_symbol
from app.strategy import settings as strat_settings


def main():
    parser = argparse.ArgumentParser(description="Parametre grid search")
    parser.add_argument("--symbols", type=int, default=10, help="Kullanilacak sembol sayisi")
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--interval", default="4h")
    parser.add_argument("--objective", default="combined",
                        choices=["combined", "return", "sharpe", "pf"])
    parser.add_argument("--workers", type=int, default=1, help="Paralel process sayisi")
    parser.add_argument("--top", type=int, default=10, help="Gosterilecek kombinasyon sayisi")
    parser.add_argument("--confirmations", default="", help="Orn: rqk,ema (bos = varsayilan)")
    parser.add_argument("--apply", action="store_true", help="En iyi ayarlari varsayilan yap")
    parser.add_argument("--grid", default="", help="Grid JSON dosyasi (bos = DEFAULT_GRID)")
    args = parser.parse_args()

    symbols = [s for s in loader.list_symbols(args.interval) if not is_stablecoin_symbol(s)][: args.symbols]
    if not symbols:
        print("Sembol bulunamadi.")
        return

    strat_settings.load()
    base = strat_settings.default_settings()
    if args.confirmations:
        enabled = {c.strip() for c in args.confirmations.split(",") if c.strip()}
        base["confirmations"] = {k: k in enabled for k in base["confirmations"]}

    grid = DEFAULT_GRID
    if args.grid:
        with open(args.grid, "r", encoding="utf-8") as f:
            grid = json.load(f)

    total = 1
    for values in grid.values():
        total *= len(values)
    print(f"Sembol: {len(symbols)} | Kombinasyon: {total} | Objective: {args.objective} | Workers: {args.workers}")
    print(f"Confirmations: {base['confirmations']}")

    t0 = time.time()
    search = GridSearch(grid=grid, objective=args.objective, max_workers=args.workers)
    result = search.run(
        symbols,
        base_settings=base,
        interval=args.interval,
        limit=args.limit,
    )

    if not result["results"]:
        print("Sonuc yok.")
        return

    rows = []
    for r in result["results"][: args.top]:
        row = {"score": round(r["score"], 2), "count": r["count"], **r["combo"]}
        if r.get("details"):
            avg = pd.DataFrame(r["details"])
            row["avg_return"] = round(avg["total_return_pct"].mean(), 2)
            row["avg_win"] = round(avg["win_rate"].mean(), 1)
            row["avg_trades"] = round(avg["total_trades"].mean(), 0)
        rows.append(row)
    df = pd.DataFrame(rows)
    print(f"\n=== TOP {len(df)} (sure: {time.time() - t0:.1f}s) ===")
    with pd.option_context("display.width", 200, "display.max_columns", None):
        print(df.to_string(index=False))

    best = result["best"]
    print(f"\nBEST: {best['combo']}  score={best['score']:.2f}  (sembol sayisi: {best['count']})")

    if args.apply:
        path = best_settings_to_file(best)
        print(f"Optimize ayarlar yazildi: {path}  (yeniden baslatmada varsayilan olur)")


if __name__ == "__main__":
    main()
