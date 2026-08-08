from pathlib import Path

from app.core.config import BACKEND_DIR, ENV_FILE, get_settings


def test_defaults():
    settings = get_settings()
    assert settings.APP_NAME == "ATOS X"
    assert settings.APP_ENV == "dev"
    assert settings.BINANCE_TESTNET is False


def test_env_override(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("BINANCE_TESTNET", "false")
    settings = get_settings()
    assert settings.APP_ENV == "test"
    assert settings.BINANCE_TESTNET is False


def test_env_file_resolves_to_backend_dotenv():
    assert BACKEND_DIR == Path(__file__).resolve().parents[1]
    assert ENV_FILE == BACKEND_DIR / ".env"
    assert ENV_FILE.is_absolute()
