from app.core.config import get_settings


def test_defaults():
    settings = get_settings()
    assert settings.APP_NAME == "ATOS X"
    assert settings.APP_ENV == "dev"
    assert settings.BINANCE_TESTNET is True
    assert settings.LEVERAGE == 10


def test_env_override(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("BINANCE_TESTNET", "false")
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.APP_ENV == "test"
    assert settings.BINANCE_TESTNET is False
    get_settings.cache_clear()
