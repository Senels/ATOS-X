import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from pathlib import Path
from loguru import logger

from app.core.config import get_settings
from app.core.database import Database
from app.exchange.binance_client import BinanceClient
from app.strategy.auto_trader import AutoTrader
from app.strategy import settings as strat_settings
from app.strategy.tradebot_v23 import TradeBotV23
from app.api.backtest import router as backtest_router
from app.api.optimization import router as optimize_router
from app.websocket.client import BinanceWebSocket
from app.notifications.telegram import TelegramNotifier

load_dotenv()
settings = get_settings()
_APP_DIR = Path(__file__).resolve().parent

ws = BinanceWebSocket()
telegram = TelegramNotifier()
auto_trader = None
daily_report_task = None
system_status = {"status": "initializing", "start_time": datetime.utcnow()}

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    global auto_trader, daily_report_task
    system_status["status"] = "starting"
    strat_settings.load()

    app.state.binance = BinanceClient()
    for attempt in range(1, 6):
        if await app.state.binance.connect():
            break
        logger.warning(f"Binance baglanti denemesi {attempt}/5 basarisiz, 10s sonra tekrar")
        await asyncio.sleep(10)
    else:
        system_status["status"] = "degraded"
        logger.error("Binance baglantisi kurulamadi; AutoTrader yeniden denemeye devam edecek")
        await telegram.send("ATOS X: Binance baglantisi kurulamadi, degrade modda basladi")
    app.state.db = Database()

    auto_trader = AutoTrader(app.state.binance, telegram=telegram)
    app.state.auto_trader = auto_trader
    asyncio.create_task(auto_trader.start())

    app.state.ws = ws
    symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "ADAUSDT"]
    for symbol in symbols:
        ws.subscribe(symbol, on_price_update)
    await ws.start(symbols)

    if system_status["status"] != "degraded":
        system_status["status"] = "online"
    daily_report_task = asyncio.create_task(_daily_report_loop())
    ws_sync_task = asyncio.create_task(_ws_sync_loop())
    telegram.start_listener(_telegram_command)
    await telegram.send(f"ATOS X v{settings.APP_VERSION} baslatildi!")

    yield
    system_status["status"] = "shutting_down"
    if daily_report_task:
        daily_report_task.cancel()
    ws_sync_task.cancel()
    telegram.stop_listener()
    await auto_trader.stop()
    await ws.stop()
    await app.state.binance.close()

async def on_price_update(symbol: str, price: float):
    if auto_trader:
        auto_trader.update_price(symbol, price)

async def _signal_for_symbol(symbol: str, interval: str = "4h") -> dict:
    """Canli kline'dan v23 sinyali hesaplar; hata durumunda bos dict doner."""
    client = getattr(app.state, "binance", None)
    if client is None:
        return {}
    try:
        df = await client.get_klines(symbol, interval, 400)
        return TradeBotV23(strat_settings.get_settings()).generate_signal(df)
    except Exception as e:
        logger.error(f"Sinyal hesap hatasi {symbol}: {e}")
        return {}

async def _send_symbol_signal(symbol: str, interval: str = "4h"):
    """Bir sembolun sinyalini Telegram'a gonderir."""
    sig = await _signal_for_symbol(symbol, interval)
    signal = sig.get("signal")
    if not signal:
        await telegram.send(f"ATOS X: {symbol} icin sinyal alinamadi")
        return
    await telegram.send_signal(symbol, signal, sig.get("price") or 0.0, sig.get("reason", ""))

async def _set_sl(symbol: str, new_sl: float):
    """Pozisyonun SL'sini gunceller ve sonucu Telegram'a bildirir."""
    res = await auto_trader.update_sl(symbol, new_sl)
    if res.get("ok"):
        await telegram.send(
            f"ATOS X: {symbol} SL guncellendi ${res['old_sl']} -> ${res['new_sl']}"
        )
        return
    msg = {
        "position_not_found": f"ATOS X: {symbol} icin acik pozisyon yok",
        "sl_above_entry": f"ATOS X: {symbol} BUY pozisyonunda SL giris fiyatinin ALTINDA olmali",
        "sl_below_entry": f"ATOS X: {symbol} SELL pozisyonunda SL giris fiyatinin USTUNDE olmali",
    }.get(res.get("error"), f"ATOS X: SL guncellenemedi ({res.get('error')})")
    await telegram.send(msg)

