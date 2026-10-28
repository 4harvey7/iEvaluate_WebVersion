"""Pages marking could not save (app/unsaved_pages.py, migration 008): kept at
ingest, listed on the review page, "Try again" and "Enter the answers by hand",
and the batch counters. In-memory fake Supabase + fake n8n: no network, no DB."""
import base64
import hashlib
import io
import uuid
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from postgrest.exceptions import APIError

from app import audit as audit_module
from app import logic
from app import unsaved_pages as uv
from app.main import app
from app.routers import batches, reviews, scans
from app.security import require_staff, require_station
from tests.test_reviews import (
    ADMIN,
    BATCH,
    IT101,
    JUAN,
    OUTSIDER,
    ROLE_BY_NAME,
    STAFF,
    STATION,
    STATION_STAFF,
    TERM,
    FakeDB,
    FakeQuery,
    FakeResp,
    _ago,
    _jpeg_b64,
    _now,
    seed,
    user,
)

U1 = "f0000000-0000-0000-0000-000000000001"
I1 = "f1000000-0000-0000-0000-000000000001"
TASK = f"web:{BATCH}:ffff000000000001"
SHA = "ffff000000000001" + "0" * 48
REASONS = ["omr_agreement_below_80", "python_weak_disagreement_1_rows", "internal_error_not_saved"]
UNIQUE = {"failed_scan_queue": "task_id", "scan_unsaved_pages": "item_id"}


# ------------------------------------------------------------------ fake PostgREST with insert / delete / rpc
class Q(FakeQuery):
    def _missing_table(self):
        if self.table in self.db.missing_tables:
            raise APIError({"code": "PGRST205",
                            "message": f"Could not find the table 'public.{self.table}' in the schema cache"})

    def select(self, cols="*", count=None, head=False):
        self._missing_table()
        return super().select(cols, count, head)

    def update(self, values):
        self._missing_table()
        return super().update(values)

    def insert(self, values):
        self._missing_table()
        self.op, self.values = "insert", dict(values)
        return self

    def delete(self):
        self.op = "delete"
        return self

    def or_(self, *_a, **_k):
        return self

    def execute(self):
        rows = self.db.tables.setdefault(self.table, [])
        if self.op == "insert":
            new = dict(self.values)
            key = UNIQUE.get(self.table)
            if key and new.get(key) is not None and any(r.get(key) == new.get(key) for r in rows):
                raise APIError({"code": "23505", "message": "duplicate key value violates unique constraint"})
            new.setdefault("id", str(uuid.uuid4()))
            new.setdefault("created_at", _now().isoformat())
            if self.table == "scan_unsaved_pages":
                new.setdefault("version", 0)
                new.setdefault("updated_at", new["created_at"])
            if self.table == "failed_scan_queue":
                new.update({k: v for k, v in {"updated_at": new["created_at"], "review_version": 0, "review_state": None,
                            "review_action": None, "review_error": None, "review_submitted_at": None,
                            "review_claimed_by": None, "review_claimed_at": None, "review_correction": None,
                            "validated_by": None, "validated_at": None}.items() if k not in new})
            rows.append(new)
            self.db.inserts.append((self.table, new))
            return SimpleNamespace(data=[dict(new)], count=1)
        if self.op == "delete":
            keep = [r for r in rows if not self._matches(r)]
            gone = len(rows) - len(keep)
            rows[:] = keep
            return SimpleNamespace(data=[], count=gone)
        return super().execute()


class DB(FakeDB):
    def __init__(self, migrated=True, unsaved_table=True):
        super().__init__(migrated)
        self.missing_tables = set() if unsaved_table else {"scan_unsaved_pages"}
        self.inserts, self.rpcs = [], []

    def table(self, name):
        return Q(self, name)

    def touch(self, table, old, new):
        if table == "scan_unsaved_pages":  # migration 008 trigger
            new["updated_at"] = _now().isoformat()
            new["version"] = int(old.get("version") or 0) + 1
            return
        super().touch(table, old, new)

    def rpc(self, name, params):
        self.rpcs.append((name, dict(params)))
        result = None
        if name == "complete_batch_item":
            result = self._complete(params)
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=result))

    def _complete(self, p):
        """complete_batch_item() from migration 002: only 'processing' rows move."""
        item = next((r for r in self.tables.get("scan_batch_items", []) if r["id"] == p["p_item_id"]), None)
        if not item or item["status"] != "processing":
            return False
        status = p["p_status"]
        item.update(status=status, review_reasons=p.get("p_review_reasons") or [], identity=p.get("p_identity"),
                    message=(p.get("p_message") or None), retryable=(status != "error" or p.get("p_retryable", True)))
        if status == "error" and p.get("p_count_failure"):
            item["pipeline_failures"] = int(item.get("pipeline_failures") or 0) + 1
        field = logic.COUNTER_FOR_STATUS.get(status)
        if field:
            b = next(r for r in self.tables["scan_batches"] if r["id"] == item["batch_id"])
            b[field] = int(b.get(field) or 0) + 1
        return True

    def counters(self):
        b = next(r for r in self.tables["scan_batches"] if r["id"] == BATCH)
        return {k: b.get(k) or 0 for k in ("processed_count", "flagged_count", "rejected_count", "duplicate_count")}


def _unsaved(**kw):
    row = {
        "id": U1, "item_id": I1, "batch_id": BATCH, "task_id": TASK, "filename": "Scan_20260930_104119_001.png",
        "image_base64": _jpeg_b64(), "header_image_base64": None,
        "page_meta": {"file_sha256": SHA, "station_id": STATION, "orientation_applied": 90,
                      "orientation_confidence": 0.9, "preprocess": "grey_pad_5", "paper_size": "LONG"},
        "review_reasons": list(REASONS), "message": "NOT SAVED: database error in n8n (credentials). Send this page again.",
        "status": "needs_review", "outcome": None, "watcher_failures": 5, "website_attempts": 0, "last_error": None,
        "fsq_id": None, "acted_by": None, "sending_started_at": None, "version": 2,
        "created_at": _ago(minutes=15), "updated_at": _ago(minutes=15),
    }
    row.update(kw)
    return row


