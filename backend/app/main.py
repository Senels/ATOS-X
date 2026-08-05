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
from app.strategy.market_intel import analyze as analyze_market
from app.strategy.coin_intel import coin_score
from app.strategy.decision import decide as decide_symbol
from app.data.collector import backfill as backfill_klines
from app.data.collector import collect as collect_klines
from app.data.collector import _INTERVAL_MS
from app.data import loader
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
_PRICE_ALERTS = {}  # symbol -> [{price, side, created}]
_alarm_task = None

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    global auto_trader, daily_report_task, _alarm_task
    import os
    if os.environ.get("ATOS_TEST_MODE"):
        yield
        return
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
    _alarm_task = asyncio.create_task(_alarm_loop())
    telegram.start_listener(_telegram_command)
    await telegram.send(f"ATOS X v{settings.APP_VERSION} baslatildi!")

    yield
    system_status["status"] = "shutting_down"
    if daily_report_task:
        daily_report_task.cancel()
    ws_sync_task.cancel()
    if _alarm_task:
        _alarm_task.cancel()
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
    await telegram.send_signal(symbol, signal, sig.get("price") or 0.0,
                               sig.get("reason", ""), sig.get("sl"), sig.get("tp"))

async def _send_batch_signals(symbols: list, interval: str = "4h", sig_filter: str = None):
    """Tarama listesi icin toplu sinyal ozetini Telegram'a gonderir."""
    from app.strategy.coin_intel import trend_regime
    arrows = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⚪"}
    trend_icons = {"UPTREND": "📈", "DOWNTREND": "📉", "RANGE": "➡️"}
    header = f"ATOS X tarama ({interval}"
    if sig_filter:
        header += f", {sig_filter}"
    header += "):"
    lines = [header]
    client = getattr(app.state, "binance", None)
    for sym in symbols:
        sig = await _signal_for_symbol(sym, interval)
        signal = sig.get("signal")
        if not signal:
            continue
        if sig_filter and signal != sig_filter:
            continue
        arrow = arrows.get(signal, "")
        price = sig.get("price") or 0.0
        sl, tp = sig.get("sl"), sig.get("tp")
        extra = ""
        if sl and tp:
            risk = abs(price - sl)
            reward = abs(tp - price)
            rr = reward / risk if risk > 0 else 0.0
            extra = f" SL:${sl:g} TP:${tp:g} R:R{rr:.1f}"
        elif sl:
            extra = f" SL:${sl:g}"
        elif tp:
            extra = f" TP:${tp:g}"
        trend_str = ""
        try:
            if client:
                df = await client.get_klines(sym, interval, 200)
                tr = trend_regime(df)
                ti = trend_icons.get(tr.get("regime", ""), "")
                trend_str = f" {ti}" if ti else ""
        except Exception:
            pass
        lines.append(f"{sym} {arrow} {signal} ${price:.4g}{extra}{trend_str} - {sig.get('reason', '')[:40]}")
    if len(lines) == 1:
        lines.append("Sinyal alinamadi")
    await telegram.send("\n".join(lines))

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

async def _alarm_loop():
    """Fiyat alarmlarini kontrol eder; esik asilinca Telegram'dan bildirir."""
    while True:
        await asyncio.sleep(20)
        try:
            if not _PRICE_ALERTS or not auto_trader:
                continue
            for sym, alerts in list(_PRICE_ALERTS.items()):
                if not alerts:
                    continue
                price = auto_trader.live_prices.get(sym)
                if price is None:
                    continue
                still = []
                for a in alerts:
                    hit = price >= a["price"] if a["side"] == "above" else price <= a["price"]
                    if hit:
                        dir_word = "ustune cikti" if a["side"] == "above" else "altina indi"
                        await telegram.send(
                            f"⏰ ATOS X ALARM: {sym} ${price:g} "
                            f"{a['price']:g} {dir_word} (esik {a['side']})"
                        )
                    else:
                        still.append(a)
                _PRICE_ALERTS[sym] = still
            for sym in [s for s, a in list(_PRICE_ALERTS.items()) if not a]:
                del _PRICE_ALERTS[sym]
        except Exception as e:
            logger.error(f"Alarm kontrol hatasi: {e}")

def _alarm_list() -> str:
    if not _PRICE_ALERTS:
        return "ATOS X: aktif alarm yok"
    lines = [f"ATOS X aktif alarmlar ({sum(len(a) for a in _PRICE_ALERTS.values())}):"]
    for sym, alerts in _PRICE_ALERTS.items():
        for a in alerts:
            side_word = "ustu" if a["side"] == "above" else "alti"
            lines.append(f"  {sym} {side_word} ${a['price']:g}")
    return "\n".join(lines)

