"""HTTP-level checks that never reach the database (no auth -> rejected early)."""
import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app, raise_server_exceptions=False)


def test_health_is_public():
    assert client.get("/health").json() == {"status": "ok"}


@pytest.mark.parametrize("method,path", [
    ("get", "/stations"),
    ("post", "/stations"),
    ("get", "/departments"),
    ("get", "/instructors/search?q=ab"),
    ("post", "/instructors"),
    ("post", "/instructors/00000000-0000-0000-0000-000000000000/activate"),
    ("get", "/subjects/search?q=ab"),
    ("post", "/batches/start"),
    ("get", "/batches/00000000-0000-0000-0000-000000000000"),
    ("post", "/batches/00000000-0000-0000-0000-000000000000/close"),
    ("get", "/watcher/download"),
    ("get", "/config"),
    ("get", "/auth/me"),
])
def test_staff_endpoints_require_bearer(method, path):
    r = getattr(client, method)(path)
    assert r.status_code in (401, 422), (path, r.status_code)
    if r.status_code == 401:
        assert r.json()["detail"]["code"] == "not_authenticated"


def test_malformed_bearer_rejected():
    r = client.get("/stations", headers={"Authorization": "Basic abc"})
    assert r.status_code == 401


@pytest.mark.parametrize("path", ["/scans/ingest", "/scans/heartbeat", "/scans/offline"])
def test_watcher_endpoints_require_station_key(path):
    r = client.post(path, json={})
    assert r.status_code in (401, 422)
    r = client.post(path, json={"filename": "a", "image_base64": "x" * 10, "sha256": "a" * 64, "captured_at": "2026-01-01T00:00:00Z"})
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "invalid_station_key"


def test_cors_preflight_allowed_origin_no_credentials():
    r = client.options("/stations", headers={
        "Origin": "http://localhost:5173",
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "authorization",
    })
    assert r.status_code == 200
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert "access-control-allow-credentials" not in r.headers


def test_cors_rejects_random_lan_origin_by_default():
    r = client.options("/stations", headers={
        "Origin": "http://192.168.1.50:5173",
        "Access-Control-Request-Method": "GET",
    })
    assert r.headers.get("access-control-allow-origin") is None
