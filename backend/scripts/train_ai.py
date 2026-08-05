"""AI model egitimi: yerel 4h CSV arsivinden TensorFlow yon tahmini modeli.

Kullanim (backend/ icinden):
    python scripts/train_ai.py [--symbols 400] [--min-bars 300]
        [--horizon 12] [--atr-mult 1.0] [--epochs 30] [--model ai_direction]

Kayit: backend/models/<model>/ -> model.keras + scaler.joblib + meta.joblib + metrics.joblib
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai.model import train_from_archive  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="TensorFlow AI yon modeli egit")
    ap.add_argument("--symbols", type=int, default=400)
    ap.add_argument("--min-bars", type=int, default=300)
    ap.add_argument("--horizon", type=int, default=12)
    ap.add_argument("--atr-mult", type=float, default=1.0)
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--model", type=str, default="ai_direction")
    args = ap.parse_args()

    try:
        res = train_from_archive(
            interval="4h",
            max_symbols=args.symbols,
            min_bars=args.min_bars,
            horizon=args.horizon,
            atr_mult=args.atr_mult,
            epochs=args.epochs,
            model_name=args.model,
        )
    except RuntimeError as e:
        print(f"[AI] EGITIM ATLANDI: {e}")
        return 1

    print(f"[AI] Model egitildi: {res['model_dir']}")
    print(f"[AI] Ornek: {res['samples']} | val_loss: {res['val_loss']:.4f} | val_acc: {res['val_acc']:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
