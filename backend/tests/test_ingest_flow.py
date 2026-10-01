"""Ingest endpoint behaviour with a fake Supabase + fake n8n (no network, no DB)."""
import base64
import hashlib
import io
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import logic
from app.main import app
from app.routers import scans
from app.security import require_station

BATCH = {
    "id": "11111111-1111-1111-1111-111111111111",
    "instructor_id": "i", "subject_id": "s", "term_id": "t", "created_by": "u",
    "created_at": (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(),
}


class _Query:
    def __init__(self, data):
        self._data = data

    def __getattr__(self, name):  # select / eq / limit ... are chainable no-ops
        return lambda *a, **k: self

    def execute(self):
        return SimpleNamespace(data=self._data, count=len(self._data))


class FakeSB:
    def __init__(self, claim_row):
        self.claim_row = claim_row
        self.rpcs = []

    def table(self, name):
        return _Query([dict(BATCH)] if name == "scan_batches" else [])

    def rpc(self, name, params):
        self.rpcs.append((name, params))
        data = [self.claim_row] if name == "claim_batch_item" else [True]
        return _Query(data)

    def completes(self):
        return [p for n, p in self.rpcs if n == "complete_batch_item"]


class FakeResp:
    def __init__(self, body, status=200):
        self._body, self.status_code = body, status

    def json(self):
        return self._body


class FakeN8n:
    def __init__(self, body, status=200, raises=None):
        self.body, self.status, self.calls, self.raises = body, status, 0, raises
        self.headers = None

    def post(self, url, json=None, headers=None):
        self.calls += 1
        self.headers = headers
        assert json["task_id"].startswith("web:")
        if self.raises:
            raise self.raises
        return FakeResp(self.body, self.status)


def _payload():
    buf = io.BytesIO()
    Image.new("L", (30, 30), 200).save(buf, format="JPEG")
    raw = buf.getvalue()
    return {
        "filename": "p1.jpg",
        "image_base64": base64.b64encode(raw).decode(),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }


@pytest.fixture
def setup(monkeypatch):
    def _setup(claim_row, n8n_body=None, n8n_status=200, max_attempts=5, raises=None):
        sb = FakeSB(claim_row)
        n8n = FakeN8n(n8n_body, n8n_status, raises)
        settings = SimpleNamespace(
            max_upload_mb=12, predate_grace_seconds=30, n8n_timeout_seconds=120,
            n8n_batch_webhook_url="http://n8n.invalid/hook", n8n_webhook_secret="s",
            simulate_scans=False, max_pipeline_attempts=max_attempts,
        )
        monkeypatch.setattr(scans, "get_supabase", lambda: sb)
        monkeypatch.setattr(scans, "get_settings", lambda: settings)
        monkeypatch.setattr(scans, "_n8n", lambda: n8n)
        app.dependency_overrides[require_station] = lambda: {"id": "st-1", "label": "Laptop 1"}
        return sb, n8n, TestClient(app, raise_server_exceptions=False, headers={"X-Station-Key": "test-station-key"})

    yield _setup
    app.dependency_overrides.clear()


def claimed(failures=0, attempts=1):
    return {"item_id": "item-1", "claimed": True, "item_status": "processing", "item_attempts": attempts,
            "item_retryable": True, "item_failures": failures}


UNSAVED = {"status": "flagged", "flagged": True, "review_reasons": ["internal_error_not_saved"]}


def test_unsaved_internal_error_is_retryable_502(setup):
    sb, n8n, client = setup(claimed(0), UNSAVED)
    r = client.post("/scans/ingest", json=_payload())
    assert r.status_code == 502
    d = r.json()["detail"]
    assert d["code"] == "pipeline_not_saved"
    assert "n8n.invalid" not in r.text
    (c,) = sb.completes()
    assert c["p_status"] == "error" and c["p_retryable"] is True  # not counted as flagged
    assert c["p_count_failure"] is True


def test_unsaved_internal_error_after_cap_is_permanent_422(setup):
    sb, n8n, client = setup(claimed(failures=4, attempts=9), UNSAVED)
    r = client.post("/scans/ingest", json=_payload())
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "failed_after_retries"
    (c,) = sb.completes()
    assert c["p_status"] == "error" and c["p_retryable"] is False


def test_permanently_failed_item_not_resent_to_n8n(setup):
    row = {"item_id": "item-1", "claimed": False, "item_status": "error", "item_attempts": 5, "item_retryable": False, "item_failures": 5}
    sb, n8n, client = setup(row, {"status": "success"})
    r = client.post("/scans/ingest", json=_payload())
    assert r.status_code == 422 and r.json()["detail"]["code"] == "failed_after_retries"
    assert n8n.calls == 0


def test_bad_http_status_counts_toward_cap(setup):
    sb, n8n, client = setup(claimed(4), {"oops": 1}, n8n_status=500)
    r = client.post("/scans/ingest", json=_payload())
    assert r.status_code == 422
    sb2, _, client2 = setup(claimed(1, attempts=20), {"oops": 1}, n8n_status=500)  # timeouts inflate attempts, not failures
    assert client2.post("/scans/ingest", json=_payload()).status_code == 502


def test_duplicate_response_recorded_as_duplicate(setup):
    sb, n8n, client = setup(claimed(), {"status": "duplicate", "flagged": False, "review_reasons": []})
    r = client.post("/scans/ingest", json=_payload())
    assert r.status_code == 200 and r.json()["status"] == "duplicate"
    (c,) = sb.completes()
    assert c["p_status"] == "duplicate"
    assert logic.COUNTER_FOR_STATUS[c["p_status"]] == "duplicate_count"


def test_regular_flagged_still_flagged(setup):
    sb, n8n, client = setup(claimed(), {"status": "flagged", "flagged": True, "review_reasons": ["low confidence"]})
    r = client.post("/scans/ingest", json=_payload())
    assert r.status_code == 200 and r.json()["flagged"] is True
    assert sb.completes()[0]["p_status"] == "flagged"


# ---------------------------------------------------------------- pure helpers
def test_is_unsaved_internal_error():
    v = logic.validate_n8n_response(UNSAVED)
    assert logic.is_unsaved_internal_error(v)
    assert logic.is_unsaved_internal_error({"status": "flagged", "review_reasons": [" Internal_Error_Not_Saved "]})
    assert not logic.is_unsaved_internal_error({"status": "flagged", "review_reasons": ["low confidence"]})
    assert not logic.is_unsaved_internal_error({"status": "success", "review_reasons": ["internal_error_not_saved"]})


def test_retries_exhausted():
    assert not logic.retries_exhausted(4, 5)
    assert logic.retries_exhausted(5, 5)
    assert not logic.retries_exhausted(None, 5)


def test_counter_mapping_matches_statuses():
    assert set(logic.COUNTER_FOR_STATUS) == set(logic.N8N_STATUSES)


# ---------------------------------------------------------------- security review items 2 + 3
def test_n8n_auth_rejection_is_config_error_not_counted(setup):
    for status in (401, 403):
        sb, n8n, client = setup(claimed(4), {"message": "unauthorized"}, n8n_status=status)
        r = client.post("/scans/ingest", json=_payload())
        assert r.status_code == 503
        assert r.json()["detail"]["code"] == "pipeline_misconfigured"
        (c,) = sb.completes()
        assert c["p_retryable"] is True and c["p_count_failure"] is False  # 4 prior failures, still no permanent fail
        assert n8n.headers == {"X-Webhook-Secret": "s"}


@pytest.mark.parametrize("exc,code,msg", [
    (httpx.ReadTimeout("t"), "pipeline_timeout", logic.PIPELINE_TIMEOUT_MSG),
    (httpx.ConnectError("c"), "pipeline_unavailable", logic.PIPELINE_UNAVAILABLE_MSG),
    (httpx.RemoteProtocolError("r"), "pipeline_unavailable", logic.PIPELINE_UNAVAILABLE_MSG),
])
def test_timeout_or_transport_error_is_retryable_error(setup, exc, code, msg):
    sb, n8n, client = setup(claimed(0), raises=exc)
    r = client.post("/scans/ingest", json=_payload())
    assert r.status_code == 502
    assert r.json()["detail"]["code"] == code
    assert "n8n.invalid" not in r.text
    # Stored as a retryable error ("Trying again" on the page), counted toward the cap.
    (c,) = sb.completes()
    assert c["p_status"] == "error" and c["p_retryable"] is True and c["p_count_failure"] is True
    assert c["p_message"] == msg
    assert logic.item_error_code({"status": "error", "retryable": True, "message": c["p_message"]}) == code


@pytest.mark.parametrize("exc", [httpx.ReadTimeout("t"), httpx.ConnectError("c")])
def test_timeout_or_transport_error_after_cap_is_permanent_422(setup, exc):
    sb, n8n, client = setup(claimed(failures=4, attempts=9), raises=exc)
    r = client.post("/scans/ingest", json=_payload())
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "failed_after_retries"
    (c,) = sb.completes()
    assert c["p_status"] == "error" and c["p_retryable"] is False


def test_marking_off_and_practice_mode_unchanged(setup, monkeypatch):
    sb, n8n, client = setup(claimed(4))
    s = scans.get_settings()
    s.n8n_batch_webhook_url = ""
    r = client.post("/scans/ingest", json=_payload())
    assert r.status_code == 503 and r.json()["detail"]["code"] == "pipeline_not_configured"
    (c,) = sb.completes()
    assert c["p_retryable"] is True and c["p_count_failure"] is False and n8n.calls == 0
    s.simulate_scans = True
    sb.rpcs.clear()
    r = client.post("/scans/ingest", json=_payload())
    assert r.status_code == 200 and r.json()["simulated"] is True and n8n.calls == 0


# ---------------------------------------------------------------- not_saved_permanent + staff_id
PERMANENT = {"status": "flagged", "flagged": True, "review_reasons": ["invalid_request", "not_saved_permanent"]}


def test_not_saved_permanent_is_permanent_422_not_flagged(setup):
    sb, n8n, client = setup(claimed(0), PERMANENT)
    r = client.post("/scans/ingest", json=_payload())
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "pipeline_rejected_request"
    assert "n8n.invalid" not in r.text
    (c,) = sb.completes()
    assert c["p_status"] == "error" and c["p_retryable"] is False  # never counted as flagged
    assert n8n.calls == 1


def test_internal_error_not_saved_still_retryable(setup):
    sb, n8n, client = setup(claimed(0), UNSAVED)
    assert client.post("/scans/ingest", json=_payload()).status_code == 502


def test_staff_id_always_sent(setup, monkeypatch):
    class Capture(FakeN8n):
        def post(self, url, json=None, headers=None):
            self.payload = json
            return super().post(url, json=json, headers=headers)
    sb, _, client = setup(claimed(), {"status": "success"})
    cap = Capture({"status": "success"})
    monkeypatch.setattr(scans, "_n8n", lambda: cap)
    assert client.post("/scans/ingest", json=_payload()).status_code == 200
    assert cap.payload["staff_id"] == BATCH["created_by"]


def test_staff_id_falls_back_to_station_staff(setup, monkeypatch):
    sb, _, client = setup(claimed(), {"status": "success"})
    monkeypatch.setitem(BATCH, "created_by", None)
    app.dependency_overrides[require_station] = lambda: {"id": "st-1", "label": "L1", "current_staff_id": "staff-9"}
    cap = FakeN8n({"status": "success"})
    seen = {}
    orig = cap.post
    cap.post = lambda url, json=None, headers=None: (seen.update(json), orig(url, json=json, headers=headers))[1]
    monkeypatch.setattr(scans, "_n8n", lambda: cap)
    assert client.post("/scans/ingest", json=_payload()).status_code == 200
    assert seen["staff_id"] == "staff-9"


def test_is_permanent_not_saved():
    assert logic.is_permanent_not_saved(logic.validate_n8n_response(PERMANENT))
    assert not logic.is_permanent_not_saved(logic.validate_n8n_response(UNSAVED))
    assert not logic.is_unsaved_internal_error(logic.validate_n8n_response(PERMANENT))
