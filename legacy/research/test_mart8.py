"""
MARTINGALE8 Strategy — Dogru backtest (rolling range, anlik grid baslatma)
"""
import numpy as np, pandas as pd, yfinance as yf, warnings
from datetime import datetime
warnings.filterwarnings("ignore")

today = datetime.utcnow().strftime("%Y-%m-%d")
frames = []
for s, e in [("2026-06-25","2026-07-02"),("2026-07-02","2026-07-09"),("2026-07-09","2026-07-16"),("2026-07-16",today)]:
    try:
        df = yf.download("GC=F", interval="1m", start=s, end=e, progress=False)
        if df is not None and not df.empty: frames.append(df)
    except: pass
df = pd.concat(frames)
df = df[~df.index.duplicated(keep="first")].sort_index()
if isinstance(df.columns, pd.MultiIndex): df.columns = [c[0].lower() for c in df.columns]
else: df.columns = [str(c).lower() for c in df.columns]
print(f"Data: {len(df)} bars  {df.index[0]} -> {df.index[-1]}")

o=df["open"].values; h=df["high"].values; l=df["low"].values; c=df["close"].values; n=len(df)

def backtest(lookback, max_lvls, mult, base_qty, od_in, pd_in):
    EQ = 10000.0; PEAK = 10000.0; DD = 0.0; MAX_DD = 0.0
    TRADES = []; GRID = False; FILLED = 0; CUMQ = 0.0; CUMC = 0.0; TP = 0.0
    LINE_ZERO = 0.0; ORDER_DEV = 0.0; PROFIT_DEV = 0.0
    LIMIT_FILLED = set(); ENTRY_BAR = 0
    warmup = lookback * 2

    def lvl_price(lv):
        return LINE_ZERO - LINE_ZERO * ((lv - 1) * ORDER_DEV) / 100

    def lvl_qty(lv):
        return max(int(base_qty * (mult ** (lv - 1))), 1)

    for i in range(warmup, n):
        PEAK = max(PEAK, EQ); DD = min(DD, (EQ - PEAK) / PEAK * 100); MAX_DD = min(MAX_DD, DD)

        # Rolling range
        lH = np.max(h[i-lookback+1:i+1])
        lL = np.min(l[i-lookback+1:i+1])
        ran = 100 * (lH - lL) / lH if lH > 0 else 0
        ods = ran / 10
        pf = ods * 2.5
        od = od_in if od_in > 0 else ods
        pd = pd_in if pd_in > 0 else pf

        # Grid baslat
        if not GRID and i > warmup and c[i] > 0:
            LINE_ZERO = c[i]
            ORDER_DEV = od; PROFIT_DEV = pd
            q1 = lvl_qty(1)
            CUMQ = q1; CUMC = q1 * c[i]; TP = c[i] * (1 + pd / 100)
            FILLED = 1; GRID = True; ENTRY_BAR = i
            LIMIT_FILLED = set()

        # DCA limit dolum
        if GRID:
            for lv in range(2, max_lvls + 1):
                if lv not in LIMIT_FILLED and l[i] <= lvl_price(lv):
                    LIMIT_FILLED.add(lv); FILLED = lv
                    q = lvl_qty(lv); fp = lvl_price(lv)
                    CUMQ += q; CUMC += q * fp
                    TP = CUMC / CUMQ * (1 + pd / 100)

        # TP kontrol
        if GRID and h[i] >= TP and i > ENTRY_BAR:
            avg = CUMC / CUMQ if CUMQ > 0 else c[i]
            dpnl = (TP - avg) * CUMQ
            ppnl = dpnl / EQ * 100 if EQ > 0 else 0
            EQ += dpnl
            TRADES.append({"pnl": ppnl, "win": True, "levels": FILLED, "bars": i - ENTRY_BAR})
            GRID = False; CUMQ = 0; CUMC = 0; FILLED = 0; LIMIT_FILLED = set()

    return TRADES, MAX_DD

print(f"\n{'Config':<50} Trades  WR%   Ret%    PF  MaxDD  AvgLv")
print("-"*95)

results = []
for lb in [42, 84, 168]:
    for ml in [3, 5, 10]:
        for mp in [1.0, 1.5, 2.0]:
            for bs in [1, 2]:
                trades, mdd = backtest(lb, ml, mp, bs, 0.0, 0.0)
                if len(trades) < 2: continue
                wins = sum(1 for t in trades if t["win"])
                losses = len(trades) - wins
                tp = sum(t["pnl"] for t in trades)
                gp = sum(t["pnl"] for t in trades if t["pnl"] > 0)
                gl = abs(sum(t["pnl"] for t in trades if t["pnl"] < 0))
                pf = gp / gl if gl > 0 else 999
                avg_lv = np.mean([t["levels"] for t in trades])
                results.append((lb, ml, mp, bs, len(trades), wins/len(trades)*100, tp, pf, mdd, avg_lv, losses))

results.sort(key=lambda r: r[6], reverse=True)
for r in results[:20]:
    lb, ml, mp, bs, t, wr, tp, pf, mdd, alv, los = r
    label = f"LB={lb} L={ml} mult={mp} base={bs}"
    print(f"{label:<50} {t:5d} {wr:5.1f} {tp:+8.2f} {pf:6.2f} {mdd:6.1f} {alv:5.1f}")
