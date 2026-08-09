"""AI model katmani.

Production training entry-point `train_from_dataframe` is delegated to the
leakage-safe trainer. The legacy implementation is intentionally removed from
this public API so random train/validation splitting cannot be invoked by
accident. Model architecture and prediction APIs remain compatible.
"""
from pathlib import Path
from typing import Any, Dict, List, Optional

import joblib
import numpy as np
import pandas as pd

from app.ai.features import FEATURE_NAMES, build_features
from app.ai.labeling import make_labels
from app.data import loader

try:
    import tensorflow as tf
    _HAVE_TF = True
except Exception:
    _HAVE_TF = False

_MODEL_ROOT = Path(__file__).resolve().parents[1] / "models"
_N_CLASSES = 3


def _require_tf():
    if not _HAVE_TF:
        raise RuntimeError("tensorflow kurulu degil; AI egitimi icin: pip install -e 'backend[ai]'")


def model_dir(name: str) -> Path:
    return _MODEL_ROOT / name


def build_model(n_features: int, n_classes: int = _N_CLASSES):
    _require_tf()
    model = tf.keras.Sequential([
        tf.keras.Input(shape=(n_features,)),
        tf.keras.layers.Dense(128, activation="relu"),
        tf.keras.layers.Dropout(0.25),
        tf.keras.layers.Dense(64, activation="relu"),
        tf.keras.layers.Dropout(0.25),
        tf.keras.layers.Dense(32, activation="relu"),
        tf.keras.layers.Dense(n_classes, activation="softmax"),
    ])
    model.compile(optimizer="adam", loss="sparse_categorical_crossentropy", metrics=["accuracy"])
    return model


def build_lstm_model(n_features: int, seq_len: int = 20, n_classes: int = _N_CLASSES):
    _require_tf()
    model = tf.keras.Sequential([
        tf.keras.Input(shape=(seq_len, n_features)),
        tf.keras.layers.LSTM(64, return_sequences=False),
        tf.keras.layers.Dropout(0.25),
        tf.keras.layers.Dense(32, activation="relu"),
        tf.keras.layers.Dense(n_classes, activation="softmax"),
    ])
    model.compile(optimizer="adam", loss="sparse_categorical_crossentropy", metrics=["accuracy"])
    return model


def _standardize(df: pd.DataFrame) -> pd.DataFrame:
    return df.fillna(0.0).replace([np.inf, -np.inf], 0.0)


def _dataset(df: pd.DataFrame, horizon: int, atr_mult: float, drop_hnd: bool = True) -> tuple:
    feats = _standardize(build_features(df))
    labels = make_labels(df, horizon=horizon, atr_mult=atr_mult)
    both = pd.concat([feats, labels.rename("y")], axis=1).dropna()
    if drop_hnd:
        both = both[both["y"] != 0.0]
    y = both["y"].to_numpy(dtype=np.float32)
    X = both[FEATURE_NAMES].to_numpy(dtype=np.float32)
    y_idx = ((y + 1.0)).astype(np.int32) if y.size else y.astype(np.int32)
    return X, y_idx


def _dataset_seq(df: pd.DataFrame, horizon: int, atr_mult: float, seq_len: int = 20, drop_hnd: bool = True) -> tuple:
    X_flat, y_flat = _dataset(df, horizon, atr_mult, drop_hnd)
    if len(X_flat) <= seq_len:
        return np.empty((0, seq_len, X_flat.shape[-1]), dtype=np.float32), np.empty(0, dtype=np.int32)
    Xs, ys = [], []
    for i in range(seq_len, len(X_flat)):
        Xs.append(X_flat[i - seq_len:i])
        ys.append(y_flat[i])
    return np.array(Xs, dtype=np.float32), np.array(ys, dtype=np.int32)


