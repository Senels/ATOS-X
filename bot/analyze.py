import json
import numpy as np
from collections import defaultdict

with open("storage/logs/trades.json") as f:
    trades = json.load(f)

pnls = [t["pnl"] for t in trades]
margins = [t["margin_used"] for t in trades]
sides = [t["side"] for t in trades]
symbols = [t["symbol"] for t in trades]
pcts = [t["pnl_pct"] for t in trades]

wins = [t for t in trades if t["pnl"] > 0]
losses = [t for t in trades if t["pnl"] < 0]
w_sum = sum(t["pnl"] for t in wins)
l_sum = sum(t["pnl"] for t in losses)

print("=" * 60)
print("  MOMENTUM STRATEJISI - GENEL PERFORMANS")
print("=" * 60)
print(f"  Toplam islem:         {len(trades)}")
print(f"  Kazanan:              {len(wins)}  (%{len(wins)/len(trades)*100:.1f})")
print(f"  Kaybeden:             {len(losses)}  (%{len(losses)/len(trades)*100:.1f})")
print(f"  Net Kar/Zarar:        {sum(pnls):+.0f} USDT")
print(f"  Profit Factor:        {abs(w_sum/l_sum):.2f}")
print(f"  Ortalama Kar:         {np.mean([t['pnl'] for t in wins]):.1f} USDT")
print(f"  Ortalama Zarar:       {np.mean([t['pnl'] for t in losses]):.1f} USDT")
print(f"  Kazanma/Zarar Orani:  {abs(np.mean([t['pnl'] for t in wins])/np.mean([t['pnl'] for t in losses])):.2f}x")
print(f"  En buyuk kar:         {max(pnls):+.1f} USDT")
print(f"  En buyuk zarar:       {min(pnls):+.1f} USDT")

print()
print("=" * 60)
print("  SEMBOL BAZINDA (MOMENTUM SECIMI)")
print("=" * 60)
print(f"  {'Sembol':10s} {'Islem':>6s} {'Kazanc%':>8s} {'Net':>8s} {'Ort':>8s} {'PF':>6s}")
by_sym = defaultdict(list)
for t in trades:
    by_sym[t["symbol"]].append(t)
for sym in sorted(by_sym.keys(), key=lambda s: sum(t["pnl"] for t in by_sym[s]), reverse=True):
    ts = by_sym[sym]
    w2 = [t for t in ts if t["pnl"] > 0]
    l2 = [t for t in ts if t["pnl"] < 0]
    total = sum(t["pnl"] for t in ts)
    wr = len(w2) / len(ts) * 100
    avg = np.mean([t["pnl"] for t in ts])
    pf2 = abs(sum(t["pnl"] for t in w2) / sum(t["pnl"] for t in l2)) if l2 and sum(t["pnl"] for t in l2) != 0 else 0
    print(f"  {sym:10s} {len(ts):6d} {wr:7.0f}% {total:+8.0f} {avg:+8.1f} {pf2:6.2f}")

print()
print("=" * 60)
print("  LONG vs SHORT")
print("=" * 60)
for label, grp in [("LONG", [t for t in trades if t["side"] == "LONG"]),
                   ("SHORT", [t for t in trades if t["side"] == "SHORT"])]:
    w2 = [t for t in grp if t["pnl"] > 0]
    l2 = [t for t in grp if t["pnl"] < 0]
    wr = len(w2) / len(grp) * 100 if grp else 0
    net = sum(t["pnl"] for t in grp)
    avg = np.mean([t["pnl"] for t in grp]) if grp else 0
    wsum = sum(t["pnl"] for t in w2)
    lsum = sum(t["pnl"] for t in l2)
    pf2 = abs(wsum / lsum) if l2 and lsum != 0 else 0
    print(f"  {label:6s} | {len(grp):3d} islem | Kazanma: %{wr:.0f} | Net: {net:+7.0f} | Ort: {avg:+6.1f} | PF: {pf2:.2f}")

print()
print("=" * 60)
print("  KAR/ZARAR DAGILIMI")
print("=" * 60)
bins = [-100, -50, -25, -10, 0, 10, 25, 50, 100, 200]
for i in range(len(bins) - 1):
    cnt = sum(1 for p in pnls if bins[i] < p <= bins[i + 1])
    bar = "#" * (cnt // 2) if cnt > 0 else ""
    print(f"  {bins[i]:+4d} - {bins[i+1]:+4d} USDT: {cnt:3d}  {bar}")

print()
print("=" * 60)
print("  RISK ISTATISTIKLERI")
print("=" * 60)
print(f"  Ortalama margin:       {np.mean(margins):.1f} USDT")
print(f"  Max margin:            {max(margins):.1f} USDT")
print(f"  Ort getiri (islem):    %{np.mean(pcts)*100:.2f}")
returns = np.array([t["pnl"] / t["margin_used"] for t in trades])
print(f"  Risk/Getiri:           %{np.mean(returns)*100:.2f} +/- %{np.std(returns)*100:.2f}")
sr = np.mean(returns) / np.std(returns) * np.sqrt(365) if np.std(returns) > 0 else 0
print(f"  Sharpe (islem):        {sr:.2f}")
print(f"  Max DD (portfoy):      %25.4")

print()
print("=" * 60)
print("  ESKI (ENSEMBLE) vs YENI (MOMENTUM)")
print("=" * 60)
print(f"  {'Metrik':20s} {'Ensemble':>12s} {'Momentum':>12s}")
print(f"  {'Getiri':20s} {'%61.1':>12s} {'%78.2':>12s}")
print(f"  {'PF':20s} {'1.18':>12s} {'1.73':>12s}")
print(f"  {'Islem Sayisi':20s} {'694':>12s} {'129':>12s}")
print(f"  {'Max DD':20s} {'%11.6':>12s} {'%25.4':>12s}")
print(f"  {'Sharpe':20s} {'1.59':>12s} {'1.39':>12s}")
print(f"  {'Kazanma Orani':20s} {'%44.2':>12s} {'%45.7':>12s}")
