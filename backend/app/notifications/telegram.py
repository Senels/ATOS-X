import asyncio

import aiohttp
from loguru import logger

from app.core.config import get_settings
from app.core.security import is_authorized_chat, parse_chat_ids


class TelegramNotifier:
    def __init__(self):
        settings = get_settings()
        self.token = settings.TELEGRAM_TOKEN if hasattr(settings, "TELEGRAM_TOKEN") else None
        self.chat_id = settings.TELEGRAM_CHAT_ID if hasattr(settings, "TELEGRAM_CHAT_ID") else None
        self.allowed_chat_ids = parse_chat_ids(
            settings.TELEGRAM_ALLOWED_CHAT_IDS if hasattr(settings, "TELEGRAM_ALLOWED_CHAT_IDS") else "")
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
                        logger.info("📨 Telegram mesajı gönderildi")
                    else:
                        logger.error(f"❌ Telegram hatası: {await resp.text()}")
        except Exception as e:
            logger.error(f"❌ Telegram gönderme hatası: {e}")

    async def send_signal(self, symbol: str, signal: str, price: float, reason: str = "",
                          sl: float = None, tp: float = None, strength: float = None):
        emoji = {"BUY": "🟢", "SELL": "🔴", "HOLD": "🟡"}.get(signal, "⚪")
        msg = f"{emoji} <b>ATOS X Sinyal</b>\n"
        msg += f"Symbol: {symbol}\n"
        msg += f"Signal: <b>{signal}</b>\n"
        if strength is not None:
            msg += f"Guc: %{strength * 100:.0f}\n"
        msg += f"Price: ${price:.2f}\n"
        if sl is not None:
            msg += f"SL: ${sl:g}"
            if tp is not None:
                risk = abs(price - sl)
                reward = abs(tp - price)
                rr = reward / risk if risk > 0 else 0.0
                msg += f" | TP: ${tp:g} | R:R {rr:.1f}"
            msg += "\n"
        elif tp is not None:
            msg += f"TP: ${tp:g}\n"
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

    async def send_daily_summary(self, trades, equity, open_positions, top_symbols=None,
                                 marks=None, risk_events=None, loss_halted=False,
                                 daily_loss_halted=False, equity_halted=False,
                                 day_pnl=None, drawdown_pct=None, worst_sym=None,
                                 data_status=None, protection_stats=None, days=1):
        await self.send(format_daily_summary(
            trades, equity, open_positions, top_symbols, marks, risk_events,
            loss_halted, daily_loss_halted, equity_halted, day_pnl,
            drawdown_pct, worst_sym, data_status, protection_stats, days,
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
            new_offset, replies = _process_updates(updates, handler, self.allowed_chat_ids)
            if new_offset:
                offset = new_offset
            for reply in replies:
                await self.send(reply)
            await asyncio.sleep(1)


def _process_updates(updates: list, handler, allowed_chat_ids=None) -> tuple:
    """Gelen Telegram update'lerini isler; (son offset, yanitlar) dondurur.

    `allowed_chat_ids` bos/None ise filtre uygulanmaz; dolu ise yalnizca
    whitelist'teki sohbetlerin mesajlari handler'a iletilir.
    """
    offset = 0
    replies = []
    for u in updates:
        offset = u["update_id"] + 1
        msg = u.get("message") or {}
        text = msg.get("text", "")
        if not text:
            continue
        if allowed_chat_ids:
            chat_id = (msg.get("chat") or {}).get("id")
            if not is_authorized_chat(chat_id, allowed_chat_ids):
                logger.warning(f"Yetkisiz Telegram sohbeti engellendi: chat_id={chat_id}")
                continue
        reply = handler(text)
        if reply:
            replies.append(reply)
    return offset, replies


def format_daily_summary(trades, equity, open_positions, top_symbols=None,
                         marks=None, risk_events=None, loss_halted=False,
                         daily_loss_halted=False, equity_halted=False,
                         day_pnl=None, drawdown_pct=None, worst_sym=None,
                         data_status=None, protection_stats=None, days=1) -> str:
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

    period = f"Son {days} Gun" if days > 1 else "Gunluk"
    msg = f"📊 <b>ATOS X {period} Ozet</b>\n"
    msg += f"Equity: <b>${equity:.2f}</b>\n"
    msg += f"Kapanan islem: {len(closed)} ({wins}W/{losses}L)\n"
    msg += f"Win Rate: {win_rate:.1f}%\n"
    pf_str = "inf" if pf == float("inf") else f"{pf:.2f}"
    msg += f"Profit Factor: {pf_str}\n"
    msg += f"Toplam PnL: <b>{'+' if pnl >= 0 else ''}{pnl:.2f}</b>\n"
    if day_pnl is not None:
        msg += f"Gunluk net (kapanan): {'+' if day_pnl >= 0 else ''}{day_pnl:.2f}\n"
    if open_positions and marks:
        msg += f"Gerceklesmemis PnL: <b>{'+' if upnl >= 0 else ''}{upnl:.2f}</b>\n"
    if best:
        msg += f"En iyi: {best[1]} {('+' if best[6] >= 0 else '')}{best[6]:.2f}\n"
    if best_sym:
        msg += f"En iyi sembol: {best_sym[0]} {'+' if best_sym[1] >= 0 else ''}{best_sym[1]:.2f}\n"
    if worst_sym and worst_sym[1] < 0:
        msg += f"En kotu sembol: {worst_sym[0]} {worst_sym[1]:.2f}\n"
    if by_sym:
        top_syms = sorted(by_sym.items(), key=lambda kv: kv[1], reverse=True)[:5]
        msg += "Semboller: " + ", ".join(
            f"{s} {'+' if p >= 0 else ''}{p:.2f}" for s, p in top_syms) + "\n"
    if drawdown_pct is not None and drawdown_pct > 0:
        msg += f"Drawdown: %{drawdown_pct:.1f}\n"
    if risk_events:
        msg += f"Risk olayi: {len(risk_events)}\n"
    msg += f"Acik pozisyon: {len(open_positions) if open_positions else 0}"
    if top_symbols:
        msg += f"\nTarama: {', '.join(top_symbols[:8])}"
    if data_status and data_status.get("ok"):
        msg += (f"\nVeri: {data_status['fresh']} guncel / "
                f"{data_status['stale']} eski / {data_status['missing']} eksik")
    if protection_stats:
        parts = []
        if protection_stats.get("trailing"):
            t_pnl = protection_stats.get("trailing_pnl")
            parts.append(f"Trailing: {protection_stats['trailing']}"
                         + (f" ({'+' if t_pnl >= 0 else ''}{t_pnl:.2f})" if t_pnl is not None else ""))
        if protection_stats.get("breakeven"):
            b_pnl = protection_stats.get("breakeven_pnl")
            parts.append(f"Breakeven: {protection_stats['breakeven']}"
                         + (f" ({'+' if b_pnl >= 0 else ''}{b_pnl:.2f})" if b_pnl is not None else ""))
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