async def _set_tp(symbol: str, new_tp: float):
    """Pozisyonun TP'sini gunceller ve sonucu Telegram'a bildirir."""
    res = await auto_trader.update_tp(symbol, new_tp)
    if res.get("ok"):
        await telegram.send(
            f"ATOS X: {symbol} TP guncellendi ${res['old_tp']} -> ${res['new_tp']}"
        )
        return
    msg = {
        "position_not_found": f"ATOS X: {symbol} icin acik pozisyon yok",
        "tp_below_entry": f"ATOS X: {symbol} BUY pozisyonunda TP giris fiyatinin USTUNDE olmali",
        "tp_above_entry": f"ATOS X: {symbol} SELL pozisyonunda TP giris fiyatinin ALTINDA olmali",
    }.get(res.get("error"), f"ATOS X: TP guncellenemedi ({res.get('error')})")
    await telegram.send(msg)

async def _ws_sync_loop():
    """WebSocket fiyat aboneliklerini tarama listesine (top_symbols) gore hizalar."""
    while True:
        await asyncio.sleep(60)
        try:
            if auto_trader:
                target = auto_trader.top_symbols or []
                if target:
                    await ws.sync(target, on_price_update)
        except Exception as e:
            logger.error(f"WebSocket sembol senkronizasyonu hatasi: {e}")

def _protected_count() -> int:
    """Exchange-side SL/TP ile korunan acik pozisyon sayisi."""
    if not auto_trader:
        return 0
    return sum(
        1 for p in auto_trader.active_positions.values()
        if p.get("sl_order_id") or p.get("tp_order_id")
    )

def _position_upnl(pos: dict, mark: float | None):
    """Pozisyonun gerceklesmemis PnL'i ve yuzdesi (fiyat yoksa None)."""
    if mark is None:
        return None, None
    if pos["side"] == "BUY":
        upnl = (mark - pos["entry_price"]) * pos["quantity"]
    else:
        upnl = (pos["entry_price"] - mark) * pos["quantity"]
    notional = pos["entry_price"] * pos["quantity"]
    pct = (upnl / notional * 100.0) if notional else 0.0
    return upnl, pct

def _positions_payload() -> dict:
    """Acil pozisyonlari koruma durumu ve gerceklesmemis PnL isaretli doner."""
    positions = auto_trader.active_positions if auto_trader else {}
    enriched = {}
    for symbol, pos in positions.items():
        mark = auto_trader.live_prices.get(symbol) if auto_trader else None
        upnl, pct = _position_upnl(pos, mark)
        enriched[symbol] = {**pos,
                            "protected": bool(pos.get("sl_order_id") or pos.get("tp_order_id")),
                            "trailing": bool(pos.get("trailing")),
                            "breakeven": bool(pos.get("breakeven")),
                            "mark": mark,
                            "upnl": upnl,
                            "upnl_pct": pct}
    return {
        "positions": enriched,
        "count": len(positions),
        "protected": sum(1 for p in enriched.values() if p["protected"]),
        "unprotected": sum(1 for p in enriched.values() if not p["protected"]),
        "total_upnl": sum(p["upnl"] for p in enriched.values() if p["upnl"] is not None),
    }