def _item(**kw):
    row = {"id": I1, "batch_id": BATCH, "station_id": STATION, "file_sha256": SHA, "filename": "Scan_20260930_104119_001.png",
           "task_id": TASK, "status": "error", "retryable": False, "review_reasons": list(REASONS),
           "message": logic.UNSAVED_REVIEW_MSG + " (5 automatic tries: Scoring pipeline internal error; page not saved.)",
           "identity": None, "simulated": False, "attempts": 5, "pipeline_failures": 5,
           "orientation_applied": 0, "orientation_confidence": 0.9, "orientation_uncertain": False,
           "created_at": _ago(minutes=20), "updated_at": _ago(minutes=15)}
    row.update(kw)
    return row


class N8n:
    def __init__(self, body=None, status=200, raises=None):
        self.body, self.status, self.raises = body, status, raises
        self.calls = []

    def post(self, url, json=None, headers=None):
        self.calls.append((url, json, headers))
        if self.raises:
            raise self.raises
        return FakeResp(self.status, self.body)


SETTINGS = SimpleNamespace(
    n8n_batch_webhook_url="http://n8n.invalid/webhook/sast-batch-scan",
    n8n_correction_webhook_url="http://n8n.invalid/webhook/sast-manual-correction",
    n8n_import_error_webhook_url="http://n8n.invalid/webhook/import-error-correction",
    n8n_webhook_secret="s", n8n_correction_timeout_seconds=180, n8n_timeout_seconds=360,
)


@pytest.fixture
def env(monkeypatch):
    state = {}

    def _setup(n8n=None, as_user=STAFF, role="SAO_STAFF", settings=SETTINGS, unsaved_rows=None, items=None,
               unsaved_table=True, correction=None):
        db = seed(DB(unsaved_table=unsaved_table))
        for b in db.tables["scan_batches"]:
            b.update(processed_count=0, flagged_count=0, rejected_count=0, duplicate_count=0)
        db.tables["scan_batch_items"] = items if items is not None else [_item()]
        db.tables["scan_unsaved_pages"] = unsaved_rows if unsaved_rows is not None else [_unsaved()]
        batch_n8n = n8n or N8n({"status": "success", "flagged": False, "review_reasons": [], "message": "Saved."})
        corr_n8n = correction or N8n({"success": True})
        audits = []
        monkeypatch.setattr(reviews, "get_supabase", lambda: db)
        monkeypatch.setattr(reviews, "get_settings", lambda: settings)
        monkeypatch.setattr(reviews, "_batch_n8n", lambda: batch_n8n)
        # the retry pool runs inline here (one test below uses the real pool)
        monkeypatch.setattr(reviews, "_retry_pool_submit", lambda fn, *a: fn(*a))
        reviews._retry_inflight["n"] = 0
        monkeypatch.setattr(reviews, "_n8n", lambda: corr_n8n)
        monkeypatch.setattr(reviews, "role_maps", lambda: (ROLE_BY_NAME, {v: k for k, v in ROLE_BY_NAME.items()}))
        monkeypatch.setattr(reviews, "audit", lambda uid, action, meta=None: audits.append((uid, action, meta)))
        monkeypatch.setattr(audit_module, "get_supabase", lambda: (_ for _ in ()).throw(AssertionError("no real DB")))
        reviews._mig.update(missing_until=0.0, warned=False)
        reviews._ctx_cache.clear()
        uv.reset_state()
        app.dependency_overrides[require_staff] = lambda: user(as_user, role)
        state.update(db=db, n8n=batch_n8n, audits=audits, correction=corr_n8n)
        return db, batch_n8n, TestClient(app, raise_server_exceptions=False, headers={"X-Station-Key": "test-station-key"})

    _setup.state = state
    yield _setup
    app.dependency_overrides.clear()
    uv.reset_state()
    reviews._retry_inflight["n"] = 0


def _row(db, table, rid):
    return next(r for r in db.tables[table] if str(r["id"]) == str(rid))


# ------------------------------------------------------------------ list + image + access
def test_list_shows_unsaved_pages_with_plain_reasons(env):
    db, _, client = env()
    d = client.get(f"/reviews/batch/{BATCH}").json()
    assert d["unsaved_ready"] is True and d["retry_ready"] is True and d["unsaved_pending"] == 1
    (u,) = d["unsaved"]
    assert u["id"] == U1 and u["status"] == "needs_review" and u["filename"] == "Scan_20260930_104119_001.png"
    # the "not saved" code is the headline, not a reason to show
    assert u["review_reasons"] == ["omr_agreement_below_80", "python_weak_disagreement_1_rows"]
    assert u["can_retry"] is True and u["can_enter_by_hand"] is True and u["has_image"] is True
    assert "image_base64" not in u and "page_meta" not in u
    assert d["counts"] == {"queue_pending": 2, "queue_done": 1, "import_pending": 1}  # unchanged shape


