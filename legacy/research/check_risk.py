import sys; sys.path.insert(0, '.')
from sweep_lbs_dca import run

cfgs = [
    ("ADX>30 DCA=0.5 TP=2.0x2.0 ATR10", 30, 0.5, 2.0, 2.0, 10),
    ("ADX>35 DCA=1.0 TP=2.5x1.0 ATR20", 35, 1.0, 2.5, 1.0, 20),
    ("ADX>35 DCA=1.0 TP=1.5x2.0 ATR20", 35, 1.0, 1.5, 2.0, 20),
    ("ADX>35 DCA=1.0 TP=2.0x1.5 ATR20", 35, 1.0, 2.0, 1.5, 20),
    ("ADX>35 DCA=1.0 TP=2.5x1.5 ATR10", 35, 1.0, 2.5, 1.5, 10),
]
print(f"{'Config':<50} risk  Trades    WR%   NetP%     PF")
print("-"*85)
for label, adx, dsp, tpbm, tpm, atr in cfgs:
    for rp in [1.0, 2.0]:
        t, wr, npnl, pf = run(adx, dsp, tpbm, tpm, rp, 5, atr)
        print(f"{label:<50} {rp:4.1f} {t:5d} {wr:6.1f} {npnl:+8.2f} {pf:6.2f}")
