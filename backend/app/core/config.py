import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    APP_NAME: str = "ATOS X"
    APP_VERSION: str = "1.0.0"
    APP_ENV: str = "dev"

    # Binance
    BINANCE_API_KEY: str = ""
    BINANCE_SECRET_KEY: str = ""
    BINANCE_TESTNET: bool = True

    # Paper mod: True = emirler borsaya gitmez, simule edilir
    PAPER_TRADING: bool = True

    # Telegram
    TELEGRAM_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""

    class Config:
        env_file = ".env"
        extra = "ignore"

def get_settings():
    return Settings()