def test_unsaved_list_and_actions_need_batch_access(env):
    db, _, client = env(as_user=OUTSIDER)
    d = client.get(f"/reviews/batch/{BATCH}")
    assert d.status_code == 403  # no row of theirs in this batch
    assert client.get(f"/reviews/unsaved/{U1}/image").status_code == 403
    assert client.post(f"/reviews/unsaved/{U1}/retry", json={"expected_version": 2}).json()["detail"]["code"] == "not_your_batch"
    assert client.post(f"/reviews/unsaved/{U1}/to-review", json={"expected_version": 2}).status_code == 403
    assert _row(db, "scan_unsaved_pages", U1)["status"] == "needs_review"
    for who, role in ((STAFF, "SAO_STAFF"), (ADMIN, "SAO_ADMIN")):
        app.dependency_overrides[require_staff] = lambda who=who, role=role: user(who, role)
        assert len(client.get(f"/reviews/batch/{BATCH}").json()["unsaved"]) == 1
    # Logged in at the batch's laptop but didn't start it: no access (security review).
    app.dependency_overrides[require_staff] = lambda: user(STATION_STAFF, "SAO_STAFF")
    assert client.get(f"/reviews/batch/{BATCH}").status_code == 403
    assert client.get(f"/reviews/unsaved/{U1}/image").status_code == 403


def test_unsaved_image(env):
    _, _, client = env()
    r = client.get(f"/reviews/unsaved/{U1}/image")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert r.content[:3] == b"\xff\xd8\xff"
    assert client.get("/reviews/unsaved/not-a-uuid/image").status_code == 404
    assert client.get(f"/reviews/unsaved/{uuid.uuid4()}/image").status_code == 404


def test_unsaved_routes_require_login():
    app.dependency_overrides.clear()
    client = TestClient(app, raise_server_exceptions=False, headers={"X-Station-Key": "test-station-key"})
    for method, path in (("get", f"/reviews/unsaved/{U1}/image"), ("post", f"/reviews/unsaved/{U1}/retry"),
                         ("post", f"/reviews/unsaved/{U1}/to-review")):
        r = getattr(client, method)(path, json={"expected_version": 0}) if method == "post" else client.get(path)
        assert r.status_code == 401, path


def test_without_migration_008_everything_still_works(env):
    db, _, client = env(unsaved_table=False)
    d = client.get(f"/reviews/batch/{BATCH}").json()
    assert d["unsaved_ready"] is False and d["unsaved"] == [] and d["unsaved_pending"] == 0
    assert d["queue"]  # the rest of the page is unaffected
    uv.reset_state()
    r = client.post(f"/reviews/unsaved/{U1}/retry", json={"expected_version": 2})
    assert r.status_code == 503 and r.json()["detail"]["code"] == "migration_required"


# ------------------------------------------------------------------ Try again
def test_retry_success_is_counted_once(env):
    db, n8n, client = env()
    r = client.post(f"/reviews/unsaved/{U1}/retry", json={"expected_version": 2})
    assert r.status_code == 200, r.text
    assert r.json()["outcome"] == "sending" and r.json()["row"]["status"] == "sending"
    # background part ran: n8n got the kept copy with the ingest contract
    (url, payload, headers) = n8n.calls[0]
    assert url == SETTINGS.n8n_batch_webhook_url and headers == {"X-Webhook-Secret": "s"}
    assert payload["task_id"] == TASK and payload["image"] == _unsaved()["image_base64"]
    assert payload["batch_id"] == BATCH and payload["instructor_id"] == JUAN and payload["subject_id"] == IT101
    assert payload["staff_id"] == STAFF and payload["paper_size"] == "LONG" and payload["orientation_applied"] == 90
    assert payload["file_sha256"] == SHA and payload["mime_type"] == "image/jpeg"
    u = _row(db, "scan_unsaved_pages", U1)
    assert u["status"] == "saved" and u["outcome"] == "success" and u["website_attempts"] == 1
    assert _row(db, "scan_batch_items", I1)["status"] == "success"
    assert db.counters()["processed_count"] == 1
    # a second click (same or new version) changes nothing and counts nothing
    again = client.post(f"/reviews/unsaved/{U1}/retry", json={"expected_version": u["version"]})
    assert again.status_code == 409 and again.json()["detail"]["code"] == "already_resolved"
    assert len(n8n.calls) == 1 and db.counters()["processed_count"] == 1
    actions = [a[1] for a in env.state["audits"]]
    assert sorted(actions) == ["scan_unsaved_retry", "scan_unsaved_retry_result"]
    assert "n8n.invalid" not in r.text


def test_retry_not_saved_again_waits_for_review(env):
    db, _, client = env(n8n=N8n({"status": "flagged", "flagged": True, "review_reasons": ["omr_agreement_below_80",
                                 "internal_error_not_saved"], "message": "NOT SAVED: database error in n8n (x)."}))
    assert client.post(f"/reviews/unsaved/{U1}/retry", json={"expected_version": 2}).status_code == 200
    u = _row(db, "scan_unsaved_pages", U1)
    assert u["status"] == "needs_review" and u["last_error"] == uv.RETRY_NOT_SAVED_MSG and u["website_attempts"] == 1
    assert u["review_reasons"] == ["omr_agreement_below_80", "internal_error_not_saved"]
    item = _row(db, "scan_batch_items", I1)
    assert item["status"] == "error" and item["retryable"] is False
    assert logic.item_error_code(item) == "not_saved_needs_review"
    assert db.counters() == {"processed_count": 0, "flagged_count": 0, "rejected_count": 0, "duplicate_count": 0}
    d = client.get(f"/reviews/batch/{BATCH}").json()
    assert d["unsaved"][0]["last_error"] == uv.RETRY_NOT_SAVED_MSG and d["unsaved_pending"] == 1


def test_retry_flagged_goes_to_the_review_queue_count(env):
    db, _, client = env(n8n=N8n({"status": "flagged", "flagged": True, "review_reasons": ["omr_agreement_below_80"],
                                 "message": "Flagged for manual review."}))
    client.post(f"/reviews/unsaved/{U1}/retry", json={"expected_version": 2})
    assert _row(db, "scan_unsaved_pages", U1)["outcome"] == "flagged"
    assert _row(db, "scan_batch_items", I1)["status"] == "flagged" and db.counters()["flagged_count"] == 1