def _protected_count() -> int:
    """Exchange-side SL/TP ile korunan acik pozisyon sayisi."""
    if not auto_trader:
        return 0
    return sum(
        1 for p in list(auto_trader.active_positions.values())
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
    "use_decision_council": "use_decision_council",
    "council_min_confidence": "council_min_confidence",
    "use_score_ranking": "use_score_ranking",
    "data_backfill_hours": "data_backfill_hours",
    "data_freshness_hours": "data_freshness_hours",
}
_INT_RISK_KEYS = {"max_open_positions", "max_consecutive_losses", "max_position_age_hours"}
_BOOL_RISK_KEYS = {"use_decision_council", "use_score_ranking"}


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
        f"Decision Council: {'acik' if s.get('use_decision_council') else 'kapali'} | Min guven: %{s.get('council_min_confidence', 0.6) * 100:.0f}\n"
        f"Skor siralamasi: {'acik' if s.get('use_score_ranking') else 'kapali'}\n"
        f"Otomatik backfill: {s.get('data_backfill_hours', 0.0):g} saat arayla | Tazelik: {s.get('data_freshness_hours', 12.0):g} saat\n"
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
                "/sl breakeven [SEMBOL] - SL'leri giris fiyatina tasir\n"
                "/tp <SEMBOL> <FIYAT> - acik pozisyonun TP'sini gunceller\n"
                "/durdur - acil durdurma (tum pozisyonlari kapatir)\n"
                "/kapatall - acik tum pozisyonlari kapatir\n"
                "/sinyal <SEMBOL> - sembol icin canli sinyal gonder\n"
                "/sinyalall [N] - ilk N sembolun toplu tarama ozeti\n"
                "/koruma [ANAHTAR] [DEGER] - risk ayarlarini gor/degistir\n"
                "/ac - motoru yeniden baslatir\n"
                "/rapor [GUN] - gunluk rapor (varsayilan: 1 gun)\n"
                "/risk - risk durumu\n"
                "/gecmis [N] [SEMBOL] - son N islem (sembol ile filtrelenir)\n"
                "/istatistik - islem performansi ozeti\n"
                "/veri - veri tazeligi ozeti (ok/esk/esik)\n"
                "/backfill [SEMBOLLER] [GUN] - eksik/eski CSV verisini tazeler\n"
                "/temizle [hepsi] - kapanan islem gecmisini temizler (hepsi: +sinyal/backtest/risk/performans)\n"
                "/izleme [N] - oncelik listesi + canli skor siralamasi\n"
                "/performans - equity curve ozeti + aylik istatistik\n"
                "/son - son kapanan islem detayi\n"
                "/islem - bugunun kapanan islemleri\n"
                "/bekleyen - bekleyen TP/SL emirleri\n"
                "/bakiye - bakiye + pozisyon ozeti\n"
                "/alarm <SEMBOL> <FIYAT> [ust/alt] - fiyat alarmi ekle\n"
                "/alarm - aktif alarmlari listele\n"
                "/alarm temizle - tum alarmlari sil\n"
                "/ayarla - tum ayarlari ozet goruntusu\n"
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
        now = datetime.utcnow()
        for sym, pos in auto_trader.active_positions.items():
            prot = "korumali" if (pos.get("sl_order_id") or pos.get("tp_order_id")) else "KORUMASIZ"
            if pos.get("trailing"):
                prot += " + TRAILING"
            if pos.get("breakeven"):
                prot += " + BREAKEVEN"
            mark = auto_trader.live_prices.get(sym)
            upnl, pct = _position_upnl(pos, mark)
            line = f"{sym} {pos['side']} qty={pos['quantity']} @ ${pos['entry_price']} {prot}"
            sl_p, tp_p = pos.get("sl"), pos.get("tp")
            if sl_p or tp_p:
                bits = []
                if sl_p:
                    bits.append(f"SL: ${sl_p:g}")
                if tp_p:
                    bits.append(f"TP: ${tp_p:g}")
                line += " | " + " ".join(bits)
            if upnl is not None:
                sign = "+" if upnl >= 0 else ""
                line += f" | PnL: {sign}{upnl:.2f} ({sign}{pct:.2f}%)"
            ot = pos.get("open_time")
            if ot:
                try:
                    opened = datetime.fromisoformat(ot)
                    age_h = (now - opened).total_seconds() / 3600.0
                    line += f" | {age_h:.1f}h"
                except Exception:
                    pass
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
        if settings_key in _BOOL_RISK_KEYS:
            value = bool(value)
        elif settings_key in _INT_RISK_KEYS:
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
        parts = text.strip().split()
        confirmed = len(parts) > 1 and parts[1].lower() == "onay"
        if not confirmed:
            n = len(auto_trader.active_positions)
            syms = ", ".join(auto_trader.active_positions.keys())
            return (f"ATOS X: {n} pozisyon kapatilacak ({syms})\n"
                    "Devam icin: /kapatall onay")
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
        if len(parts) >= 2 and parts[1].upper() == "BREAKEVEN":
            sym = parts[2].upper() if len(parts) == 3 else None
            if sym:
                if sym not in auto_trader.active_positions:
                    return f"ATOS X: {sym} icin acik pozisyon yok"
                entry = auto_trader.active_positions[sym].get("entry_price")
                if entry is None:
                    return f"ATOS X: {sym} giris fiyati bulunamadi"
                auto_trader.active_positions[sym]["sl"] = entry
                return f"ATOS X: {sym} SL -> giris fiyati ${entry:g} (breakeven)"
            syms = list(auto_trader.active_positions.keys())
            if not syms:
                return "ATOS X: acik pozisyon yok"
            count = 0
            for s in syms:
                entry = auto_trader.active_positions[s].get("entry_price")
                if entry is not None:
                    auto_trader.active_positions[s]["sl"] = entry
                    count += 1
            return f"ATOS X: {count} pozisyonun SL'si giris fiyatina tasindi (breakeven)"
        if len(parts) == 3 and parts[1].upper() == "ALL":
            arg = parts[2]
            syms = list(auto_trader.active_positions.keys())
            if not syms:
                return "ATOS X: acik pozisyon yok"
            if arg.startswith("%"):
                try:
                    pct = float(arg[1:]) / 100.0
                except ValueError:
                    return "ATOS X: gecersiz yuzde"
                count = 0
                for s in syms:
                    pos = auto_trader.active_positions[s]
                    entry = pos.get("entry_price")
                    cur_sl = pos.get("sl")
                    price = auto_trader.live_prices.get(s)
                    if entry is None or price is None:
                        continue
                    if pos.get("side") == "BUY":
                        new_sl = entry + (price - entry) * pct
                    else:
                        new_sl = entry - (entry - price) * pct
                    auto_trader.active_positions[s]["sl"] = new_sl
                    count += 1
                return f"ATOS X: {count} pozisyonun SL guncellendi (%{pct*100:.0f} mesafe)"
            try:
                new_sl = float(arg)
            except ValueError:
                return "ATOS X: gecersiz SL fiyati"
            for s in syms:
                auto_trader.active_positions[s]["sl"] = new_sl
            return f"ATOS X: tum pozisyonlarin SL guncellendi -> ${new_sl:g} ({len(syms)} pozisyon)"
        if len(parts) != 3:
            return "ATOS X: kullanim /sl <SEMBOL> <FIYAT> veya /sl all <FIYAT|%> veya /sl breakeven"
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
        if len(parts) == 3 and parts[1].upper() == "ALL":
            arg = parts[2]
            syms = list(auto_trader.active_positions.keys())
            if not syms:
                return "ATOS X: acik pozisyon yok"
            if arg.startswith("%"):
                try:
                    pct = float(arg[1:]) / 100.0
                except ValueError:
                    return "ATOS X: gecersiz yuzde"
                count = 0
                for s in syms:
                    pos = auto_trader.active_positions[s]
                    entry = pos.get("entry_price")
                    cur_tp = pos.get("tp")
                    price = auto_trader.live_prices.get(s)
                    if entry is None or price is None:
                        continue
                    if pos.get("side") == "BUY":
                        new_tp = entry + (price - entry) * pct
                    else:
                        new_tp = entry - (entry - price) * pct
                    auto_trader.active_positions[s]["tp"] = new_tp
                    count += 1
                return f"ATOS X: {count} pozisyonun TP guncellendi (%{pct*100:.0f} mesafe)"
            try:
                new_tp = float(arg)
            except ValueError:
                return "ATOS X: gecersiz TP fiyati"
            for s in syms:
                auto_trader.active_positions[s]["tp"] = new_tp
            return f"ATOS X: tum pozisyonlarin TP guncellendi -> ${new_tp:g} ({len(syms)} pozisyon)"
        if len(parts) != 3:
            return "ATOS X: kullanim /tp <SEMBOL> <FIYAT> veya /tp all <FIYAT|%>"
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
    if cmd.startswith("/sinyalall") or cmd.startswith("/scan"):
        if not auto_trader:
            return "ATOS X: motor calismiyor"
        parts = text.strip().split()
        n, interval, sig_filter = 5, "4h", None
        for p in parts[1:]:
            if p.lower() in _INTERVAL_MS:
                interval = p.lower()
            elif p.upper() in ("BUY", "SELL", "HOLD"):
                sig_filter = p.upper()
            elif p.isdigit():
                n = int(p)
        n = max(1, min(n, 10))
        symbols = (auto_trader.priority or auto_trader.trading_symbols)[:n]
        if not symbols:
            return "ATOS X: tarama listesi bos"
        if not _run_later(_send_batch_signals(symbols, interval, sig_filter)):
            return "ATOS X: komut arka planda calistirilamadi"
        flt = f" ({sig_filter})" if sig_filter else ""
        return f"ATOS X: {len(symbols)} sembol taranacak ({interval}{flt})"
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
        parts = text.strip().split()
        confirmed = len(parts) > 1 and parts[1].lower() == "onay"
        if not confirmed:
            n = len(auto_trader.active_positions)
            return (f"ATOS X: motor durdurulacak ve {n} pozisyon kapatilacak\n"
                    "Devam icin: /durdur onay")
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
        pos = auto_trader.active_positions
        prot_n = _protected_count()
        unprot_n = len(pos) - prot_n
        n_trail = sum(1 for p in pos.values() if p.get("trailing"))
        n_be = sum(1 for p in pos.values() if p.get("breakeven"))
        prot_detail = f"{prot_n} korumali"
        if unprot_n > 0:
            prot_detail += f", {unprot_n} KORUMASIZ"
        extras = []
        if n_trail:
            extras.append(f"Trailing: {n_trail}")
        if n_be:
            extras.append(f"Breakeven: {n_be}")
        prot_line = f"Pozisyon: {len(pos)} ({prot_detail})"
        if extras:
            prot_line += " | " + " ".join(extras)
        upnl = _positions_payload()['total_upnl']
        upnl_sign = "+" if upnl >= 0 else ""
        now = datetime.utcnow()
        max_age_h = float(strat_settings.get_settings().get("max_position_age_hours", 0))
        old_syms = []
        if max_age_h > 0:
            for s, p in pos.items():
                ot = p.get("open_time")
                if ot:
                    try:
                        age = (now - datetime.fromisoformat(ot)).total_seconds() / 3600
                        if age >= max_age_h * 0.8:
                            old_syms.append(f"{s}({age:.0f}h)")
                    except Exception:
                        pass
        lines = [
            f"ATOS X durum",
            f"Trade motoru: {trading}",
            f"Equity: ${auto_trader.equity:.2f}",
            prot_line,
            f"Gerceklesmemis PnL: {upnl_sign}{upnl:.2f}",
            f"Maruziyet - LONG: %{conc['long_pct']} SHORT: %{conc['short_pct']}",
            f"Engeller: {', '.join(blocks) if blocks else 'yok'}",
            f"Drawdown: %{auto_trader.drawdown_pct} | Durma: {halt_line}",
            f"Ardisik zarar: {auto_trader.consecutive_losses}/{auto_trader.max_consecutive_losses} | Zarar durma: {loss_line}",
            f"Gunluk zarar: {auto_trader.day_pnl:.2f} USDT | Gunluk durma: {daily_line}",
            f"Equity taban: ${auto_trader.min_equity:.0f} | Taban durma: {eq_line}",
            f"Risk olayi: {len(events)} (son: {evt_line})",
        ]
        if old_syms:
            lines.append(f"Uzun pozisyonlar: {', '.join(old_syms)} (>{max_age_h * 0.8:.0f}h)")
        return "\n".join(lines)
    if cmd.startswith("/rapor") or cmd.startswith("/report"):
        if not auto_trader:
            return "ATOS X: motor calismiyor"
        parts = text.strip().split()
        days = 1
        if len(parts) > 1:
            try:
                days = max(1, min(int(parts[1]), 90))
            except ValueError:
                pass
        trades = auto_trader.db.get_closed_trades_since(days=days)
        hist = auto_trader.trade_history
        protection_stats = {
            "trailing": sum(1 for t in hist if t.get("trailing")),
            "trailing_pnl": sum(t.get("pnl", 0) or 0 for t in hist if t.get("trailing")),
            "breakeven": sum(1 for t in hist if t.get("breakeven")),
            "breakeven_pnl": sum(t.get("pnl", 0) or 0 for t in hist if t.get("breakeven")),
        }
        closed_today = [t for t in trades if t[6] is not None]
        worst_sym = None
        if closed_today:
            by_sym = {}
            for t in closed_today:
                by_sym[t[1]] = by_sym.get(t[1], 0.0) + t[6]
            worst_sym = min(by_sym.items(), key=lambda kv: kv[1]) if by_sym else None
        if not _run_later(telegram.send_daily_summary(
                trades, auto_trader.equity, auto_trader.active_positions,
                auto_trader.top_symbols, marks=auto_trader.live_prices,
                risk_events=auto_trader.risk_events,
                loss_halted=auto_trader.loss_halted,
                daily_loss_halted=auto_trader.daily_loss_halted,
                equity_halted=auto_trader.equity_halted,
                day_pnl=auto_trader.day_pnl,
                drawdown_pct=auto_trader.drawdown_pct,
                worst_sym=worst_sym,
                data_status=_data_freshness(300),
                protection_stats=protection_stats,
                days=days)):
            return "ATOS X: komut arka planda calistirilamadi"
        return f"ATOS X: {days} gunluk rapor gonderiliyor"
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
        sym_filter = None
        for p in parts[1:]:
            if p.isdigit():
                n = int(p)
            else:
                sym_filter = p.upper()
        n = max(1, min(n, 20))
        hist = auto_trader.trade_history
        if sym_filter:
            hist = [t for t in hist if t.get("symbol") == sym_filter]
        history = hist[-n:]
        if not history:
            return "ATOS X: kapanis gecmisi yok"
        title = f"ATOS X son islemler ({len(history)}"
        if sym_filter:
            title += f", {sym_filter}"
        title += "):"
        lines = [title]
        pnls = [t.get("pnl", 0) or 0 for t in history]
        wins = [p for p in pnls if p > 0]
        net = sum(pnls)
        win_rate = len(wins) / len(pnls) * 100 if pnls else 0.0
        gross_w = sum(p for p in pnls if p > 0)
        gross_l = abs(sum(p for p in pnls if p <= 0))
        pf = gross_w / gross_l if gross_l > 0 else (float("inf") if gross_w > 0 else 0.0)
        pf_str = "inf" if pf == float("inf") else f"{pf:.2f}"
        lines.append(f"Net: {net:+.2f} | Kazanma: %{win_rate:.0f} | PF: {pf_str}")
        for t in reversed(history):
            pnl = t.get("pnl", 0) or 0
            sign = "+" if pnl >= 0 else ""
            line = f"{t['symbol']} {t['side']} {sign}{pnl:.2f} ({t.get('reason', '')})"
            entry, exit_p = t.get("entry"), t.get("exit")
            if entry is not None and exit_p is not None:
                line += f" [{float(entry):g} -> {float(exit_p):g}]"
            ts = t.get("time")
            if ts:
                line += f" {str(ts)[5:16]}"
            lines.append(line)
        return "\n".join(lines)
    if cmd.startswith("/istatistik") or cmd.startswith("/stats"):
        if not auto_trader:
            return "ATOS X: motor calismiyor"
        hist = auto_trader.trade_history
        if not hist:
            return "ATOS X: islem gecmisi yok"
        pnls = [t.get("pnl", 0) or 0 for t in hist]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        net = sum(pnls)
        gross_w = sum(wins)
        gross_l = abs(sum(losses))
        pf = gross_w / gross_l if gross_l > 0 else (float("inf") if gross_w > 0 else 0.0)
        pf_str = "inf" if pf == float("inf") else f"{pf:.2f}"
        avg_w = gross_w / len(wins) if wins else 0.0
        avg_l = gross_l / len(losses) if losses else 0.0
        by_sym = {}
        for t in hist:
            sym = t.get("symbol", "?")
            by_sym[sym] = by_sym.get(sym, 0.0) + (t.get("pnl", 0) or 0)
        best = max(by_sym.items(), key=lambda kv: kv[1])
        lines = [
            f"ATOS X istatistik ({len(hist)} islem):",
            f"Net PnL: {net:+.2f} | Kazanma: %{len(wins) / len(pnls) * 100:.0f}",
            f"PF: {pf_str} | Ort kar: {avg_w:+.2f} | Ort zarar: {avg_l:.2f}",
            f"En iyi sembol: {best[0]} {best[1]:+.2f}",
        ]
        n_trail = sum(1 for t in hist if t.get("trailing"))
        n_be = sum(1 for t in hist if t.get("breakeven"))
        prot = []
        if n_trail:
            trail_pnl = sum(t.get("pnl", 0) or 0 for t in hist if t.get("trailing"))
            prot.append(f"Trailing: {n_trail} ({trail_pnl:+.2f})")
        if n_be:
            be_pnl = sum(t.get("pnl", 0) or 0 for t in hist if t.get("breakeven"))
            prot.append(f"Breakeven: {n_be} ({be_pnl:+.2f})")
        if prot:
            lines.append(" | ".join(prot))
        return "\n".join(lines)
    if cmd.startswith("/veri") or cmd.startswith("/data"):
        st = _data_freshness(200)
        if not st.get("ok"):
            return f"ATOS X: motor calismiyor"
        lines = [
            f"ATOS X veri durumu ({st['interval']}, esik {st['freshness_hours']:g} saat):",
            f"Guncel: {st['fresh']} | Eski: {st['stale']} | Eksik: {st['missing']}",
        ]
        bad = [r for r in st["rows"] if r["state"] != "ok"]
        if bad:
            lines.append("Eski/Eksik: " + ", ".join(
                f"{r['symbol']}({r['age_hours'] if r['age_hours'] is not None else 'yok'}s)"
                for r in bad[:15]))
        return "\n".join(lines)
    if cmd.startswith("/backfill"):
        if not auto_trader or not auto_trader.binance:
            return "ATOS X: motor calismiyor"
        parts = text.strip().split()
        symbols, days = [], 30
        for p in parts[1:]:
            if p.isdigit():
                days = int(p)
            else:
                symbols += [s.strip().upper() for s in p.split(",") if s.strip()]
        src = "istenen"
        if not symbols:
            st = _data_freshness(300)
            symbols = [r["symbol"] for r in st["rows"] if r["state"] != "ok"][:10]
            if not symbols:
                return "ATOS X: backfill gereken sembol yok (veriler guncel)"
            src = "eski/eksik"
        days = max(1, min(days, 90))
        if not _run_later(_run_backfill(symbols, days)):
            return "ATOS X: motor calismiyor"
        return (f"ATOS X backfill basladi ({src} {len(symbols)} sembol, "
                f"{days} gun): {', '.join(symbols[:8])}")
    if cmd.startswith("/temizle"):
        if not auto_trader:
            return "ATOS X: motor calismiyor"
        parts = text.strip().split()
        hard = len(parts) > 1 and parts[1].lower() == "hepsi"
        confirm = len(parts) > 1 and parts[1].lower() == "onay"
        hard_and_confirm = hard and len(parts) > 2 and parts[2].lower() == "onay"
        if hard and not hard_and_confirm and not confirm:
            return ("ATOS X: /temizle hepsi TUM verileri siler!\n"
                    "Onay icin: /temizle hepsi onay")
        if hard_and_confirm:
            n_closed = auto_trader.db.clear_closed_trades()
            auto_trader.trade_history = []
            counts = auto_trader.db.clear_operational()
            return ("ATOS X temizlendi (hepsi):\n"
                    f"Kapanan islem: {n_closed}\n"
                    f"Sinyal: {counts['signals']} | Backtest: {counts['backtest_runs']} | "
                    f"Risk olayi: {counts['risk_events']} | Performans: {counts['performance']}")
        n_closed = auto_trader.db.clear_closed_trades()
        auto_trader.trade_history = []
        return ("ATOS X kapanan islem gecmisi temizlendi.\n"
                f"Silinen kayit: {n_closed}\n"
                "Diger tablolar icin: /temizle hepsi (onay gerekli)")
    if cmd.startswith("/izleme"):
        if not auto_trader:
            return "ATOS X: motor calismiyor"
        parts = text.strip().split()
        n = 10
        if len(parts) > 1 and parts[1].isdigit():
            n = int(parts[1])
        n = max(1, min(n, 20))
        symbols = (auto_trader.priority or auto_trader.trading_symbols)[:n]
        if not symbols:
            return "ATOS X: tarama listesi bos"
        if not _run_later(_send_watchlist(symbols)):
            return "ATOS X: komut arka planda calistirilamadi"
        return f"ATOS X: {len(symbols)} sembol hesaplaniyor"
    if cmd.startswith("/performans"):
        if not auto_trader:
            return "ATOS X: motor calismiyor"
        hist = auto_trader.trade_history
        if not hist:
            return "ATOS X: islem gecmisi yok"
        perf = auto_trader.db.get_performance_series(500)
        peak = max((r[1] for r in perf), default=auto_trader.equity)
        dd_pct = ((peak - auto_trader.equity) / peak * 100) if peak > 0 else 0.0
        pnls = [t.get("pnl", 0) or 0 for t in hist]
        wins = sum(1 for p in pnls if p > 0)
        total = len(pnls)
        wr = wins / total * 100 if total else 0.0
        avg_pnl = sum(pnls) / total if total else 0.0
        sharpe = 0.0
        if total >= 2:
            var = sum((p - avg_pnl) ** 2 for p in pnls) / (total - 1)
            std = var ** 0.5
            sharpe = avg_pnl / std if std > 0 else 0.0
        win_streak = loss_streak = cur_w = cur_l = 0
        for p in pnls:
            if p > 0:
                cur_w += 1
                cur_l = 0
            elif p < 0:
                cur_l += 1
                cur_w = 0
            else:
                cur_w = cur_l = 0
            win_streak = max(win_streak, cur_w)
            loss_streak = max(loss_streak, cur_l)
        by_month = {}
        for t in hist:
            ts = str(t.get("time", ""))
            month = ts[:7] if ts else "?"
            if month not in by_month:
                by_month[month] = {"count": 0, "pnl": 0.0, "wins": 0}
            by_month[month]["count"] += 1
            by_month[month]["pnl"] += t.get("pnl", 0) or 0
            if (t.get("pnl", 0) or 0) > 0:
                by_month[month]["wins"] += 1
        months = sorted(by_month.items(), reverse=True)[:6]
        lines = [
            f"ATOS X performans ({total} islem):",
            f"Equity: ${auto_trader.equity:.2f} | Peak: ${peak:.2f}",
            f"Drawdown: %{dd_pct:.1f} | Kazanma: %{wr:.0f}",
            f"Sharpe: {sharpe:.2f} | Ort PnL: {avg_pnl:+.2f}",
            f"Seri: {win_streak}W max / {loss_streak}L max",
        ]
        if months:
            lines.append("Aylik ozet:")
            for m, d in months:
                mwr = d["wins"] / d["count"] * 100 if d["count"] else 0
                sign = "+" if d["pnl"] >= 0 else ""
                lines.append(f"  {m}: {d['count']} islem {sign}{d['pnl']:.2f} (%{mwr:.0f})")
        return "\n".join(lines)
    if cmd.startswith("/son"):
        if not auto_trader:
            return "ATOS X: motor calismiyor"
        hist = auto_trader.trade_history
        if not hist:
            return "ATOS X: kapanan islem yok"
        t = hist[-1]
        pnl = t.get("pnl", 0) or 0
        sign = "+" if pnl >= 0 else ""
        lines = [
            f"ATOS X son islem:",
            f"Sembol: {t['symbol']} {t['side']}",
        ]
        entry, exit_p = t.get("entry"), t.get("exit")
        if entry is not None:
            lines.append(f"Giris: ${float(entry):g}")
        if exit_p is not None:
            lines.append(f"Cikis: ${float(exit_p):g}")
        lines.append(f"PnL: {sign}{pnl:.2f}")
        reason = t.get("reason", "")
        if reason:
            lines.append(f"Neden: {reason}")
        prot = []
        if t.get("trailing"):
            prot.append("Trailing")
        if t.get("breakeven"):
            prot.append("Breakeven")
        if prot:
            lines.append(f"Koruma: {' + '.join(prot)}")
        ts = t.get("time")
        if ts:
            lines.append(f"Zaman: {str(ts)[:16].replace('T', ' ')}")
        return "\n".join(lines)
    if cmd.startswith("/islem"):
        if not auto_trader:
            return "ATOS X: motor calismiyor"
        trades = auto_trader.db.get_closed_trades_since(days=1)
        closed = [t for t in trades if t[6] is not None]
        if not closed:
            return "ATOS X: bugun kapanan islem yok"
        lines = [f"ATOS X bugun ({len(closed)} islem):"]
        for t in closed[:15]:
            sym, side, entry, exit_p = t[1], t[2], t[3], t[4]
            pnl = t[6] or 0
            sign = "+" if pnl >= 0 else ""
            lines.append(f"  {sym} {side} ${exit_p:g} {sign}{pnl:.2f}")
        if len(closed) > 15:
            lines.append(f"  ...+{len(closed)-15} daha")
        total_pnl = sum(t[6] or 0 for t in closed)
        wins = sum(1 for t in closed if (t[6] or 0) > 0)
        lines.append(f"Toplam: {total_pnl:+.2f} ({wins}W/{len(closed)-wins}L)")
        return "\n".join(lines)
    if cmd.startswith("/bekleyen"):
        if not auto_trader or not auto_trader.binance:
            return "ATOS X: motor calismiyor"
        async def _fetch_orders():
            try:
                orders = await auto_trader.binance.get_open_algo_orders()
            except Exception as e:
                await telegram.send(f"ATOS X: emir sorgusu hatasi: {e}")
                return
            if not orders:
                await telegram.send("ATOS X: bekleyen emir yok")
                return
            lines = [f"ATOS X bekleyen emirler ({len(orders)}):"]
            for o in orders[:15]:
                sym = o.get("symbol", "?")
                side = o.get("side", "?")
                tp = o.get("triggerPrice") or o.get("price", "")
                otype = "TP" if "TP" in str(o.get("ordType", "")).upper() else "SL"
                lines.append(f"  {sym} {otype} {side} @{tp}")
            if len(orders) > 15:
                lines.append(f"  ...+{len(orders)-15} daha")
            await telegram.send("\n".join(lines))
        _run_later(_fetch_orders())
        return "ATOS X: bekleyen emirler sorgulanıyor"
    if cmd.startswith("/alarm"):
        parts = text.strip().split()
        if len(parts) == 1:
            return _alarm_list()
        sub = parts[1].lower()
        if sub in ("temizle", "sil", "clear"):
            n = sum(len(a) for a in _PRICE_ALERTS.values())
            _PRICE_ALERTS.clear()
            return f"ATOS X: {n} alarm silindi"
        if len(parts) >= 3:
            sym = parts[1].upper()
            try:
                target = float(parts[2])
            except ValueError:
                return "ATOS X: gecersiz fiyat"
            side = "above"
            if len(parts) >= 4 and parts[3].lower() in ("alt", "alti", "below", "down"):
                side = "below"
            current = auto_trader.live_prices.get(sym) if auto_trader else None
            if current is not None and side == "above" and current >= target:
                return f"ATOS X: {sym} zaten ${current:g} - esik ${target:g} ustunde"
            if current is not None and side == "below" and current <= target:
                return f"ATOS X: {sym} zaten ${current:g} - esik ${target:g} altinda"
            if sym not in _PRICE_ALERTS:
                _PRICE_ALERTS[sym] = []
            _PRICE_ALERTS[sym].append({
                "price": target, "side": side, "created": datetime.utcnow().isoformat()
            })
            side_word = "ustune cikinca" if side == "above" else "altina inince"
            return f"ATOS X: {sym} alarm eklendi - ${target:g} {side_word} bildir"
        return "ATOS X: kullanim /alarm <SEMBOL> <FIYAT> [ust/alt] | /alarm | /alarm temizle"
    if cmd.startswith("/bakiye"):
        if not auto_trader:
            return "ATOS X: motor calismiyor"
        pos = auto_trader.active_positions
        upnl = _positions_payload()['total_upnl']
        total_eq = auto_trader.equity + upnl
        long_n = sum(1 for p in pos.values() if p.get("side") == "BUY")
        short_n = len(pos) - long_n
        lines = [
            f"ATOS X bakiye:",
            f"Equity: ${auto_trader.equity:.2f}",
            f"Gerceklesmemis: {upnl:+.2f}",
            f"Toplam: ${total_eq:.2f}",
            f"Pozisyon: {len(pos)} (L:{long_n} S:{short_n})",
        ]
        if auto_trader.day_pnl != 0:
            sign = "+" if auto_trader.day_pnl >= 0 else ""
            lines.append(f"Gunluk PnL: {sign}{auto_trader.day_pnl:.2f}")
        if auto_trader.drawdown_pct > 0:
            lines.append(f"Drawdown: %{auto_trader.drawdown_pct:.1f}")
        if pos:
            now = datetime.utcnow()
            lines.append("---")
            for sym, p in pos.items():
                side = p.get("side", "?")
                entry = p.get("entry_price", 0)
                mark = auto_trader.live_prices.get(sym, entry)
                qty = p.get("quantity", 0)
                if side == "BUY":
                    pnl = (mark - entry) * qty
                else:
                    pnl = (entry - mark) * qty
                sign = "+" if pnl >= 0 else ""
                age_h = ""
                ot = p.get("open_time")
                if ot:
                    try:
                        h = (now - datetime.fromisoformat(ot)).total_seconds() / 3600
                        age_h = f" {h:.0f}h"
                    except Exception:
                        pass
                lines.append(f"  {sym} {side} ${entry:g} {sign}{pnl:.2f}{age_h}")
        return "\n".join(lines)
    if cmd.startswith("/ayarla"):
        s = strat_settings.get_settings()
        lines = [
            "ATOS X ayarlar:",
            f"Indikator: {s.get('leading_indicator', '')}",
            f"Risk/trade: %{s['risk_per_trade']*100:.1f} | Kaldirac: {s['max_leverage']}",
            f"Pozisyon limit: {s['max_open_positions']} | Max yas: {s['max_position_age_hours']}sa",
            f"R:R: {s.get('rr_ratio', 1.5)} | ATR: {s.get('atr_mult', 1.5)}",
            f"Trailing: %{s.get('trailing_activate_pct', 0):g} on / %{s.get('trailing_sl_pct', 0):g} off",
            f"Breakeven: %{s.get('breakeven_activate_pct', 0):g}",
            f"Drawdown: %{s['max_daily_loss_pct']:.0f} gunluk / %{s['max_drawdown_pct']:.0f} genel",
            f"Council: {'Acik' if s.get('use_decision_council') else 'Kapali'}",
            f"Score ranking: {'Acik' if s.get('use_score_ranking') else 'Kapali'}",
            f"Backfill: {s.get('data_backfill_hours', 0):g}sa | Tazelik: {s.get('data_freshness_hours', 12):g}sa",
            f"Min equity: ${s['min_equity']:.0f} | Max zarar: {s['max_consecutive_losses']}",
        ]
        return "\n".join(lines)
    return None

