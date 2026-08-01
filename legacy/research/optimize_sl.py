import optuna, json
import pandas as pd, numpy as np, yfinance as yf
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings("ignore")

def load_data():
    print("Veri indiriliyor...")
    today = datetime.utcnow().strftime("%Y-%m-%d")
    weeks = [("2026-06-18","2026-06-25"),("2026-06-25","2026-07-02"),
             ("2026-07-02","2026-07-09"),("2026-07-09","2026-07-16"),
             ("2026-07-16",today)]
    frames = []
    for s, e in weeks:
        try:
            df = yf.download("GC=F", interval="1m", start=s, end=e, progress=False)
            if df is not None and not df.empty:
                frames.append(df)
        except:
            pass
    df = pd.concat(frames)
    df = df[~df.index.duplicated(keep="first")]
    df.sort_index(inplace=True)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0].lower() for c in df.columns]
    else:
        df.columns = [str(c).lower() for c in df.columns]
    print(f"Toplam: {len(df)} bar")
    return df


def bt(df, p):
    o = df["open"].values
    h = df["high"].values
    l = df["low"].values
    c = df["close"].values
    v = df["volume"].values
    n = len(c)

    eb, es = p["base_mult"], p["band_step"]
    m = [eb, eb*(1+es), eb*(1+2*es), eb*(1+3*es)]
    slm = p["sl_mult"]
    tp1m = p["tp1_mult"]
    tp2m = p["tp2_mult"]
    tp3m = p["tp3_mult"]
    wlm = p.get("sl_wick_lo_mult", 0.75)
    wcm = p.get("sl_wick_close_mult", 1.5)

    def atr_s(pr):
        tr = np.zeros(n)
        for i in range(1, n):
            tr[i] = max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1]))
        a = np.zeros(n)
        a[0] = tr[0]
        for i in range(1, n):
            if i >= pr:
                a[i] = (a[i-1]*(pr-1)+tr[i])/pr
            else:
                a[i] = np.mean(tr[1:i+1])
        return a

    trad = atr_s(p["atr_len"])
    rskd = atr_s(p["atr_len_risk"])
    warmup = max(p["atr_len"]*3, 60)

    ema50 = np.zeros(n)
    ema50[0] = c[0]
    al = 2.0/51.0
    for i in range(1, n):
        ema50[i] = (c[i]-ema50[i-1])*al + ema50[i-1]

    volSma = np.zeros(n)
    for i in range(n):
        if i == 0:
            volSma[i] = v[0] if not np.isnan(v[0]) else 0.0
        elif i < 19:
            volSma[i] = np.nanmean(v[max(0,i-19):i+1])
        else:
            volSma[i] = (volSma[i-1]*19 + (v[i] if not np.isnan(v[i]) else 0.0))/20.0
    symVol = np.nancumsum(v)[-1] > 0

    ct = 1
    cts = np.full(4, np.nan)
    cur_start = 0
    pend = {"l": 0, "s": 0, "ld": 0, "sd": 0}
    lsg = -10000
    act = {"d": 0, "e": 0.0, "sl": 0.0, "tp1": 0.0, "tp2": 0.0, "tp3": 0.0,
           "b": -100, "t1r": False, "t2r": False, "t3r": False, "be": False}
    trades = []
    pt = 1

    for i in range(n):
        if i == 0 or trad[i] == 0 or np.isnan(trad[i]):
            continue

        src, hi, lo, op = c[i], h[i], l[i], o[i]
        ru = [src - trad[i]*mk for mk in m]
        rl = [src + trad[i]*mk for mk in m]

        if np.isnan(cts[0]):
            cts = np.array([ru[j] if ct == 1 else rl[j] for j in range(4)])
            continue

        fp = cts[p["flip_band"]-1]
        if ct == 1 and not np.isnan(fp) and src < fp:
            ct = -1
            cts = np.array(rl)
            cur_start = i
        elif ct == -1 and not np.isnan(fp) and src > fp:
            ct = 1
            cts = np.array(ru)
            cur_start = i
        elif ct == 1:
            cts = np.maximum(ru, cts)
        else:
            cts = np.minimum(rl, cts)

        if i < warmup:
            continue

        flip = pt != ct
        pt = ct

        if flip:
            pend["l"] = pend["s"] = pend["ld"] = pend["sd"] = 0

        pend["l"] = max(pend["l"]-1, 0)
        pend["s"] = max(pend["s"]-1, 0)
        if pend["l"] == 0:
            pend["ld"] = 0
        if pend["s"] == 0:
            pend["sd"] = 0

        if ct == 1:
            td = 4 if lo <= cts[3] else 3 if lo <= cts[2] else 2 if lo <= cts[1] else 1 if lo <= cts[0] else 0
            if td:
                pend["l"] = p["retest_window"]
                pend["ld"] = max(pend["ld"], td)
        if ct == -1:
            td = 4 if hi >= cts[3] else 3 if hi >= cts[2] else 2 if hi >= cts[1] else 1 if hi >= cts[0] else 0
            if td:
                pend["s"] = p["retest_window"]
                pend["sd"] = max(pend["sd"], td)

        lrc = pend["l"] > 0 and ct == 1 and not np.isnan(cts[0]) and src > cts[0] and src > op
        src_r = pend["s"] > 0 and ct == -1 and not np.isnan(cts[0]) and src < cts[0] and src < op

        rng = max(hi - lo, 1e-10)
        cl_ = (src - lo) / rng
        cs_ = (hi - src) / rng
        dp = {2: 25, 3: 18, 1: 15, 4: 10}
        dl = dp.get(pend["ld"], 0)
        ds = dp.get(pend["sd"], 0)
        ca_l = 20 if cl_ > 0.7 else (12 if cl_ > 0.5 else 5)
        ca_s = 20 if cs_ > 0.7 else (12 if cs_ > 0.5 else 5)
        bars_in = i - cur_start
        ag = 15 if 10 <= bars_in <= 150 else (8 if bars_in < 10 else 5)

        bd = 0
        if i > 0 and not np.isnan(c[i-1]) and not np.isnan(ema50[i-1]):
            bd = 1 if c[i-1] > ema50[i-1] else (-1 if c[i-1] < ema50[i-1] else 0)
        bpl = 20 if bd == 1 else (10 if bd == 0 else 0)
        bps = 20 if bd == -1 else (10 if bd == 0 else 0)

        vb = volSma[i-1] if i > 0 else volSma[i]
        rv = v[i] if not np.isnan(v[i]) else 0.0
        vp = 20 if (symVol and rv > vb*1.2) else (12 if (symVol and rv > vb) else 5)

        lsc = dl + ca_l + ag + vp + bpl
        ssc = ds + ca_s + ag + vp + bps
        cdok = i - lsg >= p["cooldown"]

        cl_ok = lrc and cdok and lsc >= p["min_score"]
        cs_ok = src_r and cdok and ssc >= p["min_score"]

        lsig = cl_ok or (flip and ct == 1)
        ssig = cs_ok or (flip and ct == -1)

        if cl_ok or cs_ok or flip:
            lsg = i

        sl_hit = tp1_hit = tp2_hit = tp3_hit = False
        if act["d"] != 0 and i > act["b"]:
            sl_hit = (act["d"] == 1 and lo <= act["sl"]) or (act["d"] == -1 and hi >= act["sl"])
            tp1_hit = (act["d"] == 1 and hi >= act["tp1"]) or (act["d"] == -1 and lo <= act["tp1"])
            tp2_hit = (act["d"] == 1 and hi >= act["tp2"]) or (act["d"] == -1 and lo <= act["tp2"])
            tp3_hit = (act["d"] == 1 and hi >= act["tp3"]) or (act["d"] == -1 and lo <= act["tp3"])

            if tp1_hit and not act["t1r"] and not sl_hit:
                act["t1r"] = True
            if tp2_hit and not act["t2r"] and not sl_hit:
                act["t2r"] = True
            if tp3_hit and not act["t3r"] and not sl_hit:
                act["t3r"] = True

            if sl_hit or tp3_hit:
                ep = act["sl"] if sl_hit else act["tp3"]
                lv = p["long_leverage"] if act["d"] == 1 else p["short_leverage"]
                pnl = (ep - act["e"])/act["e"]*100*act["d"]*lv
                trades.append({"e": act["e"], "x": ep, "pnl": pnl, "win": act["t1r"],
                               "bars": i-act["b"], "d": "L" if act["d"] == 1 else "S",
                               "eb": act["b"], "xb": i})
                act = {"d": 0, "e": 0.0, "sl": 0.0, "tp1": 0.0, "tp2": 0.0, "tp3": 0.0,
                       "b": -100, "t1r": False, "t2r": False, "t3r": False, "be": False}

        rev = (ssig and act["d"] == 1) or (lsig and act["d"] == -1)
        if rev and act["d"] != 0:
            lv = p["long_leverage"] if act["d"] == 1 else p["short_leverage"]
            pnl = (c[i] - act["e"])/act["e"]*100*act["d"]*lv
            trades.append({"e": act["e"], "x": c[i], "pnl": pnl, "win": act["t1r"],
                           "bars": i-act["b"], "d": "L" if act["d"] == 1 else "S",
                           "eb": act["b"], "xb": i})
            act = {"d": 0, "e": 0.0, "sl": 0.0, "tp1": 0.0, "tp2": 0.0, "tp3": 0.0,
                   "b": -100, "t1r": False, "t2r": False, "t3r": False, "be": False}

        if act["d"] == 0 and lsig:
            slw = min(lo - rskd[i]*wlm, src - rskd[i]*wcm)
            risk = src - slw
            if risk > 0:
                act = {"d": 1, "e": src, "sl": slw,
                       "tp1": src+risk*tp1m, "tp2": src+risk*tp2m, "tp3": src+risk*tp3m,
                       "b": i, "t1r": False, "t2r": False, "t3r": False, "be": False}

        if act["d"] == 0 and ssig:
            slw = max(hi + rskd[i]*wlm, src + rskd[i]*wcm)
            risk = slw - src
            if risk > 0:
                act = {"d": -1, "e": src, "sl": slw,
                       "tp1": src-risk*tp1m, "tp2": src-risk*tp2m, "tp3": src-risk*tp3m,
                       "b": i, "t1r": False, "t2r": False, "t3r": False, "be": False}

    return trades


