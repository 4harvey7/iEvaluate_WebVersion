"""Page viewer (migration 010): score summaries (app/page_scores.py), the
viewing copy in the private bucket (app/page_images.py), the ingest hook,
the batch list fields and the routes in app/routers/batch_pages.py.
In-memory fake Supabase + fake Storage: no network, no DB."""
import base64
import io
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from storage3.exceptions import StorageApiError

from app import page_images as pi
from app import page_scores as ps
from app import unsaved_pages as uv
from app.main import app
from app.routers import batch_pages, batches, reviews, scans
from app.security import require_staff, require_station
from tests.test_reviews import (
    ADMIN,
    BATCH,
    IT101,
    JUAN,
    OTHER_BATCH,
    OUTSIDER,
    PEDRO,
    ROLE_BY_NAME,
    STAFF,
    STATION,
    STATION_STAFF,
    TERM,
    _ago,
    _now,
    seed,
    user,
)
from tests.test_unsaved_pages import DB, Q

I_OK = "d0000000-0000-0000-0000-000000000001"
I_FLAG = "d0000000-0000-0000-0000-000000000002"
I_OLD = "d0000000-0000-0000-0000-000000000003"
I_GONE = "d0000000-0000-0000-0000-000000000004"
I_REJ = "d0000000-0000-0000-0000-000000000005"
I_ELSEWHERE = "d0000000-0000-0000-0000-000000000009"
T_OK = f"web:{BATCH}:dddd000000000001"
T_FLAG = f"web:{BATCH}:aaaa000000000001"  # = queue row Q1 of tests.test_reviews.seed (and its scan_error_images row)
OK_PATH = f"{BATCH}/{I_OK}.jpg"
RATINGS = {**{f"m{i}": 5 for i in range(1, 11)}, **{f"p{i}": 4 for i in range(1, 11)}}


def _jpeg(size=(40, 60), mode="L", color=180) -> bytes:
    buf = io.BytesIO()
    Image.new(mode, size, color).save(buf, format="JPEG")
    return buf.getvalue()


# ------------------------------------------------------------------ fakes
class PQ(Q):
    def lt(self, col, val):
        return self._filter(col, lambda v: v is not None and str(v) < str(val))


class PageDB(DB):
    def __init__(self, migrated_010=True):
        super().__init__()
        if not migrated_010:
            self.missing["scan_batch_items"] = pi.NEW_COLUMNS
        self.storage = FakeStorage()

    def table(self, name):
        return PQ(self, name)


class FakeStorage:
    def __init__(self):
        self.buckets = {"scan-pages": {}}
        self.uploads, self.removed = [], []
        self.fail_upload = None

    def from_(self, name):
        return FakeBucket(self, name)


class FakeBucket:
    def __init__(self, storage, name):
        self.s, self.name = storage, name

    def _files(self):
        if self.name not in self.s.buckets:
            raise StorageApiError("Bucket not found", "Bucket not found", 404)
        return self.s.buckets[self.name]

    def upload(self, path, data, options=None):
        files = self._files()
        if self.s.fail_upload:
            raise self.s.fail_upload
        files[path] = bytes(data)
        self.s.uploads.append((path, dict(options or {})))

    def download(self, path):
        files = self._files()
        if path not in files:
            raise StorageApiError("Object not found", "not_found", 400)
        return files[path]

    def remove(self, paths):
        files = self._files()
        for p in paths:
            files.pop(p, None)
        self.s.removed.extend(paths)
        return []


SETTINGS = SimpleNamespace(
    scan_image_bucket="scan-pages", scan_image_retention_days=90, scan_image_max_side=1800,
    scan_image_jpeg_quality=75,
    n8n_batch_webhook_url="http://n8n.invalid/webhook/sast-batch-scan", simulate_scans=False,
    abandoned_batch_hours=3, watcher_online_seconds=90, simulation_mode=False,
    n8n_correction_webhook_url="http://n8n.invalid/webhook/sast-manual-correction",
    n8n_import_error_webhook_url="", n8n_webhook_secret="s", n8n_correction_timeout_seconds=180,
    n8n_timeout_seconds=360,
)


