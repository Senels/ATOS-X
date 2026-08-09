"""AI model training with leakage-safe chronological validation."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import joblib
import numpy as np
import pandas as pd

from app.ai.features import FEATURE_NAMES, build_features
from app.ai.labeling import make_labels
from app.ai.sequence import build_sequences
from app.data.validation.leakage import assert_unique_sorted_timestamps
from app.data.validation.time_split import PurgedWalkForward

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


def build_lstm_model(n_features: int, seq_len: int, n_classes: int = _N_CLASSES):
    _require_tf()
    model = tf.keras.Sequential([
        tf.keras.Input(shape=(seq_len, n_features)),
        tf.keras.layers.LSTM(64, return_sequences=True),
        tf.keras.layers.Dropout(0.20),
        tf.keras.layers.LSTM(32),
        tf.keras.layers.Dropout(0.20),
        tf.keras.layers.Dense(32, activation="relu"),
        tf.keras.layers.Dense(n_classes, activation="softmax"),
    ])
    model.compile(optimizer="adam", loss="sparse_categorical_crossentropy", metrics=["accuracy"])
    return model


def _standardize(df: pd.DataFrame) -> pd.DataFrame:
    return df.fillna(0.0).replace([np.inf, -np.inf], 0.0)


def _prepare_dataframe(df: pd.DataFrame, horizon: int, atr_mult: float) -> tuple:
    features = _standardize(build_features(df))
    labels = make_labels(df, horizon=horizon, atr_mult=atr_mult).rename("y")
    both = pd.concat([features, labels], axis=1).dropna()
    both = both[both["y"] != 0.0]
    if both.empty:
        return np.empty((0, len(FEATURE_NAMES)), dtype=np.float32), np.empty(0, dtype=np.int32), np.empty(0, dtype=np.int64)
    ts = pd.to_datetime(both.index, utc=True).astype("int64").to_numpy()
    X = both[FEATURE_NAMES].to_numpy(dtype=np.float32)
    y = (both["y"].to_numpy(dtype=np.float32) + 1.0).astype(np.int32)
    return X, y, ts


def _concat_datasets(dfs: List[pd.DataFrame], horizon: int, atr_mult: float) -> tuple:
    parts = [_prepare_dataframe(df, horizon, atr_mult) for df in dfs]
    parts = [p for p in parts if len(p[0])]
    if not parts:
        raise ValueError("Egittm icin yeterli veri yok")
    X = np.vstack([p[0] for p in parts])
    y = np.concatenate([p[1] for p in parts])
    ts = np.concatenate([p[2] for p in parts])
    order = np.argsort(ts, kind="stable")
    X, y, ts = X[order], y[order], ts[order]
    assert_unique_sorted_timestamps(ts.tolist())
    return X, y, ts


def _archive_frames(interval: str = "4h", max_symbols: int = 400, min_bars: int = 300) -> List[pd.DataFrame]:
    archive = Path(__file__).resolve().parents[1] / "data" / "archive"
    frames: List[pd.DataFrame] = []
    for path in sorted(archive.glob(f"*_{interval}.csv"))[:max_symbols]:
        try:
            df = pd.read_csv(path)
            if len(df) < min_bars:
                continue
            if "timestamp" in df.columns:
                df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
                df = df.set_index("timestamp")
            else:
                df.index = pd.to_datetime(df.index, utc=True)
            frames.append(df.sort_index())
        except Exception:
            continue
    return frames


def _fold_sizes(n: int) -> tuple[int, int, int]:
    train = max(100, int(n * 0.60))
    val = max(50, int(n * 0.15))
    test = max(50, int(n * 0.15))
    if train + val + test > n:
        test = max(1, int(n * 0.10))
        val = max(1, int(n * 0.10))
        train = n - val - test
    return train, val, test


def _folds(n: int, horizon: int, **kwargs):
    train, val, test = _fold_sizes(n)
    if train <= 0:
        raise ValueError("Walk-forward icin yeterli veri yok")
    splitter = PurgedWalkForward(
        train_size=train, validation_size=val, test_size=test, step=test,
        embargo=kwargs.get("embargo", 0), label_horizon=kwargs.get("label_horizon", horizon),
    )
    result = splitter.split(n)
    if not result:
        raise ValueError("Walk-forward fold olusturulamadi")
    return result


def _train_lstm(X: np.ndarray, y: np.ndarray, folds, seq_len: int, epochs: int, model_name: str) -> Dict[str, Any]:
    fold_metrics = []
    final_model = final_scaler = None
    for fold_no, (train_w, val_w, test_w) in enumerate(folds, start=1):
        from sklearn.preprocessing import StandardScaler
        scaler = StandardScaler()
        # Scaling is fit only on the training fold; sequences are then built
        # independently for each fold, so no window can cross a fold boundary.
        X_train_raw = scaler.fit_transform(X[train_w.start:train_w.end]).astype(np.float32)
        X_val_raw = scaler.transform(X[val_w.start:val_w.end]).astype(np.float32)
        X_test_raw = scaler.transform(X[test_w.start:test_w.end]).astype(np.float32)
        tr = build_sequences(X_train_raw, y[train_w.start:train_w.end], seq_len)
        va = build_sequences(X_val_raw, y[val_w.start:val_w.end], seq_len)
        te = build_sequences(X_test_raw, y[test_w.start:test_w.end], seq_len)
        if not len(tr.X) or not len(va.X) or not len(te.X):
            continue
        model = build_lstm_model(X.shape[1], seq_len)
        hist = model.fit(tr.X, tr.y, validation_data=(va.X, va.y), epochs=epochs, batch_size=256, verbose=0)
        test_loss, test_acc = model.evaluate(te.X, te.y, verbose=0)
        fold_metrics.append({
            "fold": fold_no, "train": train_w.__dict__, "validation": val_w.__dict__, "test": test_w.__dict__,
            "sequence_length": seq_len, "train_sequences": len(tr.X), "validation_sequences": len(va.X), "test_sequences": len(te.X),
            "val_loss": float(hist.history["val_loss"][-1]), "val_acc": float(hist.history["val_accuracy"][-1]),
            "test_loss": float(test_loss), "test_acc": float(test_acc),
        })
        final_model, final_scaler = model, scaler
    if final_model is None:
        raise ValueError("LSTM foldlari icin yeterli sequence verisi yok")
    out_dir = model_dir(model_name)
    out_dir.mkdir(parents=True, exist_ok=True)
    final_model.save(out_dir / "model.keras")
    joblib.dump(final_scaler, out_dir / "scaler.joblib")
    joblib.dump({"features": FEATURE_NAMES, "n_classes": _N_CLASSES, "model_type": "lstm", "sequence_length": seq_len,
                 "validation_method": "purged_walk_forward_fold_local_sequences", "folds": fold_metrics, "random_split": False}, out_dir / "meta.joblib")
    metrics = {"samples": int(len(X)), "folds": len(fold_metrics),
               "mean_val_acc": float(np.mean([f["val_acc"] for f in fold_metrics])),
               "mean_test_acc": float(np.mean([f["test_acc"] for f in fold_metrics]))}
    joblib.dump(metrics, out_dir / "metrics.joblib")
    return {"model_dir": str(out_dir), "model_type": "lstm", **metrics}


def train_from_archive(interval: str = "4h", max_symbols: int = 400, min_bars: int = 300, horizon: int = 12,
                       atr_mult: float = 1.0, epochs: int = 30, model_name: str = "ai_direction",
                       model_type: str = "dense", lstm_seq_len: int = 20, **kwargs) -> Dict[str, Any]:
    frames = _archive_frames(interval, max_symbols, min_bars)
    X, y, _ = _concat_datasets(frames, horizon, atr_mult)
    folds = _folds(len(X), horizon, **kwargs)
    if model_type == "lstm":
        return _train_lstm(X, y, folds, lstm_seq_len, epochs, model_name)
    if model_type == "ensemble":
        raise RuntimeError("ensemble, Dense ve LSTM OOS modelleri ayni foldlarda karsilastirildiktan sonra etkinlestirilecek")
    return train_from_dataframe(frames, horizon=horizon, atr_mult=atr_mult, epochs=epochs, model_name=model_name, **kwargs)


def train_from_dataframe(dfs: List[pd.DataFrame], horizon: int = 12, atr_mult: float = 1.0, epochs: int = 30,
                         val_fraction: float = 0.2, seed: int = 7, model_name: str = "ai_direction", **kwargs) -> Dict[str, Any]:
    _require_tf()
    np.random.seed(seed); tf.random.set_seed(seed)
    X, y, _ = _concat_datasets(dfs, horizon, atr_mult)
    folds = _folds(len(X), horizon, **kwargs)
    fold_metrics = []
    final_model = final_scaler = None
    for fold_no, (train_w, val_w, test_w) in enumerate(folds, start=1):
        from sklearn.preprocessing import StandardScaler
        scaler = StandardScaler(); X_train = scaler.fit_transform(X[train_w.start:train_w.end]).astype(np.float32)
        X_val = scaler.transform(X[val_w.start:val_w.end]).astype(np.float32); X_test = scaler.transform(X[test_w.start:test_w.end]).astype(np.float32)
        model = build_model(X.shape[1]); hist = model.fit(X_train, y[train_w.start:train_w.end], validation_data=(X_val, y[val_w.start:val_w.end]), epochs=epochs, batch_size=256, verbose=0)
        test_loss, test_acc = model.evaluate(X_test, y[test_w.start:test_w.end], verbose=0)
        fold_metrics.append({"fold": fold_no, "train": train_w.__dict__, "validation": val_w.__dict__, "test": test_w.__dict__, "val_loss": float(hist.history["val_loss"][-1]), "val_acc": float(hist.history["val_accuracy"][-1]), "test_loss": float(test_loss), "test_acc": float(test_acc)})
        final_model, final_scaler = model, scaler
    out_dir = model_dir(model_name); out_dir.mkdir(parents=True, exist_ok=True); final_model.save(out_dir / "model.keras"); joblib.dump(final_scaler, out_dir / "scaler.joblib")
    joblib.dump({"features": FEATURE_NAMES, "n_classes": _N_CLASSES, "horizon": horizon, "atr_mult": atr_mult, "validation_method": "purged_walk_forward", "folds": fold_metrics, "samples": len(X), "random_split": False, "model_type": "dense"}, out_dir / "meta.joblib")
    metrics = {"samples": len(X), "folds": len(fold_metrics), "mean_val_acc": float(np.mean([f["val_acc"] for f in fold_metrics])), "mean_test_acc": float(np.mean([f["test_acc"] for f in fold_metrics])), "best_test_acc": float(np.max([f["test_acc"] for f in fold_metrics]))}
    joblib.dump(metrics, out_dir / "metrics.joblib")
    return {"model_dir": str(out_dir), "model_type": "dense", **metrics}
