"""
Final parameter fine-tuning around best config
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

o=df["open"].values; h=df["high"].values; l=df["low"].values; c=df["close"].values; n=len(df)

def rma(arr, period):
    res=np.full(n,np.nan); a=1.0/period
    for i in range(n):
        if i==0: res[i]=arr[i] if not np.isnan(arr[i]) else 0
        elif np.isnan(res[i-1]): res[i]=arr[i] if not np.isnan(arr[i]) else 0
        else: res[i]=arr[i]*a+res[i-1]*(1-a)
    return res
def ema(arr, period):
    res=np.full_like(arr,np.nan,dtype=np.float64); res[0]=arr[0]; a=2.0/(period+1)
    for i in range(1,len(arr)): res[i]=arr[i]*a+res[i-1]*(1-a)
    return res

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

def run(adx_th, dsp, tpbm, tpm, mdca, atr_l):
    atr_vals=rma(tr,atr_l)
    bullish=(adx_val>adx_th)&macd_cross_up; bearish=(adx_val>adx_th)&macd_cross_down
    AD=0; AE=0.0; ATP=0.0; DL=1; DBE=0.0; EBQ=0.0; EB=-999; TPR=False; EQ=10000.0
    TRADES=[]; PEAK=10000.0; DD=0.0; MAX_DD=0.0
    warmup=max(atr_l,14,13)*3
    def eqty(eq,pr,td):
        rc=eq*1.0/100.0; rpc=max(td,0.1)
        rq=int(rc/rpc) if (not np.isnan(rpc) and rpc>0) else 999999
        mq=int(eq/max(pr,0.1)); return float(max(min(rq,max(mq,1)),1))
    def avg_calc(ad,dl,ebq,ae,dbe,ratr,dsp):
        tq=ebq; spq=ebq*ae
        for lvl in range(2,dl+1):
            q=ebq*(2**(lvl-1)); le=dbe-(lvl-1)*ratr*dsp if ad==1 else dbe+(lvl-1)*ratr*dsp
            spq+=q*le; tq+=q
        return (spq/tq if tq>0 else ae, tq)
    for i in range(warmup,n):
        ratr=atr_vals[i] if not np.isnan(atr_vals[i]) else 0
        tpd=ratr*tpbm; tpt=tpd*tpm
        PEAK=max(PEAK,EQ); DD=min(DD,(EQ-PEAK)/PEAK*100); MAX_DD=min(MAX_DD,DD)
        if AD!=0 and not TPR and i>EB:
            if (AD==1 and h[i]>=ATP) or (AD==-1 and l[i]<=ATP): TPR=True
        if TPR and AD!=0:
            avg,tq=avg_calc(AD,DL,EBQ,AE,DBE,ratr,dsp)
            dpnl=(ATP-avg)*tq if AD==1 else (avg-ATP)*tq; EQ+=dpnl
            TRADES.append({'win':True,'dca':DL,'reason':'tp','pnl':dpnl})
            AD=0; TPR=False; DL=1
        if AD!=0 and DBE!=0 and not TPR and i>EB and ratr>0:
            sp=ratr*dsp
            for lvl in range(2,mdca+1):
                if lvl>DL:
                    if (AD==1 and l[i]<=DBE-(lvl-1)*sp) or (AD==-1 and h[i]>=DBE+(lvl-1)*sp): DL=lvl
        rev=(AD==1 and bearish[i]) or (AD==-1 and bullish[i])
        if rev and AD!=0:
            avg,tq=avg_calc(AD,DL,EBQ,AE,DBE,ratr,dsp)
            dpnl=(c[i]-avg)*tq if AD==1 else (avg-c[i])*tq; EQ+=dpnl
            won=(AD==1 and c[i]>=avg) or (AD==-1 and c[i]<=avg)
            TRADES.append({'win':won,'dca':DL,'reason':'rev','pnl':dpnl})
            AD=0; TPR=False; DL=1
            if bearish[i]: AD=-1; AE=c[i]; ATP=c[i]-tpt; DBE=c[i]; DL=1; EBQ=eqty(EQ,c[i],tpd); EB=i
            elif bullish[i]: AD=1; AE=c[i]; ATP=c[i]+tpt; DBE=c[i]; DL=1; EBQ=eqty(EQ,c[i],tpd); EB=i
        if AD==0:
            if bullish[i]: AD=1; AE=c[i]; ATP=c[i]+tpt; DBE=c[i]; DL=1; EBQ=eqty(EQ,c[i],tpd); EB=i
            elif bearish[i]: AD=-1; AE=c[i]; ATP=c[i]-tpt; DBE=c[i]; DL=1; EBQ=eqty(EQ,c[i],tpd); EB=i
    if not TRADES: return None
    wins=sum(1 for t in TRADES if t['win'])
    cret=(EQ/10000-1)*100
    gp=sum(t['pnl'] for t in TRADES if t['pnl']>0); gl=abs(sum(t['pnl'] for t in TRADES if t['pnl']<0))
    pf=gp/gl if gl>0 else 999
    return len(TRADES), wins/len(TRADES)*100, cret, pf, MAX_DD

print(f"{'Config':<50} Trades  WR%   Ret%    PF   MaxDD")
print("-"*90)
cfgs = [
    (30, 3.0, 1.5, 1.5, 3, 10),
    (35, 2.5, 1.5, 1.5, 3, 10),
    (35, 3.0, 1.5, 1.5, 3, 10),
    (35, 3.5, 1.5, 1.5, 3, 10),
    (35, 3.0, 1.5, 2.0, 3, 10),
    (35, 3.0, 2.0, 1.5, 3, 10),
    (35, 3.0, 1.5, 1.5, 3, 14),
    (35, 3.0, 1.5, 1.5, 2, 10),
    (35, 3.0, 2.0, 2.0, 3, 10),
    (40, 3.0, 1.5, 1.5, 3, 10),
]
for cfg in cfgs:
    r = run(*cfg)
    if r is None: continue
    t, wr, cret, pf, mdd = r
    label = f"ADX>{cfg[0]} dsp={cfg[1]} TP={cfg[2]}x{cfg[3]} DCA{cfg[4]} ATR{cfg[5]}"
    print(f"{label:<50} {t:5d} {wr:5.1f} {cret:+7.2f} {pf:5.2f} {mdd:7.1f}")
