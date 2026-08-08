"""Agent konseyi egitimi: analog bellek kurulumu + geri bildirim agirliklari.

1. Arsivdeki (legacy/data/futures_4h_data) sembol havuzundan analog bellek
   kurulur (`app.agents.analog.AnalogMemory.build`); artifact
   `agents/agent_params/memory/analog.npz` olarak yazilir.
2. `--skip-weights` verilmezse cozulmus oy gecmisi uzerinden ajan agirliklari
   EWMA ile guncellenir ve settings.json'a kalici yazilir.

Kullanim (workdir: backend):
    python scripts/train_agents.py --symbols 150 --max-bars 1500 --horizon 24
"""
import argparse
import sys
import time

from app.agents.analog import AnalogMemory
from app.agents.feedback import ensure_table, update_weights
from app.core.database import Database
from app.data import loader


def build_memory(symbols: list, max_bars: int, horizon: int) -> dict:
    mem = AnalogMemory(horizon=horizon, min_rows=60, k=25)
    info = mem.build(symbols, max_bars=max_bars, horizon=horizon)
    if info["rows"] == 0:
        print("UYARI: analog bellek bos — arsivde yeterli veri yok")
    else:
        print(f"Analog bellek: {info['rows']} ornek, {info['symbols']} sembol, "
              f"{info['skipped']} atlanan")
    return info


def main() -> int:
    ap = argparse.ArgumentParser(description="Agent konseyi egitimi")
    ap.add_argument("--symbols", type=int, default=150,
                    help="Bellege alinacak sembol sayisi (0 = tumu)")
    ap.add_argument("--max-bars", type=int, default=1500,
                    help="Sembol basina alinacak bar sayisi")
    ap.add_argument("--horizon", type=int, default=24,
                    help="Ileri getiri ufku (bar)")
    ap.add_argument("--skip-weights", action="store_true",
                    help="Agirlik guncellemesini atla (yalnizca bellek)")
    args = ap.parse_args()

    t0 = time.time()
    symbols = loader.list_symbols("4h")
    if args.symbols and args.symbols > 0:
        symbols = symbols[: args.symbols]
    print(f"Sembol havuzu: {len(symbols)}")
    info = build_memory(symbols, args.max_bars, args.horizon)

    if args.skip_weights or info["rows"] == 0:
        print(f"Surec tamam ({time.time() - t0:.1f}s)")
        return 0

    db = Database("atos.db")
    ensure_table(db)
    summary = update_weights(db, {}, apply=True)
    print(f"Agirlik guncellenen ajan: {len(summary['updated'])}, "
          f"devre disi: {len(summary['disabled'])}")
    for d in summary["disabled"]:
        print(f"  - {d['agent_id']}: acc={d['accuracy']} ({d['reason']})")
    print(f"Surec tamam ({time.time() - t0:.1f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
