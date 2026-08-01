"""
Evaluate best params found from optuna logs (trial 234),
then run a final optimized backtest + save summary.
"""
import json, sys
sys.path.insert(0, '.')
from optimize_bb_rsi_mart import load_data, backtest

best = {
    "rsi_len": 11, "rsi_os": 32, "rsi_ob": 68,
    "bb_len": 18, "bb_mult": 2.3,
    "atr_len": 5, "tp_mult": 2.8, "sl_mult": 2.1,
    "base_qty": 0.05, "qty_mult": 3.0, "max_levels": 5,
}

df = load_data()
trades, eq = backtest(df, best)

base = best["base_qty"]
mult = best["qty_mult"]
qty_levels = [round(base * (mult ** i), 4) for i in range(5)]

wins    = sum(1 for t in trades if t["win"])
total   = len(trades)
wr      = wins / total * 100 if total > 0 else 0
net     = sum(t["pnl"] for t in trades)
gross_p = sum(t["pnl"] for t in trades if t["pnl"] > 0) or 0.001
gross_l = abs(sum(t["pnl"] for t in trades if t["pnl"] < 0)) or 0.001
pf      = gross_p / gross_l

by_level = {}
for t in trades:
    key = t["level"]
    by_level.setdefault(key, {"total": 0, "wins": 0, "pnl": 0.0, "longs": 0, "shorts": 0})
    by_level[key]["total"] += 1
    by_level[key]["wins"]  += 1 if t["win"] else 0
    by_level[key]["pnl"]   += t["pnl"]
    if t["dir"] == "L": by_level[key]["longs"] += 1
    else:               by_level[key]["shorts"] += 1

print()
print("=" * 66)
print("  BEST PARAMETERS — BB + RSI Scalp Martingale")
print("=" * 66)
for k, v in best.items():
    print(f"     {k:15s} = {v}")
print()
print(f"     Level Quantities:")
for i, q in enumerate(qty_levels):
    print(f"        L{i+1} = {q}")
print()
print("  " + "-" * 62)
print("  BACKTEST RESULTS")
print("  " + "-" * 62)
print(f"     Total Trades  : {total}")
print(f"     Wins / Losses : {wins} / {total - wins}")
print(f"     Win Rate      : {wr:.1f}%")
print(f"     Net PnL       : ${net:+.2f}")
print(f"     Profit Factor : {pf:.2f}")
longs = sum(1 for t in trades if t["dir"] == "L")
shorts = sum(1 for t in trades if t["dir"] == "S")
print(f"     Long/Short Split: {longs}L / {shorts}S ({longs/total*100:.1f}% / {shorts/total*100:.1f}%)")
print()
print("  " + "-" * 62)
print("  PERFORMANCE BY MARTINGALE LEVEL")
print("  " + "-" * 62)
for lvl in sorted(by_level.keys()):
    d = by_level[lvl]
    lwr = d["wins"] / d["total"] * 100 if d["total"] > 0 else 0
    print(f"     L{lvl} | {d['total']:3d} trades ({d['longs']}L/{d['shorts']}S) "
          f"| WR={lwr:5.1f}% | PnL=${d['pnl']:+8.2f}")
print("=" * 66)

# Save
out = {
    "best_params": best,
    "level_quantities": {f"L{i+1}": q for i, q in enumerate(qty_levels)},
    "results": {
        "trades": total, "wins": wins, "losses": total - wins,
        "win_rate_pct": round(wr, 1), "net_pnl_usd": round(net, 2),
        "profit_factor": round(pf, 2),
    },
    "level_breakdown": {str(lvl): d for lvl, d in by_level.items()},
}
path = r"C:\Users\svkts\OneDrive\Belgeler\Default Project\best_params_bb_rsi.json"
with open(path, "w") as f:
    json.dump(out, f, indent=2)
print(f"\nSaved to {path}")