def _item(iid, **kw):
    row = {"id": iid, "batch_id": BATCH, "station_id": STATION, "file_sha256": iid.replace("-", "")[:32] * 2,
           "filename": f"{iid[-2:]}.jpg", "task_id": f"web:{BATCH}:{iid[-16:]}", "status": "success",
           "retryable": True, "review_reasons": [], "message": "Saved.", "identity": None, "simulated": False,
           "attempts": 1, "pipeline_failures": 0, "orientation_applied": 0, "orientation_confidence": 0.9,
           "orientation_uncertain": False, "created_at": _ago(minutes=10), "updated_at": _ago(minutes=10),
           "result_summary": None, "page_image_path": None, "page_image_saved_at": None}
    row.update(kw)
    return row


def _raw_row(task_id, **kw):
    row = {"task_id": task_id, "instructor": "Juan Dela Cruz", "instructor_ID": JUAN, "subject_id": IT101,
           "term_id": TERM, "student_id": "2021-0100", **RATINGS}
    row.update(kw)
    return row


@pytest.fixture
def env(monkeypatch):
    def _setup(as_user=STAFF, role="SAO_STAFF", settings=SETTINGS, migrated_010=True, items=None):
        db = seed(PageDB(migrated_010))
        for b in db.tables["scan_batches"]:
            b.update(processed_count=0, flagged_count=0, rejected_count=0, duplicate_count=0)
        db.tables["scan_unsaved_pages"] = []
        db.tables["scan_batch_items"] = items if items is not None else [
            _item(I_OK, task_id=T_OK, page_image_path=OK_PATH, page_image_saved_at=_ago(minutes=9),
                  result_summary=ps.from_raw_row(_raw_row(T_OK), {"subject_code": "IT 101", "subject_name": "Intro"})),
            _item(I_FLAG, task_id=T_FLAG, status="flagged", review_reasons=["instructor_mismatch_found"],
                  created_at=_ago(days=3), updated_at=_ago(days=3)),
            _item(I_OLD, created_at=_ago(days=30), updated_at=_ago(days=30)),
            _item(I_GONE, page_image_saved_at=_ago(days=100), created_at=_ago(days=100), updated_at=_ago(days=100)),
            _item(I_REJ, status="rejected", review_reasons=["not_a_sast_form"], message="Rejected, nothing was saved: x"),
            _item(I_ELSEWHERE, batch_id=OTHER_BATCH, page_image_path=f"{OTHER_BATCH}/{I_ELSEWHERE}.jpg",
                  page_image_saved_at=_ago(minutes=5)),
        ]
        db.tables["sast_all_raw_data_survey"] = [_raw_row(T_OK)]
        db.storage.buckets["scan-pages"][OK_PATH] = _jpeg()
        for mod in (batch_pages, batches, reviews):
            monkeypatch.setattr(mod, "get_supabase", lambda: db)
            monkeypatch.setattr(mod, "get_settings", lambda: settings)
        monkeypatch.setattr(reviews, "role_maps", lambda: (ROLE_BY_NAME, {v: k for k, v in ROLE_BY_NAME.items()}))
        monkeypatch.setattr(reviews, "audit", lambda *a, **k: None)
        monkeypatch.setattr(pi, "_pool_submit", lambda fn, *a: fn(*a))  # background jobs run inline
        reviews._mig.update(missing_until=0.0, warned=False)
        reviews._ctx_cache.clear()
        pi.reset_state()
        uv.reset_state()
        app.dependency_overrides[require_staff] = lambda: user(as_user, role)
        return db, TestClient(app, raise_server_exceptions=False, headers={"X-Station-Key": "test-station-key"})

    yield _setup
    app.dependency_overrides.clear()
    pi.reset_state()


def _row(db, iid):
    return next(r for r in db.tables["scan_batch_items"] if r["id"] == iid)


# ------------------------------------------------------------------ score extraction
def test_summarize_ratings_averages_answered_rows_only():
    s = ps.summarize_ratings({"m1": 5, "m2": "4", "M3": 3, "m4": None, "p1": "VS", "p2": 0, "p3": 7, "p4": "blank"})
    assert s["answered"] == 4 and s["questions"] == 20
    assert s["ratings"]["m3"] == 3 and s["ratings"]["p1"] == 4 and s["ratings"]["p2"] is None and s["ratings"]["p3"] is None
    assert s["management_avg"] == 4.0 and s["performance_avg"] == 4.0 and s["average"] == 4.0
    empty = ps.summarize_ratings(None)
    assert empty["answered"] == 0 and empty["average"] is None


