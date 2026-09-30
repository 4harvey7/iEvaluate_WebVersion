"""Review page API (routers/reviews.py) with an in-memory fake Supabase and a
fake n8n: no network, no database. The fake evaluates the filters the router
uses, so version-conditional updates really match or miss, and it mimics the
migration-005 trigger (updated_at + review_version) or its absence."""
import base64
import io
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from postgrest.exceptions import APIError

from app import audit as audit_module
from app import review_logic as rl
from app.db import derive_webhook_url
from app.logic import parse_ts
from app.main import app
from app.routers import reviews
from app.security import CurrentUser, require_staff

BATCH = "11111111-1111-1111-1111-111111111111"
OTHER_BATCH = "22222222-2222-2222-2222-222222222222"
STATION = "33333333-3333-3333-3333-333333333333"
STAFF = "44444444-4444-4444-4444-444444444444"      # started the batch
STATION_STAFF = "55555555-5555-5555-5555-555555555555"  # logged in at the batch's laptop
OUTSIDER = "66666666-6666-6666-6666-666666666666"
ADMIN = "77777777-7777-7777-7777-777777777777"
JUAN = "a0000000-0000-0000-0000-000000000001"       # batch instructor
PEDRO = "a0000000-0000-0000-0000-000000000002"      # another approved instructor
PENDING_PERSON = "a0000000-0000-0000-0000-000000000003"  # not approved, not placeholder
PLACEHOLDER = "a0000000-0000-0000-0000-000000000004"
NO_ROLE = "a0000000-0000-0000-0000-000000000005"    # approved but no instructor role
IT101 = "b0000000-0000-0000-0000-000000000001"      # batch subject
IT102 = "b0000000-0000-0000-0000-000000000002"
TERM = "c0000000-0000-0000-0000-000000000001"
Q1 = "d0000000-0000-0000-0000-000000000001"         # instructor mismatch, suggestion Pedro
Q2 = "d0000000-0000-0000-0000-000000000002"         # older n8n build: no form-check keys
Q3 = "d0000000-0000-0000-0000-000000000003"         # already validated
Q4 = "d0000000-0000-0000-0000-000000000004"         # other staff member's row, other batch
IMG1 = "e0000000-0000-0000-0000-000000000001"
IMG2 = "e0000000-0000-0000-0000-000000000002"

ROLE_BY_NAME = {"FULL-TIME": 1, "PART-TIME": 2, "DEPARTMENT_HEAD": 3, "SAO_STAFF": 4, "SAO_ADMIN": 5}

FSQ_REVIEW_COLS = ("updated_at", "review_version", "review_state", "review_action", "review_error",
                   "review_submitted_at", "review_claimed_by", "review_claimed_at", "review_correction")
IE_REVIEW_COLS = ("updated_at", "review_version", "review_state", "review_error", "review_submitted_at",
                  "review_claimed_by", "review_claimed_at")
TRIGGER_FIELDS = {
    "failed_scan_queue": ("status", "partial_data", "validated_data", "review_state", "review_action"),
    "import_errors": ("status", "raw_data", "error_type", "raw_instructor_name", "raw_subject_name", "raw_student_id", "review_state"),
}


def _now():
    return datetime.now(timezone.utc)


def _ago(**kw):
    return (_now() - timedelta(**kw)).isoformat()


def _jpeg_b64():
    buf = io.BytesIO()
    Image.new("L", (20, 20), 180).save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode()


# ------------------------------------------------------------------ fake PostgREST
def _s(v):
    return None if v is None else str(v)


def _cmp_value(v):
    t = parse_ts(v) if isinstance(v, str) and re.match(r"^\d{4}-\d{2}-\d{2}T", v) else None
    return t if t is not None else v


class FakeQuery:
    def __init__(self, db, table):
        self.db, self.table = db, table
        self.op, self.cols, self.values = "select", "*", None
        self.count, self.head = None, False
        self.filters, self._order, self._limit = [], None, None

    # -- building
    def _check_cols(self, cols, code="42703"):
        for c in cols:
            if c in self.db.missing.get(self.table, ()):
                msg = (f"column {self.table}.{c} does not exist" if code == "42703"
                       else f"Could not find the '{c}' column of '{self.table}' in the schema cache")
                raise APIError({"code": code, "message": msg})

    def select(self, cols="*", count=None, head=False):
        self.cols = cols
        self.count, self.head = count, head
        if cols != "*":
            self._check_cols([c.strip() for c in cols.split(",")])
        return self

    def update(self, values):
        self._check_cols(list(values), code="PGRST204")
        self.op, self.values = "update", dict(values)
        return self

    def _filter(self, col, fn):
        self._check_cols([col])
        self.filters.append((col, fn))
        return self

    def eq(self, col, val):
        return self._filter(col, lambda v: v is not None and _s(v) == _s(val))

    def neq(self, col, val):
        return self._filter(col, lambda v: v is not None and _s(v) != _s(val))

    def in_(self, col, vals):
        wanted = {_s(x) for x in vals}
        return self._filter(col, lambda v: _s(v) in wanted)

    def gte(self, col, val):
        return self._filter(col, lambda v: v is not None and _cmp_value(v) >= _cmp_value(val))

    def like(self, col, pattern):
        rx = re.compile("^" + re.escape(pattern).replace("%", ".*").replace("_", ".") + "$")
        return self._filter(col, lambda v: v is not None and bool(rx.match(str(v))))

    def order(self, col, desc=False):
        self._order = (col, desc)
        return self

    def limit(self, n):
        self._limit = n
        return self

    # -- running
    def _matches(self, row):
        return all(fn(row.get(col)) for col, fn in self.filters)

    def _project(self, row):
        if self.cols == "*":
            return dict(row)
        return {c.strip(): row.get(c.strip()) for c in self.cols.split(",")}

    def execute(self):
        rows = self.db.tables.setdefault(self.table, [])
        hits = [r for r in rows if self._matches(r)]
        if self.op == "update":
            out = []
            for r in hits:
                old = dict(r)
                r.update(self.values)
                self.db.touch(self.table, old, r)
                out.append(dict(r))
            self.db.updates.append((self.table, dict(self.values), len(out)))
            return SimpleNamespace(data=out, count=len(out))
        if self._order:
            col, desc = self._order
            hits.sort(key=lambda r: str(r.get(col) or ""), reverse=desc)
        if self._limit is not None:
            hits = hits[: self._limit]
        if self.head:
            return SimpleNamespace(data=[], count=len(hits))
        return SimpleNamespace(data=[self._project(r) for r in hits], count=len(hits))


