"""Toplu backtest AI kapisi simulasyonu: strateji sinyallerine AI yon tahmini
filtresini geriye donuk uygular, temiz vs AI filtreli performansi karsilastirir.

Kullanim (backend/ icinden):
    python scripts/ai_backtest.py [--symbols 60] [--min-bars 300]
        [--threshold 0.55] [--model ai_direction] [--interval 4h]
        [--strategy v23|ttp]

Cikti: tarama ozeti (sinyal/engellenen isabet oranlari, trade/win rate/net
temiz vs AI) + sembol bazli tablo. AI filtre degeri hakkinda karar vermek
icin kullanilir (ornek: engellenen sinyallerin isabet orani gecenlerden
dusukse filtre deger katiyor).
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from app.ai import model as m  # noqa: E402
from app.ai.backtest_sim import simulate, summarize_scan  # noqa: E402
from app.data import loader  # noqa: E402
from app.strategy import get_strategy  # noqa: E402
from app.strategy import settings as strat_settings  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="AI kapisi backtest simulasyonu")
    ap.add_argument("--symbols", type=int, default=60)
    ap.add_argument("--min-bars", type=int, default=300)
    ap.add_argument("--threshold", type=float, default=0.55)
    ap.add_argument("--model", type=str, default="ai_direction")
    ap.add_argument("--interval", default="4h")
    ap.add_argument("--strategy", default=None,
                    help="aktif stratejiyi gecici olarak degistir (v23/ttp); "
                         "yoksa settings'teki ayar kullanilir")
    ap.add_argument("--ttp", default="",
                    help="TTP parametre override'lari (virgulle: key=value,key=value); "
                         "ornek: tp_qty_pct=1.0,sl_trail_mode=OFF")
    ap.add_argument("--max-age", type=float, default=None,
                    help="max_position_age_hours override (saat; yoksa settings degeri)")
    ap.add_argument("--out", default="",
                    help="Sembol bazli sonuclari JSON olarak yaz (bos = yok)")
    args = ap.parse_args()

    pred = m.load_predictor(args.model)
    if pred is None:
        print(f"[AI] SIMULASYON ATLANDI: '{args.model}' modeli veya tensorflow yok")
        return 1

    strat_settings.load()
    settings = strat_settings.get_settings()
    if args.strategy:
        settings["active_strategy"] = args.strategy
    if args.ttp:
        overrides = dict(pair.split("=", 1) for pair in args.ttp.split(",") if "=" in pair)
        coerced = {}
        for k, v in overrides.items():
            try:
                coerced[k] = float(v)
            except ValueError:
                coerced[k] = v
        settings["ttp"] = {**settings.get("ttp", {}), **coerced}
        print(f"[TTP] Override: {coerced}")
    print(f"[AI] Strateji: {settings.get('active_strategy')} | "
          f"Sembol: {args.symbols} | EsiK: {args.threshold} | Model: {args.model}")
    bot = get_strategy(settings)
    analyze = getattr(bot, "analyze_full", None)
    engine_kwargs = {
        "initial_equity": settings["initial_equity"],
        "risk_per_trade": settings["risk_per_trade"],
        "fee_rate": settings["fee_rate"],
        "slippage": 0.0001,
        "max_leverage": settings["max_leverage"],
        "max_position_age_hours": float(args.max_age if args.max_age is not None
                                        else settings.get("max_position_age_hours", 0.0)),
        "vol_sizing_enabled": bool(settings.get("vol_sizing_enabled", False)),
        "vol_mult_hi": float(settings.get("vol_mult_hi", 1.5)),
        "vol_mult_lo": float(settings.get("vol_mult_lo", 0.6)),
        "vol_mult_factor": float(settings.get("vol_mult_factor", 0.5)),
    }
    horizon = int(getattr(pred, "horizon", settings.get("ai_horizon", 12)))

    t0 = time.time()
    rows, errors = [], 0
    symbols = loader.list_symbols(args.interval)[:args.symbols]
    for i, symbol in enumerate(symbols, 1):
        try:
            df = loader.load_csv(symbol, args.interval)
            if len(df) < args.min_bars:
                continue
            result = analyze(df) if analyze else bot.analyze(df)
            res = simulate(pred, df, result["orders"], args.interval,
                           threshold=args.threshold, engine_kwargs=engine_kwargs,
                           horizon=horizon)
            if res["baseline"].get("total_trades", 0) == 0 \
                    and res["with_ai"].get("total_trades", 0) == 0:
                continue
            rows.append({
                "symbol": symbol,
                "signal_stats": res["signal_stats"],
                "signals": res["signal_stats"]["signals"],
                "blocked": res["signal_stats"]["blocked"],
                "passed": res["signal_stats"]["passed"],
                "base_trades": res["baseline"].get("total_trades", 0),
                "ai_trades": res["with_ai"].get("total_trades", 0),
                "base_wins": res["baseline"].get("winning_trades", 0),
                "ai_wins": res["with_ai"].get("winning_trades", 0),
                "base_net": res["baseline"].get("net_profit", 0.0),
                "ai_net": res["with_ai"].get("net_profit", 0.0),
                "base_wr": res["baseline"].get("win_rate", 0.0),
                "ai_wr": res["with_ai"].get("win_rate", 0.0),
                "blocked_acc": res["signal_stats"]["blocked_accuracy"],
                "passed_acc": res["signal_stats"]["passed_accuracy"],
            })
        except Exception:
            errors += 1
        if i % 50 == 0:
            print(f"[{i}/{len(symbols)}] {len(rows)} basarili, {errors} hata")

    if not rows:
        print("Sonuc yok.")
        return 1

    agg = summarize_scan(rows)
    print(f"\nTaranan: {len(symbols)} | Basarili: {len(rows)} | Hata: {errors} | Sure: {time.time() - t0:.0f}s")
    print(f"Sinyal: {agg['signals']} | Engellenen: {agg['blocked']} (%{agg['blocked'] / max(agg['signals'], 1) * 100:.0f}) | Gecen: {agg['passed']}")
    print(f"Sinyal isabeti  -> engellenen: %{agg['blocked_accuracy'] * 100:.0f} | gecen: %{agg['passed_accuracy'] * 100:.0f}")
    print(f"Trade: temiz {agg['base_trades']} vs AI {agg['ai_trades']} | "
          f"win rate: %{agg['base_win_rate']:.1f} vs %{agg['ai_win_rate']:.1f} | "
          f"net: {agg['base_net']:.0f} vs {agg['ai_net']:.0f} USDT")

    df = pd.DataFrame(rows).sort_values("signals", ascending=False)
    print("\nSembol bazli (sinyal sirali, ilk 12):")
    cols = ["symbol", "signals", "blocked", "passed", "base_trades", "ai_trades",
            "base_net", "ai_net", "blocked_acc", "passed_acc"]
    print(df[cols].head(12).to_string(index=False, float_format=lambda v: f"{v:.2f}"))

    if args.out:
        payload = {
            "strategy": settings.get("active_strategy"),
            "threshold": args.threshold,
            "model": args.model,
            "interval": args.interval,
            "horizon": horizon,
            "engine": engine_kwargs,
            "results": rows,
        }
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        print(f"\nKaydedildi: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
