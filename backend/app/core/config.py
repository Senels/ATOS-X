from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_NAME: str = "ATOS X"
    APP_ENV: str = "dev"
    LOG_LEVEL: str = "INFO"

    DATABASE_URL: str = "postgresql+asyncpg://atosx:atosx@localhost:5432/atosx"
    REDIS_URL: str = "redis://localhost:6379/0"

    BINANCE_API_KEY: str = ""
    BINANCE_API_SECRET: str = ""
    BINANCE_TESTNET: bool = True
    BINANCE_REST_BASE: str = "https://fapi.binance.com"
    BINANCE_WS_BASE: str = "wss://fstream.binance.com"

    LEVERAGE: int = 10
    MAX_CONCURRENT_POSITIONS: int = 4
    RISK_PER_TRADE_PCT: float = 0.30
    KELLY_FRACTIONAL: float = 0.50
    SL_ATR_MULT: float = 1.5
    TP_ATR_MULT: float = 6.0
    TRAIL_ACTIVATION_MULT: float = 0.3
    TRAIL_DISTANCE_MULT: float = 1.5


@lru_cache
def get_settings() -> Settings:
    return Settings()
