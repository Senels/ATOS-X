import asyncio
import aiohttp
from loguru import logger
from app.core.config import get_settings

class TelegramNotifier:
    def __init__(self):
        settings = get_settings()
        self.token = settings.TELEGRAM_TOKEN if hasattr(settings, "TELEGRAM_TOKEN") else None
        self.chat_id = settings.TELEGRAM_CHAT_ID if hasattr(settings, "TELEGRAM_CHAT_ID") else None
        self.enabled = bool(self.token and self.chat_id)
        self._listener_task = None

    async def send(self, message: str):
        if not self.enabled:
            logger.warning("⚠️ Telegram ayarları eksik")
            return
        try:
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json={
                    "chat_id": self.chat_id,
                    "text": message,
                    "parse_mode": "HTML"
                }) as resp:
                    if resp.status == 200:
                        logger.info(f"📨 Telegram mesajı gönderildi")
                    else:
                        logger.error(f"❌ Telegram hatası: {await resp.text()}")
        except Exception as e:
            logger.error(f"❌ Telegram gönderme hatası: {e}")

    async def send_signal(self, symbol: str, signal: str, price: float, reason: str = ""):
        emoji = {"BUY": "🟢", "SELL": "🔴", "HOLD": "🟡"}.get(signal, "⚪")
        msg = f"{emoji} <b>ATOS X Sinyal</b>\n"
        msg += f"Symbol: {symbol}\n"
        msg += f"Signal: <b>{signal}</b>\n"
        msg += f"Price: ${price:.2f}\n"
        if reason:
            msg += f"Reason: {reason}\n"
        await self.send(msg)

    async def send_trade(self, symbol: str, side: str, price: float, quantity: float, status: str):
        emoji = "✅" if status == "ok" else "❌"
        msg = f"{emoji} <b>Trade İşlemi</b>\n"
        msg += f"Symbol: {symbol}\n"
        msg += f"Side: <b>{side}</b>\n"
        msg += f"Price: ${price:.2f}\n"
        msg += f"Quantity: {quantity}\n"
        msg += f"Status: {status}"
        await self.send(msg)

    async def send_performance(self, metrics: dict):
        msg = f"📊 <b>Performans Raporu</b>\n"
        msg += f"Equity: ${metrics.get('equity', 0):.2f}\n"
        msg += f"Trades: {metrics.get('total_trades', 0)}\n"
        msg += f"Win Rate: {metrics.get('win_rate', 0)*100:.1f}%\n"
        msg += f"Status: {metrics.get('status', 'unknown')}"
        await self.send(msg)

    async def send_daily_summary(self, trades, equity, open_positions, top_symbols=None,
                                 marks=None, risk_events=None, loss_halted=False,
                                 daily_loss_halted=False, equity_halted=False,
                                 day_pnl=None, data_status=None, protection_stats=None):
        await self.send(format_daily_summary(
            trades, equity, open_positions, top_symbols, marks, risk_events,
            loss_halted, daily_loss_halted, equity_halted, day_pnl, data_status,
            protection_stats,
        ))

    async def send_stop_summary(self, closed):
        await self.send(format_stop_summary(closed))

    def start_listener(self, handler):
        """Arka planda komut mesajlarini dinler; task doner (disabled ise None)."""
        if not self.enabled:
            logger.warning("Telegram dinleyicisi devre disi (ayarlar eksik)")
            return None
        if self._listener_task and not self._listener_task.done():
            return self._listener_task
        self._listener_task = asyncio.create_task(self._listen_loop(handler))
        return self._listener_task

    def stop_listener(self):
        task = self._listener_task
        self._listener_task = None
        if task:
            task.cancel()

    async def _fetch_updates(self, offset: int) -> list:
        url = f"https://api.telegram.org/bot{self.token}/getUpdates"
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json={
                "offset": offset, "timeout": 25, "allowed_updates": ["message"],
            }) as resp:
                if resp.status != 200:
                    logger.error(f"Telegram getUpdates hatasi: {await resp.text()}")
                    return []
                data = await resp.json()
                return data.get("result", [])

    async def _listen_loop(self, handler):
        offset = 0
        while True:
            try:
                updates = await self._fetch_updates(offset)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Telegram dinleyici hatasi: {e}")
                await asyncio.sleep(15)
                continue
            new_offset, replies = _process_updates(updates, handler)
            if new_offset:
                offset = new_offset
            for reply in replies:
                await self.send(reply)
            await asyncio.sleep(1)


def _process_updates(updates: list, handler) -> tuple:
    """Gelen Telegram update'lerini isler; (son offset, yanitlar) dondurur."""
    offset = 0
    replies = []
    for u in updates:
        offset = u["update_id"] + 1
        text = (u.get("message") or {}).get("text", "")
        if not text:
            continue
        reply = handler(text)
        if reply:
            replies.append(reply)
    return offset, replies