def test_retry_duplicate_is_counted_where_the_page_is(env):
    db, _, client = env(n8n=N8n({"status": "duplicate", "flagged": False, "review_reasons": []}))
    db.tables["sast_all_raw_data_survey"].append({"task_id": TASK})
    client.post(f"/reviews/unsaved/{U1}/retry", json={"expected_version": 2})
    assert _row(db, "scan_unsaved_pages", U1)["outcome"] == "success"
    assert db.counters()["processed_count"] == 1 and db.counters()["duplicate_count"] == 0


@pytest.mark.parametrize("exc,msg", [(httpx.ReadTimeout("t"), uv.RETRY_TIMEOUT_MSG),
                                     (httpx.ConnectError("c"), uv.RETRY_UNREACHABLE_MSG)])
def test_retry_timeout_or_unreachable(env, exc, msg):
    db, _, client = env(n8n=N8n(raises=exc))
    assert client.post(f"/reviews/unsaved/{U1}/retry", json={"expected_version": 2}).status_code == 200
    u = _row(db, "scan_unsaved_pages", U1)
    assert u["status"] == "needs_review" and u["last_error"] == msg
    item = _row(db, "scan_batch_items", I1)
    assert item["status"] == "error" and item["pipeline_failures"] == 5  # not counted as a failure


def test_retry_http_error_and_bad_secret(env):
    for status, msg in ((500, "HTTP 500"), (403, "N8N_WEBHOOK_SECRET")):
        db, _, client = env(n8n=N8n({"x": 1}, status=status))
        client.post(f"/reviews/unsaved/{U1}/retry", json={"expected_version": 2})
        assert msg in _row(db, "scan_unsaved_pages", U1)["last_error"]
        assert _row(db, "scan_batch_items", I1)["status"] == "error"


def test_retry_guards(env):
    db, n8n, client = env()
    r = client.post(f"/reviews/unsaved/{U1}/retry", json={"expected_version": 1})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "stale_row"
    _row(db, "scan_unsaved_pages", U1)["website_attempts"] = uv.MAX_WEBSITE_ATTEMPTS
    r = client.post(f"/reviews/unsaved/{U1}/retry", json={"expected_version": 2})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "too_many_tries"
    assert client.get(f"/reviews/batch/{BATCH}").json()["unsaved"][0]["can_retry"] is False
    _row(db, "scan_unsaved_pages", U1)["website_attempts"] = 0
    _row(db, "scan_unsaved_pages", U1)["status"] = "sending"
    r = client.post(f"/reviews/unsaved/{U1}/retry", json={"expected_version": 2})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "in_progress"
    assert n8n.calls == []


def test_retry_not_configured(env):
    db, n8n, client = env(settings=SimpleNamespace(**{**vars(SETTINGS), "n8n_batch_webhook_url": ""}))
    r = client.post(f"/reviews/unsaved/{U1}/retry", json={"expected_version": 2})
    assert r.status_code == 503 and r.json()["detail"]["code"] == "pipeline_not_configured"
    assert client.get(f"/reviews/batch/{BATCH}").json()["unsaved"][0]["can_retry"] is False


def test_retry_when_item_changed_meanwhile(env):
    db, n8n, client = env(items=[_item(status="success", retryable=True)])
    r = client.post(f"/reviews/unsaved/{U1}/retry", json={"expected_version": 2})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "item_changed"
    u = _row(db, "scan_unsaved_pages", U1)
    assert u["status"] == "needs_review" and u["website_attempts"] == 0 and n8n.calls == []


def test_retry_success_clears_the_kept_image(env):
    db, _, client = env()
    client.post(f"/reviews/unsaved/{U1}/retry", json={"expected_version": 2})
    u = _row(db, "scan_unsaved_pages", U1)
    assert u["image_base64"] == "" and u["header_image_base64"] is None
    assert client.get(f"/reviews/batch/{BATCH}").json()["unsaved"][0]["has_image"] is False
    assert client.get(f"/reviews/unsaved/{U1}/image").status_code == 404


# ------------------------------------------------------------------ retry pool
def test_retry_busy_pool_answers_429_and_changes_nothing(env):
    db, n8n, client = env()
    reviews._retry_inflight["n"] = reviews.RETRY_WORKERS
    r = client.post(f"/reviews/unsaved/{U1}/retry", json={"expected_version": 2})
    assert r.status_code == 429 and r.json()["detail"]["code"] == "retry_busy"
    u = _row(db, "scan_unsaved_pages", U1)
    assert u["status"] == "needs_review" and u["version"] == 2 and u["website_attempts"] == 0
    assert _row(db, "scan_batch_items", I1)["status"] == "error" and n8n.calls == []
    assert reviews._retry_inflight["n"] == reviews.RETRY_WORKERS  # a refused click holds no slot


def test_retry_slots_are_released(env):
    db, _, client = env()
    assert client.post(f"/reviews/unsaved/{U1}/retry", json={"expected_version": 9}).status_code == 409  # stale
    assert reviews._retry_inflight["n"] == 0
    assert client.post(f"/reviews/unsaved/{U1}/retry", json={"expected_version": 2}).status_code == 200
    assert reviews._retry_inflight["n"] == 0  # the worker released it when done


