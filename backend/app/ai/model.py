"""AI model training with leakage-safe chronological validation."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

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


def build_lstm_model(sequence_length: int, n_features: int, n_classes: int = _N_CLASSES):
    _require_tf()
    model = tf.keras.Sequential([
        tf.keras.Input(shape=(sequence_length, n_features)),
        tf.keras.layers.LSTM(64, dropout=0.20),
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
                raw_timestamp = df["timestamp"]
                if pd.api.types.is_numeric_dtype(raw_timestamp):
                    df["timestamp"] = pd.to_datetime(raw_timestamp, unit="ms", utc=True)
                else:
                    df["timestamp"] = pd.to_datetime(raw_timestamp, utc=True)
                df = df.set_index("timestamp")
            else:
                df.index = pd.to_datetime(df.index, utc=True)
            df = df.sort_index()
            df.attrs["symbol"] = path.name.rsplit("_", 1)[0]
            df.attrs["source_file"] = str(path)
            frames.append(df)
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


def _folds(n: int, horizon: int, embargo: int = 0):
    train, val, test = _fold_sizes(n)
    splitter = PurgedWalkForward(train, val, test, step=test, embargo=embargo, label_horizon=horizon)
    return splitter.split(n)


def train_from_archive(interval: str = "4h", max_symbols: int = 400,
                       min_bars: int = 300, horizon: int = 12,
                       atr_mult: float = 1.0, epochs: int = 30,
                       model_name: str = "ai_direction", model_type: str = "dense",
                       lstm_seq_len: int = 20, **kwargs) -> Dict[str, Any]:
    frames = _archive_frames(interval, max_symbols, min_bars)
    if not frames:
        raise ValueError("Arsivde yeterli sembol verisi yok")
    if model_type == "dense":
        return train_from_dataframe(frames, horizon=horizon, atr_mult=atr_mult, epochs=epochs, model_name=model_name, **kwargs)
    if model_type == "lstm":
        return train_lstm_from_dataframe(
            frames, horizon=horizon, atr_mult=atr_mult, epochs=epochs,
            model_name=model_name, sequence_length=lstm_seq_len, **kwargs,
        )
    if model_type == "ensemble":
        dense = train_from_dataframe(
            frames, horizon=horizon, atr_mult=atr_mult, epochs=epochs,
            model_name=f"{model_name}_dense", **kwargs,
        )
        lstm = train_lstm_from_dataframe(
            frames, horizon=horizon, atr_mult=atr_mult, epochs=epochs,
            model_name=f"{model_name}_lstm", sequence_length=lstm_seq_len, **kwargs,
        )
        return {"model_dir": str(model_dir(model_name)), "model_type": "ensemble", "samples": dense["samples"], "dense": dense, "lstm": lstm}
    raise ValueError(f"Bilinmeyen model tipi: {model_type}")


def train_from_dataframe(dfs: List[pd.DataFrame], horizon: int = 12,
                         atr_mult: float = 1.0, epochs: int = 30,
                         val_fraction: float = 0.2, seed: int = 7,
                         model_name: str = "ai_direction", **kwargs) -> Dict[str, Any]:
    _require_tf()
    np.random.seed(seed)
    try:
        tf.random.set_seed(seed)
    except AttributeError:
        pass
    X, y, timestamps = _concat_datasets(dfs, horizon, atr_mult)
    folds = _folds(len(X), horizon, kwargs.get("embargo", 0))
    if not folds:
        raise ValueError("Walk-forward fold olusturulamadi")
    fold_metrics = []
    final_model = final_scaler = None
    for fold_no, (train_w, val_w, test_w) in enumerate(folds, start=1):
        from sklearn.preprocessing import StandardScaler
        scaler = StandardScaler()
        X_train = scaler.fit_transform(X[train_w.start:train_w.end]).astype(np.float32)
        X_val = scaler.transform(X[val_w.start:val_w.end]).astype(np.float32)
        X_test = scaler.transform(X[test_w.start:test_w.end]).astype(np.float32)
        model = build_model(X.shape[1])
        hist = model.fit(X_train, y[train_w.start:train_w.end], validation_data=(X_val, y[val_w.start:val_w.end]), epochs=epochs, batch_size=256, verbose=0)
        test_loss, test_acc = model.evaluate(X_test, y[test_w.start:test_w.end], verbose=0)
        fold_metrics.append({"fold": fold_no, "train": train_w.__dict__, "validation": val_w.__dict__, "test": test_w.__dict__, "val_loss": float(hist.history["val_loss"][-1]), "val_acc": float(hist.history["val_accuracy"][-1]), "test_loss": float(test_loss), "test_acc": float(test_acc)})
        final_model, final_scaler = model, scaler
    out_dir = model_dir(model_name)
    out_dir.mkdir(parents=True, exist_ok=True)
    final_model.save(out_dir / "model.keras")
    joblib.dump(final_scaler, out_dir / "scaler.joblib")
    meta = {"features": FEATURE_NAMES, "n_classes": _N_CLASSES, "horizon": horizon, "atr_mult": atr_mult, "validation_method": "purged_walk_forward", "folds": fold_metrics, "samples": len(X), "random_split": False, "model_type": "dense"}
    joblib.dump(meta, out_dir / "meta.joblib")
    metrics = {"samples": len(X), "folds": len(fold_metrics), "mean_val_acc": float(np.mean([f["val_acc"] for f in fold_metrics])), "mean_test_acc": float(np.mean([f["test_acc"] for f in fold_metrics])), "best_test_acc": float(np.max([f["test_acc"] for f in fold_metrics]))}
    joblib.dump(metrics, out_dir / "metrics.joblib")
    return {"model_dir": str(out_dir), "model_type": "dense", **metrics}


def train_lstm_from_dataframe(dfs: List[pd.DataFrame], horizon: int = 12,
                              atr_mult: float = 1.0, epochs: int = 30,
                              seed: int = 7, model_name: str = "ai_direction_lstm",
                              sequence_length: int = 20, **kwargs) -> Dict[str, Any]:
    """Train an LSTM with sequences built independently inside every fold."""
    _require_tf()
    np.random.seed(seed)
    try:
        tf.random.set_seed(seed)
    except AttributeError:
        pass
    if sequence_length < 2:
        raise ValueError("sequence_length en az 2 olmali")
    prepared = [_prepare_dataframe(df, horizon, atr_mult) for df in dfs]
    prepared = [(X, y) for X, y, _ in prepared if len(X)]
    if not prepared:
        raise ValueError("Egitim icin yeterli veri yok")
    fold_metrics = []
    final_model = final_scaler = None
    total_samples = 0
    for X, y in prepared:
        folds = _folds(len(X), horizon, kwargs.get("embargo", 0))
        for fold_no, (train_w, val_w, test_w) in enumerate(folds, start=1):
            from sklearn.preprocessing import StandardScaler
            scaler = StandardScaler()
            X_train = scaler.fit_transform(X[train_w.start:train_w.end]).astype(np.float32)
            X_val = scaler.transform(X[val_w.start:val_w.end]).astype(np.float32)
            X_test = scaler.transform(X[test_w.start:test_w.end]).astype(np.float32)
            train_seq = build_sequences(X_train, y[train_w.start:train_w.end], sequence_length)
            val_seq = build_sequences(X_val, y[val_w.start:val_w.end], sequence_length)
            test_seq = build_sequences(X_test, y[test_w.start:test_w.end], sequence_length)
            if not len(train_seq.X) or not len(val_seq.X) or not len(test_seq.X):
                continue
            model = build_lstm_model(sequence_length, X.shape[1])
            history = model.fit(train_seq.X, train_seq.y, validation_data=(val_seq.X, val_seq.y), epochs=epochs, batch_size=256, verbose=0)
            test_loss, test_acc = model.evaluate(test_seq.X, test_seq.y, verbose=0)
            fold_metrics.append({"fold": fold_no, "val_loss": float(history.history["val_loss"][-1]), "val_acc": float(history.history["val_accuracy"][-1]), "test_loss": float(test_loss), "test_acc": float(test_acc)})
            final_model, final_scaler = model, scaler
            total_samples += len(X)
    if final_model is None or final_scaler is None:
        raise ValueError("LSTM foldleri sequence_length icin yetersiz")
    out_dir = model_dir(model_name)
    out_dir.mkdir(parents=True, exist_ok=True)
    final_model.save(out_dir / "model.keras")
    joblib.dump(final_scaler, out_dir / "scaler.joblib")
    joblib.dump({"features": FEATURE_NAMES, "n_classes": _N_CLASSES, "horizon": horizon, "atr_mult": atr_mult, "sequence_length": sequence_length, "validation_method": "purged_walk_forward", "folds": fold_metrics, "samples": total_samples, "random_split": False, "model_type": "lstm"}, out_dir / "meta.joblib")
    metrics = {"samples": total_samples, "folds": len(fold_metrics), "mean_val_acc": float(np.mean([fold["val_acc"] for fold in fold_metrics])), "mean_test_acc": float(np.mean([fold["test_acc"] for fold in fold_metrics])), "best_test_acc": float(np.max([fold["test_acc"] for fold in fold_metrics]))}
    joblib.dump(metrics, out_dir / "metrics.joblib")
    return {"model_dir": str(out_dir), "model_type": "lstm", **metrics}


class Predictor:
    """Loaded Dense model, scaler and feature contract for live-safe inference."""

    def __init__(self, model: Any, scaler: Any, features: List[str]):
        self.model = model
        self.scaler = scaler
        self.features = features

    def predict(self, df: pd.DataFrame) -> Dict[str, Any]:
        features = _standardize(build_features(df))
        if features.empty:
            return {
                "direction": "HOLD",
                "confidence": 0.0,
                "probabilities": [0.0, 1.0, 0.0],
                "loaded": True,
            }
        row = features.iloc[[-1]][self.features].to_numpy(dtype=np.float32)
        vector = self.scaler.transform(row).astype(np.float32)
        probabilities = np.asarray(self.model.predict(vector, verbose=0)[0], dtype=np.float64)
        index = int(np.argmax(probabilities))
        return {
            "direction": ["SELL", "HOLD", "BUY"][index],
            "confidence": float(probabilities[index]),
            "probabilities": [float(value) for value in probabilities],
            "loaded": True,
        }


def load_predictor(model_name: str) -> Optional[Predictor]:
    """Load model artifacts only when their feature/scaler contract is complete."""
    if not _HAVE_TF:
        return None
    out_dir = model_dir(model_name)
    required = [out_dir / "model.keras", out_dir / "scaler.joblib", out_dir / "meta.joblib"]
    if not all(path.is_file() for path in required):
        return None
    try:
        model = tf.keras.models.load_model(out_dir / "model.keras")
        scaler = joblib.load(out_dir / "scaler.joblib")
        meta = joblib.load(out_dir / "meta.joblib")
        features = list(meta.get("features", FEATURE_NAMES))
        if not features or set(features) - set(FEATURE_NAMES):
            return None
        return Predictor(model, scaler, features)
    except Exception:
        return None