async def _run_backfill(symbols: list, days: int):
    """Arka planda CSV backfill calistirir, sonucu Telegram'dan bildirir."""
    try:
        res = await backfill_klines(auto_trader.binance, symbols,
                                    interval="4h", days=days)
        msg = (f"ATOS X backfill bitti: {len(res.get('written', []))} yazildi, "
               f"{len(res.get('failed', []))} hatali")
        if res.get("skipped"):
            msg += f", {len(res['skipped'])} atlandi"
        if res.get("failed"):
            msg += ": " + ", ".join(map(str, res["failed"]))
        await telegram.send(msg)
    except Exception as e:
        logger.error(f"Backfill hatasi: {e}")
        await telegram.send(f"ATOS X backfill hatasi: {e}")

async def _send_watchlist(symbols: list):
    """Oncelik sirasina gore canli coin skorlarini Telegram'a gonderir."""
    try:
        scored = []
        freshness = _data_freshness(len(symbols))
        fresh_map = {r["symbol"]: r.get("state", "missing") for r in freshness.get("rows", [])}
        for sym in symbols:
            try:
                df = loader.load_csv(sym, "4h", limit=30)
                info = coin_score(df)
                scored.append((info.get("score", 0.0), info.get("trend", "RANGE"),
                               info.get("momentum_pct", 0.0), sym))
            except Exception:
                scored.append((0.0, "RANGE", 0.0, sym))
        scored.sort(key=lambda x: x[0], reverse=True)
        lines = [f"ATOS X izleme ({len(scored)} sembol):"]
        for i, (sc, trend, mom, sym) in enumerate(scored, 1):
            icon = "🟢" if sc > 0.5 else ("🔴" if sc < -0.5 else "⚪")
            sign = "+" if mom >= 0 else ""
            state = fresh_map.get(sym, "missing")
            ds = "✓" if state == "ok" else ("⚠" if state == "stale" else "?")
            lines.append(f"{i}. {sym}  {sc:+.2f}  {sign}{mom:.1f}% {icon} {ds}")
        await telegram.send("\n".join(lines))
    except Exception as e:
        logger.error(f"Watchlist hatasi: {e}")
        await telegram.send(f"ATOS X izleme hatasi: {e}")