def test_retry_runs_on_its_own_pool_not_the_request_threads(env, monkeypatch):
    import threading
    seen, futures = [], []

    class Recording(N8n):
        def post(self, url, json=None, headers=None):
            seen.append(threading.current_thread().name)
            return super().post(url, json=json, headers=headers)

    db, _, client = env(n8n=Recording({"status": "success", "review_reasons": []}))
    monkeypatch.setattr(reviews, "_retry_pool_submit", lambda fn, *a: futures.append(reviews._retry_pool.submit(fn, *a)))
    assert client.post(f"/reviews/unsaved/{U1}/retry", json={"expected_version": 2}).status_code == 200
    futures[0].result(timeout=10)
    assert seen and seen[0].startswith("unsaved-retry")
    assert _row(db, "scan_unsaved_pages", U1)["status"] == "saved" and reviews._retry_inflight["n"] == 0


# ------------------------------------------------------------------ list housekeeping
def test_list_repairs_an_item_left_behind_by_a_saved_page(env):
    db, _, client = env(unsaved_rows=[_unsaved(status="saved", outcome="success", image_base64="")])
    client.get(f"/reviews/batch/{BATCH}")
    assert _row(db, "scan_batch_items", I1)["status"] == "success" and db.counters()["processed_count"] == 1
    client.get(f"/reviews/batch/{BATCH}")
    assert db.counters()["processed_count"] == 1  # repeating the repair never counts twice


def test_list_repairs_an_item_left_behind_by_hand_entry(env):
    db, _, client = env(unsaved_rows=[_unsaved(status="manual", fsq_id=str(uuid.uuid4()), image_base64="")],
                        items=[_item(status="processing")])
    client.get(f"/reviews/batch/{BATCH}")
    item = _row(db, "scan_batch_items", I1)
    assert item["status"] == "flagged" and "not_saved_by_pipeline" in item["review_reasons"]
    assert db.counters()["flagged_count"] == 1


def test_list_repairs_an_item_stuck_processing_behind_a_waiting_page(env):
    db, _, client = env(items=[_item(status="processing", updated_at=_ago(minutes=10))])
    client.get(f"/reviews/batch/{BATCH}")
    item = _row(db, "scan_batch_items", I1)
    assert item["status"] == "error" and item["retryable"] is False and logic.item_error_code(item) == "not_saved_needs_review"


def test_closed_batch_turns_laptop_retries_into_review(env):
    db, _, client = env(unsaved_rows=[_unsaved(status="retrying")],
                        items=[_item(retryable=True, message="Scoring pipeline internal error; page not saved.")])
    d = client.get(f"/reviews/batch/{BATCH}").json()
    assert d["unsaved"][0]["status"] == "retrying"  # batch still open: the laptop keeps trying
    next(b for b in db.tables["scan_batches"] if b["id"] == BATCH)["status"] = "closed"
    d = client.get(f"/reviews/batch/{BATCH}").json()
    assert d["unsaved"][0]["status"] == "needs_review" and d["unsaved"][0]["can_retry"] is True
    assert d["unsaved_pending"] == 1
    item = _row(db, "scan_batch_items", I1)
    assert item["status"] == "error" and item["retryable"] is False and logic.item_error_code(item) == "not_saved_needs_review"
    assert item["pipeline_failures"] == 5  # not counted as another failure



def test_list_gives_up_on_a_try_that_never_finished(env):
    db, _, client = env(unsaved_rows=[_unsaved(status="sending", sending_started_at=_ago(minutes=20))],
                        items=[_item(status="processing", retryable=True)])
    (u,) = client.get(f"/reviews/batch/{BATCH}").json()["unsaved"]
    assert u["status"] == "needs_review" and u["last_error"] == uv.RETRY_STALE_MSG
    assert _row(db, "scan_batch_items", I1)["status"] == "error"


def test_list_leaves_a_recent_try_alone(env):
    db, _, client = env(unsaved_rows=[_unsaved(status="sending", sending_started_at=_ago(seconds=30))],
                        items=[_item(status="processing", retryable=True)])
    (u,) = client.get(f"/reviews/batch/{BATCH}").json()["unsaved"]
    assert u["status"] == "sending"


def test_list_settles_a_page_marking_stored_later(env):
    db, _, client = env()
    db.tables["sast_all_raw_data_survey"].append({"task_id": TASK})
    d = client.get(f"/reviews/batch/{BATCH}").json()
    assert d["unsaved"][0]["status"] == "saved" and d["unsaved"][0]["outcome"] == "success"
    assert d["unsaved_pending"] == 0 and db.counters()["processed_count"] == 1
    client.get(f"/reviews/batch/{BATCH}")
    assert db.counters()["processed_count"] == 1  # never twice


def test_since_cursor_still_sends_open_rows(env):
    db, _, client = env()
    cursor = client.get(f"/reviews/batch/{BATCH}").json()["cursor"]
    d = client.get(f"/reviews/batch/{BATCH}", params={"since": cursor}).json()
    assert [u["id"] for u in d["unsaved"]] == [U1] and d["unsaved_pending"] == 1


