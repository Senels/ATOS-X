from app.core.config import get_settings
from app.core.security import APIKeyMiddleware, is_authorized_chat, parse_chat_ids
from app.notifications.telegram import _process_updates
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route


async def _ok(request):
    return JSONResponse({"ok": True})


def _make_app(api_key: str = "") -> Starlette:
    app = Starlette(routes=[
        Route("/api/v1/test", _ok),
        Route("/api/v1/data/status", _ok),
        Route("/health", _ok),
        Route("/", _ok),
    ])
    app.add_middleware(APIKeyMiddleware, api_key=api_key)
    return app


# ---- config ----

def test_settings_has_security_fields():
    s = get_settings()
    assert hasattr(s, "API_KEY")
    assert hasattr(s, "TELEGRAM_ALLOWED_CHAT_IDS")
    assert hasattr(s, "ALLOWED_ORIGINS")


# ---- parse_chat_ids ----

def test_parse_chat_ids_empty():
    assert parse_chat_ids("") == set()
    assert parse_chat_ids(None) == set()
    assert parse_chat_ids("   , ; ,") == set()


def test_parse_chat_ids_comma_semicolon():
    assert parse_chat_ids("123,-100456789") == {"123", "-100456789"}
    assert parse_chat_ids("123;456; 789 ") == {"123", "456", "789"}


# ---- is_authorized_chat ----

def test_is_authorized_chat_no_whitelist():
    assert is_authorized_chat("anything", set()) is True
    assert is_authorized_chat(None, set()) is True


def test_is_authorized_chat_matches():
    allowed = {"123", "-100456789"}
    assert is_authorized_chat(123, allowed) is True
    assert is_authorized_chat("-100456789", allowed) is True
    assert is_authorized_chat(999, allowed) is False
    assert is_authorized_chat(None, allowed) is False


# ---- APIKeyMiddleware ----

def test_middleware_disabled_when_no_key():
    with TestClient(_make_app(api_key="")) as client:
        assert client.get("/api/v1/test").status_code == 200
        assert client.get("/health").status_code == 200


def test_middleware_rejects_missing_or_wrong_key():
    with TestClient(_make_app(api_key="sekret")) as client:
        assert client.get("/api/v1/test").status_code == 401
        assert client.get("/api/v1/test", headers={"X-API-Key": "wrong"}).status_code == 401
        assert client.get("/api/v1/test", headers={"X-API-Key": "SEKRET"}).status_code == 401


def test_middleware_accepts_correct_key():
    with TestClient(_make_app(api_key="sekret")) as client:
        r = client.get("/api/v1/test", headers={"X-API-Key": "sekret"})
        assert r.status_code == 200
        assert r.json() == {"ok": True}


def test_middleware_leaves_non_api_paths_open():
    with TestClient(_make_app(api_key="sekret")) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/").status_code == 200


# ---- Telegram chat whitelist ----

def _handler(text):
    return f"handled:{text}"


def test_process_updates_no_whitelist_all_processed():
    updates = [
        {"update_id": 1, "message": {"text": "/durum", "chat": {"id": 111}}},
        {"update_id": 2, "message": {"text": "/rapor", "chat": {"id": 999}}},
    ]
    offset, replies = _process_updates(updates, _handler)
    assert offset == 3
    assert replies == ["handled:/durum", "handled:/rapor"]


def test_process_updates_whitelist_filters_unauthorized():
    updates = [
        {"update_id": 1, "message": {"text": "/durdur onay", "chat": {"id": 111}}},
        {"update_id": 2, "message": {"text": "/durum", "chat": {"id": 222}}},
    ]
    offset, replies = _process_updates(updates, _handler, {"222"})
    assert offset == 3
    assert replies == ["handled:/durum"]


def test_process_updates_whitelist_skips_non_text():
    updates = [
        {"update_id": 1, "message": {"chat": {"id": 222}}},
        {"update_id": 2, "message": {"text": "", "chat": {"id": 222}}},
    ]
    offset, replies = _process_updates(updates, _handler, {"222"})
    assert offset == 3
    assert replies == []