class FakeDB:
    def __init__(self, migrated=True):
        self.tables = {}
        self.updates = []
        self.missing = {} if migrated else {"failed_scan_queue": FSQ_REVIEW_COLS, "import_errors": IE_REVIEW_COLS}
        self.migrated = migrated

    def table(self, name):
        return FakeQuery(self, name)

    def touch(self, table, old, new):
        """migrations/005 trigger."""
        if not self.migrated or table not in TRIGGER_FIELDS:
            return
        new["updated_at"] = _now().isoformat()
        changed = any(old.get(f) != new.get(f) for f in TRIGGER_FIELDS[table])
        new["review_version"] = int(old.get("review_version") or 0) + (1 if changed else 0)

    def row(self, table, rid):
        return next(r for r in self.tables[table] if str(r["id"]) == str(rid))


def seed(db: FakeDB):
    m = db.migrated
    review = lambda **kw: ({"updated_at": _ago(minutes=30), "review_version": 3, "review_state": None, "review_action": None,
                            "review_error": None, "review_submitted_at": None, "review_claimed_by": None,
                            "review_claimed_at": None, "review_correction": None, **kw} if m else {})
    db.tables.update({
        "scan_batches": [
            {"id": BATCH, "station_id": STATION, "instructor_id": JUAN, "subject_id": IT101, "term_id": TERM,
             "status": "open", "created_by": STAFF, "created_at": _ago(hours=1)},
            {"id": OTHER_BATCH, "station_id": None, "instructor_id": JUAN, "subject_id": IT101, "term_id": TERM,
             "status": "closed", "created_by": ADMIN, "created_at": _ago(hours=5)},
        ],
        "stations": [{"id": STATION, "label": "Laptop 1", "current_staff_id": STATION_STAFF}],
        "user_info": [
            {"id": JUAN, "first_name": "Juan", "last_name": "Dela Cruz", "account_status": "approved", "is_placeholder": False},
            {"id": PEDRO, "first_name": "Pedro", "last_name": "Santos", "account_status": "approved", "is_placeholder": False},
            {"id": PENDING_PERSON, "first_name": "Ana", "last_name": "Reyes", "account_status": "pending", "is_placeholder": False},
            {"id": PLACEHOLDER, "first_name": "Rosa", "last_name": "Lim", "account_status": "pending", "is_placeholder": True},
            {"id": NO_ROLE, "first_name": "Maria", "last_name": "Cruz", "account_status": "approved", "is_placeholder": False},
            {"id": STAFF, "first_name": "Sara", "last_name": "Staff"},
            {"id": STATION_STAFF, "first_name": "Sam", "last_name": "Station"},
            {"id": ADMIN, "first_name": "Ada", "last_name": "Admin"},
        ],
        "department_table": [
            {"id": 1, "user_id": JUAN, "roles": 1},
            {"id": 2, "user_id": PEDRO, "roles": 2},
            {"id": 3, "user_id": PENDING_PERSON, "roles": 1},
            {"id": 4, "user_id": NO_ROLE, "roles": 4},
        ],
        "subjects": [
            {"id": IT101, "subject_code": "IT 101", "subject_name": "Intro to Computing"},
            {"id": IT102, "subject_code": "IT 102", "subject_name": "Programming 1"},
        ],
        "academic_terms": [{"id": TERM, "semester": "1st Semester", "academic_year": "2026-2027"}],
        "failed_scan_queue": [
            {"id": Q1, "task_id": f"web:{BATCH}:aaaa000000000001", "user_id": STAFF, "term_id": TERM, "table_found": True,
             "grid_source": "python", "status": "pending", "validated_by": None, "validated_at": None,
             "review_reasons": ["instructor_mismatch_found"], "omr_comparison": {"agreement_percent": 95, "comparisons": [
                 {"question": "m1", "python": 4, "gemini": 5, "used": 4, "agree": False}]},
             "validation_status": "flagged", "scan_image_id": IMG1, "batch_id": BATCH, "created_at": _ago(minutes=40),
             "partial_data": {
                 "instructor": "Pedro Santos", "subject": "IT 101", "student_id": "2021-0001", "remarks": "Good",
                 "ratings": {"m1": 4, "m2": "VS", "m3": None, "p1": 5}, "filename": "p1.jpg",
                 "review_note": "The sheet names a different instructor.", "form_check_version": 1,
                 "instructor_match": "found", "subject_match": "same",
                 "suggested_instructor_id": PEDRO, "suggested_instructor_name": "Pedro Santos",
                 "instructor_match_confidence": 0.873, "suggested_subject_id": IT101, "suggested_subject_code": "IT 101",
                 "suggested_subject_name": "Intro to Computing", "subject_match_confidence": 1,
                 "instructor_candidates": [{"id": PEDRO, "name": "Pedro Santos", "score": 0.87, "is_placeholder": False, "is_batch": False},
                                           {"id": JUAN, "name": "Juan Dela Cruz", "score": 0.2, "is_batch": True},
                                           {"id": "not-a-uuid", "name": "x"}],
                 "subject_candidates": [], "ocr_instructor": "Prof. P. Santos", "ocr_subject": "IT101",
                 "batch_instructor_name": "Juan Dela Cruz", "batch_subject_code": "IT 101",
             }, **review()},
            {"id": Q2, "task_id": f"web:{BATCH}:aaaa000000000002", "user_id": STAFF, "term_id": TERM, "table_found": False,
             "grid_source": "fallback", "status": "pending", "validated_by": None, "validated_at": None,
             "review_reasons": ["grid_not_detected"], "omr_comparison": None, "validation_status": "flagged",
             "scan_image_id": None, "batch_id": BATCH, "created_at": _ago(minutes=35),
             "partial_data": {"instructor": "Juan Dela Cruz", "subject": "IT 101", "student_id": "2021-0002",
                              "ratings": {"m1": 3}, "filename": "p2.jpg", "ocr_instructor": "Juan"},
             **review(review_version=1)},
            {"id": Q3, "task_id": f"web:{BATCH}:aaaa000000000003", "user_id": STAFF, "term_id": TERM, "table_found": True,
             "grid_source": "python", "status": "validated", "validated_by": ADMIN, "validated_at": _ago(minutes=20),
             "review_reasons": ["low_confidence"], "omr_comparison": None, "validation_status": "flagged",
             "scan_image_id": None, "batch_id": BATCH, "created_at": _ago(minutes=38),
             "partial_data": {"filename": "p3.jpg"}, **review(review_version=5, review_action="approved")},
            {"id": Q4, "task_id": f"web:{OTHER_BATCH}:bbbb000000000001", "user_id": OUTSIDER, "term_id": TERM,
             "table_found": True, "grid_source": "python", "status": "pending", "validated_by": None, "validated_at": None,
             "review_reasons": [], "omr_comparison": None, "validation_status": "flagged", "scan_image_id": None,
             "batch_id": OTHER_BATCH, "created_at": _ago(hours=4), "partial_data": {}, **review(review_version=0)},
        ],
        "import_errors": [
            {"id": 101, "error_type": "instructor_not_found", "source": "scan", "status": "pending", "resolved_at": None,
             "resolved_by": None, "raw_instructor_name": "Prof Nobody", "raw_subject_name": "Intro to Computing",
             "raw_student_id": "2021-0009", "raw_term_id": TERM, "task_id": f"web:{BATCH}:cccc000000000001",
             "review_reasons": ["low_confidence"], "scan_image_id": IMG2, "created_at": _ago(minutes=10),
             "raw_data": {"Student ID": "2021-0009", "M1": 4, "M2": 3, "P10": 5, "Remarks & Suggestions": "ok",
                          "review_note": "Instructor not found."}, **({"updated_at": _ago(minutes=10), "review_version": 1,
                          "review_state": None, "review_error": None, "review_submitted_at": None,
                          "review_claimed_by": None, "review_claimed_at": None} if m else {})},
            {"id": 102, "error_type": "subject_not_found", "source": "google_sheet", "status": "pending",
             "resolved_at": None, "resolved_by": None, "raw_instructor_name": "x", "raw_subject_name": "y",
             "raw_student_id": "1", "raw_term_id": TERM, "task_id": None, "review_reasons": [], "scan_image_id": None,
             "created_at": _ago(minutes=9), "raw_data": {},
             **({"updated_at": _ago(minutes=9), "review_version": 0, "review_state": None, "review_error": None,
                 "review_submitted_at": None, "review_claimed_by": None, "review_claimed_at": None} if m else {})},
        ],
        "scan_error_images": [
            {"id": IMG1, "task_id": f"web:{BATCH}:aaaa000000000001", "image_base64": _jpeg_b64(), "created_at": _ago(minutes=40)},
            {"id": IMG2, "task_id": f"web:{BATCH}:cccc000000000001", "image_base64": "data:image/jpeg;base64," + _jpeg_b64(),
             "created_at": _ago(minutes=10)},
        ],
        "sast_all_raw_data_survey": [],
    })
    return db