def _run_later(coro) -> bool:
    """Calisan event loop varsa coroutine'i arka planda calistirir."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    asyncio.create_task(coro)
    return True

# /koruma komutu ile canli ayarlanabilen risk anahtarlari (takma ad -> settings key)
_EDITABLE_RISK_KEYS = {
    "max_positions": "max_open_positions",
    "max_drawdown_pct": "max_drawdown_pct",
    "max_consecutive_losses": "max_consecutive_losses",
    "max_daily_loss_pct": "max_daily_loss_pct",
    "min_equity": "min_equity",
    "risk_per_trade": "risk_per_trade",
    "max_position_pct": "max_position_pct",
    "max_side_pct": "max_side_pct",
    "trailing_activate_pct": "trailing_activate_pct",
    "trailing_sl_pct": "trailing_sl_pct",
    "trailing_min_move_pct": "trailing_min_move_pct",
    "breakeven_activate_pct": "breakeven_activate_pct",
    "max_position_age_hours": "max_position_age_hours",
    "max_leverage": "max_leverage",
}
_INT_RISK_KEYS = {"max_open_positions", "max_consecutive_losses", "max_position_age_hours"}


def _format_koruma() -> str:
    s = strat_settings.get_settings()
    return (
        "ATOS X risk ayarlari:\n"
        f"Max pozisyon: {s['max_open_positions']}\n"
        f"Risk/trade: %{s['risk_per_trade'] * 100:.1f} | Kaldirac: {s['max_leverage']}\n"
        f"Max drawdown: %{s['max_drawdown_pct']:.0f}\n"
        f"Max ardisik zarar: {s['max_consecutive_losses']}\n"
        f"Gunluk zarar: %{s['max_daily_loss_pct']:.0f} | Equity taban: ${s['min_equity']:.0f}\n"
        f"Tek sembol: %{s['max_position_pct']:.0f} | Tek yon: %{s['max_side_pct']:.0f}\n"
        f"Trailing: kar %{s['trailing_activate_pct']:.0f}+ / SL %{s['trailing_sl_pct']:.1f}\n"
        f"Breakeven: %{s['breakeven_activate_pct']:.0f} | Pozisyon yasi: {s['max_position_age_hours']} saat\n"
        "Ayarlamak icin: /koruma <anahtar> <deger> "
        "(anahtarlar: " + ", ".join(sorted(_EDITABLE_RISK_KEYS)) + ")"
    )

def _telegram_command(text: str):
    """Telegram komutlarini yanitlar; bilinmeyen komutlar None doner."""
    cmd = text.strip().lower()
    if not cmd.startswith("/"):
        return None
    if cmd.startswith("/yardim") or cmd.startswith("/help"):
        return ("ATOS X komutlari:\n"
                "/durum - sistem durumu\n"
                "/blok - aktif engeller\n"
                "/pozisyon - acik pozisyonlar\n"
                "/kapat <SEMBOL> - tek pozisyonu kapatir\n"
                "/sl <SEMBOL> <FIYAT> - acik pozisyonun SL'sini gunceller\n"
                "/tp <SEMBOL> <FIYAT> - acik pozisyonun TP'sini gunceller\n"
                "/durdur - acil durdurma (tum pozisyonlari kapatir)\n"
                "/kapatall - acik tum pozisyonlari kapatir\n"
                "/sinyal <SEMBOL> - sembol icin canli sinyal gonder\n"
                "/koruma [ANAHTAR] [DEGER] - risk ayarlarini gor/degistir\n"
                "/ac - motoru yeniden baslatir\n"
                "/rapor - gunluk rapor gonder\n"
                "/risk - risk durumu\n"
                "/gecmis [N] - son N islem\n"
                "/yardim - bu liste")
    if cmd.startswith("/blok"):
        blocks = sorted(auto_trader._conc_blocks) if auto_trader else []
        return f"ATOS X aktif engeller: {', '.join(blocks) if blocks else 'yok'}"
    if cmd.startswith("/pozisyon"):
        if not auto_trader:
            return "ATOS X: motor calismiyor"
        if not auto_trader.active_positions:
            return "ATOS X: acik pozisyon yok"
        lines = ["ATOS X pozisyonlar:"]
        for sym, pos in auto_trader.active_positions.items():
            prot = "korumali" if (pos.get("sl_order_id") or pos.get("tp_order_id")) else "KORUMASIZ"
            if pos.get("trailing"):
                prot += " + TRAILING"
            if pos.get("breakeven"):
                prot += " + BREAKEVEN"
            mark = auto_trader.live_prices.get(sym)
            upnl, pct = _position_upnl(pos, mark)
            line = f"{sym} {pos['side']} qty={pos['quantity']} @ ${pos['entry_price']} {prot}"
            if upnl is not None:
                sign = "+" if upnl >= 0 else ""
                line += f" | PnL: {sign}{upnl:.2f} ({sign}{pct:.2f}%)"
            lines.append(line)
        return "\n".join(lines)
    if cmd.startswith("/koruma") or cmd.startswith("/ayar"):
        parts = text.strip().split()
        if len(parts) == 1:
            return _format_koruma()
        if len(parts) != 3:
            return "ATOS X: kullanim /koruma <anahtar> <deger> (anahtarlar icin /koruma yaz)"
        key = parts[1].lower()
        if key not in _EDITABLE_RISK_KEYS:
            return (f"ATOS X: bilinmeyen anahtar '{key}'. "
                    "Mevcut anahtarlar: " + ", ".join(sorted(_EDITABLE_RISK_KEYS)))
        try:
            value = float(parts[2])
        except ValueError:
            return "ATOS X: gecersiz deger"
        settings_key = _EDITABLE_RISK_KEYS[key]
        if settings_key in _INT_RISK_KEYS:
            value = int(value)
        strat_settings.update_settings({settings_key: value})
        strat_settings.persist()
        if auto_trader:
            auto_trader._apply_risk_settings(strat_settings.get_settings())
        return f"ATOS X: {settings_key} = {value} olarak ayarlandi (kalici)"
    if cmd.startswith("/kapatall"):
        if not auto_trader:
            return "ATOS X: motor calismiyor"
        if not auto_trader.active_positions:
            return "ATOS X: kapatilacak pozisyon yok"
        if not _run_later(auto_trader.close_all("manual_close_all")):
            return "ATOS X: komut arka planda calistirilamadi"
        return f"ATOS X: {len(auto_trader.active_positions)} pozisyon kapatiliyor"
    if cmd.startswith("/kapat"):
        parts = text.strip().split()
        if not auto_trader:
            return "ATOS X: motor calismiyor"
        if len(parts) != 2:
            return "ATOS X: kullanim /kapat <SEMBOL> (orn. /kapat BTCUSDT)"
        sym = parts[1].upper()
        if sym not in auto_trader.active_positions:
            return f"ATOS X: {sym} icin acik pozisyon yok"
        price = auto_trader.live_prices.get(sym)
        if price is None:
            return f"ATOS X: {sym} guncel fiyati bulunamadi, kapatma iptal"
        if not _run_later(auto_trader.close_position(sym, price, "manual_close")):
            return "ATOS X: komut arka planda calistirilamadi"
        return f"ATOS X: {sym} kapatiliyor (${price})"
    if cmd.startswith("/sl"):
        parts = text.strip().split()
        if not auto_trader:
            return "ATOS X: motor calismiyor"
        if len(parts) != 3:
            return "ATOS X: kullanim /sl <SEMBOL> <FIYAT> (orn. /sl BTCUSDT 64000)"
        sym = parts[1].upper()
        try:
            new_sl = float(parts[2])
        except ValueError:
            return "ATOS X: gecersiz SL fiyati"
        if sym not in auto_trader.active_positions:
            return f"ATOS X: {sym} icin acik pozisyon yok"
        if not _run_later(_set_sl(sym, new_sl)):
            return "ATOS X: komut arka planda calistirilamadi"
        return f"ATOS X: {sym} SL guncelleniyor -> ${new_sl}"
    if cmd.startswith("/tp"):
        parts = text.strip().split()
        if not auto_trader:
            return "ATOS X: motor calismiyor"
        if len(parts) != 3:
            return "ATOS X: kullanim /tp <SEMBOL> <FIYAT> (orn. /tp BTCUSDT 69000)"
        sym = parts[1].upper()
        try:
            new_tp = float(parts[2])
        except ValueError:
            return "ATOS X: gecersiz TP fiyati"
        if sym not in auto_trader.active_positions:
            return f"ATOS X: {sym} icin acik pozisyon yok"
        if not _run_later(_set_tp(sym, new_tp)):
            return "ATOS X: komut arka planda calistirilamadi"
        return f"ATOS X: {sym} TP guncelleniyor -> ${new_tp}"
    if cmd.startswith("/sinyal") or cmd.startswith("/signal"):
        parts = text.strip().split()
        if not auto_trader:
            return "ATOS X: motor calismiyor"
        if len(parts) < 2:
            return "ATOS X: kullanim /sinyal <SEMBOL> (orn. /sinyal BTCUSDT)"
        sym = parts[1].upper()
        if not _run_later(_send_symbol_signal(sym)):
            return "ATOS X: komut arka planda calistirilamadi"
        return f"ATOS X: {sym} sinyali hesaplaniyor"
    if cmd.startswith("/durdur") or cmd.startswith("/stop"):
        if not auto_trader or not auto_trader.running:
            return "ATOS X: motor zaten durdurulmus"
        if not _run_later(auto_trader.stop()):
            return "ATOS X: komut arka planda calistirilamadi"
        return "ATOS X: DURDURULDU - tum pozisyonlar kapatiliyor, yeni girisler kapali"
    if cmd.startswith("/ac") or cmd.startswith("/resume"):
        if not auto_trader:
            return "ATOS X: motor hazir degil"
        if auto_trader.running:
            return "ATOS X: motor zaten calisiyor"
        if not _run_later(auto_trader.start()):
            return "ATOS X: komut arka planda calistirilamadi"
        return "ATOS X: motor yeniden baslatiliyor"
    if cmd.startswith("/durum") or cmd.startswith("/status"):
        if not auto_trader:
            return "ATOS X: motor calismiyor"
        conc = _concentration_summary()
        blocks = conc["blocks"]
        halt = auto_trader.risk_halted
        halt_line = "AKTIF - girisler durduruldu" if halt else "yok"
        trading = "DURDURULDU" if not auto_trader.running else "calisiyor"
        events = auto_trader.risk_events
        last_evt = events[-1] if events else None
        evt_line = f"{last_evt['type']} ({last_evt['time'][:16].replace('T',' ')})" if last_evt else "yok"
        loss_line = "AKTIF" if auto_trader.loss_halted else "yok"
        daily_line = "AKTIF" if auto_trader.daily_loss_halted else "yok"
        eq_line = "AKTIF" if auto_trader.equity_halted else "yok"
        return (
            f"ATOS X durum\n"
            f"Trade motoru: {trading}\n"
            f"Equity: ${auto_trader.equity:.2f}\n"
            f"Acik pozisyon: {len(auto_trader.active_positions)} (korumali: {_protected_count()})\n"
            f"Gerceklesmemis PnL: {_positions_payload()['total_upnl']:.2f}\n"
            f"Maruziyet - LONG: %{conc['long_pct']} SHORT: %{conc['short_pct']}\n"
            f"Engeller: {', '.join(blocks) if blocks else 'yok'}\n"
            f"Drawdown: %{auto_trader.drawdown_pct} | Durma: {halt_line}\n"
            f"Ardisik zarar: {auto_trader.consecutive_losses}/{auto_trader.max_consecutive_losses} | Zarar durma: {loss_line}\n"
            f"Gunluk zarar: {auto_trader.day_pnl:.2f} USDT | Gunluk durma: {daily_line}\n"
            f"Equity taban: ${auto_trader.min_equity:.0f} | Taban durma: {eq_line}\n"
            f"Risk olayi: {len(events)} (son: {evt_line})"
        )
    if cmd.startswith("/rapor") or cmd.startswith("/report"):
        if not auto_trader:
            return "ATOS X: motor calismiyor"
        trades = auto_trader.db.get_closed_trades_since(days=1)
        if not _run_later(telegram.send_daily_summary(
                trades, auto_trader.equity, auto_trader.active_positions,
                auto_trader.top_symbols, marks=auto_trader.live_prices,
                risk_events=auto_trader.risk_events,
                loss_halted=auto_trader.loss_halted,
                daily_loss_halted=auto_trader.daily_loss_halted,
                equity_halted=auto_trader.equity_halted,
                day_pnl=auto_trader.day_pnl)):
            return "ATOS X: komut arka planda calistirilamadi"
        return "ATOS X: gunluk rapor gonderiliyor"
    if cmd.startswith("/risk"):
        if not auto_trader:
            return "ATOS X: motor calismiyor"
        conc = _concentration_summary()
        blocks = conc["blocks"]
        halt = auto_trader.risk_halted
        halt_line = "AKTIF - girisler durduruldu" if halt else "yok"
        loss_line = "AKTIF" if auto_trader.loss_halted else "yok"
        daily_line = "AKTIF" if auto_trader.daily_loss_halted else "yok"
        eq_line = "AKTIF" if auto_trader.equity_halted else "yok"
        return (
            f"ATOS X risk\n"
            f"Equity: ${auto_trader.equity:.2f}\n"
            f"Maruziyet - LONG: %{conc['long_pct']} SHORT: %{conc['short_pct']}\n"
            f"Engeller: {', '.join(blocks) if blocks else 'yok'}\n"
            f"Drawdown: %{auto_trader.drawdown_pct} | Durma: {halt_line}\n"
            f"Ardisik zarar: {auto_trader.consecutive_losses}/{auto_trader.max_consecutive_losses} | Zarar durma: {loss_line}\n"
            f"Gunluk zarar: {auto_trader.day_pnl:.2f} USDT | Gunluk durma: {daily_line}\n"
            f"Equity taban: ${auto_trader.min_equity:.0f} | Taban durma: {eq_line}\n"
            f"Risk olayi: {len(auto_trader.risk_events)}"
        )
    if cmd.startswith("/gecmis") or cmd.startswith("/history"):
        if not auto_trader:
            return "ATOS X: motor calismiyor"
        parts = text.strip().split()
        n = 5
        if len(parts) > 1:
            try:
                n = int(parts[1])
            except ValueError:
                return "ATOS X: kullanim /gecmis [N]"
        n = max(1, min(n, 20))
        history = auto_trader.trade_history[-n:]
        if not history:
            return "ATOS X: kapanis gecmisi yok"
        lines = ["ATOS X son islemler:"]
        for t in reversed(history):
            pnl = t.get("pnl", 0) or 0
            sign = "+" if pnl >= 0 else ""
            lines.append(
                f"{t['symbol']} {t['side']} {sign}{pnl:.2f} "
                f"({t.get('reason', '')})"
            )
        return "\n".join(lines)
    return None

def _is_connected() -> bool:
    return bool(auto_trader and auto_trader.binance and auto_trader.binance.client)

def _concentration_summary() -> dict:
    """Maruziyet ve aktif konsantrasyon engelleri (ops gorunurlugu)."""
    if not auto_trader:
        return {"long_pct": 0.0, "short_pct": 0.0, "blocks": [],
                "max_position_pct": 0.0, "max_side_pct": 0.0}
    equity = auto_trader.equity or 1.0
    long_n = short_n = 0.0
    for p in auto_trader.active_positions.values():
        notional = float(p["entry_price"]) * float(p["quantity"])
        if p["side"] == "BUY":
            long_n += notional
        else:
            short_n += notional
    return {
        "long_pct": round(long_n / equity * 100.0, 1),
        "short_pct": round(short_n / equity * 100.0, 1),
        "blocks": sorted(auto_trader._conc_blocks),
        "max_position_pct": auto_trader.max_position_pct,
        "max_side_pct": auto_trader.max_side_pct,
    }

async def _daily_report_loop():
    """Her gun `DAILY_REPORT_HOUR` saatinde (yerel) ozet raporu gonderir."""
    while True:
        now = datetime.now()
        target = now.replace(hour=settings.DAILY_REPORT_HOUR, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())
        try:
            if auto_trader:
                trades = auto_trader.db.get_closed_trades_since(days=1)
                await telegram.send_daily_summary(
                    trades,
                    auto_trader.equity,
                    auto_trader.active_positions,
                    auto_trader.top_symbols,
                    marks=auto_trader.live_prices,
                    risk_events=auto_trader.risk_events,
                    loss_halted=auto_trader.loss_halted,
                    daily_loss_halted=auto_trader.daily_loss_halted,
                    equity_halted=auto_trader.equity_halted,
                    day_pnl=auto_trader.day_pnl,
                )
        except Exception as e:
            logger.error(f"Gunluk rapor hatasi: {e}")

app = FastAPI(title="ATOS X API", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.include_router(backtest_router)
app.include_router(optimize_router)

@app.get("/")
async def root():
    return {"message": "🚀 ATOS X", "status": system_status["status"], "version": "1.0.0"}

@app.get("/health")
async def health():
    return {
        "status": system_status["status"],
        "connected": _is_connected(),
        "positions": len(auto_trader.active_positions) if auto_trader else 0,
        "protected_positions": _protected_count(),
        "concentration": _concentration_summary(),
        "trades": len(auto_trader.trade_history) if auto_trader else 0,
        "drawdown_pct": auto_trader.drawdown_pct if auto_trader else 0.0,
        "risk_halted": auto_trader.risk_halted if auto_trader else False,
        "loss_halted": auto_trader.loss_halted if auto_trader else False,
        "consecutive_losses": auto_trader.consecutive_losses if auto_trader else 0,
        "daily_loss_halted": auto_trader.daily_loss_halted if auto_trader else False,
        "day_pnl": auto_trader.day_pnl if auto_trader else 0.0,
        "equity_halted": auto_trader.equity_halted if auto_trader else False,
        "min_equity": auto_trader.min_equity if auto_trader else 0.0,
        "trading": auto_trader.running if auto_trader else False,
        "uptime": int((datetime.utcnow() - system_status["start_time"]).total_seconds())
    }

@app.get("/api/v1/signals")
async def live_signals(limit: int = 12, interval: str = "4h"):
    """Tarama listesi icin canli v23 sinyalleri (sinyal, fiyat, SL, TP, sebep)."""
    if not auto_trader:
        return {"signals": [], "count": 0, "scanned": []}
    limit = max(1, min(limit, 30))
    candidates = (auto_trader.priority or auto_trader.trading_symbols)[:limit]
    if not candidates:
        return {"signals": [], "count": 0, "scanned": []}
    bot = TradeBotV23(strat_settings.get_settings())

    async def fetch(symbol):
        try:
            df = await app.state.binance.get_klines(symbol, interval, 400)
            return symbol, bot.generate_signal(df)
        except Exception:
            return symbol, None

    results = await asyncio.gather(*(fetch(s) for s in candidates))
    signals = []
    for symbol, sig in results:
        if not sig:
            continue
        signals.append({
            "symbol": symbol,
            "signal": sig.get("signal", "HOLD"),
            "price": sig.get("price"),
            "sl": sig.get("sl"),
            "tp": sig.get("tp"),
            "reason": sig.get("reason", ""),
            "indicator": sig.get("indicator", ""),
        })
    order = {"BUY": 0, "SELL": 1, "HOLD": 2}
    signals.sort(key=lambda s: order.get(s["signal"], 3))
    return {"signals": signals, "count": len(signals), "scanned": candidates}

@app.get("/api/v1/status")
async def get_status():
    return {
        "status": system_status["status"],
        "connected": _is_connected(),
        "symbols": len(auto_trader.trading_symbols) if auto_trader else 0,
        "positions": len(auto_trader.active_positions) if auto_trader else 0,
        "protected_positions": _protected_count(),
        "concentration": _concentration_summary(),
        "trades": len(auto_trader.trade_history) if auto_trader else 0,
        "drawdown_pct": auto_trader.drawdown_pct if auto_trader else 0.0,
        "risk_halted": auto_trader.risk_halted if auto_trader else False,
        "loss_halted": auto_trader.loss_halted if auto_trader else False,
        "consecutive_losses": auto_trader.consecutive_losses if auto_trader else 0,
        "daily_loss_halted": auto_trader.daily_loss_halted if auto_trader else False,
        "day_pnl": auto_trader.day_pnl if auto_trader else 0.0,
        "equity_halted": auto_trader.equity_halted if auto_trader else False,
        "min_equity": auto_trader.min_equity if auto_trader else 0.0,
        "paper": auto_trader.paper if auto_trader else True,
        "top_symbols": auto_trader.top_symbols if auto_trader else [],
        "equity": auto_trader.equity if auto_trader else 10000
    }

@app.get("/api/v1/priority")
async def get_priority():
    if not auto_trader:
        return {"count": 0, "symbols": []}
    return {
        "count": len(auto_trader.priority),
        "scanned": auto_trader.top_symbols,
        "symbols": auto_trader.priority,
    }

@app.get("/api/v1/equity_curve")
async def equity_curve(points: int = 200):
    points = max(10, min(points, 1000))
    rows = auto_trader.db.get_performance_series(points) if auto_trader else []
    return {
        "timestamps": [r[0] for r in rows],
        "equity": [r[1] for r in rows],
        "open_positions": [r[2] for r in rows],
    }

@app.get("/api/v1/trades/summary")
async def trades_summary():
    if not auto_trader:
        return {"symbols": [], "count": 0}
    symbols = auto_trader.db.get_symbol_pnl(limit=100)
    return {"symbols": symbols, "count": len(symbols)}

# ============ STRATEJİ AYARLARI ENDPOINT'LERİ ============
@app.get("/api/v1/strategy/settings")
async def get_strategy_settings():
    return {"settings": strat_settings.get_settings(), "timestamp": datetime.utcnow().isoformat()}

@app.post("/api/v1/strategy/settings")
async def update_strategy_settings(request: Request):
    try:
        data = await request.json()
        strat_settings.update_settings(data)
        return {"status": "ok", "message": "Settings saved"}
    except Exception as e:
        return {"status": "error", "error": str(e)}

@app.get("/api/v1/strategy/defaults")
async def get_default_settings():
    return {"settings": strat_settings.default_settings()}

@app.get("/api/v1/signal/{symbol}")
async def get_signal(symbol: str, interval: str = "4h", limit: int = 400):
    try:
        df = await app.state.binance.get_klines(symbol, interval, limit)
        bot = TradeBotV23(strat_settings.get_settings())
        return {"symbol": symbol, "interval": interval, **bot.generate_signal(df)}
    except Exception as e:
        return {"symbol": symbol, "error": str(e)}

@app.get("/api/v1/positions")
async def get_positions():
    return _positions_payload()

@app.get("/api/v1/trades")
async def get_trades():
    return {"trades": auto_trader.trade_history[-50:] if auto_trader else [], "count": len(auto_trader.trade_history) if auto_trader else 0}

@app.post("/api/v1/emergency_stop")
async def emergency_stop():
    if auto_trader:
        await auto_trader.stop()
        return {"status": "ok", "message": "All positions closed"}
    return {"status": "error", "message": "Not running"}

@app.get("/api/v1/risk/events")
async def risk_events(limit: int = 50, type: str = ""):
    events = auto_trader.risk_events if auto_trader else []
    if type:
        events = [e for e in events if e["type"] == type]
    return {"events": events[-limit:], "count": len(events[-limit:])}

@app.get("/api/v1/risk/positions")
async def risk_positions():
    """Acik pozisyonlarin pozisyon bazli risk metrikleri."""
    positions = auto_trader.active_positions if auto_trader else {}
    equity = auto_trader.equity if auto_trader else 1.0
    out = {}
    for symbol, pos in positions.items():
        mark = auto_trader.live_prices.get(symbol) if auto_trader else None
        upnl, pct = _position_upnl(pos, mark)
        entry = float(pos["entry_price"])
        qty = float(pos["quantity"])
        notional = entry * qty
        side = pos["side"]
        sl = pos.get("sl")
        if sl:
            sl_val = float(sl)
            if side == "BUY":
                sl_dist = (entry - sl_val) / entry * 100.0
                risk_amt = qty * max(entry - sl_val, 0.0)
            else:
                sl_dist = (sl_val - entry) / entry * 100.0
                risk_amt = qty * max(sl_val - entry, 0.0)
        else:
            sl_val, sl_dist, risk_amt = None, None, None
        age_h = None
        if pos.get("open_time"):
            try:
                opened = datetime.fromisoformat(pos["open_time"].replace("Z", "+00:00"))
                if opened.tzinfo:
                    opened = opened.replace(tzinfo=None)
                age_h = (datetime.utcnow() - opened).total_seconds() / 3600.0
            except Exception:
                age_h = None
        out[symbol] = {
            "side": side,
            "quantity": qty,
            "entry": entry,
            "sl": sl_val,
            "tp": pos.get("tp"),
            "notional": round(notional, 2),
            "size_pct": round(notional / equity * 100.0, 2),
            "sl_distance_pct": round(sl_dist, 2) if sl_dist is not None else None,
            "risk_amount": round(risk_amt, 2) if risk_amt is not None else None,
            "mark": mark,
            "upnl": upnl,
            "upnl_pct": pct,
            "protected": bool(pos.get("sl_order_id") or pos.get("tp_order_id")),
            "trailing": bool(pos.get("trailing")),
            "breakeven": bool(pos.get("breakeven")),
            "age_hours": round(age_h, 2) if age_h is not None else None,
        }
    return {
        "positions": out,
        "count": len(positions),
        "equity": round(equity, 2),
        "total_notional": round(sum(p["notional"] for p in out.values()), 2),
        "total_risk_amount": round(
            sum(p["risk_amount"] for p in out.values() if p["risk_amount"] is not None), 2
        ),
        "max_position_pct": auto_trader.max_position_pct if auto_trader else 0.0,
    }

@app.post("/api/v1/positions/{symbol}/sl")
async def position_update_sl(symbol: str, request: Request):
    """Dashboard/API'den acik pozisyonun SL'sini gunceller."""
    if not auto_trader:
        return {"ok": False, "error": "not_running"}
    try:
        body = await request.json()
        price = float(body.get("price"))
    except Exception:
        return {"ok": False, "error": "invalid_body"}
    return await auto_trader.update_sl(symbol.upper(), price)

@app.post("/api/v1/positions/{symbol}/tp")
async def position_update_tp(symbol: str, request: Request):
    """Dashboard/API'den acik pozisyonun TP'sini gunceller."""
    if not auto_trader:
        return {"ok": False, "error": "not_running"}
    try:
        body = await request.json()
        price = float(body.get("price"))
    except Exception:
        return {"ok": False, "error": "invalid_body"}
    return await auto_trader.update_tp(symbol.upper(), price)

@app.post("/api/v1/positions/{symbol}/close")
async def position_close(symbol: str):
    """Dashboard/API'den acik pozisyonu canli fiyatla kapatir."""
    if not auto_trader:
        return {"ok": False, "error": "not_running"}
    sym = symbol.upper()
    if sym not in auto_trader.active_positions:
        return {"ok": False, "error": "position_not_found"}
    price = auto_trader.live_prices.get(sym)
    if price is None:
        try:
            prices = await app.state.binance.get_all_tickers()
            price = prices.get(sym)
        except Exception:
            price = None
    if price is None:
        return {"ok": False, "error": "price_not_found"}
    await auto_trader.close_position(sym, price, "dashboard_close")
    return {"ok": True, "symbol": sym, "price": price}

