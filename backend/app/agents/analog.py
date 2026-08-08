"""Analog bellek: gecmis benzesik orneklerle kNN benzerlik analizi.

`build`, arsivdeki (legacy/data/futures_4h_data) her sembolun ozellik
vektorlerini (app.ai.features, 23 ozellik) sembol bazinda z-skorlar ve her
satirin ileri getirisini (horizon bar sonrasi) diskteki artifact'a yazar.
`query`, guncel bar vektorunu bu bellege kNN ile karsilastirir; en yakin
k ornegin ileri getiri dagilimi uzerinden yon oyu ve guven uretir.

Deterministiktir (rastgelelik yok), geriye bakma icermez (bellek yalnizca
tamamlanmis ileri getirileri barindirir). Egitim alt surec tarafindan
(`scripts/train_agents.py`) yeniden kurulur; `get_memory` cache'li yukleyici
restart'siz yeni bellege gecmeyi saglar.
"""
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from app.ai.features import FEATURE_NAMES, build_features
from app.data import loader

AGENT_PARAMS_DIR = Path(__file__).resolve().parent / "agent_params"
MEMORY_DIR = AGENT_PARAMS_DIR / "memory"
MEMORY_PATH = MEMORY_DIR / "analog.npz"
META_PATH = MEMORY_DIR / "meta.json"

_DIM = len(FEATURE_NAMES)

# Benzesik anahtar -> ozellik alt kumesi (FEATURE_NAMES indeksleri)
MASKS: Dict[str, List[int]] = {
    "trend": [3, 7, 8, 9, 20, 16, 13],
    "momentum": [1, 2, 18, 14, 6, 11],
    "reversal": [0, 5, 10, 12, 16, 22],
    "regime": list(range(_DIM)),
}


def _zscore(feats: pd.DataFrame) -> pd.DataFrame:
    """Per-sembol z-skoru; sabit kolonlar ve asiri degerler kilitlenir."""
    std = feats.std().replace(0.0, 1.0)
    return ((feats - feats.mean()) / std).clip(-6.0, 6.0)


