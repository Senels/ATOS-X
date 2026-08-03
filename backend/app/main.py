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
    await telegram.send(f"ATOS X v{settings.APP_VERSION} baslatildi!")

    yield
    system_status["status"] = "shutting_down"
    if daily_report_task:
        daily_report_task.cancel()
    await auto_trader.stop()
    await ws.stop()
    await app.state.binance.close()

async def on_price_update(symbol: str, price: float):
    if auto_trader:
        auto_trader.update_price(symbol, price)

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
        "positions": len(auto_trader.active_positions) if auto_trader else 0,
        "trades": len(auto_trader.trade_history) if auto_trader else 0,
        "uptime": int((datetime.utcnow() - system_status["start_time"]).total_seconds())
    }

@app.get("/api/v1/status")
async def get_status():
    return {
        "status": system_status["status"],
        "symbols": len(auto_trader.trading_symbols) if auto_trader else 0,
        "positions": len(auto_trader.active_positions) if auto_trader else 0,
        "trades": len(auto_trader.trade_history) if auto_trader else 0,
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
    return {"positions": auto_trader.active_positions if auto_trader else {}, "count": len(auto_trader.active_positions) if auto_trader else 0}

@app.get("/api/v1/trades")
async def get_trades():
    return {"trades": auto_trader.trade_history[-50:] if auto_trader else [], "count": len(auto_trader.trade_history) if auto_trader else 0}

@app.post("/api/v1/emergency_stop")
async def emergency_stop():
    if auto_trader:
        await auto_trader.stop()
        return {"status": "ok", "message": "All positions closed"}
    return {"status": "error", "message": "Not running"}

@app.get("/dashboard/metrics")
async def metrics():
    try:
        return {
            "status": system_status["status"],
            "symbols": len(auto_trader.trading_symbols) if auto_trader else 0,
            "active_positions": len(auto_trader.active_positions) if auto_trader else 0,
            "total_trades": len(auto_trader.trade_history) if auto_trader else 0,
            "equity": auto_trader.equity if auto_trader else 10000,
            "paper": auto_trader.paper if auto_trader else True,
            "top_symbols": auto_trader.top_symbols if auto_trader else [],
            "positions": auto_trader.active_positions if auto_trader else {},
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