@app.get("/dashboard/metrics")
async def metrics():
    try:
        return {
            "status": system_status["status"],
            "connected": _is_connected(),
            "symbols": len(auto_trader.trading_symbols) if auto_trader else 0,
            "active_positions": len(auto_trader.active_positions) if auto_trader else 0,
            "protected_positions": _protected_count(),
            "total_trades": len(auto_trader.trade_history) if auto_trader else 0,
            "equity": auto_trader.equity if auto_trader else 10000,
            "paper": auto_trader.paper if auto_trader else True,
            "top_symbols": auto_trader.top_symbols if auto_trader else [],
            "positions": _positions_payload()["positions"],
            "concentration": _concentration_summary(),
            "drawdown_pct": auto_trader.drawdown_pct if auto_trader else 0.0,
            "risk_halted": auto_trader.risk_halted if auto_trader else False,
            "loss_halted": auto_trader.loss_halted if auto_trader else False,
            "consecutive_losses": auto_trader.consecutive_losses if auto_trader else 0,
            "daily_loss_halted": auto_trader.daily_loss_halted if auto_trader else False,
            "day_pnl": auto_trader.day_pnl if auto_trader else 0.0,
            "equity_halted": auto_trader.equity_halted if auto_trader else False,
            "min_equity": auto_trader.min_equity if auto_trader else 0.0,
            "trading": auto_trader.running if auto_trader else False,
            "risk_events": auto_trader.risk_events[-10:] if auto_trader else [],
            "uptime": int((datetime.utcnow() - system_status["start_time"]).total_seconds())
        }
    except Exception as e:
        return {"error": str(e)}

@app.get("/dashboard/html")
async def dashboard_html():
    try:
        with open(_APP_DIR / "dashboard.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except:
        return HTMLResponse(content="<h1>Dashboard not found</h1>")

@app.get("/dashboard/settings")
async def settings_html():
    try:
        with open(_APP_DIR / "strategy_settings.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except:
        return HTMLResponse(content="<h1>Settings not found</h1>")

@app.get("/optimize/html")
async def optimize_html():
    try:
        with open(_APP_DIR / "optimize.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except:
        return HTMLResponse(content="<h1>Optimize not found</h1>")

@app.get("/backtest/html")
async def backtest_html():
    try:
        with open(_APP_DIR / "backtest.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except:
        return HTMLResponse(content="<h1>Backtest not found</h1>")