df = load_data()

with open("best_params.json") as f:
    old_best = json.load(f)["full_config"]

fixed_p = {}
for k, v in old_best.items():
    if k not in ("sl_wick_lo_mult", "sl_wick_close_mult"):
        fixed_p[k] = v
fixed_p["reverse_signal"] = False

def obj(trial):
    p = dict(fixed_p)
    p["sl_wick_lo_mult"] = trial.suggest_float("sl_wick_lo_mult", 0.10, 5.0, step=0.05)
    p["sl_wick_close_mult"] = trial.suggest_float("sl_wick_close_mult", 0.25, 8.0, step=0.05)
    trades = bt(df, p)
    if len(trades) < 5:
        return -9999.0
    wins = sum(1 for t in trades if t["win"])
    wr = wins / len(trades)
    net = sum(t["pnl"] for t in trades)
    gp = sum(t["pnl"] for t in trades if t["pnl"] > 0) or 0.001
    gl = abs(sum(t["pnl"] for t in trades if t["pnl"] < 0)) or 0.001
    pf = gp / gl
    score = net * (wr ** 0.3) * (pf ** 0.4)
    if len(trades) < 10:
        score *= 0.2
    return score


print("SL Wick carpanlari optimize ediliyor (500 trial)...")
study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=42))
study.optimize(obj, n_trials=500, show_progress_bar=True)

