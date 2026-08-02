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