# ------------------------------------------------------------------ Enter the answers by hand
def test_to_review_makes_a_normal_queue_row_and_approve_works(env):
    db, _, client = env()
    r = client.post(f"/reviews/unsaved/{U1}/to-review", json={"expected_version": 2})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["outcome"] == "moved" and d["row"]["status"] == "manual"
    q = d["queue_row"]
    assert q["task_id"] == TASK and q["status"] == "pending" and q["has_image"] is True
    assert q["review_reasons"] == ["omr_agreement_below_80", "python_weak_disagreement_1_rows", "not_saved_by_pipeline"]
    assert q["suggested_instructor"]["id"] == JUAN and q["suggested_subject"]["id"] == IT101
    assert q["filename"] == "Scan_20260930_104119_001.png" and all(v is None for v in q["ratings"].values())

    fsq = _row(db, "failed_scan_queue", q["id"])
    assert fsq["user_id"] == STAFF and fsq["batch_id"] == BATCH and fsq["term_id"] == TERM
    assert fsq["validation_status"] == "flagged" and fsq["partial_data"]["source"] == "website_batch"
    assert fsq["partial_data"]["instructor"] == "Juan Dela Cruz" and fsq["partial_data"]["subject"] == "IT 101"
    img = _row(db, "scan_error_images", fsq["scan_image_id"])
    assert img["task_id"] == TASK and img["image_base64"] == _unsaved()["image_base64"]
    assert _row(db, "scan_unsaved_pages", U1)["fsq_id"] == q["id"]
    item = _row(db, "scan_batch_items", I1)
    assert item["status"] == "flagged" and db.counters()["flagged_count"] == 1

    # second click: nothing new
    again = client.post(f"/reviews/unsaved/{U1}/to-review", json={"expected_version": 3})
    assert again.status_code == 409 and again.json()["detail"]["code"] == "already_resolved"
    assert len([t for t, _ in db.inserts if t == "failed_scan_queue"]) == 1 and db.counters()["flagged_count"] == 1

    # the existing approve flow takes it from here
    body = {"expected_version": q["review_version"], "instructor_id": JUAN, "subject_id": IT101, "student_id": "2021-0042",
            "remarks": "", "ratings": {"m1": 5, "m2": 5, "m3": 5, "m4": 5, "m5": 5}}
    a = client.post(f"/reviews/queue/{q['id']}/approve", json=body)
    assert a.status_code == 200, a.text
    (_, payload, _) = env.state["correction"].calls[0]
    assert payload["failed_scan_id"] == q["id"] and payload["task_id"] == TASK and payload["m1"] == 5


def test_to_review_links_a_queue_row_that_already_exists(env):
    db, _, client = env()
    existing = {"id": str(uuid.uuid4()), "task_id": TASK, "user_id": STAFF, "term_id": TERM, "table_found": True,
                "grid_source": "python", "partial_data": {"filename": "x.png"}, "status": "pending",
                "validated_by": None, "validated_at": None, "review_reasons": ["low_confidence"], "omr_comparison": None,
                "validation_status": "flagged", "scan_image_id": None, "batch_id": BATCH, "created_at": _ago(minutes=1),
                "updated_at": _ago(minutes=1), "review_version": 0, "review_state": None, "review_action": None,
                "review_error": None, "review_submitted_at": None, "review_claimed_by": None, "review_claimed_at": None,
                "review_correction": None}
    db.tables["failed_scan_queue"].append(existing)
    d = client.post(f"/reviews/unsaved/{U1}/to-review", json={"expected_version": 2}).json()
    assert d["queue_row"]["id"] == existing["id"]
    assert not [t for t, _ in db.inserts if t in ("failed_scan_queue", "scan_error_images")]


def test_to_review_when_marking_already_saved_it(env):
    db, _, client = env()
    db.tables["sast_all_raw_data_survey"].append({"task_id": TASK})
    d = client.post(f"/reviews/unsaved/{U1}/to-review", json={"expected_version": 2}).json()
    assert d["outcome"] == "already_saved" and d["queue_row"] is None and d["row"]["status"] == "saved"
    assert not db.inserts  # no duplicate flagged form, no extra image
    assert _row(db, "scan_batch_items", I1)["status"] == "success" and db.counters()["processed_count"] == 1


def test_to_review_clears_the_kept_image(env):
    db, _, client = env()
    client.post(f"/reviews/unsaved/{U1}/to-review", json={"expected_version": 2})
    assert _row(db, "scan_unsaved_pages", U1)["image_base64"] == ""


def test_to_review_stale_version(env):
    db, _, client = env()
    r = client.post(f"/reviews/unsaved/{U1}/to-review", json={"expected_version": 7})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "stale_row"
    assert not db.inserts


# ------------------------------------------------------------------ batch page counters
def test_batch_counts_needs_review_not_failed(env, monkeypatch):
    db, _, client = env(items=[
        _item(),
        _item(id="i2", file_sha256="1" * 64, message="Failed after 5 attempts: x", review_reasons=[]),
        _item(id="i3", file_sha256="2" * 64, status="error", retryable=True, message="Scoring pipeline returned HTTP 500."),
    ])
    monkeypatch.setattr(batches, "get_supabase", lambda: db)
    monkeypatch.setattr(batches, "get_settings", lambda: SimpleNamespace(
        n8n_batch_webhook_url="x", simulate_scans=False, abandoned_batch_hours=3, watcher_online_seconds=90,
        simulation_mode=False))
    d = client.get(f"/batches/{BATCH}").json()
    assert d["counts"]["unsaved"] == 1 and d["counts"]["failed"] == 1 and d["counts"]["errors"] == 1
    codes = {it["id"]: it["error_code"] for it in d["attention_items"]}
    assert codes[I1] == "not_saved_needs_review" and codes["i2"] == "failed_after_retries" and codes["i3"] == "pipeline_error"


# ------------------------------------------------------------------ ingest keeps a copy
class IngestSB:
    """Chainable no-op tables (scan_batches -> the open batch, scan_batch_items -> `item`) + rpc capture."""

    def __init__(self, claim_row, item=None):
        self.claim_row, self.item, self.rpcs, self.updates = claim_row, item, [], []

    def table(self, name):
        sb = self
        data = ([{"id": BATCH, "station_id": STATION, "instructor_id": JUAN, "subject_id": IT101, "term_id": TERM,
                  "created_by": STAFF, "created_at": _ago(minutes=30), "status": "open"}] if name == "scan_batches"
                else [dict(self.item)] if (name == "scan_batch_items" and self.item) else [])

        class _T:
            def __getattr__(self, attr):
                if attr == "update":
                    return lambda values: (sb.updates.append((name, values)), self)[1]
                return lambda *a, **k: self

            def execute(self):
                return SimpleNamespace(data=data, count=len(data))
        return _T()

    def rpc(self, name, params):
        self.rpcs.append((name, params))
        data = [self.claim_row] if name == "claim_batch_item" else True
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=data))