def test_summary_from_the_stored_raw_row():
    s = ps.from_raw_row(_raw_row(T_OK, m1=None), {"subject_code": "IT 101", "subject_name": "Intro to Computing"})
    assert s["credited"] is True and s["source"] == "marking"
    assert s["answered"] == 19 and s["management_avg"] == 5.0 and s["performance_avg"] == 4.0 and s["average"] == 4.47
    assert s["instructor"] == "Juan Dela Cruz" and s["subject_code"] == "IT 101" and s["student_id"] == "2021-0100"


def test_summary_from_a_flagged_queue_row_and_after_approval():
    db = seed(PageDB())
    q1 = db.tables["failed_scan_queue"][0]
    s = ps.from_queue_row(q1)
    assert s["credited"] is False and s["source"] == "review_queue"
    assert s["ratings"]["m1"] == 4 and s["ratings"]["m2"] == 4 and s["ratings"]["p1"] == 5 and s["answered"] == 3
    assert s["instructor"] == "Pedro Santos" and s["subject_code"] == "IT 101" and s["agreement_percent"] == 95
    done = {**q1, "status": "validated", "review_correction": {
        "action": "rerouted", "instructor_name": "Pedro Santos", "subject_code": "IT 102",
        "ratings": {"m1": 3, "p1": 5}, "student_id": "2021-0001"}}
    s2 = ps.from_queue_row(done)
    assert s2["credited"] is True and s2["source"] == "review" and s2["subject_code"] == "IT 102" and s2["average"] == 4.0


def test_lookup_prefers_the_right_table_and_skips_unscored():
    db = seed(PageDB())
    db.tables["sast_all_raw_data_survey"] = [_raw_row(T_FLAG)]
    assert ps.lookup(db, T_FLAG, "flagged")["source"] == "review_queue"
    assert ps.lookup(db, T_FLAG, "success")["source"] == "marking"
    assert ps.lookup(db, T_FLAG, "duplicate")["source"] == "marking"  # the original page's stored result
    assert ps.lookup(db, T_FLAG, "rejected") is None and ps.lookup(db, T_FLAG, "error") is None
    assert ps.lookup(db, f"web:{BATCH}:nothing", "success") is None


def test_list_summary_has_no_ratings():
    s = ps.list_summary(ps.from_raw_row(_raw_row(T_OK)))
    assert "ratings" not in s and "student_id" not in s and s["average"] == 4.5
    assert ps.list_summary(None) is None and ps.list_summary({}) is None


# ------------------------------------------------------------------ viewing copy + background job
def test_viewing_copy_is_a_smaller_jpeg():
    big = _jpeg(size=(2400, 3600), mode="RGB", color=(250, 250, 250))
    out = pi.viewing_copy(big, max_side=1800, quality=75)
    img = Image.open(io.BytesIO(out))
    assert img.format == "JPEG" and max(img.size) == 1800 and img.size == (1200, 1800)
    small = Image.open(io.BytesIO(pi.viewing_copy(_jpeg(size=(40, 60)))))
    assert small.size == (40, 60)  # never enlarged
    with pytest.raises(Exception):
        pi.viewing_copy(b"not an image")


def test_object_path_only_for_uuids():
    assert pi.object_path(BATCH, I_OK) == OK_PATH
    assert pi.object_path("../x", I_OK) is None and pi.object_path(BATCH, "a/b") is None


def test_record_page_stores_picture_and_score(env):
    db, _ = env(items=[_item(I_OK, task_id=T_OK)])
    pi.record_page(db, SETTINGS, BATCH, I_OK, T_OK, _jpeg())
    it = _row(db, I_OK)
    assert it["page_image_path"] == OK_PATH and it["page_image_saved_at"]
    assert it["result_summary"]["average"] == 4.5 and it["result_summary"]["credited"] is True
    ((path, opts),) = db.storage.uploads
    assert path == OK_PATH and opts["content-type"] == "image/jpeg"
    assert db.storage.buckets["scan-pages"][OK_PATH][:3] == b"\xff\xd8\xff"
    # a resend of the same page changes nothing
    pi.record_page(db, SETTINGS, BATCH, I_OK, T_OK, _jpeg())
    assert len(db.storage.uploads) == 1


