"""
MARTINGALE8 vs Legend BUY SELL DCA — Karsilastirma
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

def rma(arr, p):
    res=np.full(n,np.nan); a=1.0/p
    for i in range(n):
        if i==0: res[i]=arr[i] if not np.isnan(arr[i]) else 0
        elif np.isnan(res[i-1]): res[i]=arr[i] if not np.isnan(arr[i]) else 0
        else: res[i]=arr[i]*a+res[i-1]*(1-a)
    return res
def ema(arr, p):
    res=np.full_like(arr,np.nan,dtype=np.float64); res[0]=arr[0]; a=2.0/(p+1)
    for i in range(1,len(arr)): res[i]=arr[i]*a+res[i-1]*(1-a)
    return res

# Precompute legend indicators
fast_ma=ema(c,5); slow_ma=ema(c,13); macd=fast_ma-slow_ma; signal=ema(macd,9)
tr=np.full(n,np.nan); tr[0]=h[0]-l[0]
for i in range(1,n): tr[i]=max(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1]))
tr_rma=rma(tr,10); up_chg=np.zeros(n); down_chg=np.zeros(n)
for i in range(1,n): up_chg[i]=max(h[i]-h[i-1],0); down_chg[i]=max(l[i-1]-l[i],0)
plus_dm=np.where((up_chg>down_chg)&(up_chg>0),up_chg,0)
minus_dm=np.where((down_chg>up_chg)&(down_chg>0),down_chg,0)
plus_dm_rma=rma(plus_dm,10); minus_dm_rma=rma(minus_dm,10)
plus=np.full(n,np.nan); minus=np.full(n,np.nan)
for i in range(n):
    if tr_rma[i]>0 and not np.isnan(tr_rma[i]):
        plus[i]=100*plus_dm_rma[i]/tr_rma[i]; minus[i]=100*minus_dm_rma[i]/tr_rma[i]
sd=plus+minus; sd=np.where(sd==0,1,sd); dx=100*np.abs(plus-minus)/sd
adx_val=rma(np.nan_to_num(dx,nan=0),14)
macd_cross_up=np.full(n,False); macd_cross_down=np.full(n,False)
for i in range(1,n):
    if not np.isnan(macd[i]) and not np.isnan(signal[i]) and not np.isnan(macd[i-1]) and not np.isnan(signal[i-1]):
        if macd[i]<0 and macd[i]>signal[i] and macd[i-1]<=signal[i-1]: macd_cross_up[i]=True
        if macd[i]>0 and macd[i]<signal[i] and macd[i-1]>=signal[i-1]: macd_cross_down[i]=True

def run_legend():
    atr_vals=rma(tr,10)
    bullish=(adx_val>35)&macd_cross_up; bearish=(adx_val>35)&macd_cross_down
    AD=0; AE=0.0; ATP=0.0; DL=1; DBE=0.0; EBQ=0.0; EB=-999; TPR=False; EQ=10000.0
    TRADES=[]; PEAK=10000.0; DD=0.0; MAX_DD=0.0; CUMQ=0; CUMC=0
    warmup=42
    def eqty(eq,pr,td):
        rc=eq*1.0/100.0; rpc=max(td,0.1); rq=int(rc/rpc) if (not np.isnan(rpc) and rpc>0) else 999999
        mq=int(eq/max(pr,0.1)); return float(max(min(rq,max(mq,1)),1))
    for i in range(warmup,n):
        ratr=atr_vals[i] if not np.isnan(atr_vals[i]) else 0
        tpd=ratr*1.5; tpt=tpd*1.5
        PEAK=max(PEAK,EQ); DD=min(DD,(EQ-PEAK)/PEAK*100); MAX_DD=min(MAX_DD,DD)
        if AD!=0 and not TPR and i>EB:
            if (AD==1 and h[i]>=ATP) or (AD==-1 and l[i]<=ATP): TPR=True
        if TPR and AD!=0:
            dpnl=(ATP-CUMC/CUMQ)*CUMQ if AD==1 else (CUMC/CUMQ-ATP)*CUMQ; EQ+=dpnl
            TRADES.append({'pnl':dpnl/EQ*100,'win':True,'reason':'tp'})
            AD=0; TPR=False; DL=1; CUMQ=0; CUMC=0
        if AD!=0 and DBE!=0 and not TPR and i>EB and ratr>0:
            sp=ratr*3.0
            for lvl in range(2,4):
                if lvl>DL:
                    if AD==1 and l[i]<=DBE-(lvl-1)*sp:
                        DL=lvl; nq=EBQ*(2**(lvl-1)); CUMQ+=nq; CUMC+=nq*c[i]; ATP=CUMC/CUMQ+tpt
                    elif AD==-1 and h[i]>=DBE+(lvl-1)*sp:
                        DL=lvl; nq=EBQ*(2**(lvl-1)); CUMQ+=nq; CUMC+=nq*c[i]; ATP=CUMC/CUMQ-tpt
        rev=(AD==1 and bearish[i]) or (AD==-1 and bullish[i])
        if rev and AD!=0:
            avg=CUMC/CUMQ if CUMQ>0 else AE
            dpnl=(c[i]-avg)*CUMQ if AD==1 else (avg-c[i])*CUMQ; EQ+=dpnl
            won=(AD==1 and c[i]>=avg) or (AD==-1 and c[i]<=avg)
            TRADES.append({'pnl':dpnl/EQ*100,'win':won,'reason':'rev'}); AD=0; TPR=False; DL=1; CUMQ=0; CUMC=0
            if bearish[i]: AD=-1; AE=c[i]; ATP=c[i]-tpt; DBE=c[i]; DL=1; EBQ=eqty(EQ,c[i],tpd); CUMQ=EBQ; CUMC=EBQ*c[i]; EB=i
            elif bullish[i]: AD=1; AE=c[i]; ATP=c[i]+tpt; DBE=c[i]; DL=1; EBQ=eqty(EQ,c[i],tpd); CUMQ=EBQ; CUMC=EBQ*c[i]; EB=i
        if AD==0:
            if bullish[i]: AD=1; AE=c[i]; ATP=c[i]+tpt; DBE=c[i]; DL=1; EBQ=eqty(EQ,c[i],tpd); CUMQ=EBQ; CUMC=EBQ*c[i]; EB=i
            elif bearish[i]: AD=-1; AE=c[i]; ATP=c[i]-tpt; DBE=c[i]; DL=1; EBQ=eqty(EQ,c[i],tpd); CUMQ=EBQ; CUMC=EBQ*c[i]; EB=i
    w=sum(1 for t in TRADES if t['win']); gp=sum(t['pnl'] for t in TRADES if t['pnl']>0); gl=abs(sum(t['pnl'] for t in TRADES if t['pnl']<0)); pf=gp/gl if gl>0 else 999; cret=(EQ/10000-1)*100
    return len(TRADES), w/len(TRADES)*100, cret, pf, MAX_DD

def run_mart8(lookback, max_lvls, mult, base_qty):
    EQ=10000.0; PEAK=10000.0; DD=0.0; MAX_DD=0.0; TRADES=[]; GRID=False; FILLED=0; CUMQ=0.0; CUMC=0.0; TP=0.0; NZ=0.0; OD=0.0; PD=0.0; LIMIT=set(); EB=0
    warmup=lookback*2
    for i in range(warmup,n):
        PEAK=max(PEAK,EQ); DD=min(DD,(EQ-PEAK)/PEAK*100); MAX_DD=min(MAX_DD,DD)
        lH=np.max(h[i-lookback+1:i+1]); lL=np.min(l[i-lookback+1:i+1]); ran=100*(lH-lL)/lH if lH>0 else 0; ods=ran/10; pf=ods*2.5
        if not GRID and i>warmup and c[i]>0:
            NZ=c[i]; OD=ods; PD=pf; q1=max(int(base_qty),1); CUMQ=q1; CUMC=q1*c[i]; TP=c[i]*(1+PD/100); FILLED=1; GRID=True; EB=i; LIMIT=set()
        if GRID:
            for lv in range(2,max_lvls+1):
                if lv not in LIMIT and l[i]<=NZ-NZ*((lv-1)*OD)/100:
                    LIMIT.add(lv); FILLED=lv; q=max(int(base_qty*(mult**(lv-1))),1); fp=NZ-NZ*((lv-1)*OD)/100; CUMQ+=q; CUMC+=q*fp; TP=CUMC/CUMQ*(1+PD/100)
        if GRID and h[i]>=TP and i>EB:
            avg=CUMC/CUMQ if CUMQ>0 else c[i]; dpnl=(TP-avg)*CUMQ; ppnl=dpnl/EQ*100 if EQ>0 else 0; EQ+=dpnl
            TRADES.append({'pnl':ppnl,'win':True,'levels':FILLED,'bars':i-EB}); GRID=False; CUMQ=0; CUMC=0; FILLED=0; LIMIT=set()
    w=sum(1 for t in TRADES if t['win']); gp=sum(t['pnl'] for t in TRADES if t['pnl']>0); gl=abs(sum(t['pnl'] for t in TRADES if t['pnl']<0)); pf=gp/gl if gl>0 else 999; cret=(EQ/10000-1)*100
    return len(TRADES), w/len(TRADES)*100, cret, pf, MAX_DD, np.mean([t.get('levels',1) for t in TRADES]) if TRADES else 0

print(f"\n{'Strateji':<55} Trades  WR%   Ret%    PF  MaxDD  AvgLv")
print("="*95)
# Legend
t, wr, ret, pf, mdd = run_legend()
print(f"{'Legend BUY SELL DCA (ADX35 DCA3 dsp3.0 TP1.5x1.5)':<55} {t:5d} {wr:5.1f} {ret:+8.2f} {pf:6.2f} {mdd:6.1f} {'-':>5}")

# Martingale8 best configs
for lb, ml, mp, bs in [(42,10,2.0,2), (84,10,2.0,2), (42,10,1.5,2), (84,5,2.0,2), (42,3,2.0,2)]:
    t, wr, ret, pf, mdd, alv = run_mart8(lb, ml, mp, bs)
    label = f"MART8 LB={lb} L={ml} mult={mp} base={bs}"
    print(f"{label:<55} {t:5d} {wr:5.1f} {ret:+8.2f} {pf:6.2f} {mdd:6.1f} {alv:5.1f}")