def _ingest_payload():
    buf = io.BytesIO()
    Image.new("L", (30, 30), 200).save(buf, format="JPEG")
    raw = buf.getvalue()
    return {"filename": "p1.jpg", "image_base64": base64.b64encode(raw).decode(),
            "sha256": hashlib.sha256(raw).hexdigest(), "captured_at": _now().isoformat()}


@pytest.fixture
def ingest(monkeypatch):
    def _setup(claim_row, n8n_body, item=None, keep_ok=True):
        sb = IngestSB(claim_row, item)
        kept = []

        def fake_record(sb_, **kw):
            kept.append(kw)
            return keep_ok
        monkeypatch.setattr(uv, "record", fake_record)
        settings = SimpleNamespace(max_upload_mb=12, predate_grace_seconds=30, n8n_timeout_seconds=120,
                                   n8n_batch_webhook_url="http://n8n.invalid/hook", n8n_webhook_secret="s",
                                   simulate_scans=False, max_pipeline_attempts=5)
        monkeypatch.setattr(scans, "get_supabase", lambda: sb)
        monkeypatch.setattr(scans, "get_settings", lambda: settings)
        monkeypatch.setattr(scans, "_n8n", lambda: N8n(n8n_body))
        uv.reset_state()
        app.dependency_overrides[require_station] = lambda: {"id": STATION, "label": "Laptop 1"}
        return sb, kept, TestClient(app, raise_server_exceptions=False, headers={"X-Station-Key": "test-station-key"})
    yield _setup
    app.dependency_overrides.clear()


def _claimed(failures):
    return {"item_id": I1, "claimed": True, "item_status": "processing", "item_attempts": failures + 1,
            "item_retryable": True, "item_failures": failures}


NOT_SAVED = {"status": "flagged", "flagged": True, "review_reasons": REASONS, "message": "NOT SAVED: x"}


def test_ingest_keeps_a_copy_on_the_first_not_saved(ingest):
    sb, kept, client = ingest(_claimed(0), NOT_SAVED)
    r = client.post("/scans/ingest", json=_ingest_payload())
    assert r.status_code == 502 and r.json()["detail"]["code"] == "pipeline_not_saved"
    (k,) = kept
    assert k["final"] is False and k["item_id"] == I1 and k["task_id"].startswith(f"web:{BATCH}:")
    assert k["reasons"] == REASONS and k["message"] == "NOT SAVED: x" and k["failures"] == 1
    assert base64.b64decode(k["image_b64"])[:3] == b"\xff\xd8\xff"
    assert k["meta"]["file_sha256"] == _ingest_payload()["sha256"] and k["meta"]["station_id"] == STATION


def test_ingest_last_try_waits_for_review_instead_of_failing(ingest):
    sb, kept, client = ingest(_claimed(4), NOT_SAVED)
    r = client.post("/scans/ingest", json=_ingest_payload())
    assert r.status_code == 422
    d = r.json()["detail"]
    assert d["code"] == "failed_after_retries" and d["kept_for_review"] is True and d["batch_id"] == BATCH
    assert kept[0]["final"] is True
    (c,) = [p for n, p in sb.rpcs if n == "complete_batch_item"]
    assert c["p_status"] == "error" and c["p_retryable"] is False
    assert logic.item_error_code({"status": "error", "retryable": False, "message": c["p_message"]}) == "not_saved_needs_review"


def test_ingest_without_a_kept_copy_fails_as_before(ingest):
    sb, kept, client = ingest(_claimed(4), NOT_SAVED, keep_ok=False)
    d = client.post("/scans/ingest", json=_ingest_payload()).json()["detail"]
    assert d["code"] == "failed_after_retries" and "kept_for_review" not in d
    (c,) = [p for n, p in sb.rpcs if n == "complete_batch_item"]
    assert c["p_message"].startswith("Failed after 5 attempts")


def test_ingest_http_errors_also_keep_a_copy(ingest, monkeypatch):
    sb, kept, client = ingest(_claimed(4), None)
    monkeypatch.setattr(scans, "_n8n", lambda: N8n({"oops": 1}, status=500))
    d = client.post("/scans/ingest", json=_ingest_payload()).json()["detail"]
    assert d["kept_for_review"] is True and kept[0]["final"] is True and kept[0]["reasons"] == []


def test_ingest_resent_old_failed_page_is_kept_now(ingest):
    old_item = _item(message="Failed after 5 attempts: Scoring pipeline internal error; page not saved.")
    row = {"item_id": I1, "claimed": False, "item_status": "error", "item_attempts": 5, "item_retryable": False,
           "item_failures": 5}
    sb, kept, client = ingest(row, {"status": "success"}, item=old_item)
    d = client.post("/scans/ingest", json=_ingest_payload()).json()["detail"]
    assert d["kept_for_review"] is True and kept[0]["final"] is True and kept[0]["reasons"] == REASONS
    (table, values) = sb.updates[-1]
    assert table == "scan_batch_items" and values["message"].startswith(logic.UNSAVED_REVIEW_MSG)


def test_ingest_success_after_a_failure_settles_the_copy(ingest, monkeypatch):
    closed = []
    monkeypatch.setattr(uv, "close_for_item", lambda sb, item_id, outcome: closed.append((item_id, outcome)))
    sb, kept, client = ingest(_claimed(2), {"status": "success", "flagged": False, "review_reasons": []})
    assert client.post("/scans/ingest", json=_ingest_payload()).status_code == 200
    assert closed == [(I1, "success")] and kept == []


