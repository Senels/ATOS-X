"""Agent konseyi analog bellegi icin uzun gecmis (5 yil) 4h veri geri doldu.

Binance public kline uzerinden `--days` kadar geriye parcalar halinde cekilir
ve `legacy/data/futures_4h_data/` arsivine yazilir. Canli sembol listesinden
(`--symbols` adet) seçilir; stablecoin'ler atlanir. Sonrasinda
`python scripts/train_agents.py` ile bellek kurulmalidir.

Kullanim (workdir: backend):
    python scripts/backfill_agents.py --symbols 150 --days 1825
"""
import argparse
import asyncio
import sys
import time

from app.data.collector import backfill
from app.exchange.binance_client import BinanceClient


async def run(symbols: list, days: int) -> int:
    client = BinanceClient()
    t0 = time.time()
    print(f"Backfill basladi: {len(symbols)} sembol x {days} gun (4h)")
    res = await backfill(client, symbols, interval="4h", days=days,
                         skip_stablecoins=True)
    elapsed = time.time() - t0
    print(f"Yazilan: {len(res['written'])}, hatali: {len(res['failed'])} "
          f"({elapsed:.0f}s)")
    if res["failed"]:
        print("Hatali semboller: " + ", ".join(res["failed"][:10]))
    return 0 if not res["failed"] else 1


async def _load_symbols(client) -> list:
    return await client.load_all_symbols()


def main() -> int:
    ap = argparse.ArgumentParser(description="Agent bellegi icin 5y backfill")
    ap.add_argument("--symbols", type=int, default=150)
    ap.add_argument("--days", type=int, default=1825,
                    help="Geriye cekilecek gun sayisi (1825 = 5 yil)")
    ap.add_argument("--symbol", type=str, default=None,
                    help="Tek sembol backfill (liste yerine)")
    args = ap.parse_args()

    from app.data.loader import list_symbols

    if args.symbol:
        symbols = [args.symbol.upper()]
    else:
        try:
            client_probe = BinanceClient()
            symbols = asyncio.run(_load_symbols(client_probe))
        except Exception as e:
            print(f"Sembol listesi cekilemedi: {e}")
            symbols = list_symbols("4h")
        if args.symbols and args.symbols > 0:
            symbols = symbols[: args.symbols]
    print(f"Sembol havuzu: {len(symbols)}")
    return asyncio.run(run(symbols, args.days))


if __name__ == "__main__":
    sys.exit(main())
