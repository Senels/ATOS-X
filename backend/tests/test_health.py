from fastapi.testclient import TestClient

from app.main import app


def test_health():
    with TestClient(app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in ("online", "starting", "initializing")
    assert "uptime" in body


def test_dashboard_pages():
    client = TestClient(app)
    cases = [
        ("/dashboard/html", "ATOS X Dashboard"),
        ("/dashboard/settings", "ATOS X - Strategy Manager"),
    ]
    for path, marker in cases:
        resp = client.get(path)
        assert resp.status_code == 200
        assert marker in resp.text, f"{path} dosyasi bulunamadi"
    client.close()
