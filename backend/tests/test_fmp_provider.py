"""FMP provider entegrasyon testleri.

Canlý testler yapmaz — sadece FMPProvider sýnýfÝnðn doðru yapÝyý kurduðunu
ve API key olmadıðında boþ veri döndürdüðünü kontrol eder.
"""

import pytest

from app.data.providers import FMPProvider


def test_provider_name():
    p = FMPProvider()
    assert p.name == "fmp"
    assert p.ratelimit > 0


@pytest.mark.asyncio
async def test_no_api_key_returns_empty():
    """FMP_API_KEY olmadan API çağrısı yapılmaz."""
    p = FMPProvider(api_key="")
    assert await p.economic_indicator(name="GDP") == []
    assert await p.treasury_rates() == []
    assert await p.economic_calendar() == []
    assert await p.quote("AAPL") is None


@pytest.mark.asyncio
async def test_quote_without_key():
    p = FMPProvider(api_key=None)
    assert await p.quote("AAPL") is None


def test_params_has_apikey():
    p = FMPProvider(api_key="test123")
    params = p._params({"name": "CPI"})
    assert params["apikey"] == "test123"
    assert params["name"] == "CPI"


def test_params_without_key():
    p = FMPProvider(api_key=None)
    params = p._params({"name": "CPI"})
    assert "apikey" not in params
    assert params["name"] == "CPI"
