import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from datetime import datetime
from pathlib import Path

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
system_status = {"status": "initializing", "start_time": datetime.utcnow()}

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    global auto_trader
    system_status["status"] = "starting"
    strat_settings.load()

    app.state.binance = BinanceClient()
    await app.state.binance.connect()
    app.state.db = Database()

    auto_trader = AutoTrader(app.state.binance, telegram=telegram)
    app.state.auto_trader = auto_trader
    asyncio.create_task(auto_trader.start())

    app.state.ws = ws
    symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "ADAUSDT"]
    for symbol in symbols:
        ws.subscribe(symbol, on_price_update)
    await ws.start(symbols)

    system_status["status"] = "online"
    await telegram.send(f"ATOS X v{settings.APP_VERSION} baslatildi!")

    yield
    system_status["status"] = "shutting_down"
    await auto_trader.stop()
    await ws.stop()
    await app.state.binance.close()

async def on_price_update(symbol: str, price: float):
    if auto_trader:
        auto_trader.update_price(symbol, price)

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
        "equity": auto_trader.equity if auto_trader else 10000
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