class AnalogMemory:
    """Gecmis benzesik ornek havuzu + kNN sorgu motoru."""

    def __init__(self, horizon: int = 24, min_rows: int = 60, k: int = 25,
                 path: Optional[Path] = None, meta_path: Optional[Path] = None):
        self.horizon = int(horizon)
        self.min_rows = int(min_rows)
        self.k = int(k)
        self.path = path or MEMORY_PATH
        self.meta_path = meta_path or META_PATH
        self.vectors: Optional[np.ndarray] = None
        self.fwd: Optional[np.ndarray] = None
        self.codes: Optional[np.ndarray] = None
        self.ts: Optional[np.ndarray] = None
        self.symbols: List[str] = []
        self.built_at: Optional[float] = None

    # ------------------------------------------------------------------
    def build(self, symbols: List[str], data_dir: Optional[str] = None,
              max_bars: int = 1500, horizon: Optional[int] = None) -> Dict[str, Any]:
        """Arsivden bellek kurar; (satir sayisi, atlanan sembol) dondurur."""
        horizon = horizon or self.horizon
        vecs, fwds, codes, tss = [], [], [], []
        skipped = 0
        used: List[str] = []
        for sym in symbols:
            try:
                df = loader.load_csv(sym, "4h", limit=max_bars + horizon + 5,
                                     data_dir=data_dir)
            except Exception:
                skipped += 1
                continue
            if df is None or len(df) < self.min_rows + horizon:
                skipped += 1
                continue
            feats = build_features(df)
            if feats.empty or len(feats) < self.min_rows:
                skipped += 1
                continue
            z = _zscore(feats)
            close = df["close"]
            fwd = (close.shift(-horizon) / close - 1.0) * 100.0
            ok = fwd.notna().to_numpy() & np.isfinite(z.to_numpy()).all(axis=1)
            if int(ok.sum()) < self.min_rows:
                skipped += 1
                continue
            code = len(used)
            used.append(sym)
            vecs.append(z.to_numpy(dtype=np.float32)[ok][-max_bars:])
            fwds.append(fwd.to_numpy(dtype=np.float32)[ok][-max_bars:])
            codes.append(np.full(int(ok.sum()), code, dtype=np.int32)[-max_bars:])
            tss.append(df.index.asi8[ok][-max_bars:])
        if not vecs:
            self.symbols = []
            return {"rows": 0, "symbols": 0, "skipped": skipped}
        self.vectors = np.vstack(vecs)
        self.fwd = np.concatenate(fwds)
        self.codes = np.concatenate(codes)
        self.ts = np.concatenate(tss)
        self.symbols = used
        self.built_at = time.time()
        self.save()
        return {"rows": int(self.vectors.shape[0]),
                "symbols": len(self.symbols), "skipped": skipped}

    # ------------------------------------------------------------------
    def save(self) -> None:
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(self.path, vectors=self.vectors, fwd=self.fwd,
                            codes=self.codes, ts=self.ts)
        meta = {"symbols": self.symbols, "horizon": self.horizon,
                "built_at": self.built_at}
        with self.meta_path.open("w", encoding="utf-8") as f:
            json.dump(meta, f)

    def load(self) -> bool:
        """Artifact'i yukler; yoksa veya bozuksa False."""
        if not self.path.exists():
            return False
        try:
            data = np.load(self.path)
            with self.meta_path.open("r", encoding="utf-8") as f:
                meta = json.load(f)
            self.vectors = data["vectors"]
            self.fwd = data["fwd"]
            self.codes = data["codes"]
            self.ts = data["ts"]
            self.symbols = list(meta.get("symbols", []))
            self.built_at = meta.get("built_at")
            self.horizon = int(meta.get("horizon", self.horizon))
        except Exception:
            return False
        return self.vectors is not None and len(self.vectors) >= self.k

    # ------------------------------------------------------------------
    def query(self, df: pd.DataFrame, key: str = "regime", k: Optional[int] = None) -> Dict[str, Any]:
        """Guncel barin k en yakin benzesik orneginin ileri getiri ozeti.

        Cikti: mean_fwd_pct (komsu ileri getiri ort.), hit_ratio (isaret
        mutabakati), confidence (buyukluk x mutabakat), neighbors, top_fwd.
        Bellek yoksa/yetersizse bos dict doner.
        """
        if self.vectors is None or len(self.vectors) == 0:
            return {}
        k = k or self.k
        if len(self.vectors) < k:
            k = len(self.vectors)
        feats = build_features(df)
        if feats.empty:
            return {}
        q = _zscore(feats).iloc[-1].to_numpy(dtype=np.float32)
        mask = np.asarray(MASKS.get(key, MASKS["regime"]), dtype=int)
        sub = self.vectors[:, mask]
        qs = q[mask]
        d = ((sub - qs) ** 2.0).sum(axis=1)
        idx = np.argpartition(d, k - 1)[:k]
        idx = idx[np.argsort(d[idx])]
        fwd = self.fwd[idx]
        mean = float(fwd.mean())
        sign = 1.0 if mean > 0 else (-1.0 if mean < 0 else 0.0)
        if sign > 0:
            hits = float((fwd > 0).mean())
        elif sign < 0:
            hits = float((fwd < 0).mean())
        else:
            hits = 0.5
        top = sorted(fwd.tolist(), reverse=True)[:5]
        return {
            "key": key, "neighbors": int(k),
            "mean_fwd_pct": round(mean, 3),
            "hit_ratio": round(hits, 3),
            "confidence": round(min(abs(mean) / 3.0, 1.0) * hits, 3),
            "top_fwd": [round(float(x), 3) for x in top],
        }

    def describe(self) -> Dict[str, Any]:
        if self.vectors is None:
            return {"loaded": False}
        return {"loaded": True, "rows": int(self.vectors.shape[0]),
                "symbols": len(self.symbols), "horizon": self.horizon,
                "built_at": self.built_at}


_memory: Optional[AnalogMemory] = None
_memory_loaded = False


def get_memory(k: int = 25, horizon: int = 24) -> Optional[AnalogMemory]:
    """Cache'li bellek yukleyici; artifact yoksa None."""
    global _memory, _memory_loaded
    if not _memory_loaded:
        _memory = AnalogMemory(k=k, horizon=horizon)
        _memory.load()
        _memory_loaded = True
    return _memory


def reset_memory() -> None:
    """Bellek cache'ini temizler (yeniden egitim sonrasi)."""
    global _memory, _memory_loaded
    _memory = None
    _memory_loaded = False