def _is_connected() -> bool:
    return bool(auto_trader and auto_trader.binance and auto_trader.binance.client)

def _data_freshness(limit: int = 100) -> dict:
    """Trading sembollerinin yerel CSV veri tazeligi durumu.

    `data_freshness_hours` eşiğine gore ok/stale/missing etiketlenir;
    otomatik backfill ile birlikte ops gorunurlugu saglar.
    """
    if not auto_trader:
        return {"ok": False, "error": "not_running", "count": 0,
                "fresh": 0, "stale": 0, "missing": 0, "rows": []}
    limit = max(1, min(limit, 300))
    symbols = (auto_trader.priority or auto_trader.trading_symbols)[:limit]
    fresh_h = float(strat_settings.get_settings().get("data_freshness_hours", 12.0))
    now = datetime.utcnow()
    rows, fresh, stale, missing = [], 0, 0, 0
    for symbol in symbols:
        try:
            df = loader.load_csv(symbol, "4h", limit=30)
            last = df.index[-1].to_pydatetime()
            if last.tzinfo is not None:
                last = last.replace(tzinfo=None)
            age_h = (now - last).total_seconds() / 3600.0
            state = "ok" if age_h <= fresh_h else "stale"
            if age_h > fresh_h:
                stale += 1
            else:
                fresh += 1
            rows.append({"symbol": symbol, "bars": len(df),
                         "last": last.isoformat(), "age_hours": round(age_h, 2),
                         "state": state})
        except Exception:
            missing += 1
            rows.append({"symbol": symbol, "bars": 0, "last": None,
                         "age_hours": None, "state": "missing"})
    order = {"ok": 0, "stale": 1, "missing": 2}
    rows.sort(key=lambda r: (order[r["state"]], r["symbol"]))
    return {"ok": True, "interval": "4h", "freshness_hours": fresh_h,
            "count": len(rows), "fresh": fresh, "stale": stale, "missing": missing,
            "rows": rows}