class FakeResp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = {"success": True} if body is None else body

    def json(self):
        return self._body


class FakeN8n:
    def __init__(self, status=200, raises=None, on_post=None):
        self.status, self.raises, self.on_post = status, raises, on_post
        self.calls = []

    def post(self, url, json=None, headers=None):
        self.calls.append((url, json, headers))
        if self.raises:
            raise self.raises
        if self.on_post:
            self.on_post(url, json)
        return FakeResp(self.status)


def user(uid, role="SAO_STAFF"):
    return CurrentUser(uid, f"{uid[:4]}@x", "First", uid[:4], role, "tok")


SETTINGS = SimpleNamespace(
    n8n_correction_webhook_url="http://n8n.invalid/webhook/sast-manual-correction",
    n8n_import_error_webhook_url="http://n8n.invalid/webhook/import-error-correction",
    n8n_webhook_secret="s", n8n_correction_timeout_seconds=180,
)


@pytest.fixture
def env(monkeypatch):
    state = {}

    def _setup(migrated=True, as_user=STAFF, role="SAO_STAFF", n8n=None, settings=SETTINGS):
        db = seed(FakeDB(migrated))
        fake_n8n = n8n or FakeN8n()
        audits = []
        monkeypatch.setattr(reviews, "get_supabase", lambda: db)
        monkeypatch.setattr(reviews, "get_settings", lambda: settings)
        monkeypatch.setattr(reviews, "_n8n", lambda: fake_n8n)
        monkeypatch.setattr(reviews, "role_maps", lambda: (ROLE_BY_NAME, {v: k for k, v in ROLE_BY_NAME.items()}))
        monkeypatch.setattr(reviews, "audit", lambda uid, action, meta=None: audits.append((uid, action, meta)))

        def _no_db():
            raise AssertionError("tests must never reach the real Supabase")
        monkeypatch.setattr(audit_module, "get_supabase", _no_db)
        reviews._mig.update(missing_until=0.0, warned=False)
        reviews._ctx_cache.clear()
        app.dependency_overrides[require_staff] = lambda: user(as_user, role)
        state.update(db=db, n8n=fake_n8n, audits=audits)
        return db, fake_n8n, TestClient(app, raise_server_exceptions=False, headers={"X-Station-Key": "test-station-key"})

    def _as(uid, role="SAO_STAFF"):
        app.dependency_overrides[require_staff] = lambda: user(uid, role)

    _setup.as_user = _as
    _setup.state = state
    yield _setup
    app.dependency_overrides.clear()
    reviews._mig.update(missing_until=0.0, warned=False)


def approve_body(**kw):
    body = {"expected_version": 3, "instructor_id": PEDRO, "subject_id": IT101, "student_id": "2021-0001",
            "remarks": "Good", "ratings": {"m1": 4, "m2": 4, "p1": 5}}
    body.update(kw)
    return body


def by_id(rows):
    return {r["id"]: r for r in rows}


