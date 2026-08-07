"""Binance gercek hesap baglantisi (exchange API) testleri.

Anahtarlar `.env`/os.environ'a yazilir; testler gecici tmp .env uzerinden
calsir ve anahtarlari asla donup donmedigini dogrular.
"""
import asyncio
import os

import pytest

from app.api import exchange as ex


@pytest.fixture
def env_tmp(tmp_path, monkeypatch):
    """Gecici .env dosyasi (cwd)."""
    p = tmp_path / ".env"
    p.write_text("BINANCE_API_KEY=\nBINANCE_SECRET_KEY=\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    for k in ("BINANCE_API_KEY", "BINANCE_SECRET_KEY", "BINANCE_TESTNET"):
        monkeypatch.delenv(k, raising=False)
    return p


def test_env_path_uses_cwd(env_tmp):
    assert ex._env_path() == env_tmp


def test_set_env_writes_file_and_os_environ(env_tmp):
    ex._set_env("BINANCE_API_KEY", "abc123")
    assert os.environ["BINANCE_API_KEY"] == "abc123"
    assert "BINANCE_API_KEY=abc123" in env_tmp.read_text(encoding="utf-8")
    ex._set_env("BINANCE_API_KEY", "xyz789")
    lines = [l for l in env_tmp.read_text(encoding="utf-8").splitlines() if l.startswith("BINANCE_API_KEY=")]
    assert lines == ["BINANCE_API_KEY=xyz789"]


def test_set_env_appends_new_key(env_tmp):
    ex._set_env("BINANCE_TESTNET", "False")
    assert "BINANCE_TESTNET=False" in env_tmp.read_text(encoding="utf-8")


def test_masked():
    assert ex._masked("") == ""
    assert ex._masked("123456789012") == "1234...9012"
    assert ex._masked("short") == "***"


def test_credentials_roundtrip(monkeypatch, env_tmp):
    class Req:
        app = type("App", (), {"state": type("S", (), {"binance": None})})()
        async def json(self):
            return {"api_key": "AAAABBBBCCCCDDDD", "secret": "s3cr3t", "testnet": False}

    res = asyncio.run(ex.exchange_credentials(Req()))
    assert res["status"] == "ok"
    assert res["api_key_masked"] == "AAAA...DDDD"
    assert "s3cr3t" not in str(res)
    assert os.environ["BINANCE_API_KEY"] == "AAAABBBBCCCCDDDD"
    assert os.environ["BINANCE_TESTNET"] == "False"


def test_credentials_requires_keys(monkeypatch, env_tmp):
    class Req:
        async def json(self):
            return {"api_key": "", "secret": ""}

    res = asyncio.run(ex.exchange_credentials(Req()))
    assert res["status"] == "error"


def test_status_masks_secret(monkeypatch, env_tmp):
    monkeypatch.setenv("BINANCE_API_KEY", "ABCDEFGH12345678")
    class App:
        state = type("S", (), {"binance": type("B", (), {"client": None})})()
    res = asyncio.run(ex.exchange_status(type("Req", (), {"app": App})()))
    assert res["api_key_masked"] == "ABCD...5678"
    assert "ABCDEFGH12345678" not in str(res)
    assert res["connected"] is False


class _FakeBc:
    """BinanceClient yerine gecici surum: anahtar bilgisini toplar, balance dondurur."""

    def __init__(self):
        self.api_key = ""
        self.api_secret = ""
        self.testnet = True
        self.client = None

    async def connect(self):
        self.client = object()
        return True

    async def get_account_balance(self):
        return {"balance": 100.0, "available": 50.0, "unrealized": 10.0}

    async def close(self):
        pass


class _BodyReq:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


def test_test_uses_temp_credentials(monkeypatch, env_tmp):
    captured = {}

    def fake_bc():
        bc = _FakeBc()
        captured["bc"] = bc
        return bc

    monkeypatch.setattr(ex, "BinanceClient", fake_bc)
    res = asyncio.run(ex.exchange_test(_BodyReq(
        {"api_key": "TEMPKEY123", "secret": "temps3cr3t", "testnet": False}
    )))
    assert res["status"] == "ok"
    assert res["balance"] == 100.0
    assert captured["bc"].api_key == "TEMPKEY123"
    assert captured["bc"].api_secret == "temps3cr3t"
    assert captured["bc"].testnet is False
    assert "temps3cr3t" not in str(res)


def test_test_falls_back_to_env(monkeypatch, env_tmp):
    monkeypatch.setenv("BINANCE_API_KEY", "ENVKEY")
    monkeypatch.setenv("BINANCE_SECRET_KEY", "envsecret")
    captured = {}

    def fake_bc():
        bc = _FakeBc()
        captured["bc"] = bc
        return bc

    monkeypatch.setattr(ex, "BinanceClient", fake_bc)
    res = asyncio.run(ex.exchange_test(_BodyReq({})))
    assert res["status"] == "ok"
    assert captured["bc"].api_key == "ENVKEY"
    assert captured["bc"].api_secret == "envsecret"


def test_test_connect_failure(monkeypatch, env_tmp):
    class _FailingBc(_FakeBc):
        async def connect(self):
            return False

    monkeypatch.setattr(ex, "BinanceClient", _FailingBc)
    res = asyncio.run(ex.exchange_test(_BodyReq({})))
    assert res["status"] == "error"