def _concentration_summary() -> dict:
    """Maruziyet ve aktif konsantrasyon engelleri (ops gorunurlugu)."""
    if not auto_trader:
        return {"long_pct": 0.0, "short_pct": 0.0, "blocks": [],
                "max_position_pct": 0.0, "max_side_pct": 0.0}
    equity = auto_trader.equity or 1.0
    long_n = short_n = 0.0
    for p in list(auto_trader.active_positions.values()):
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
                    data_status=_data_freshness(300),
                    protection_stats={
                        "trailing": sum(1 for t in auto_trader.trade_history
                                        if t.get("trailing")),
                        "breakeven": sum(1 for t in auto_trader.trade_history
                                         if t.get("breakeven")),
                    },
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

@app.get("/api/v1/market/regime")
async def market_regime(symbol: str = "BTCUSDT", interval: str = "4h"):
    """Tek sembol icin rejim + volatilite + likidite tespiti."""
    try:
        df = await app.state.binance.get_klines(symbol.upper(), interval, 400)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    out = analyze_market(df)
    out["ok"] = True
    out["symbol"] = symbol.upper()
    out["interval"] = interval
    return out

@app.get("/api/v1/market/regimes")
async def market_regimes(limit: int = 10, interval: str = "4h"):
    """Tarama listesi icin rejim ozeti (dashboard kartı)."""
    if not auto_trader:
        return {"regimes": [], "count": 0, "scanned": []}
    limit = max(1, min(limit, 30))
    candidates = (auto_trader.priority or auto_trader.trading_symbols)[:limit]
    if not candidates:
        return {"regimes": [], "count": 0, "scanned": []}

    async def fetch(symbol):
        try:
            df = await app.state.binance.get_klines(symbol, interval, 400)
            m = analyze_market(df)
            m["symbol"] = symbol
            return m
        except Exception:
            return None

    results = await asyncio.gather(*(fetch(s) for s in candidates))
    regimes = [r for r in results if r is not None]
    return {"regimes": regimes, "count": len(regimes), "scanned": candidates}