# ------------------------------------------------------------------ list + since cursor
def test_list_full_then_since_cursor(env):
    db, _, client = env()
    r = client.get(f"/reviews/batch/{BATCH}")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["full"] is True and d["migration_ready"] is True and d["access"] == "full"
    assert d["batch"]["instructor"] == {"id": JUAN, "name": "Juan Dela Cruz"}
    assert d["batch"]["subject"]["code"] == "IT 101" and d["batch"]["term"]["label"] == "1st Semester 2026-2027"
    q = by_id(d["queue"])
    assert set(q) == {Q1, Q2, Q3}  # all statuses of this batch, never another batch's rows
    assert [r["id"] for r in d["queue"]] == [Q1, Q3, Q2]  # scan order (created_at), stable for appends
    assert [e["id"] for e in d["import_errors"]] == [101]  # website rows only, no Google Sheet errors
    assert d["counts"] == {"queue_pending": 2, "queue_done": 1, "import_pending": 1}

    one = q[Q1]
    assert one["instructor_match"] == "found" and one["subject_match"] == "same"
    assert one["suggested_instructor"] == {"id": PEDRO, "name": "Pedro Santos", "confidence": 0.87}
    assert one["batch_instructor"] == {"id": JUAN, "name": "Juan Dela Cruz"}
    assert [c["id"] for c in one["instructor_candidates"]] == [PEDRO, JUAN]  # invalid ids dropped
    assert one["ratings"]["m1"] == 4 and one["ratings"]["m2"] == 4 and one["ratings"]["m3"] is None and one["ratings"]["p1"] == 5
    assert one["has_image"] is True and one["filename"] == "p1.jpg" and one["agreement_percent"] == 95
    assert one["ocr_instructor"] == "Prof. P. Santos" and one["review_version"] == 3
    assert one["comparisons"][0] == {"question": "m1", "python": 4, "gemini": 5, "used": 4, "agree": False, "python_weak": False}

    old = q[Q2]  # no form-check keys: suggestions are the batch's own, match 'unchecked'
    assert old["instructor_match"] == "unchecked" and old["subject_match"] == "unchecked"
    assert old["suggested_instructor"] == {"id": JUAN, "name": "Juan Dela Cruz", "confidence": None}
    assert old["suggested_subject"] == {"id": IT101, "code": "IT 101", "name": "Intro to Computing", "confidence": None}
    assert old["instructor_candidates"] == [] and old["has_image"] is False
    assert q[Q3]["validated_by_name"] == "Ada Admin"

    ie = d["import_errors"][0]
    assert ie["student_id"] == "2021-0009" and ie["ratings"]["m1"] == 4 and ie["ratings"]["p10"] == 5
    assert ie["review_note"] == "Instructor not found." and ie["has_image"] is True

    # Nothing changed since the cursor -> empty delta; a changed row comes back alone.
    cursor = d["cursor"]
    r2 = client.get(f"/reviews/batch/{BATCH}", params={"since": cursor})
    assert r2.status_code == 200 and r2.json()["full"] is False
    assert r2.json()["queue"] == [] and r2.json()["import_errors"] == []
    db.row("failed_scan_queue", Q2)["updated_at"] = _now().isoformat()
    db.row("import_errors", 101)["updated_at"] = _now().isoformat()
    r3 = client.get(f"/reviews/batch/{BATCH}", params={"since": cursor}).json()
    assert [x["id"] for x in r3["queue"]] == [Q2] and [x["id"] for x in r3["import_errors"]] == [101]
    assert r3["counts"]["queue_pending"] == 2  # counts are always totals


def test_since_overlap_includes_rows_just_before_cursor(env):
    db, _, client = env()
    cursor = _now()
    db.row("failed_scan_queue", Q1)["updated_at"] = (cursor - timedelta(seconds=3)).isoformat()
    r = client.get(f"/reviews/batch/{BATCH}", params={"since": rl.iso(cursor)}).json()
    assert [x["id"] for x in r["queue"]] == [Q1]


# ------------------------------------------------------------------ access
def test_access_rules(env):
    db, _, client = env(as_user=OUTSIDER)
    assert client.get(f"/reviews/batch/{BATCH}").status_code == 403
    assert client.get(f"/reviews/batch/{BATCH}").json()["detail"]["code"] == "not_your_batch"
    assert client.post(f"/reviews/queue/{Q1}/approve", json=approve_body()).status_code == 403
    assert client.post(f"/reviews/queue/{Q1}/reject", json={"expected_version": 3}).status_code == 403
    assert client.get(f"/reviews/queue/{Q1}/image").status_code == 403
    assert client.get("/reviews/import-errors/101/image").status_code == 403
    assert client.post("/reviews/import-errors/101/discard", json={"expected_version": 1}).status_code == 403

    # Own row in a batch the outsider didn't start: only their own rows, no import errors.
    r = client.get(f"/reviews/batch/{OTHER_BATCH}")
    assert r.status_code == 200 and r.json()["access"] == "own"
    assert [x["id"] for x in r.json()["queue"]] == [Q4] and r.json()["import_errors"] == []
    assert client.get(f"/reviews/queue/{Q4}/image").status_code == 404  # allowed, just no picture

    env.as_user(STATION_STAFF)  # logged in at the batch's laptop, but didn't start it: no access
    r = client.get(f"/reviews/batch/{BATCH}")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "not_your_batch"
    env.as_user(STAFF)  # started the batch
    assert client.get(f"/reviews/batch/{BATCH}").json()["access"] == "full"
    env.as_user(ADMIN, "SAO_ADMIN")
    assert client.get(f"/reviews/batch/{OTHER_BATCH}").json()["access"] == "full"
    assert client.get(f"/reviews/batch/{BATCH}").status_code == 200
    assert client.get("/reviews/batch/not-a-uuid").status_code == 404


def test_non_website_rows_are_not_handled_here(env):
    db, _, client = env(as_user=ADMIN, role="SAO_ADMIN")
    db.row("failed_scan_queue", Q2)["batch_id"] = None  # a phone-app row
    r = client.post(f"/reviews/queue/{Q2}/approve", json=approve_body(expected_version=1))
    assert r.status_code == 404 and r.json()["detail"]["code"] == "not_website"
    r = client.post("/reviews/import-errors/102/discard", json={"expected_version": 0})
    assert r.status_code == 404 and r.json()["detail"]["code"] == "not_website"


