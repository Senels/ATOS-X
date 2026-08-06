"""AI model katmani: TensorFlow derin ag, egitim, yukleme ve tahmin.

`tensorflow` bu modul iceri aktarilirken zorunlu degildir (`_HAVE_TF`);
egitim/agir model islemleri gerektigi anda `_require_tf()` ile kontrol
edilir ve eksikse anlasilir bir `RuntimeError` firlatilir. Bu sayede
tensorflow olmayan ortamlarda (CI dahil) uygulama ve testler guvenle calisir.
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
    import tensorflow as tf  # noqa: F401
    _HAVE_TF = True
except Exception:  # ImportError dahil; ortamda yoksa zarif pasif
    _HAVE_TF = False

_MODEL_ROOT = Path(__file__).resolve().parents[1] / "models"
_N_CLASSES = 3


def _require_tf():
    if not _HAVE_TF:
        raise RuntimeError(
            "tensorflow kurulu degil; AI egitimi icin: pip install -e 'backend[ai]'"
        )


def model_dir(name: str) -> Path:
    return _MODEL_ROOT / name


# ---------------------------------------------------------------------------
# Ag mimarisi
# ---------------------------------------------------------------------------
def build_model(n_features: int, n_classes: int = _N_CLASSES):
    """Cok katmanli ileri beslemeli (deep) siniflandirma agi."""
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
    model.compile(
        optimizer="adam",
        loss="sparse_categorical_crossentropy",
        metrics=["accuracy"],
    )
    return model


def _standardize(df: pd.DataFrame) -> pd.DataFrame:
    return df.fillna(0.0).replace([np.inf, -np.inf], 0.0)


# ---------------------------------------------------------------------------
# Veri seti
# ---------------------------------------------------------------------------
def _dataset(df: pd.DataFrame, horizon: int, atr_mult: float,
             drop_hnd: bool = True) -> tuple:
    feats = _standardize(build_features(df))
    labels = make_labels(df, horizon=horizon, atr_mult=atr_mult)
    both = pd.concat([feats, labels.rename("y")], axis=1).dropna()
    if drop_hnd:
        both = both[both["y"] != 0.0]
    y = both["y"].to_numpy(dtype=np.float32)
    X = both[FEATURE_NAMES].to_numpy(dtype=np.float32)
    # Label'lar -1/0/1 -> 0/1/2 indeksine cevrilir
    y_idx = ((y + 1.0)).astype(np.int32) if y.size else y.astype(np.int32)
    return X, y_idx


# ---------------------------------------------------------------------------
# Egitim
# ---------------------------------------------------------------------------
def train_from_dataframe(dfs: List[pd.DataFrame], horizon: int = 12,
                         atr_mult: float = 1.0, epochs: int = 30,
                         val_fraction: float = 0.2, seed: int = 7,
                         model_name: str = "ai_direction") -> Dict[str, Any]:
    """DataFrame listesinden ortak veri seti kurar, egitir ve kaydeder.

    Her DataFrame ayri sembol kabul edilir; parcalar ust uste eklenir ve
    zaman bazli %20 val dilimine ayrilir. Kayit `backend/models/<model_name>`:
    keras model + scaler + feature listesi + metrikler.
    """
    _require_tf()
    np.random.seed(seed)
    try:
        tf.random.set_seed(seed)
    except AttributeError:
        pass

    Xs, ys = [], []
    for df in dfs:
        X, y = _dataset(df, horizon=horizon, atr_mult=atr_mult)
        if len(X) >= 100:
            Xs.append(X)
            ys.append(y)
    if not Xs:
        raise ValueError("Egittm icin yeterli veri yok (toplam <100 ornek)")
    X = np.vstack(Xs).astype(np.float32)
    y = np.concatenate(ys).astype(np.int32)

    from sklearn.model_selection import train_test_split
    from sklearn.preprocessing import StandardScaler

    scaler = StandardScaler()
    Xs_train, Xs_val, y_train, y_val = train_test_split(
        X, y, test_size=val_fraction, shuffle=True, random_state=seed)
    Xs_train = scaler.fit_transform(Xs_train).astype(np.float32)
    Xs_val = scaler.transform(Xs_val).astype(np.float32)

    model = build_model(X.shape[1])
    history = model.fit(
        Xs_train, y_train,
        validation_data=(Xs_val, y_val),
        epochs=epochs,
        batch_size=256,
        verbose=0,
    )
    val_loss = float(history.history["val_loss"][-1])
    val_acc = float(history.history["val_accuracy"][-1])

    out_dir = model_dir(model_name)
    out_dir.mkdir(parents=True, exist_ok=True)
    model.save(out_dir / "model.keras")
    joblib.dump(scaler, out_dir / "scaler.joblib")
    joblib.dump({"features": FEATURE_NAMES, "n_classes": _N_CLASSES,
                 "horizon": horizon, "atr_mult": atr_mult},
                out_dir / "meta.joblib")
    metrics = {"samples": int(len(X)), "val_loss": val_loss, "val_acc": val_acc,
               "horizon": horizon, "atr_mult": atr_mult}
    joblib.dump(metrics, out_dir / "metrics.joblib")

    return {"model_dir": str(out_dir), "samples": int(len(X)),
            "val_loss": val_loss, "val_acc": val_acc}


def train_from_archive(interval: str = "4h", max_symbols: int = 400,
                       min_bars: int = 300, **kwargs) -> Dict[str, Any]:
    """Yerel CSV arsivindeki sembolleri kullanarak ortak model egitir."""
    symbols = loader.list_symbols(interval)
    dfs = []
    for sym in symbols[:max_symbols]:
        try:
            df = loader.load_csv(sym, interval)
        except Exception:
            continue
        if len(df) >= min_bars:
            dfs.append(df)
    if not dfs:
        raise ValueError("Arsivde yeterli sembol verisi yok")
    return train_from_dataframe(dfs, **kwargs)


# ---------------------------------------------------------------------------
# Tahmin
# ---------------------------------------------------------------------------
class Predictor:
    """Yuklenmis model + scaler ile son barin yon tahmini.

    `horizon` meta'dan okunur (canli cozumleme semantigi ile uyumluluk icin);
    eski modellerde varsayilan 12'dir.
    """

    def __init__(self, model, scaler, features: List[str],
                 horizon: int = 12, atr_mult: float = 1.0):
        self.model = model
        self.scaler = scaler
        self.features = features
        self.horizon = horizon
        self.atr_mult = atr_mult

    def predict(self, df: pd.DataFrame) -> Dict[str, Any]:
        feats = _standardize(build_features(df))
        if feats.empty:
            return {"direction": "HOLD", "confidence": 0.0,
                    "probabilities": [0.0, 1.0, 0.0], "loaded": True}
        row = feats.iloc[[-1]][self.features].to_numpy(dtype=np.float32)
        vec = self.scaler.transform(row).astype(np.float32)
        probs = np.asarray(self.model.predict(vec, verbose=0)[0], dtype=np.float64)
        idx = int(np.argmax(probs))
        direction = ["SELL", "HOLD", "BUY"][idx]
        return {
            "direction": direction,
            "confidence": float(probs[idx]),
            "probabilities": [float(p) for p in probs],
            "loaded": True,
        }


def load_predictor(model_name: str) -> Optional[Predictor]:
    """`backend/models/<model_name>` altindan model+scaler yukler; yoksa None."""
    if not _HAVE_TF:
        return None
    out_dir = model_dir(model_name)
    if not (out_dir / "model.keras").exists():
        return None
    try:
        model = tf.keras.models.load_model(out_dir / "model.keras")
        scaler = joblib.load(out_dir / "scaler.joblib")
        meta = joblib.load(out_dir / "meta.joblib")
        features = meta.get("features", FEATURE_NAMES)
        return Predictor(
            model, scaler, features,
            horizon=int(meta.get("horizon", 12)),
            atr_mult=float(meta.get("atr_mult", 1.0)),
        )
    except Exception:
        return None