def test_upload_failure_is_only_logged_and_score_still_saved(env, caplog):
    db, _ = env(items=[_item(I_OK, task_id=T_OK)])
    db.storage.fail_upload = StorageApiError("boom", "InternalError", 500)
    pi.record_page(db, SETTINGS, BATCH, I_OK, T_OK, _jpeg())
    it = _row(db, I_OK)
    assert it["page_image_path"] is None and it["result_summary"]["average"] == 4.5
    assert "could not store the picture" in caplog.text and "boom" not in caplog.text


def test_missing_bucket_degrades_and_is_retried_later(env):
    db, _ = env(items=[_item(I_OK, task_id=T_OK)])
    db.storage.buckets.clear()
    pi.record_page(db, SETTINGS, BATCH, I_OK, T_OK, _jpeg())
    assert _row(db, I_OK)["page_image_path"] is None and not pi.bucket_available(SETTINGS)
    assert _row(db, I_OK)["result_summary"]  # the score does not need the bucket


def test_without_migration_010_the_job_is_a_no_op(env):
    db, _ = env(migrated_010=False, items=[_item(I_OK, task_id=T_OK)])
    pi.record_page(db, SETTINGS, BATCH, I_OK, T_OK, _jpeg())
    assert not pi.columns_available() and db.storage.uploads == []
    assert pi.after_scan(db, SETTINGS, batch_id=BATCH, item_id=I_OK, task_id=T_OK, jpeg=_jpeg()) is False


def test_pool_is_bounded(monkeypatch):
    pi.reset_state()
    held = []
    monkeypatch.setattr(pi, "_pool_submit", lambda fn: held.append(fn))
    monkeypatch.setitem(pi._pending, "n", 0)
    assert all(pi.submit(lambda: None) for _ in range(pi.MAX_PENDING))
    assert pi.submit(lambda: None) is False  # full: the picture is skipped, the scan is not
    for fn in held:
        fn()
    assert pi._pending["n"] == 0


# ------------------------------------------------------------------ ingest hook
class IngestSB:
    def __init__(self):
        self.rpcs, self.batch = [], {"id": BATCH, "station_id": STATION, "instructor_id": JUAN, "subject_id": IT101,
                                     "term_id": TERM, "created_by": STAFF, "created_at": _ago(minutes=30), "status": "open"}

    def table(self, name):
        data = [dict(self.batch)] if name == "scan_batches" else []

        class _T:
            def __getattr__(self, attr):
                return lambda *a, **k: self

            def execute(self):
                return SimpleNamespace(data=data, count=len(data))
        return _T()

    def rpc(self, name, params):
        self.rpcs.append((name, params))
        data = ([{"item_id": I_OK, "claimed": True, "item_status": "processing", "item_attempts": 1,
                  "item_retryable": True, "item_failures": 0}] if name == "claim_batch_item" else True)
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=data))


def _ingest_payload():
    import hashlib
    raw = _jpeg(size=(30, 30))
    return {"filename": "p1.jpg", "image_base64": base64.b64encode(raw).decode(),
            "sha256": hashlib.sha256(raw).hexdigest(), "captured_at": _now().isoformat()}


@pytest.mark.parametrize("n8n_status, body, expect", [
    (200, {"status": "success", "flagged": False, "review_reasons": [], "message": "Saved."}, 200),
    (500, {}, 502),  # a failed try: the picture is still kept (it may end on the review page)
])
def test_ingest_keeps_the_page_after_marking_answered(monkeypatch, n8n_status, body, expect):
    sb = IngestSB()
    calls = []
    settings = SimpleNamespace(max_upload_mb=12, predate_grace_seconds=30, n8n_timeout_seconds=120,
                               n8n_batch_webhook_url="http://n8n.invalid/hook", n8n_webhook_secret="s",
                               simulate_scans=False, max_pipeline_attempts=5)
    order = []

    class N8n:
        def post(self, *a, **k):
            order.append("n8n")
            return SimpleNamespace(status_code=n8n_status, json=lambda: body)

    def fake_after(sb_, settings_, **kw):
        order.append("after_scan")
        calls.append(kw)
        return True
    monkeypatch.setattr(scans, "get_supabase", lambda: sb)
    monkeypatch.setattr(scans, "get_settings", lambda: settings)
    monkeypatch.setattr(scans, "_n8n", lambda: N8n())
    monkeypatch.setattr(pi, "after_scan", fake_after)
    monkeypatch.setattr(uv, "record", lambda *a, **k: False)
    app.dependency_overrides[require_station] = lambda: {"id": STATION, "label": "Laptop 1"}
    try:
        r = TestClient(app, raise_server_exceptions=False, headers={"X-Station-Key": "test-station-key"}).post("/scans/ingest", json=_ingest_payload())
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == expect, r.text
    assert order == ["n8n", "after_scan"]  # never before marking
    (kw,) = calls
    assert kw["batch_id"] == BATCH and kw["item_id"] == I_OK and kw["task_id"].startswith(f"web:{BATCH}:")
    assert kw["jpeg"][:3] == b"\xff\xd8\xff"


