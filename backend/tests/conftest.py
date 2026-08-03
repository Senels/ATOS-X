import pytest

from app.data import loader

_DATA_DIR = loader.DEFAULT_DATA_DIR / "futures_4h_data"


@pytest.fixture(scope="module")
def btc_df():
    if not (_DATA_DIR / "BTCUSDT_4h.csv").exists():
        pytest.skip("BTCUSDT_4h.csv yok; CSV verisine bagli testler atlandi")
    return loader.load_csv("BTCUSDT", "4h")
