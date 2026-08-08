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
from loguru import logger

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


def build_lstm_model(n_features: int, seq_len: int = 20,
                     n_classes: int = _N_CLASSES):
    """LSTM tabanli siniflandirma agi.

    Her ornek `(seq_len, n_features)` boyutunda bir pencere dizisidir.
    Dense aga kiyasla gecici baglamlari (momentum, trend suresu) daha iyi
    yakalar.
    """
    _require_tf()
    model = tf.keras.Sequential([
        tf.keras.Input(shape=(seq_len, n_features)),
        tf.keras.layers.LSTM(64, return_sequences=False),
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


def _dataset_seq(df: pd.DataFrame, horizon: int, atr_mult: float,
                 seq_len: int = 20, drop_hnd: bool = True) -> tuple:
    """LSTM icin kayan pencere (seq_len, n_features) dizileri uretir."""
    X_flat, y_flat = _dataset(df, horizon, atr_mult, drop_hnd)
    if len(X_flat) <= seq_len:
        return np.empty((0, seq_len, X_flat.shape[-1]), dtype=np.float32), np.empty(0, dtype=np.int32)

    Xs, ys = [], []
    for i in range(seq_len, len(X_flat)):
        Xs.append(X_flat[i - seq_len:i])
        ys.append(y_flat[i])
    return np.array(Xs, dtype=np.float32), np.array(ys, dtype=np.int32)


# ---------------------------------------------------------------------------
# Egitim
# ---------------------------------------------------------------------------
def train_from_dataframe(dfs: List[pd.DataFrame], horizon: int = 12,
                         atr_mult: float = 1.0, epochs: int = 30,
                         val_fraction: float = 0.2, seed: int = 7,
                         model_name: str = "ai_direction",
                         model_type: str = "dense",
                         lstm_seq_len: int = 20) -> Dict[str, Any]:
    """DataFrame listesinden ortak veri seti kurar, egitir ve kaydeder.

    Her DataFrame ayri sembol kabul edilir; parcalar ust uste eklenir ve
    zaman bazli %20 val dilimine ayrilir. Kayit `backend/models/<model_name>`:
    keras model + scaler + feature listesi + metrikler.

    ``model_type``: "dense" (varsayilan) | "lstm" | "ensemble"
    - "dense"    : Standart ileri beslemeli ag (Sprint <=12 ile ayni).
    - "lstm"     : LSTM ag, `lstm_seq_len` uzunlugunda pencereler.
    - "ensemble" : Dense + LSTM egitilir; tahmin olasiliklarinin ortalamasini alir.
    """
    _require_tf()
    np.random.seed(seed)
    try:
        tf.random.set_seed(seed)
    except AttributeError:
        pass

    # ── Dense icin duz veri seti ──────────────────────────────────────────────
    Xs_flat, ys_flat = [], []
    for df in dfs:
        X, y = _dataset(df, horizon=horizon, atr_mult=atr_mult)
        if len(X) >= 100:
            Xs_flat.append(X)
            ys_flat.append(y)
    if not Xs_flat:
        raise ValueError("Egittm icin yeterli veri yok (toplam <100 ornek)")
    X_flat = np.vstack(Xs_flat).astype(np.float32)
    y_flat = np.concatenate(ys_flat).astype(np.int32)

    from sklearn.model_selection import train_test_split
    from sklearn.preprocessing import StandardScaler

    scaler = StandardScaler()
    X_tr, X_val, y_tr, y_val = train_test_split(
        X_flat, y_flat, test_size=val_fraction, shuffle=True, random_state=seed)
    X_tr_s = scaler.fit_transform(X_tr).astype(np.float32)
    X_val_s = scaler.transform(X_val).astype(np.float32)

    out_dir = model_dir(model_name)
    out_dir.mkdir(parents=True, exist_ok=True)

    def _save_meta(extra: dict = None):
        meta = {"features": FEATURE_NAMES, "n_classes": _N_CLASSES,
                "horizon": horizon, "atr_mult": atr_mult,
                "model_type": model_type, "lstm_seq_len": lstm_seq_len}
        if extra:
            meta.update(extra)
        joblib.dump(meta, out_dir / "meta.joblib")

    # ── Dense egitim ─────────────────────────────────────────────────────────
    dense_metrics: Dict[str, Any] = {}
    if model_type in ("dense", "ensemble"):
        dense_model = build_model(X_flat.shape[1])
        hist = dense_model.fit(
            X_tr_s, y_tr,
            validation_data=(X_val_s, y_val),
            epochs=epochs, batch_size=256, verbose=0,
        )
        dense_metrics = {
            "val_loss": float(hist.history["val_loss"][-1]),
            "val_acc": float(hist.history["val_accuracy"][-1]),
        }
        dense_model.save(out_dir / "model.keras")

    # ── LSTM egitim ───────────────────────────────────────────────────────────
    lstm_metrics: Dict[str, Any] = {}
    if model_type in ("lstm", "ensemble"):
        # LSTM icin dizi veri seti
        Xs_seq, ys_seq = [], []
        for df in dfs:
            Xs, ys = _dataset_seq(df, horizon=horizon, atr_mult=atr_mult,
                                  seq_len=lstm_seq_len)
            if len(Xs) >= 50:
                Xs_seq.append(Xs)
                ys_seq.append(ys)
        if Xs_seq:
            X_seq = np.vstack(Xs_seq).astype(np.float32)
            y_seq = np.concatenate(ys_seq).astype(np.int32)
            # Kayan pencere icin scaler her timestep'e ayri uygulanir
            n, t, f = X_seq.shape
            X_seq_flat = X_seq.reshape(-1, f)
            X_seq_scaled = scaler.transform(X_seq_flat).reshape(n, t, f).astype(np.float32)
            X_seq_tr, X_seq_val, y_seq_tr, y_seq_val = train_test_split(
                X_seq_scaled, y_seq, test_size=val_fraction,
                shuffle=True, random_state=seed)
            lstm_model = build_lstm_model(f, seq_len=lstm_seq_len)
            hist_lstm = lstm_model.fit(
                X_seq_tr, y_seq_tr,
                validation_data=(X_seq_val, y_seq_val),
                epochs=epochs, batch_size=128, verbose=0,
            )
            lstm_metrics = {
                "val_loss": float(hist_lstm.history["val_loss"][-1]),
                "val_acc": float(hist_lstm.history["val_accuracy"][-1]),
            }
            lstm_model.save(out_dir / "lstm_model.keras")

    joblib.dump(scaler, out_dir / "scaler.joblib")
    _save_meta()

    # Metrikler: dense/lstm veya her ikisi
    if model_type == "dense":
        final_metrics = dense_metrics
    elif model_type == "lstm":
        final_metrics = lstm_metrics
    else:
        # Ensemble: her iki metrigin ortalamasini al
        final_metrics = {
            "val_loss": round((dense_metrics.get("val_loss", 0) +
                               lstm_metrics.get("val_loss", 0)) / 2, 4),
            "val_acc": round((dense_metrics.get("val_acc", 0) +
                              lstm_metrics.get("val_acc", 0)) / 2, 4),
            "dense": dense_metrics,
            "lstm": lstm_metrics,
        }

    all_metrics = {
        "samples": int(len(X_flat)),
        "model_type": model_type,
        "horizon": horizon,
        "atr_mult": atr_mult,
        **final_metrics,
    }
    joblib.dump(all_metrics, out_dir / "metrics.joblib")

    return {"model_dir": str(out_dir), "samples": int(len(X_flat)),
            "model_type": model_type, **final_metrics}


def train_from_archive(interval: str = "4h", max_symbols: int = 400,
                       min_bars: int = 300, **kwargs) -> Dict[str, Any]:
    """Yerel CSV arsivindeki sembolleri kullanarak ortak model egitir."""
    symbols = loader.list_symbols(interval)
    dfs = []
    for sym in symbols[:max_symbols]:
        try:
            df = loader.load_csv(sym, interval)
        except Exception as e:
            logger.debug(f"OHLCV yuklenemedi (symbol={sym}): {e}")
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

    `lstm_model` verilirse ensemble modu aktif olur: dense ve LSTM
    olasiliklarinin ortalamasi kullanilir.
    """

    def __init__(self, model, scaler, features: List[str],
                 horizon: int = 12, atr_mult: float = 1.0,
                 lstm_model=None, lstm_seq_len: int = 20,
                 model_type: str = "dense"):
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
            return {"direction": "HOLD", "confidence": 0.0,
                    "probabilities": [0.0, 1.0, 0.0], "loaded": True}

        all_probs = []

        # Dense tahmin
        if self.model is not None and self.model_type != "lstm":
            row = feats.iloc[[-1]][self.features].to_numpy(dtype=np.float32)
            vec = self.scaler.transform(row).astype(np.float32)
            dense_probs = np.asarray(self.model.predict(vec, verbose=0)[0], dtype=np.float64)
            all_probs.append(dense_probs)

        # LSTM tahmin (ensemble veya lstm modu)
        if self.lstm_model is not None and self.model_type in ("lstm", "ensemble"):
            feat_arr = feats[self.features].to_numpy(dtype=np.float32)
            if len(feat_arr) >= self.lstm_seq_len:
                seq = feat_arr[-self.lstm_seq_len:]  # (seq_len, n_features)
                seq_scaled = self.scaler.transform(seq).astype(np.float32)
                seq_input = seq_scaled.reshape(1, self.lstm_seq_len, -1)
                lstm_probs = np.asarray(
                    self.lstm_model.predict(seq_input, verbose=0)[0], dtype=np.float64
                )
                all_probs.append(lstm_probs)

        if not all_probs:
            return {"direction": "HOLD", "confidence": 0.0,
                    "probabilities": [0.0, 1.0, 0.0], "loaded": True}

        probs = np.mean(all_probs, axis=0)
        idx = int(np.argmax(probs))
        direction = ["SELL", "HOLD", "BUY"][idx]
        return {
            "direction": direction,
            "confidence": float(probs[idx]),
            "probabilities": [float(p) for p in probs],
            "loaded": True,
            "model_type": self.model_type,
        }


def load_predictor(model_name: str) -> Optional[Predictor]:
    """`backend/models/<model_name>` altindan model+scaler yukler; yoksa None.

    Ensemble modu icin `lstm_model.keras` de yuklenir (varsa).
    """
    if not _HAVE_TF:
        return None
    out_dir = model_dir(model_name)
    if not (out_dir / "model.keras").exists() and not (out_dir / "lstm_model.keras").exists():
        return None
    try:
        scaler = joblib.load(out_dir / "scaler.joblib")
        meta = joblib.load(out_dir / "meta.joblib")
        features = meta.get("features", FEATURE_NAMES)
        mtype = str(meta.get("model_type", "dense"))
        lstm_seq_len = int(meta.get("lstm_seq_len", 20))

        dense_model = None
        lstm_model = None
        if (out_dir / "model.keras").exists():
            dense_model = tf.keras.models.load_model(out_dir / "model.keras")
        if (out_dir / "lstm_model.keras").exists():
            lstm_model = tf.keras.models.load_model(out_dir / "lstm_model.keras")

        # Eski modeller (model_type meta'si yok) dense kabul edilir
        if dense_model is None and lstm_model is None:
            return None

        return Predictor(
            model=dense_model,
            scaler=scaler,
            features=features,
            horizon=int(meta.get("horizon", 12)),
            atr_mult=float(meta.get("atr_mult", 1.0)),
            lstm_model=lstm_model,
            lstm_seq_len=lstm_seq_len,
            model_type=mtype,
        )
    except Exception:
        return None