def test_ingest_never_fails_because_of_the_picture(monkeypatch):
    sb = IngestSB()
    settings = SimpleNamespace(max_upload_mb=12, predate_grace_seconds=30, n8n_timeout_seconds=120,
                               n8n_batch_webhook_url="http://n8n.invalid/hook", n8n_webhook_secret="s",
                               simulate_scans=False, max_pipeline_attempts=5, scan_image_bucket="scan-pages")
    body = {"status": "success", "flagged": False, "review_reasons": [], "message": "Saved."}
    monkeypatch.setattr(scans, "get_supabase", lambda: sb)
    monkeypatch.setattr(scans, "get_settings", lambda: settings)
    monkeypatch.setattr(scans, "_n8n", lambda: SimpleNamespace(post=lambda *a, **k: SimpleNamespace(status_code=200, json=lambda: body)))
    monkeypatch.setattr(pi, "submit", lambda *a: (_ for _ in ()).throw(RuntimeError("pool broken")))
    pi.reset_state()
    app.dependency_overrides[require_station] = lambda: {"id": STATION, "label": "Laptop 1"}
    try:
        r = TestClient(app, raise_server_exceptions=False, headers={"X-Station-Key": "test-station-key"}).post("/scans/ingest", json=_ingest_payload())
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 200 and r.json()["status"] == "success"


# ------------------------------------------------------------------ routes: auth + access
def test_routes_require_login():
    app.dependency_overrides.clear()
    client = TestClient(app, raise_server_exceptions=False, headers={"X-Station-Key": "test-station-key"})
    for path in (f"/batches/{BATCH}/items/{I_OK}", f"/batches/{BATCH}/items/{I_OK}/image"):
        assert client.get(path).status_code == 401, path


def test_outsider_gets_403_and_allowed_people_get_in(env):
    db, client = env(as_user=OUTSIDER)
    for path in (f"/batches/{BATCH}/items/{I_OK}", f"/batches/{BATCH}/items/{I_OK}/image"):
        r = client.get(path)
        assert r.status_code == 403 and r.json()["detail"]["code"] == "not_your_batch", path
    for who, role in ((STAFF, "SAO_STAFF"), (ADMIN, "SAO_ADMIN")):
        app.dependency_overrides[require_staff] = lambda who=who, role=role: user(who, role)
        assert client.get(f"/batches/{BATCH}/items/{I_OK}/image").status_code == 200, who
    # Logged in at the batch's laptop but didn't start the batch: no access any more
    # (security review: taking over a laptop must not open someone else's pages).
    app.dependency_overrides[require_staff] = lambda: user(STATION_STAFF, "SAO_STAFF")
    assert client.get(f"/batches/{BATCH}/items/{I_OK}/image").status_code == 403


def test_item_must_belong_to_the_batch(env):
    db, client = env(as_user=ADMIN, role="SAO_ADMIN")
    assert client.get(f"/batches/{BATCH}/items/{I_ELSEWHERE}").status_code == 404
    assert client.get(f"/batches/{BATCH}/items/{I_ELSEWHERE}/image").status_code == 404
    assert client.get(f"/batches/{BATCH}/items/not-a-uuid").status_code == 404
    assert client.get(f"/batches/{uuid.uuid4()}/items/{I_OK}").status_code == 404


# ------------------------------------------------------------------ routes: image
def test_image_is_served_as_jpeg_from_the_private_bucket(env):
    db, client = env()
    r = client.get(f"/batches/{BATCH}/items/{I_OK}/image")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert r.headers["x-content-type-options"] == "nosniff" and "private" in r.headers["cache-control"]
    assert r.content == db.storage.buckets["scan-pages"][OK_PATH]


