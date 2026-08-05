from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


class APIKeyMiddleware(BaseHTTPMiddleware):
    """/api/v1 uclarini X-API-Key header'i ile korur.

    `api_key` bos ise (dev/test) istekler oldugu gibi gecer; dolu ise
    /api/v1 prefix'li tum isteklerde X-API-Key eslesmesi beklenir.
    """

    def __init__(self, app, api_key: str = ""):
        super().__init__(app)
        self.api_key = (api_key or "").strip()

    async def dispatch(self, request, call_next):
        if (self.api_key and request.url.path.startswith("/api/v1")
                and request.headers.get("X-API-Key") != self.api_key):
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        return await call_next(request)


def parse_chat_ids(raw: str) -> set:
    """Virgul/noktali virgul ayracli chat_id listesini set'e cevirir.

    Bos/None girdi bos set dondurur (whitelist yok = filtre yok).
    """
    if not raw:
        return set()
    ids = set()
    for part in str(raw).replace(";", ",").split(","):
        part = part.strip()
        if part:
            ids.add(part)
    return ids


def is_authorized_chat(chat_id, allowed: set) -> bool:
    """Chat id whitelist kontrolu; allowed bos ise herkes yetkilidir."""
    if not allowed:
        return True
    if chat_id is None:
        return False
    return str(chat_id) in allowed
