"""Security-review fixes: startup config check, body cap, Pillow bombs,
close-batch ownership, placeholder instructors. No network, no DB."""
import io
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import logic
from app.body_limit import DEFAULT_MAX_BYTES, INGEST_MAX_BYTES
from app.db import ConfigError, validate_settings
from app.main import app
from app.routers import batches, instructors
from app.security import CurrentUser, require_staff

client = TestClient(app, raise_server_exceptions=False)


class _Q:
    def __init__(self, data):
        self._data = data

    def __getattr__(self, name):
        return lambda *a, **k: self

    def execute(self):
        return SimpleNamespace(data=self._data, count=len(self._data))


class FakeSB:
    def __init__(self, tables):
        self.tables = tables
        self.updates = []

    def table(self, name):
        rows = self.tables.get(name, [])
        q = _Q(rows)
        if name == "scan_batches":
            def update(values, _rows=rows):
                self.updates.append(values)
                return _Q([dict(r, **values) for r in _rows])
            q.update = update
        return q


# ---------------------------------------------------------------- item 2
def test_startup_refuses_webhook_without_secret():
    with pytest.raises(ConfigError) as e:
        validate_settings(SimpleNamespace(n8n_batch_webhook_url="https://n8n.example/hook", n8n_webhook_secret=""))
    assert "N8N_WEBHOOK_SECRET" in str(e.value)
    assert "n8n.example" not in str(e.value)  # never echo the URL
    validate_settings(SimpleNamespace(n8n_batch_webhook_url="https://n8n.example/hook", n8n_webhook_secret="x"))
    validate_settings(SimpleNamespace(n8n_batch_webhook_url="", n8n_webhook_secret=""))


# ---------------------------------------------------------------- item 4
def test_ingest_declared_oversize_rejected_before_station_auth():
    r = client.post("/scans/ingest", content=b"x" * (INGEST_MAX_BYTES + 1), headers={"Content-Type": "application/json"})
    assert r.status_code == 413  # not 401: rejected before auth and JSON parsing
    assert r.json()["detail"]["code"] == "payload_too_large"


def test_ingest_chunked_oversize_rejected():
    def gen():
        chunk = b"x" * (1024 * 1024)
        for _ in range(18):
            yield chunk
    r = client.post("/scans/ingest", content=gen(),
                    headers={"Content-Type": "application/json", "X-Station-Key": "some-key"})
    assert r.status_code == 413


def test_other_endpoints_have_small_cap():
    r = client.post("/auth/login", content=b"x" * (DEFAULT_MAX_BYTES + 1), headers={"Content-Type": "application/json"})
    assert r.status_code == 413


def test_small_body_passes_through_to_app():
    r = client.post("/auth/login", json={"email": "a"})  # reaches FastAPI validation
    assert r.status_code == 422


# ---------------------------------------------------------------- item 5
def test_decompression_bomb_rejected_by_dimensions():
    buf = io.BytesIO()
    Image.new("L", (8000, 6000), 255).save(buf, format="PNG")  # 48 MP, tiny file
    with pytest.raises(logic.ImageValidationError) as e:
        logic.normalize_to_jpeg(buf.getvalue())
    assert e.value.status == 413 and e.value.code == "image_too_large"


def test_pillow_limits_configured():
    import importlib
    import warnings
    importlib.reload(logic)  # pytest resets warning filters per test; re-apply module setup
    assert Image.MAX_IMAGE_PIXELS == logic.MAX_IMAGE_PIXELS == 12_000_000
    assert any(f[0] == "error" and f[2] is Image.DecompressionBombWarning for f in warnings.filters)


def test_normal_page_still_accepted():
    buf = io.BytesIO()
    Image.new("L", (1700, 2200), 255).save(buf, format="JPEG")  # ~200 dpi letter page
    assert logic.normalize_to_jpeg(buf.getvalue())


# ---------------------------------------------------------------- item 6
BID = "22222222-2222-2222-2222-222222222222"
BATCH = {"id": BID, "status": "open", "station_id": "st1", "created_by": "creator",
         "created_at": "2026-09-26T10:00:00Z", "last_scan_at": None}


def _user(uid, role="SAO_STAFF"):
    return CurrentUser(id=uid, email=None, first_name=None, last_name=None, role=role, token="t")


@pytest.fixture
def close_env(monkeypatch):
    def _env(user, bound_staff="someone-else"):
        sb = FakeSB({"scan_batches": [dict(BATCH)], "stations": [{"current_staff_id": bound_staff}], "scan_batch_items": []})
        monkeypatch.setattr(batches, "get_supabase", lambda: sb)
        monkeypatch.setattr(batches, "audit", lambda *a, **k: None)
        app.dependency_overrides[require_staff] = lambda: user
        return sb
    yield _env
    app.dependency_overrides.clear()


def test_close_refused_for_unrelated_staff(close_env):
    sb = close_env(_user("stranger"))
    r = client.post(f"/batches/{BID}/close", json={})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "not_your_batch"
    assert sb.updates == []


def test_close_refused_for_staff_only_bound_to_the_station(close_env):
    # Security review: taking over the laptop must not let you close its batch.
    sb = close_env(_user("operator"), "operator")
    r = client.post(f"/batches/{BID}/close", json={})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "not_your_batch"
    assert sb.updates == []


@pytest.mark.parametrize("user,bound", [
    (_user("creator"), "someone-else"),       # started the batch
    (_user("creator"), None),                 # started it, logged out since
    (_user("boss", "SAO_ADMIN"), "someone-else"),  # admin override
])
def test_close_allowed_for_owner_or_admin(close_env, user, bound):
    sb = close_env(user, bound)
    r = client.post(f"/batches/{BID}/close", json={})
    assert r.status_code == 200, r.text
    assert sb.updates and sb.updates[0]["closed_by"] == user.id


# ---------------------------------------------------------------- item 10
def test_placeholder_instructor_is_valid_even_though_disabled(monkeypatch):
    row = {"id": "11111111-1111-1111-1111-111111111111", "first_name": "A", "last_name": "B",
           "account_status": "pending", "disabled": True, "is_placeholder": True}
    monkeypatch.setattr(instructors, "get_supabase", lambda: FakeSB({"user_info": [row], "department_table": []}))
    assert instructors.get_instructor_if_eligible(row["id"]) == row


def test_disabled_non_placeholder_is_invalid(monkeypatch):
    row = {"id": "11111111-1111-1111-1111-111111111111", "account_status": "disabled", "disabled": True, "is_placeholder": False}
    monkeypatch.setattr(instructors, "get_supabase", lambda: FakeSB({"user_info": [row], "department_table": [{"id": 1}]}))
    assert instructors.get_instructor_if_eligible(row["id"]) is None