def train_from_dataframe(dfs: List[pd.DataFrame], horizon: int = 12, atr_mult: float = 1.0,
                         epochs: int = 30, val_fraction: float = 0.2, seed: int = 7,
                         model_name: str = "ai_direction", model_type: str = "dense",
                         lstm_seq_len: int = 20) -> Dict[str, Any]:
    """Canonical leakage-safe training API.

    Training is delegated to `safe_trainer`: chronological per-symbol split,
    purge for forecast horizon, and scaler fitting on training data only.
    """
    from app.ai.safe_trainer import train_from_dataframe_safe
    return train_from_dataframe_safe(
        dfs=dfs, horizon=horizon, atr_mult=atr_mult, epochs=epochs,
        val_fraction=val_fraction, seed=seed, model_name=model_name,
        model_type=model_type, lstm_seq_len=lstm_seq_len,
    )


def train_from_archive(interval: str = "4h", max_symbols: int = 400, min_bars: int = 300, **kwargs) -> Dict[str, Any]:
    """Local Binance USDⓈ-M Futures archive -> canonical safe trainer."""
    from app.ai.safe_trainer import train_from_archive_safe
    return train_from_archive_safe(interval=interval, max_symbols=max_symbols, min_bars=min_bars, **kwargs)


class Predictor:
    """Loaded model + scaler ile son barin yon tahmini."""

    def __init__(self, model, scaler, features: List[str], horizon: int = 12, atr_mult: float = 1.0,
                 lstm_model=None, lstm_seq_len: int = 20, model_type: str = "dense"):
        self.model = model
        self.scaler = scaler
        self.features = features
        self.horizon = horizon
        self.atr_mult = atr_mult
        self.lstm_model = lstm_model
        self.lstm_seq_len = int(lstm_seq_len)
        self.model_type = model_type

    def predict(self, df: pd.DataFrame) -> Dict[str, Any]:
        feats = _standardize(build_features(df))
        if feats.empty:
            return {"direction": "HOLD", "confidence": 0.0, "probabilities": [0.0, 1.0, 0.0], "loaded": True}
        all_probs = []
        if self.model is not None and self.model_type != "lstm":
            row = feats.iloc[[-1]][self.features].to_numpy(dtype=np.float32)
            vec = self.scaler.transform(row).astype(np.float32)
            all_probs.append(np.asarray(self.model.predict(vec, verbose=0)[0], dtype=np.float64))
        if self.lstm_model is not None and self.model_type in ("lstm", "ensemble"):
            feat_arr = feats[self.features].to_numpy(dtype=np.float32)
            if len(feat_arr) >= self.lstm_seq_len:
                seq = feat_arr[-self.lstm_seq_len:]
                seq_scaled = self.scaler.transform(seq).astype(np.float32)
                all_probs.append(np.asarray(self.lstm_model.predict(seq_scaled.reshape(1, self.lstm_seq_len, -1), verbose=0)[0], dtype=np.float64))
        if not all_probs:
            return {"direction": "HOLD", "confidence": 0.0, "probabilities": [0.0, 1.0, 0.0], "loaded": True}
        probs = np.mean(all_probs, axis=0)
        idx = int(np.argmax(probs))
        return {"direction": ["SELL", "HOLD", "BUY"][idx], "confidence": float(probs[idx]),
                "probabilities": [float(p) for p in probs], "loaded": True, "model_type": self.model_type}


def load_predictor(model_name: str) -> Optional[Predictor]:
    if not _HAVE_TF:
        return None
    out_dir = model_dir(model_name)
    if not (out_dir / "model.keras").exists() and not (out_dir / "lstm_model.keras").exists():
        return None
    try:
        scaler = joblib.load(out_dir / "scaler.joblib")
        meta = joblib.load(out_dir / "meta.joblib")
        features = meta.get("features", FEATURE_NAMES)
        horizon = int(meta.get("horizon", 12))
        atr_mult = float(meta.get("atr_mult", 1.0))
        model_type = str(meta.get("model_type", "dense"))
        seq_len = int(meta.get("lstm_seq_len", 20))
        model = tf.keras.models.load_model(out_dir / "model.keras") if (out_dir / "model.keras").exists() else None
        lstm_model = tf.keras.models.load_model(out_dir / "lstm_model.keras") if (out_dir / "lstm_model.keras").exists() else None
        return Predictor(model, scaler, features, horizon, atr_mult, lstm_model, seq_len, model_type)
    except Exception:
        return None
