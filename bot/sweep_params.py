import sys, os, time, gc
import pandas as pd
import numpy as np
sys.path.insert(0, os.path.dirname(__file__))

import backtest_xauusdt as bt

CAPITAL = 1000.0
OFFSETS = [0, 90, 180]
SL_VALS = [2, 3, 4, 5, 6, 8]
TP_VALS = [0.5, 1.0, 1.5, 2.0, 3.0, 4.0]

FEATURE_SETS = {
    "all":     {"h1": True,  "atr": True,  "trail": True},
    "trail":   {"h1": False, "atr": False, "trail": True},
    "base":    {"h1": False, "atr": False, "trail": False},
}

def parse_result(trades, final_equity):
    if not trades: return None
    df_t = pd.DataFrame(trades)
    net = df_t["pnl"].sum()
    wr = len(df_t[df_t["pnl"]>0])/len(df_t)*100
    dd_peak = CAPITAL; mdd = 0
    for v in CAPITAL + df_t["pnl"].cumsum():
        if v > dd_peak: dd_peak = v
        dd = (dd_peak-v)/dd_peak*100
        if dd > mdd: mdd = dd
    return {"trades": len(df_t), "wr": round(wr,1),
            "net": round(net,2), "dd": round(mdd,1), "eq": round(final_equity,2),
            "roi": round((final_equity-CAPITAL)/CAPITAL*100, 2)}

results = []

print("="*80)
print("  PARAMETER SWEEP: XAUUSDT 1m 90d (3 periods)")
print("="*80)

for feat_name, feat in FEATURE_SETS.items():
    print(f"\n>>> Feature set: {feat_name} (h1={feat['h1']}, atr={feat['atr']}, trail={feat['trail']})")
    t0 = time.time()

    for sl in SL_VALS:
        for tp in TP_VALS:
            row = {"sl": sl, "tp": tp, "feat": feat_name}
            period_results = []

            for offset in OFFSETS:
                sys.stdout.write(f"  SL={sl:2d}% TP={tp:3.1f}% {feat_name:6s} offset={offset:3d} ... ")
                sys.stdout.flush()
                try:
                    trades, eq = bt.run_with_params(
                        sl=sl, tp=tp, days=90, offset=offset,
                        h1=feat["h1"], atr=feat["atr"], trail=feat["trail"]
                    )
                except Exception as e:
                    print(f"ERROR: {e}")
                    period_results.append(None)
                    continue

                if trades is None or len(trades) == 0:
                    period_results.append(None)
                    print("no trades")
                    continue

                r = parse_result(trades, eq)
                period_results.append(r)
                print(f"net=${r['net']:>8.2f}  wr={r['wr']:5.1f}%  dd={r['dd']:5.1f}%  trades={r['trades']:3d}  roi={r['roi']:6.2f}%")

            row["p0"] = period_results[0]  # offset=0
            row["p90"] = period_results[1]  # offset=90
            row["p180"] = period_results[2]  # offset=180

            # Compute average metrics
            nets = [r["net"] for r in period_results if r is not None]
            rois = [r["roi"] for r in period_results if r is not None]
            dds = [r["dd"] for r in period_results if r is not None]
            wrs = [r["wr"] for r in period_results if r is not None]

            row["avg_net"] = round(np.mean(nets), 2) if nets else -999
            row["avg_roi"] = round(np.mean(rois), 2) if rois else -999
            row["avg_dd"] = round(np.mean(dds), 2) if dds else 999
            row["avg_wr"] = round(np.mean(wrs), 1) if wrs else 0
            row["min_net"] = min(nets) if nets else -999
            row["net_std"] = round(np.std(nets), 2) if nets else 999
            results.append(row)

    elapsed = time.time() - t0
    print(f"  >>> {feat_name} done in {elapsed:.0f}s")

# Sort by average net across all 3 periods
results.sort(key=lambda r: r["avg_net"], reverse=True)

print("\n\n" + "="*80)
print("  TOP 20 COMBINATIONS (sorted by avg net)")
print("="*80)
print(f"  {'SL':>3} {'TP':>4} {'Feat':>7} {'AvgNet':>8} {'MinNet':>8} {'StdDev':>7} {'AvgROI':>8} {'AvgDD':>7} {'AvgWR':>6}")
print(f"  {'-'*3} {'-'*4} {'-'*7} {'-'*8} {'-'*8} {'-'*7} {'-'*8} {'-'*7} {'-'*6}")
for r in results[:20]:
    print(f"  {r['sl']:3d} {r['tp']:4.1f} {r['feat']:>7s} {r['avg_net']:>8.2f} {r['min_net']:>8.2f} {r['net_std']:>7.2f} {r['avg_roi']:>8.2f} {r['avg_dd']:>7.2f} {r['avg_wr']:>6.1f}")

print("\n\nDETAILED: Top 10 per-feature:")
for feat in ["all", "trail", "base"]:
    subset = [r for r in results if r["feat"] == feat]
    subset.sort(key=lambda r: r["avg_net"], reverse=True)
    print(f"\n--- {feat} (top 5) ---")
    print(f"  {'SL':>3} {'TP':>4} {'AvgNet':>8} {'MinNet':>8} {'AvgROI':>8} {'AvgDD':>7} {'AvgWR':>6}  |  P0 net  P90 net  P180 net")
    for r in subset[:5]:
        p0n = r["p0"]["net"] if r["p0"] else 0
        p90n = r["p90"]["net"] if r["p90"] else 0
        p180n = r["p180"]["net"] if r["p180"] else 0
        print(f"  {r['sl']:3d} {r['tp']:4.1f} {r['avg_net']:>8.2f} {r['min_net']:>8.2f} {r['avg_roi']:>8.2f} {r['avg_dd']:>7.2f} {r['avg_wr']:>6.1f}  | {p0n:>7.2f} {p90n:>7.2f} {p180n:>7.2f}")

print("\nDONE")