@app.get("/api/v1/market/scores")
async def market_scores(limit: int = 10, interval: str = "4h"):
    """Tarama listesi icin canli momentum/score siralamasi."""
    if not auto_trader:
        return {"scores": [], "count": 0, "scanned": []}
    limit = max(1, min(limit, 30))
    candidates = (auto_trader.priority or auto_trader.trading_symbols)[:limit]
    if not candidates:
        return {"scores": [], "count": 0, "scanned": []}

    async def fetch(symbol):
        try:
            df = await app.state.binance.get_klines(symbol, interval, 400)
            s = coin_score(df)
            s["symbol"] = symbol
            return s
        except Exception:
            return None

    results = await asyncio.gather(*(fetch(s) for s in candidates))
    scores = [r for r in results if r is not None]
    scores.sort(key=lambda s: s.get("score", 0.0), reverse=True)
    return {"scores": scores, "count": len(scores), "scanned": candidates}

@app.get("/api/v1/market/decision")
async def market_decision(symbol: str = "BTCUSDT", interval: str = "4h"):
    """Tek sembol icin Decision Council karari."""
    try:
        df = await app.state.binance.get_klines(symbol.upper(), interval, 400)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    out = decide_symbol(df)
    out["ok"] = True
    out["symbol"] = symbol.upper()
    out["interval"] = interval
    return out