def test_stored_bytes_that_are_not_an_image_are_never_served(env):
    db, client = env()
    db.storage.buckets["scan-pages"][OK_PATH] = b"<script>alert(1)</script>"
    r = client.get(f"/batches/{BATCH}/items/{I_OK}/image")
    assert r.status_code == 404 and r.json()["detail"]["code"] == "image_unavailable"


def test_older_flagged_page_uses_the_review_copy(env):
    db, client = env()
    r = client.get(f"/batches/{BATCH}/items/{I_FLAG}/image")
    assert r.status_code == 200 and r.content[:3] == b"\xff\xd8\xff"


def test_older_unsaved_page_uses_the_kept_copy(env):
    db, client = env()
    db.tables["scan_batch_items"].append(_item("d0000000-0000-0000-0000-000000000006", status="error", retryable=False,
                                               created_at=_ago(days=2), updated_at=_ago(days=2)))
    db.tables["scan_unsaved_pages"] = [{"id": str(uuid.uuid4()), "item_id": "d0000000-0000-0000-0000-000000000006",
                                         "batch_id": BATCH, "image_base64": base64.b64encode(_jpeg()).decode()}]
    r = client.get(f"/batches/{BATCH}/items/d0000000-0000-0000-0000-000000000006/image")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"


def test_missing_images_explain_why(env):
    db, client = env()
    _row(db, I_OLD).update(created_at=_ago(days=200), updated_at=_ago(days=200))  # before the first kept picture
    old = client.get(f"/batches/{BATCH}/items/{I_OLD}/image")
    d = old.json()["detail"]
    assert old.status_code == 404 and d["code"] == "image_not_kept"
    assert d["message"].startswith("Image not kept for pages scanned before ") and d["kept_since"]
    gone = client.get(f"/batches/{BATCH}/items/{I_GONE}/image").json()["detail"]
    assert gone["code"] == "image_removed" and "90 days" in gone["message"]


def test_recent_page_without_picture_is_not_ready_then_not_saved(env):
    db, client = env()
    _row(db, I_REJ).update(created_at=_ago(seconds=20), updated_at=_ago(seconds=20))
    assert client.get(f"/batches/{BATCH}/items/{I_REJ}/image").json()["detail"]["code"] == "image_not_ready"
    _row(db, I_REJ).update(created_at=_ago(minutes=5), updated_at=_ago(minutes=5))
    assert client.get(f"/batches/{BATCH}/items/{I_REJ}/image").json()["detail"]["code"] == "image_not_saved"


def test_degrades_when_not_configured(env):
    db, client = env(settings=SimpleNamespace(**{**vars(SETTINGS), "scan_image_bucket": ""}))
    assert client.get(f"/batches/{BATCH}/items/{I_OLD}/image").json()["detail"]["code"] == "images_off"
    d = client.get(f"/batches/{BATCH}/items/{I_OK}").json()
    assert d["page_images_enabled"] is False and d["summary"]["average"] == 4.5


def test_degrades_before_migration_010(env):
    db, client = env(migrated_010=False)
    d = client.get(f"/batches/{BATCH}/items/{I_OK}")
    assert d.status_code == 200 and d.json()["page_images_enabled"] is False and d.json()["image"] is None
    assert d.json()["summary"]["average"] == 4.5  # computed on the fly, not stored
    r = client.get(f"/batches/{BATCH}/items/{I_OLD}/image")
    assert r.status_code == 404 and r.json()["detail"]["code"] == "images_not_set_up"
    # the flagged page's older copy is still shown
    assert client.get(f"/batches/{BATCH}/items/{I_FLAG}/image").status_code == 200


# ------------------------------------------------------------------ routes: detail
def test_detail_has_per_question_ratings(env):
    db, client = env()
    d = client.get(f"/batches/{BATCH}/items/{I_OK}").json()
    assert d["status"] == "success" and d["image"] == "kept" and d["page_images_enabled"] is True
    assert d["summary"]["ratings"]["m1"] == 5 and d["summary"]["ratings"]["p10"] == 4
    assert d["summary"]["instructor"] == "Juan Dela Cruz" and d["retention_days"] == 90
    assert "page_image_path" not in d and "scan-pages" not in str(d)