# ------------------------------------------------------------------ approve
def test_approve_rerouted_happy_path(env):
    db, n8n, client = env()
    r = client.post(f"/reviews/queue/{Q1}/approve", json=approve_body())
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["outcome"] == "credited"
    assert d["row"]["status"] == "validated" and d["row"]["review_action"] == "rerouted"
    assert d["row"]["review_state"] is None and d["row"]["review_version"] == 5  # submitting (+1), done (+1)
    assert d["row"]["validated_by_name"] == "Sara Staff"

    (url, payload, headers) = n8n.calls[0]
    assert url.endswith("/sast-manual-correction") and headers == {"X-Webhook-Secret": "s"}
    assert payload["failed_scan_id"] == Q1 and payload["task_id"].startswith("web:")
    assert payload["user_id"] == STAFF and payload["term_id"] == TERM
    assert payload["instructor"] == "Pedro Santos" and payload["instructor_id"] == PEDRO
    assert payload["subject"] == "IT 101" and payload["subject_id"] == IT101
    assert payload["correction_source"] == "website_review" and payload["validation_status"] == "corrected"
    assert payload["manually_corrected"] is True and payload["reviewed_by"] == STAFF
    assert payload["m1"] == 4 and payload["m2"] == 4 and payload["p1"] == 5 and payload["m3"] == 0  # blank = 0
    corr = d["row"]["correction"]
    assert corr["instructor"] == {"id": PEDRO, "name": "Pedro Santos"} and corr["subject"]["code"] == "IT 101"
    assert corr["ratings"]["m2"] == 4 and corr["ratings"]["m3"] is None and corr["action"] == "rerouted"
    stored = db.row("failed_scan_queue", Q1)
    assert stored["validated_by"] == STAFF and stored["review_correction"]["action"] == "rerouted"
    assert any(a[1] == "scan_review_approved" for a in env.state["audits"])


def test_approve_batch_values_is_approved(env):
    db, n8n, client = env()
    r = client.post(f"/reviews/queue/{Q2}/approve", json=approve_body(expected_version=1, instructor_id=JUAN, student_id=""))
    assert r.status_code == 200, r.text
    assert r.json()["row"]["review_action"] == "approved"
    assert n8n.calls[0][1]["instructor"] == "Juan Dela Cruz" and n8n.calls[0][1]["student_id"] == ""


def test_approve_outcome_import_error_when_n8n_queues_one(env):
    def n8n_writes_import_error(url, payload):
        env.state["db"].tables["import_errors"].append({
            "id": 150, "task_id": payload["task_id"], "status": "pending", "error_type": "instructor_not_found",
            "created_at": _now().isoformat(), "review_version": 0, "updated_at": _now().isoformat()})
    db, _, client = env(n8n=FakeN8n(on_post=n8n_writes_import_error))
    r = client.post(f"/reviews/queue/{Q1}/approve", json=approve_body())
    assert r.status_code == 200 and r.json()["outcome"] == "import_error"


def test_import_error_appearing_after_the_answer_is_reported_by_the_list(env):
    # n8n answers before its shared flow runs: the import error arrives later.
    db, _, client = env()
    r = client.post(f"/reviews/queue/{Q1}/approve", json=approve_body())
    assert r.status_code == 200 and r.json()["outcome"] == "credited"
    task = r.json()["row"]["task_id"]
    d = client.get(f"/reviews/batch/{BATCH}").json()
    assert task not in d["import_pending_tasks"]
    cursor = d["cursor"]
    db.tables["import_errors"].append({
        "id": 151, "task_id": task, "status": "pending", "error_type": "subject_not_found", "source": "scan",
        "created_at": _now().isoformat(), "updated_at": _now().isoformat(), "review_version": 0, "raw_data": {}})
    d = client.get(f"/reviews/batch/{BATCH}", params={"since": cursor}).json()
    assert task in d["import_pending_tasks"] and d["counts"]["import_pending"] == 2
    assert [e["id"] for e in d["import_errors"]] == [151]
    env.as_user(ADMIN, "SAO_ADMIN")
    assert task in client.get(f"/reviews/batch/{BATCH}").json()["import_pending_tasks"]


def test_approve_stale_row(env):
    db, n8n, client = env()
    r = client.post(f"/reviews/queue/{Q1}/approve", json=approve_body(expected_version=2))
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "stale_row" and r.json()["detail"]["current_version"] == 3
    assert n8n.calls == [] and db.row("failed_scan_queue", Q1)["review_state"] is None


def test_approve_already_resolved(env):
    db, n8n, client = env()
    r = client.post(f"/reviews/queue/{Q3}/approve", json=approve_body(expected_version=5))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "already_resolved"
    assert n8n.calls == []


def test_approve_in_progress_and_stale_submission(env):
    db, n8n, client = env()
    row = db.row("failed_scan_queue", Q1)
    row.update(review_state="submitting", review_submitted_at=_ago(minutes=1))
    r = client.post(f"/reviews/queue/{Q1}/approve", json=approve_body())
    assert r.status_code == 409 and r.json()["detail"]["code"] == "in_progress"
    row.update(review_submitted_at=_ago(minutes=11))  # no answer for 10+ min: may be sent again
    assert client.post(f"/reviews/queue/{Q1}/approve", json=approve_body()).status_code == 200


def test_approve_would_overwrite_and_confirm(env):
    db, n8n, client = env()
    db.tables["sast_all_raw_data_survey"] = [
        {"instructor_ID": PEDRO, "subject_id": IT101, "term_id": TERM, "student_id": "2021-0001", "task_id": "web:x:other"}]
    r = client.post(f"/reviews/queue/{Q1}/approve", json=approve_body())
    assert r.status_code == 409
    d = r.json()["detail"]
    assert d["code"] == "would_overwrite" and d["existing_task_id"] == "web:x:other"
    assert n8n.calls == [] and db.row("failed_scan_queue", Q1)["review_version"] == 3
    r = client.post(f"/reviews/queue/{Q1}/approve", json=approve_body(confirm_overwrite=True))
    assert r.status_code == 200 and len(n8n.calls) == 1