bp = study.best_params
print(f"\n=== EN IYI SL WICK CARPANLARI ===")
print(f"  sl_wick_lo_mult    = {bp['sl_wick_lo_mult']}")
print(f"  sl_wick_close_mult = {bp['sl_wick_close_mult']}")

p_final = dict(fixed_p)
p_final["sl_wick_lo_mult"] = bp["sl_wick_lo_mult"]
p_final["sl_wick_close_mult"] = bp["sl_wick_close_mult"]

trades_final = bt(df, p_final)
wins = sum(1 for t in trades_final if t["win"])
total = len(trades_final)
net = sum(t["pnl"] for t in trades_final)
gp = sum(t["pnl"] for t in trades_final if t["pnl"] > 0) or 0.001
gl = abs(sum(t["pnl"] for t in trades_final if t["pnl"] < 0)) or 0.001

print(f"\n=== BACKTEST SONUCU ===")
print(f"  Islem Sayisi : {total}")
print(f"  Kazanma      : {wins}/{total-wins}")
print(f"  Kazanma Orani: {wins/total*100:.1f}%")
print(f"  Net Kar      : {net:+.2f}%")
print(f"  Profit Factor: {gp/gl:.2f}")

old_best["sl_wick_lo_mult"] = bp["sl_wick_lo_mult"]
old_best["sl_wick_close_mult"] = bp["sl_wick_close_mult"]
out = {"best_params": study.best_params, "full_config": old_best,
       "results": {"trades": total, "wins": wins, "losses": total-wins,
                   "win_rate_pct": round(wins/total*100, 1), "net_profit_pct": round(net, 2),
                   "profit_factor": round(gp/gl, 2)}}
with open("best_params.json", "w") as f:
    json.dump(out, f, indent=2)
print(f"\nbest_params.json guncellendi.")