def test_detail_backfills_older_pages_and_refreshes_flagged(env):
    db, client = env()
    _row(db, I_OLD)["task_id"] = T_OK
    d = client.get(f"/batches/{BATCH}/items/{I_OLD}").json()
    assert d["summary"]["average"] == 4.5 and _row(db, I_OLD)["result_summary"]["average"] == 4.5
    f = client.get(f"/batches/{BATCH}/items/{I_FLAG}").json()
    assert f["summary"]["credited"] is False and f["summary"]["instructor"] == "Pedro Santos"
    assert f["review_status"] == "pending"
    rej = client.get(f"/batches/{BATCH}/items/{I_REJ}").json()
    assert rej["summary"] is None and rej["review_reasons"] == ["not_a_sast_form"]


class FakeCorrection:
    def post(self, url, json=None, headers=None):
        return SimpleNamespace(status_code=200, json=lambda: {"success": True})


def test_approving_on_the_review_page_updates_the_row_score(env, monkeypatch):
    db, client = env()
    monkeypatch.setattr(reviews, "_n8n", lambda: FakeCorrection())
    body = {"expected_version": 3, "instructor_id": PEDRO, "subject_id": IT101, "student_id": "2021-0001",
            "remarks": "", "ratings": {"m1": 2, "m2": 2, "m3": 4, "p1": 4, "p2": 3}}
    r = client.post(f"/reviews/queue/{db.tables['failed_scan_queue'][0]['id']}/approve", json=body)
    assert r.status_code == 200, r.text
    s = _row(db, I_FLAG)["result_summary"]
    assert s["source"] == "review" and s["credited"] is True and s["instructor"] == "Pedro Santos" and s["average"] == 3.0


# ------------------------------------------------------------------ batch list
def test_batch_list_has_compact_score_and_no_picture_data(env):
    db, client = env()
    d = client.get(f"/batches/{BATCH}").json()
    assert d["can_view_pages"] is True and d["page_images_enabled"] is True
    items = {it["id"]: it for it in d["attention_items"] + d["recent_items"]}
    ok = items[I_OK]
    assert ok["summary"]["average"] == 4.5 and ok["summary"]["instructor"] == "Juan Dela Cruz"
    assert ok["image"] == "kept" and items[I_GONE]["image"] == "removed" and items[I_OLD]["image"] is None
    text = str(d)
    assert "ratings" not in text and "page_image_path" not in text and OK_PATH not in text and "student_id" not in text


def test_batch_list_hides_scores_from_staff_without_access(env):
    db, client = env(as_user=OUTSIDER)
    d = client.get(f"/batches/{BATCH}").json()
    assert d["can_view_pages"] is False
    assert all(it["summary"] is None and it["image"] is None for it in d["recent_items"] + d["attention_items"])


def test_batch_list_works_before_migration_010(env):
    db, client = env(migrated_010=False)
    r = client.get(f"/batches/{BATCH}")
    assert r.status_code == 200 and r.json()["page_images_enabled"] is False
    assert all(it["summary"] is None for it in r.json()["recent_items"])
    assert not pi.columns_available()


# ------------------------------------------------------------------ retention
def test_cleanup_removes_only_expired_pictures(env):
    db, _ = env()
    expired = _item("d0000000-0000-0000-0000-000000000007", page_image_path=f"{BATCH}/old.jpg",
                    page_image_saved_at=_ago(days=91))
    db.tables["scan_batch_items"].append(expired)
    db.storage.buckets["scan-pages"][f"{BATCH}/old.jpg"] = _jpeg()
    assert pi.cleanup_expired(db, SETTINGS) == 1
    assert db.storage.removed == [f"{BATCH}/old.jpg"]
    assert expired["page_image_path"] is None and expired["page_image_saved_at"]  # "removed", not "never kept"
    assert _row(db, I_OK)["page_image_path"] == OK_PATH
    assert pi.cleanup_expired(db, SimpleNamespace(**{**vars(SETTINGS), "scan_image_retention_days": 0})) == 0


def test_cleanup_runs_at_most_once_an_hour(env, monkeypatch):
    db, client = env()
    runs = []
    monkeypatch.setattr(pi, "cleanup_expired", lambda sb, s: runs.append(1))
    assert pi.maybe_cleanup(db, SETTINGS) is True
    assert pi.maybe_cleanup(db, SETTINGS) is False
    client.get(f"/batches/{BATCH}")  # the poll triggers it opportunistically, still at most once
    assert runs == [1]