def test_same_task_raw_row_is_not_an_overwrite(env):
    db, _, client = env()
    db.tables["sast_all_raw_data_survey"] = [
        {"instructor_ID": PEDRO, "subject_id": IT101, "term_id": TERM, "student_id": "2021-0001",
         "task_id": f"web:{BATCH}:aaaa000000000001"}]
    assert client.post(f"/reviews/queue/{Q1}/approve", json=approve_body()).status_code == 200


@pytest.mark.parametrize("person", [PENDING_PERSON, NO_ROLE, "a0000000-0000-0000-0000-00000000ffff", "nope"])
def test_non_creditable_instructor_rejected(env, person):
    db, n8n, client = env()
    r = client.post(f"/reviews/queue/{Q1}/approve", json=approve_body(instructor_id=person))
    assert r.status_code == 400 and r.json()["detail"]["code"] == "invalid_instructor"
    assert n8n.calls == [] and db.row("failed_scan_queue", Q1)["review_state"] is None


def test_placeholder_instructor_is_creditable(env):
    db, n8n, client = env()
    assert client.post(f"/reviews/queue/{Q1}/approve", json=approve_body(instructor_id=PLACEHOLDER)).status_code == 200
    assert n8n.calls[0][1]["instructor"] == "Rosa Lim"


def test_invalid_subject_and_ratings(env):
    db, n8n, client = env()
    r = client.post(f"/reviews/queue/{Q1}/approve", json=approve_body(subject_id="b0000000-0000-0000-0000-00000000ffff"))
    assert r.status_code == 400 and r.json()["detail"]["code"] == "invalid_subject"
    for bad in ({"m1": 6}, {"x9": 1}, {"m1": True}, {"m1": "abc"}, {"m1": 2.5}):
        r = client.post(f"/reviews/queue/{Q1}/approve", json=approve_body(ratings=bad))
        assert r.status_code == 400 and r.json()["detail"]["code"] == "invalid_ratings", bad
    assert n8n.calls == []


def test_timeout_leaves_submitting_then_list_finalises(env):
    db, n8n, client = env(n8n=FakeN8n(raises=httpx.ReadTimeout("slow")))
    r = client.post(f"/reviews/queue/{Q1}/approve", json=approve_body())
    assert r.status_code == 200 and r.json()["outcome"] == "submitted"
    assert r.json()["row"]["review_state"] == "submitting"
    # Meanwhile the row can't be sent twice.
    again = client.post(f"/reviews/queue/{Q1}/approve", json=approve_body(expected_version=4))
    assert again.status_code == 409 and again.json()["detail"]["code"] == "in_progress"

    # n8n finishes: 'Mark Queue Validated1' sets status (the trigger bumps the version).
    row = db.row("failed_scan_queue", Q1)
    old = dict(row)
    row.update(status="validated", validated_at=_now().isoformat())
    db.touch("failed_scan_queue", old, row)
    d = client.get(f"/reviews/batch/{BATCH}").json()
    one = by_id(d["queue"])[Q1]
    assert one["review_state"] is None and one["review_action"] == "rerouted" and one["status"] == "validated"
    assert one["validated_by_name"] == "Sara Staff"


def test_timeout_without_answer_becomes_submit_failed_after_10_minutes(env):
    db, _, client = env()
    db.row("failed_scan_queue", Q1).update(review_state="submitting", review_submitted_at=_ago(minutes=11),
                                            review_correction={"action": "rerouted", "reviewed_by": STAFF})
    since = rl.iso(_now())  # the row itself did not change since the cursor: still finalised
    d = client.get(f"/reviews/batch/{BATCH}", params={"since": since}).json()
    one = by_id(d["queue"])[Q1]
    assert one["review_state"] == "submit_failed" and "10 minutes" in one["review_error"]
    assert one["status"] == "pending"


def test_recent_submission_is_left_alone_by_the_list(env):
    db, _, client = env()
    db.row("failed_scan_queue", Q1).update(review_state="submitting", review_submitted_at=_ago(minutes=2))
    d = client.get(f"/reviews/batch/{BATCH}").json()
    assert by_id(d["queue"])[Q1]["review_state"] == "submitting"


def test_http_error_marks_submit_failed_502(env):
    db, n8n, client = env(n8n=FakeN8n(status=500))
    r = client.post(f"/reviews/queue/{Q1}/approve", json=approve_body())
    assert r.status_code == 502
    d = r.json()["detail"]
    assert d["code"] == "correction_failed" and "HTTP 500" in d["message"]
    assert d["row"]["review_state"] == "submit_failed" and d["row"]["status"] == "pending"
    assert "n8n.invalid" not in r.text
    stored = db.row("failed_scan_queue", Q1)
    assert stored["review_state"] == "submit_failed" and stored["review_error"]
    # A failed send can be retried with the new version.
    env.state["n8n"].status = 200
    r = client.post(f"/reviews/queue/{Q1}/approve", json=approve_body(expected_version=d["row"]["review_version"]))
    assert r.status_code == 200 and r.json()["row"]["status"] == "validated"


def test_unreachable_n8n_is_submit_failed(env):
    db, _, client = env(n8n=FakeN8n(raises=httpx.ConnectError("refused")))
    r = client.post(f"/reviews/queue/{Q1}/approve", json=approve_body())
    assert r.status_code == 502 and r.json()["detail"]["code"] == "correction_failed"
    assert db.row("failed_scan_queue", Q1)["review_state"] == "submit_failed"


def test_correction_webhook_not_configured(env):
    db, n8n, client = env(settings=SimpleNamespace(n8n_correction_webhook_url="", n8n_import_error_webhook_url="",
                                                   n8n_webhook_secret="", n8n_correction_timeout_seconds=180))
    r = client.post(f"/reviews/queue/{Q1}/approve", json=approve_body())
    assert r.status_code == 503 and r.json()["detail"]["code"] == "correction_not_configured"
    assert db.row("failed_scan_queue", Q1)["review_state"] is None
    assert client.get(f"/reviews/batch/{BATCH}").json()["correction_ready"] is False