@app.get("/api/v1/market/decisions")
async def market_decisions(limit: int = 10, interval: str = "4h"):
    """Tarama listesi icin Decision Council karar ozeti."""
    if not auto_trader:
        return {"decisions": [], "count": 0, "scanned": []}
    limit = max(1, min(limit, 30))
    candidates = (auto_trader.priority or auto_trader.trading_symbols)[:limit]
    if not candidates:
        return {"decisions": [], "count": 0, "scanned": []}

    async def fetch(symbol):
        try:
            df = await app.state.binance.get_klines(symbol, interval, 400)
            d = decide_symbol(df)
            d["symbol"] = symbol
            return d
        except Exception:
            return None

    results = await asyncio.gather(*(fetch(s) for s in candidates))
    decisions = [r for r in results if r is not None]
    order = {"BUY": 0, "SELL": 1, "HOLD": 2}
    decisions.sort(key=lambda d: (order.get(d["verdict"], 3), -d["confidence"]))
    return {"decisions": decisions, "count": len(decisions), "scanned": candidates}

@app.post("/api/v1/data/collect")
async def data_collect(symbols: str = "", interval: str = "4h", bars: int = 400,
                       skip_stablecoins: bool = True):
    """Belirtilen sembollerin kline'larini CSV arsivine toplar."""
    if not auto_trader or not auto_trader.binance:
        return {"ok": False, "error": "not_running"}
    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()] \
        or auto_trader.trading_symbols[:10]
    if not syms:
        return {"ok": False, "error": "empty_symbols"}
    res = await collect_klines(auto_trader.binance, syms, interval=interval,
                               bars=bars, skip_stablecoins=skip_stablecoins)
    res["ok"] = True
    return res

