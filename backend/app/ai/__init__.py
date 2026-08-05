"""AI/ML katmani: TensorFlow derin ogrenme ile yon tahmini.

Modul `tensorflow` yokken de guvenle iceri aktarilabilir; model agirliklari
lazy import edilir (`_HAVE_TF`). Egitim ve yukleme islemleri tensorflow
gerektirir; eksikken `RuntimeError` firlatilir ve AI kapisi pasif kalir.
"""
from app.ai.features import FEATURE_NAMES, build_features
from app.ai.labeling import make_labels
from app.ai.model import (
    Predictor,
    load_predictor,
    train_from_archive,
    train_from_dataframe,
)

__all__ = [
    "FEATURE_NAMES",
    "build_features",
    "make_labels",
    "Predictor",
    "load_predictor",
    "train_from_archive",
    "train_from_dataframe",
]