# ------------------------------------------------------------------ migration 005 missing
def test_missing_migration_read_only(env):
    db, n8n, client = env(migrated=False)
    r = client.get(f"/reviews/batch/{BATCH}", params={"since": rl.iso(_now())})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["migration_ready"] is False and d["full"] is True  # since ignored: always the full list
    assert {x["id"] for x in d["queue"]} == {Q1, Q2, Q3}
    assert by_id(d["queue"])[Q1]["review_version"] == 0 and by_id(d["queue"])[Q1]["review_state"] is None
    assert [e["id"] for e in d["import_errors"]] == [101]
    for path, body in ((f"/reviews/queue/{Q1}/approve", approve_body(expected_version=0)),
                       (f"/reviews/queue/{Q1}/reject", {"expected_version": 0}),
                       (f"/reviews/queue/{Q1}/claim", {}),
                       ("/reviews/import-errors/101/resolve", {"expected_version": 0, "instructor_id": JUAN, "subject_id": IT101}),
                       ("/reviews/import-errors/101/discard", {"expected_version": 0})):
        r = client.post(path, json=body)
        assert r.status_code == 503 and r.json()["detail"]["code"] == "migration_required", path
    assert n8n.calls == []
    assert client.get(f"/reviews/queue/{Q1}/image").status_code == 200  # pictures don't need 005
    # Applied later: picked up without a restart once the retry window passes.
    db.missing.clear()
    db.migrated = True
    for r_ in db.tables["failed_scan_queue"]:
        r_.update(review_version=0, updated_at=_ago(minutes=1), review_state=None, review_action=None,
                  review_claimed_by=None, review_claimed_at=None, review_correction=None, review_error=None,
                  review_submitted_at=None)
    reviews._mig.update(missing_until=0.0)
    assert client.get(f"/reviews/batch/{BATCH}").json()["migration_ready"] is True


def test_is_missing_review_column():
    assert rl.is_missing_review_column(APIError({"code": "42703", "message": "column failed_scan_queue.review_version does not exist"}))
    assert rl.is_missing_review_column(APIError({"code": "PGRST204", "message": "Could not find the 'review_state' column of 'import_errors' in the schema cache"}))
    assert rl.is_missing_review_column(APIError({"code": "42703", "message": ""}))
    assert not rl.is_missing_review_column(APIError({"code": "42703", "message": "column stations.watcher_folder does not exist"}))
    assert not rl.is_missing_review_column(APIError({"code": "23505", "message": "duplicate key"}))
    assert not rl.is_missing_review_column(ValueError("x"))


# ------------------------------------------------------------------ reject + claim
def test_reject(env):
    db, n8n, client = env()
    r = client.post(f"/reviews/queue/{Q1}/reject", json={"expected_version": 2})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "stale_row"
    r = client.post(f"/reviews/queue/{Q1}/reject", json={"expected_version": 3, "note": "  not our   form "})
    assert r.status_code == 200, r.text
    row = r.json()["row"]
    assert row["status"] == "discarded" and row["review_action"] == "rejected" and row["review_version"] == 4
    assert row["review_note_by_reviewer"] == "not our form" and row["validated_by_name"] == "Sara Staff"
    assert n8n.calls == []
    r = client.post(f"/reviews/queue/{Q1}/reject", json={"expected_version": 4})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "already_resolved"


def test_claim_is_advisory_and_keeps_version(env):
    db, _, client = env()
    r = client.post(f"/reviews/queue/{Q1}/claim", json={})
    assert r.status_code == 200 and r.json()["claimed"] is True
    assert r.json()["claimed_by"]["id"] == STAFF and r.json()["row"]["review_version"] == 3
    env.as_user(ADMIN, "SAO_ADMIN")  # a second person with access to the batch
    r = client.post(f"/reviews/queue/{Q1}/claim", json={})
    assert r.json()["claimed"] is False and r.json()["claimed_by"] == {"id": STAFF, "name": "Sara Staff"}
    r = client.post(f"/reviews/queue/{Q1}/claim", json={"release": True})  # not theirs: nothing released
    assert db.row("failed_scan_queue", Q1)["review_claimed_by"] == STAFF
    db.row("failed_scan_queue", Q1)["review_claimed_at"] = _ago(minutes=6)  # expired
    assert client.post(f"/reviews/queue/{Q1}/claim", json={}).json()["claimed"] is True
    r = client.post(f"/reviews/queue/{Q1}/claim", json={"release": True})
    assert r.json()["claimed_by"] is None and db.row("failed_scan_queue", Q1)["review_claimed_by"] is None
    assert db.row("failed_scan_queue", Q1)["review_version"] == 3