# ------------------------------------------------------------------ pure helpers
def test_record_writes_the_image_once(monkeypatch):
    db = DB()
    db.tables["scan_unsaved_pages"] = []
    uv.reset_state()
    common = dict(item_id=I1, batch_id=BATCH, task_id=TASK, filename="p.jpg", image_b64=_jpeg_b64(), header_b64=None,
                  meta={"file_sha256": SHA}, reasons=REASONS, message="m")
    assert uv.record(db, failures=1, final=False, **common) is True
    (row,) = db.tables["scan_unsaved_pages"]
    assert row["status"] == "retrying" and row["image_base64"] and row["watcher_failures"] == 1
    assert uv.record(db, failures=5, final=True, **common) is True
    (row,) = db.tables["scan_unsaved_pages"]
    assert row["status"] == "needs_review" and row["watcher_failures"] == 5
    assert "image_base64" not in db.updates[-1][1]  # later tries only refresh the details
    row["status"] = "manual"
    assert uv.record(db, failures=6, final=True, **common) is False  # someone already acted on it
    assert uv.record(db, failures=1, final=False, **{**common, "image_b64": "x" * (uv.MAX_KEEP_BASE64_CHARS + 1)}) is False


def test_record_without_the_table_is_a_quiet_no_op():
    db = DB(unsaved_table=False)
    uv.reset_state()
    assert uv.record(db, item_id=I1, batch_id=BATCH, task_id=TASK, filename=None, image_b64="abc", header_b64=None,
                     meta={}, reasons=[], message=None, failures=1, final=False) is False
    assert uv.available() is False  # not tried again for a while
    uv.reset_state()


def test_close_for_item():
    db = DB()
    db.tables["scan_unsaved_pages"] = [_unsaved(status="retrying")]
    uv.reset_state()
    uv.close_for_item(db, I1, "success")
    assert db.tables["scan_unsaved_pages"][0]["status"] == "saved"


def test_is_missing_table_error():
    assert uv.is_missing_table_error(APIError({"code": "PGRST205", "message": "Could not find the table 'public.scan_unsaved_pages' in the schema cache"}))
    assert uv.is_missing_table_error(APIError({"code": "42P01", "message": 'relation "public.scan_unsaved_pages" does not exist'}))
    assert not uv.is_missing_table_error(APIError({"code": "42P01", "message": 'relation "public.other" does not exist'}))
    assert not uv.is_missing_table_error(ValueError("x"))


def test_classify_response():
    ok = uv.classify_response(FakeResp(200, {"status": "success", "review_reasons": []}))
    assert ok[0] == "saved" and ok[1]["status"] == "success"
    assert uv.classify_response(FakeResp(200, NOT_SAVED))[2] == uv.RETRY_NOT_SAVED_MSG
    refused = {"status": "flagged", "review_reasons": ["invalid_request", "not_saved_permanent"]}
    assert uv.classify_response(FakeResp(200, refused))[2] == uv.RETRY_REFUSED_MSG
    assert uv.classify_response(FakeResp(200, {"nope": 1}))[2] == uv.RETRY_BAD_REPLY_MSG
    assert uv.classify_response(FakeResp(401, {}))[2] == uv.RETRY_AUTH_MSG
    assert "HTTP 502" in uv.classify_response(FakeResp(502, {}))[2]


def test_sending_is_stale():
    now = _now()
    assert uv.sending_is_stale({"status": "sending", "sending_started_at": (now - timedelta(seconds=600)).isoformat()}, 360, now)
    assert not uv.sending_is_stale({"status": "sending", "sending_started_at": (now - timedelta(seconds=60)).isoformat()}, 360, now)
    assert not uv.sending_is_stale({"status": "needs_review"}, 360, now)


def test_queue_row_shape_matches_n8n_internal_error_rows():
    batch = {"id": BATCH, "instructor_id": JUAN, "subject_id": IT101, "term_id": TERM, "created_by": STAFF, "station_id": STATION}
    ctx = {"instructor": {"id": JUAN, "name": "Juan Dela Cruz"}, "subject": {"id": IT101, "code": "IT 101", "name": "Intro"}}
    q = uv.queue_row(_unsaved(), batch, ctx, "img-1")
    assert set(q) == {"task_id", "user_id", "term_id", "table_found", "grid_source", "partial_data", "status",
                      "review_reasons", "omr_comparison", "validation_status", "scan_image_id", "batch_id"}
    assert q["review_reasons"][-1] == uv.NOT_SAVED_BY_PIPELINE and "internal_error_not_saved" not in q["review_reasons"]
    p = q["partial_data"]
    assert p["instructor_match"] == "unchecked" and p["suggested_instructor_id"] == JUAN and p["ratings"] is None
    assert "omr_agreement_below_80" in p["review_note"]


def test_retry_sends_reused_image_when_the_file_was_marked_in_another_batch(env):
    other = "b7000000-0000-0000-0000-000000000007"
    items = [_item(), _item(id="f1000000-0000-0000-0000-000000000099", batch_id=other, status="success",
                            task_id=f"web:{other}:ffff000000000001")]
    db, n8n, client = env(items=items)
    r = client.post(f"/reviews/unsaved/{U1}/retry", json={"expected_version": 2})
    assert r.status_code == 200, r.text
    (_, payload, _) = n8n.calls[0]
    assert payload["reused_image"] == {"batch_id": other, "status": "success"}
    # the plain retry (no other batch) carries none
    db2, n8n2, client2 = env()
    assert client2.post(f"/reviews/unsaved/{U1}/retry", json={"expected_version": 2}).status_code == 200
    assert "reused_image" not in n8n2.calls[0][1]
