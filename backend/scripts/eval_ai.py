"""AI model dogruluk degerlendirmesi: yerel CSV arsivinde canli cozumleme
semantigiyle (BUY: +12 bar yukselis hit; SELL: +12 bar dusus hit) genel,
yon bazli ve guncel piyasa accuracy'si olcer.

Kullanim (backend/ icinden):
    python scripts/eval_ai.py [--symbols 80] [--min-bars 120]
        [--horizon 12] [--recent-bars 200] [--model ai_direction]

Canli `/api/v1/ai/stats` dongusunun neye yakinsayacaginin hizli on-olcumu.
`--recent-bars` guncel dilim: sembol basina son N tahmin (4h'de ~1 ay = 180 bar).
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai import model as m  # noqa: E402
from app.ai.evaluate import evaluate_model, summarize  # noqa: E402
from app.data import loader  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Arsivde AI yon modeli dogrulugunu olc")
    ap.add_argument("--symbols", type=int, default=80)
    ap.add_argument("--min-bars", type=int, default=120)
    ap.add_argument("--horizon", type=int, default=12)
    ap.add_argument("--recent-bars", type=int, default=200)
    ap.add_argument("--model", type=str, default="ai_direction")
    args = ap.parse_args()

    pred = m.load_predictor(args.model)
    if pred is None:
        print(f"[AI] DEGERLENDIRME ATLANDI: '{args.model}' modeli veya tensorflow yok")
        return 1

    symbols = {}
    for sym in loader.list_symbols("4h")[:args.symbols]:
        try:
            df = loader.load_csv(sym, "4h")
        except Exception:
            continue
        if len(df) >= args.min_bars:
            symbols[sym] = df

    dfr = evaluate_model(pred.model, pred.scaler, pred.features,
                         symbols, horizon=args.horizon)
    s = summarize(dfr, recent_bars=args.recent_bars)
    print(f"[AI] Sembol: {len(symbols)} | Tahmin: {s['samples']}")
    if not s["samples"]:
        print("[AI] Tahmin yok (HOLD disi)")
        return 1
    print(f"[AI] Accuracy: {s['accuracy']:.3f} (hit {s['hits']} / miss {s['misses']})")
    for d, v in s["by_direction"].items():
        print(f"[AI] {d}: n={v['samples']} acc={v['accuracy']:.3f} "
              f"avg_conf={v['avg_confidence']:.3f}")
    print(f"[AI] Son ~{s['recent_samples']} tahmin (sembol basi son "
          f"{args.recent_bars}): acc={s['recent_accuracy']:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