# ------------------------------------------------------------------ import errors
def test_import_error_resolve(env):
    db, n8n, client = env()
    body = {"expected_version": 1, "instructor_id": PEDRO, "subject_id": IT102, "student_id": "2021-0010",
            "remarks": "fixed", "ratings": {"m1": 5, "p10": None}}
    r = client.post("/reviews/import-errors/101/resolve", json={**body, "expected_version": 0})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "stale_row"
    r = client.post("/reviews/import-errors/101/resolve", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["row"]["status"] == "resolved" and r.json()["row"]["resolved_by_name"] == "Sara Staff"
    (url, p, headers) = n8n.calls[0]
    assert url.endswith("/import-error-correction")
    assert p["import_error_id"] == 101 and p["corrected_instructor_id"] == PEDRO and p["corrected_subject_id"] == IT102
    assert p["term_id"] == TERM and p["resolved_by"] == STAFF and p["source"] == "scan"
    assert p["instructor"] == "Pedro Santos" and p["task_id"] == f"web:{BATCH}:cccc000000000001"
    raw = p["raw_data"]
    assert raw["M1"] == 5 and raw["m1"] == 5 and raw["P10"] == 0 and raw["M2"] == 0
    assert raw["Student ID"] == "2021-0010" and raw["Remarks & Suggestions"] == "fixed"
    assert db.row("import_errors", 101)["resolved_by"] == STAFF
    r = client.post("/reviews/import-errors/101/resolve", json={**body, "expected_version": 3})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "already_resolved"


def test_import_error_resolve_keeps_raw_values_when_not_edited(env):
    db, n8n, client = env()
    r = client.post("/reviews/import-errors/101/resolve", json={"expected_version": 1, "instructor_id": JUAN, "subject_id": IT101})
    assert r.status_code == 200
    raw = n8n.calls[0][1]["raw_data"]
    assert raw["M1"] == 4 and raw["Student ID"] == "2021-0009" and "m1" not in raw


def test_import_error_resolve_http_error_and_overwrite(env):
    db, n8n, client = env(n8n=FakeN8n(status=502))
    db.tables["sast_all_raw_data_survey"] = [
        {"instructor_ID": JUAN, "subject_id": IT101, "term_id": TERM, "student_id": "2021-0009", "task_id": None}]
    body = {"expected_version": 1, "instructor_id": JUAN, "subject_id": IT101}
    r = client.post("/reviews/import-errors/101/resolve", json=body)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "would_overwrite"
    assert r.json()["detail"]["existing_task_id"] is None
    r = client.post("/reviews/import-errors/101/resolve", json={**body, "confirm_overwrite": True})
    assert r.status_code == 502 and r.json()["detail"]["code"] == "correction_failed"
    assert db.row("import_errors", 101)["review_state"] == "submit_failed"
    assert db.row("import_errors", 101)["status"] == "pending"


def test_import_error_discard(env):
    db, n8n, client = env()
    r = client.post("/reviews/import-errors/101/discard", json={"expected_version": 0})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "stale_row"
    r = client.post("/reviews/import-errors/101/discard", json={"expected_version": 1})
    assert r.status_code == 200 and r.json()["row"]["status"] == "discarded"
    assert n8n.calls == []


def test_import_error_timeout_then_finalised(env):
    db, _, client = env(n8n=FakeN8n(raises=httpx.WriteTimeout("slow")))
    body = {"expected_version": 1, "instructor_id": JUAN, "subject_id": IT101}
    r = client.post("/reviews/import-errors/101/resolve", json=body)
    assert r.status_code == 200 and r.json()["outcome"] == "submitted"
    row = db.row("import_errors", 101)
    old = dict(row)
    row.update(status="resolved")  # n8n 'Mark import_error resolved1'
    db.touch("import_errors", old, row)
    d = client.get(f"/reviews/batch/{BATCH}").json()
    assert d["import_errors"][0]["status"] == "resolved" and d["import_errors"][0]["review_state"] is None


# ------------------------------------------------------------------ images
def test_image_endpoints(env):
    db, _, client = env()
    r = client.get(f"/reviews/queue/{Q1}/image")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content[:3] == b"\xff\xd8\xff"
    assert "private" in r.headers["cache-control"]
    r = client.get("/reviews/import-errors/101/image")  # data: URL prefix is stripped
    assert r.status_code == 200 and r.content[:3] == b"\xff\xd8\xff"
    r = client.get(f"/reviews/queue/{Q2}/image")
    assert r.status_code == 404 and r.json()["detail"]["code"] == "no_image"
    # No scan_image_id, but an image stored under the task id.
    db.tables["scan_error_images"].append({"id": "e0000000-0000-0000-0000-000000000009", "task_id": f"web:{BATCH}:aaaa000000000002",
                                           "image_base64": _jpeg_b64(), "created_at": _ago(minutes=1)})
    assert client.get(f"/reviews/queue/{Q2}/image").status_code == 200
    assert client.get("/reviews/import-errors/999/image").status_code == 404


def test_review_endpoints_require_login():
    c = TestClient(app, raise_server_exceptions=False, headers={"X-Station-Key": "test-station-key"})
    for method, path in (("get", f"/reviews/batch/{BATCH}"), ("get", f"/reviews/queue/{Q1}/image"),
                         ("post", f"/reviews/queue/{Q1}/approve"), ("post", "/reviews/import-errors/1/discard")):
        r = getattr(c, method)(path)
        assert r.status_code in (401, 422), path
        if r.status_code == 401:
            assert r.json()["detail"]["code"] == "not_authenticated"


# ------------------------------------------------------------------ pure helpers
def test_derive_webhook_url():
    assert derive_webhook_url("https://n8n.x/webhook/sast-batch-scan", "sast-manual-correction") == "https://n8n.x/webhook/sast-manual-correction"
    assert derive_webhook_url("http://h:5678/webhook/sast-batch-scan/", "import-error-correction") == "http://h:5678/webhook/import-error-correction"
    assert derive_webhook_url("https://n8n.x/webhook/other", "sast-manual-correction") == ""
    assert derive_webhook_url("", "sast-manual-correction") == ""


def test_rating_helpers():
    assert [rl.normalize_rating(v) for v in (5, "4", "VS", "us", "blank", 0, 6, 2.0, 2.5, True, None)] == \
        [5, 4, 4, 1, None, None, None, 2, None, None, None]
    ratings = rl.extract_ratings({"ratings": {"management": [{"score": "O"}, 3]}, "gemini_scores": {"p2": 2}},
                                 {"comparisons": [{"question": "p1", "used": None, "python": 4}]})
    assert ratings["m1"] == 5 and ratings["m2"] == 3 and ratings["p1"] == 4 and ratings["p2"] == 2 and ratings["m3"] is None
    assert rl.validate_ratings({"m1": "3", "p10": None, "p9": ""})["m1"] == 3
    with pytest.raises(ValueError):
        rl.validate_ratings({"m11": 1})
    assert rl.batch_id_from_task(f"web:{BATCH}:abc") == BATCH and rl.batch_id_from_task("scan-123") is None
    assert rl.review_action_for(JUAN, IT101, {"instructor_id": JUAN, "subject_id": IT101}) == "approved"
    assert rl.review_action_for(JUAN, IT102, {"instructor_id": JUAN, "subject_id": IT101}) == "rerouted"


def test_suggestions_ignore_unchecked_keys():
    s = rl.suggestions({"instructor_match": "bogus", "suggested_instructor_id": PEDRO},
                       {"instructor": {"id": JUAN, "name": "Juan"}, "subject": {"id": IT101, "code": "IT 101", "name": "x"}})
    assert s["instructor_match"] == "unchecked" and s["suggested_instructor"]["id"] == JUAN
