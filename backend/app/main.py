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
    telegram.start_listener(_telegram_command)
    await telegram.send(f"ATOS X v{settings.APP_VERSION} baslatildi!")

    yield
    system_status["status"] = "shutting_down"
    if daily_report_task:
        daily_report_task.cancel()
    telegram.stop_listener()
    await auto_trader.stop()
    await ws.stop()
    await app.state.binance.close()

async def on_price_update(symbol: str, price: float):
    if auto_trader:
        auto_trader.update_price(symbol, price)

def _protected_count() -> int:
    """Exchange-side SL/TP ile korunan acik pozisyon sayisi."""
    if not auto_trader:
        return 0
    return sum(
        1 for p in auto_trader.active_positions.values()
        if p.get("sl_order_id") or p.get("tp_order_id")
    )

def _positions_payload() -> dict:
    """Acil pozisyonlari koruma durumu isaretli olarak doner."""
    positions = auto_trader.active_positions if auto_trader else {}
    enriched = {
        symbol: {**pos,
                 "protected": bool(pos.get("sl_order_id") or pos.get("tp_order_id")),
                 "trailing": bool(pos.get("trailing"))}
        for symbol, pos in positions.items()
    }
    return {
        "positions": enriched,
        "count": len(positions),
        "protected": sum(1 for p in enriched.values() if p["protected"]),
        "unprotected": sum(1 for p in enriched.values() if not p["protected"]),
    }

def _run_later(coro) -> bool:
    """Calisan event loop varsa coroutine'i arka planda calistirir."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    asyncio.create_task(coro)
    return True

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
                "/durdur - acil durdurma (tum pozisyonlari kapatir)\n"
                "/ac - motoru yeniden baslatir\n"
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
            lines.append(f"{sym} {pos['side']} qty={pos['quantity']} @ ${pos['entry_price']} {prot}")
        return "\n".join(lines)
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
        return (
            f"ATOS X durum\n"
            f"Trade motoru: {trading}\n"
            f"Equity: ${auto_trader.equity:.2f}\n"
            f"Acik pozisyon: {len(auto_trader.active_positions)} (korumali: {_protected_count()})\n"
            f"Maruziyet - LONG: %{conc['long_pct']} SHORT: %{conc['short_pct']}\n"
            f"Engeller: {', '.join(blocks) if blocks else 'yok'}\n"
            f"Drawdown: %{auto_trader.drawdown_pct} | Durma: {halt_line}\n"
            f"Risk olayi: {len(events)} (son: {evt_line})"
        )
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
                )
        except Exception as e:
            logger.error(f"Gunluk rapor hatasi: {e}")

app = FastAPI(title="ATOS X API", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.include_router(backtest_router)

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
        "trading": auto_trader.running if auto_trader else False,
        "uptime": int((datetime.utcnow() - system_status["start_time"]).total_seconds())
    }

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
        "trading": auto_trader.running if auto_trader else False,
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
async def risk_events(limit: int = 50):
    events = auto_trader.risk_events[-limit:] if auto_trader else []
    return {"events": events, "count": len(events)}

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

