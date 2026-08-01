import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import get_settings
from app.core.events import bus
from app.exchange.binance_client import BinanceClient


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    bus.bind(asyncio.get_running_loop())
    app.state.binance = BinanceClient()
    yield
    await app.state.binance.close()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.APP_NAME, version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, object]:
        return {
            "status": "ok",
            "app": settings.APP_NAME,
            "env": settings.APP_ENV,
            "testnet": settings.BINANCE_TESTNET,
        }

    return app


app = create_app()
