"""AI model egitimi: yerel 4h CSV arsivinden TensorFlow yon tahmini modeli.

Kullanim (backend/ icinden):
    python scripts/train_ai.py [--symbols 400] [--min-bars 300]
        [--horizon 12] [--atr-mult 1.0] [--epochs 30] [--model ai_direction]
        [--model-type dense|lstm|ensemble]

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
    ap.add_argument(
        "--model-type",
        type=str,
        default="dense",
        choices=["dense", "lstm", "ensemble"],
        help="Model mimarisi: dense (varsayilan), lstm veya ensemble",
    )
    ap.add_argument("--lstm-seq-len", type=int, default=20,
                    help="LSTM pencere uzunlugu (yalnizca lstm/ensemble)")
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
            model_type=args.model_type,
            lstm_seq_len=args.lstm_seq_len,
        )
    except RuntimeError as e:
        print(f"[AI] EGITIM ATLANDI: {e}")
        return 1

    print(f"[AI] Model egitildi: {res['model_dir']}")
    mtype = res.get("model_type", "dense")
    vl = res.get("val_loss") if res.get("val_loss") is not None else res.get("dense", {}).get("val_loss", "?")
    va = res.get("val_acc") if res.get("val_acc") is not None else res.get("dense", {}).get("val_acc", "?")
    print(f"[AI] Tip: {mtype} | Ornek: {res['samples']} | val_loss: {vl} | val_acc: {va}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