def format_daily_summary(trades, equity, open_positions, top_symbols=None,
                         marks=None, risk_events=None, loss_halted=False,
                         daily_loss_halted=False, equity_halted=False,
                         day_pnl=None, data_status=None, protection_stats=None) -> str:
    """Gunluk ozet rapor metnini kurar. trades satirlari DB trades kolonlaridir."""
    closed = [t for t in trades if t[6] is not None]
    wins = sum(1 for t in closed if t[6] > 0)
    losses = sum(1 for t in closed if t[6] < 0)
    pnl = sum(t[6] for t in closed)
    win_rate = wins / len(closed) * 100 if closed else 0.0
    best = max(closed, key=lambda t: t[6]) if closed else None
    gross_w = sum(t[6] for t in closed if t[6] > 0)
    gross_l = abs(sum(t[6] for t in closed if t[6] < 0))
    pf = gross_w / gross_l if gross_l > 0 else (float("inf") if gross_w > 0 else 0.0)
    by_sym = {}
    for t in closed:
        by_sym[t[1]] = by_sym.get(t[1], 0.0) + t[6]
    best_sym = max(by_sym.items(), key=lambda kv: kv[1]) if by_sym else None

    upnl = 0.0
    if open_positions and marks:
        for sym, pos in open_positions.items():
            mark = marks.get(sym)
            if mark is None:
                continue
            if pos.get("side") == "BUY":
                upnl += (mark - pos["entry_price"]) * pos["quantity"]
            else:
                upnl += (pos["entry_price"] - mark) * pos["quantity"]

    msg = "📊 <b>ATOS X Gunluk Ozet</b>\n"
    msg += f"Equity: <b>${equity:.2f}</b>\n"
    msg += f"Kapanan islem: {len(closed)} ({wins}W/{losses}L)\n"
    msg += f"Win Rate: {win_rate:.1f}%\n"
    pf_str = "inf" if pf == float("inf") else f"{pf:.2f}"
    msg += f"Profit Factor: {pf_str}\n"
    msg += f"Gunluk PnL: <b>{'+' if pnl >= 0 else ''}{pnl:.2f}</b>\n"
    if day_pnl is not None:
        msg += f"Gunluk net (kapanan): {'+' if day_pnl >= 0 else ''}{day_pnl:.2f}\n"
    if open_positions and marks:
        msg += f"Gerceklesmemis PnL: <b>{'+' if upnl >= 0 else ''}{upnl:.2f}</b>\n"
    if best:
        msg += f"En iyi: {best[1]} {('+' if best[6] >= 0 else '')}{best[6]:.2f}\n"
    if best_sym:
        msg += f"En iyi sembol: {best_sym[0]} {'+' if best_sym[1] >= 0 else ''}{best_sym[1]:.2f}\n"
    msg += f"Acik pozisyon: {len(open_positions) if open_positions else 0}"
    if top_symbols:
        msg += f"\nTarama: {', '.join(top_symbols[:8])}"
    if data_status and data_status.get("ok"):
        msg += (f"\nVeri: {data_status['fresh']} guncel / "
                f"{data_status['stale']} eski / {data_status['missing']} eksik")
    if protection_stats:
        parts = []
        if protection_stats.get("trailing"):
            parts.append(f"Trailing: {protection_stats['trailing']}")
        if protection_stats.get("breakeven"):
            parts.append(f"Breakeven: {protection_stats['breakeven']}")
        if parts:
            msg += "\nKoruma: " + " | ".join(parts)
    halts = []
    if loss_halted:
        halts.append("ARDISIK ZARAR")
    if daily_loss_halted:
        halts.append("GUNLUK ZARAR")
    if equity_halted:
        halts.append("EQUITY TABAN")
    if halts:
        msg += f"\nDurmalar: {', '.join(halts)}"
    if risk_events:
        last_evt = risk_events[-1]
        evt_line = f"{last_evt['type']} ({last_evt['time'][:16].replace('T', ' ')})"
        msg += f"\nRisk olayi: {len(risk_events)} (son: {evt_line})"
    return msg


def format_stop_summary(closed: list) -> str:
    """Durdurma sonrasi kapanan pozisyonlarin ozetini kurar.

    `closed` trade_history formatindadir (symbol, side, entry, exit, qty,
    pnl, reason, trailing, breakeven, time).
    """
    pnl = sum(t.get("pnl", 0.0) for t in closed)
    wins = sum(1 for t in closed if t.get("pnl", 0) > 0)
    losses = sum(1 for t in closed if t.get("pnl", 0) < 0)

    msg = "🛑 <b>ATOS X Durdurma Ozeti</b>\n"
    msg += f"Kapanan pozisyon: {len(closed)}\n"
    msg += f"Kar: {wins} / Zarar: {losses}\n"
    msg += f"Gerceklesen PnL: <b>{'+' if pnl >= 0 else ''}{pnl:.2f}</b>"
    if closed:
        best = max(closed, key=lambda t: t.get("pnl", 0.0))
        worst = min(closed, key=lambda t: t.get("pnl", 0.0))
        msg += f"\nEn iyi: {best['symbol']} ({'+' if best['pnl'] >= 0 else ''}{best['pnl']:.2f})"
        msg += f"\nEn kotu: {worst['symbol']} ({'+' if worst['pnl'] >= 0 else ''}{worst['pnl']:.2f})"
    return msg
