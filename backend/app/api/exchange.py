"""Binance gercek hesap baglantisi: API key/secret kayit + baglanti testi.

Anahtarlar `.env` dosyasina ve calisma zamani `os.environ`'a yazilir
(binance_client os.getenv ile okur). Secret hicbir GET endpoint'inden
donulmez; yalnizca maskeli son-4 gosterilir.
"""
import os
from pathlib import Path

from fastapi import APIRouter, Request

from app.exchange.binance_client import BinanceClient

router = APIRouter(prefix="/api/v1/exchange", tags=["exchange"])


def _env_path() -> Path:
    """Calisilan .env dosyasini bulur (cwd ya da backend kok)."""
    for p in (Path.cwd() / ".env", Path(__file__).resolve().parents[2] / ".env"):
        if p.exists():
            return p
    return Path.cwd() / ".env"


def _set_env(key: str, value: str):
    """Ani (os.environ) ve kalici (.env) olarak ayari gunceller."""
    os.environ[key] = value
    path = _env_path()
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    found = False
    for i, line in enumerate(lines):
        if line.strip().startswith(key + "="):
            lines[i] = f"{key}={value}"
            found = True
            break
    if not found:
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _masked(key: str) -> str:
    if not key:
        return ""
    return f"{key[:4]}...{key[-4:]}" if len(key) > 8 else "***"


@router.get("/status")
async def exchange_status(request: Request):
    binance = getattr(request.app.state, "binance", None)
    return {
        "has_api_key": bool(os.getenv("BINANCE_API_KEY", "")),
        "api_key_masked": _masked(os.getenv("BINANCE_API_KEY", "")),
        "testnet": os.getenv("BINANCE_TESTNET", "True").lower() == "true",
        "paper_trading": os.getenv("PAPER_TRADING", "True").lower() == "true",
        "live_trading_enabled": os.getenv("LIVE_TRADING_ENABLED", "False").lower() == "true",
        "connected": bool(binance and binance.client),
    }


@router.post("/credentials")
async def exchange_credentials(request: Request):
    """API key + secret + testnet flag kaydeder (.env + os.environ)."""
    data = await request.json()
    api_key = (data.get("api_key") or "").strip()
    secret = (data.get("secret") or "").strip()
    if not api_key or not secret:
        return {"status": "error", "message": "API key ve secret bos olamaz"}
    testnet = bool(data.get("testnet", True))
    _set_env("BINANCE_API_KEY", api_key)
    _set_env("BINANCE_SECRET_KEY", secret)
    _set_env("BINANCE_TESTNET", "True" if testnet else "False")
    return {
        "status": "ok",
        "message": "Anahtarlar kaydedildi. Motoru yeniden baslatin; yeni anahtarlarla baglanir.",
        "api_key_masked": _masked(api_key),
        "testnet": testnet,
    }


@router.post("/test")
async def exchange_test(request: Request):
    """Baglanti + bakiye testi.

    Body'de `api_key`/`secret`/`testnet` verilirse GECICI olarak onlarla test
    eder (kaydetmez); bos body ise kayitli (os.environ) anahtarlari kullanir.
    """
    body: dict = {}
    try:
        body = await request.json()
    except Exception:
        pass
    api_key = (body.get("api_key") or "").strip() or os.getenv("BINANCE_API_KEY", "")
    secret = (body.get("secret") or "").strip() or os.getenv("BINANCE_SECRET_KEY", "")
    testnet = body.get("testnet")
    client = BinanceClient()
    if api_key:
        client.api_key = api_key
    if secret:
        client.api_secret = secret
    if testnet is not None:
        client.testnet = bool(testnet)
    ok = await client.connect()
    if not ok:
        return {"status": "error", "message": "Baglanti kurulamadi (anahtar/testnet ayari?)"}
    try:
        bal = await client.get_account_balance()
        return {
            "status": "ok",
            "message": "Baglanti basarili",
            "testnet": client.testnet,
            "balance": bal["balance"],
            "available": bal["available"],
            "unrealized": bal["unrealized"],
        }
    except Exception as e:
        return {"status": "error", "message": f"Hesap bilgisi alinamadi: {e}"}
    finally:
        try:
            await client.close()
        except Exception:
            pass
