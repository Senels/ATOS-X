from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
ENV_FILE = BACKEND_DIR / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ENV_FILE), extra="ignore")

    APP_NAME: str = "ATOS X"
    APP_VERSION: str = "1.0.0"
    APP_ENV: str = "dev"

    # Binance
    BINANCE_API_KEY: str = ""
    BINANCE_SECRET_KEY: str = ""
    BINANCE_TESTNET: bool = False

    # Paper mod: True = emirler borsaya gitmez, simule edilir
    PAPER_TRADING: bool = True

    # Canli emir kill-switch'i: False iken yeni pozisyon ACIS emirleri
    # (paper mod disinda) kod seviyesinde engellenir. Canliya gecmek icin
    # PAPER_TRADING=False + LIVE_TRADING_ENABLED=True ayarlanmasi gerekir.
    LIVE_TRADING_ENABLED: bool = False

    # Minimum pozisyon notionali (USDT); 0 = devre disi.
    # Sembolun altindaki girilen pozisyonlar acilmaz.
    MIN_NOTIONAL: float = 0.0

    # Gunluk Telegram ozet raporu (yerel saat, 0-23)
    DAILY_REPORT_HOUR: int = 21

    # Telegram
    TELEGRAM_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""

    # Guvenlik
    # Bos ise /api/v1 uclari korunmaz; dolu ise X-API-Key header'i zorunlu olur
    API_KEY: str = ""
    # Virgulle ayrilmis izinli Telegram chat_id listesi; bos ise filtre yok
    TELEGRAM_ALLOWED_CHAT_IDS: str = ""
    # Virgulle ayrilmis izinli CORS originleri; bos ise localhost varsayilani
    ALLOWED_ORIGINS: str = ""


def get_settings():
    return Settings()