@app.post("/api/v1/data/backfill")
async def data_backfill(symbols: str = "", interval: str = "4h", days: int = 30,
                        skip_stablecoins: bool = True):
    """Sembollerin gecmis kline'larini parcalar halinde CSV arsivine yazar."""
    if not auto_trader or not auto_trader.binance:
        return {"ok": False, "error": "not_running"}
    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()] \
        or auto_trader.trading_symbols[:10]
    if not syms:
        return {"ok": False, "error": "empty_symbols"}
    res = await backfill_klines(auto_trader.binance, syms, interval=interval,
                                days=days, skip_stablecoins=skip_stablecoins)
    res["ok"] = True
    return res

@app.get("/api/v1/data/status")
async def data_status(limit: int = 100):
    """Trading sembollerinin CSV veri tazeligi durumu (ok/stale/missing)."""
    return _data_freshness(limit)

@app.post("/api/v1/data/backfill/stale")
async def data_backfill_stale(days: int = 30):
    """Eski/eksik CSV verisini otomatik sembol secimiyle backfill eder."""
    if not auto_trader or not auto_trader.binance:
        return {"ok": False, "error": "not_running"}
    st = _data_freshness(300)
    symbols = [r["symbol"] for r in st["rows"] if r["state"] != "ok"][:10]
    if not symbols:
        return {"ok": True, "written": [], "failed": [], "skipped": [],
                "symbols": [], "message": "backfill gereken sembol yok"}
    days = max(1, min(days, 90))
    res = await backfill_klines(auto_trader.binance, symbols, interval="4h",
                                days=days)
    res["ok"] = True
    res["symbols"] = symbols
    return res

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
        return {"count": 0, "symbols": [], "scanned": []}
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

@app.get("/api/v1/performance/summary")
async def performance_summary():
    """Haftalik/aylik/tum-zaman PnL + win rate ozeti (dashboard performans karti)."""
    if not auto_trader:
        return {"ok": False, "error": "not_running"}
    hist = auto_trader.trade_history
    now = datetime.utcnow()

    def _period_stats(trades, days):
        cutoff = now - timedelta(days=days)
        sel = [t for t in trades if t.get("time") and datetime.fromisoformat(str(t["time"][:19])) >= cutoff]
        pnls = [t.get("pnl", 0) or 0 for t in sel]
        wins = sum(1 for p in pnls if p > 0)
        losses = sum(1 for p in pnls if p < 0)
        gross_w = sum(p for p in pnls if p > 0)
        gross_l = abs(sum(p for p in pnls if p < 0))
        pf = gross_w / gross_l if gross_l > 0 else (float("inf") if gross_w > 0 else 0.0)
        return {
            "trades": len(pnls),
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / len(pnls) * 100, 1) if pnls else 0.0,
            "pnl": round(sum(pnls), 2),
            "pf": ("inf" if pf == float("inf") else round(pf, 2)),
        }

    return {
        "ok": True,
        "all_time": _period_stats(hist, 3650),
        "monthly": _period_stats(hist, 30),
        "weekly": _period_stats(hist, 7),
        "today": _period_stats(hist, 1),
    }

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

@app.get("/api/v1/portfolio")
async def portfolio():
    """Portfoy ozeti: equity (canli senkron), bakiye, gerceklesmemis PnL, pozisyonlar."""
    if not auto_trader:
        return {"mode": "paper", "equity": 10000.0, "positions": [], "count": 0}
    bal = auto_trader.live_balance or {}
    positions = []
    total_unrealized = 0.0
    for symbol, pos in auto_trader.active_positions.items():
        mark = auto_trader.live_prices.get(symbol)
        upnl, pct = _position_upnl(pos, mark)
        if upnl is not None:
            total_unrealized += upnl
        positions.append({
            "symbol": symbol,
            "side": pos["side"],
            "quantity": float(pos["quantity"]),
            "entry": float(pos["entry_price"]),
            "mark": mark,
            "notional": round(float(pos["entry_price"]) * float(pos["quantity"]), 2),
            "upnl": upnl,
            "upnl_pct": pct,
            "sl": pos.get("sl"),
            "tp": pos.get("tp"),
            "protected": bool(pos.get("sl_order_id") or pos.get("tp_order_id")),
        })
    equity = auto_trader.equity or 0.0
    peak = auto_trader.peak_equity or equity
    return {
        "mode": "paper" if auto_trader.paper else "live",
        "synced": bool(bal),
        "balance": bal.get("balance"),
        "available": bal.get("available"),
        "unrealized_pnl": bal.get("unrealized") if bal else round(total_unrealized, 2),
        "equity": round(equity, 2),
        "peak_equity": round(peak, 2),
        "drawdown_pct": auto_trader.drawdown_pct,
        "day_pnl": auto_trader.day_pnl,
        "positions": positions,
        "count": len(positions),
        "total_notional": round(sum(p["notional"] for p in positions), 2),
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

