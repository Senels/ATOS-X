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

    async def send_daily_summary(self, trades, equity, open_positions, top_symbols=None):
        await self.send(format_daily_summary(trades, equity, open_positions, top_symbols))

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


def format_daily_summary(trades, equity, open_positions, top_symbols=None) -> str:
    """Gunluk ozet rapor metnini kurar. trades satirlari DB trades kolonlaridir."""
    closed = [t for t in trades if t[6] is not None]
    wins = sum(1 for t in closed if t[6] > 0)
    losses = sum(1 for t in closed if t[6] < 0)
    pnl = sum(t[6] for t in closed)
    win_rate = wins / len(closed) * 100 if closed else 0.0
    best = max(closed, key=lambda t: t[6]) if closed else None

    msg = "📊 <b>ATOS X Gunluk Ozet</b>\n"
    msg += f"Equity: <b>${equity:.2f}</b>\n"
    msg += f"Kapanan islem: {len(closed)} ({wins}W/{losses}L)\n"
    msg += f"Win Rate: {win_rate:.1f}%\n"
    msg += f"Gunluk PnL: <b>{'+' if pnl >= 0 else ''}{pnl:.2f}</b>\n"
    if best:
        msg += f"En iyi: {best[1]} {('+' if best[6] >= 0 else '')}{best[6]:.2f}\n"
    msg += f"Acik pozisyon: {len(open_positions) if open_positions else 0}"
    if top_symbols:
        msg += f"\nTarama: {', '.join(top_symbols[:8])}"
    return msg
