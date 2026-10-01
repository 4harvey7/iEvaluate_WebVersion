"""GET /reviews/batches: the /review page's list of batches (open and closed).
In-memory fake Supabase, no network."""
import httpx
import pytest
from fastapi.testclient import TestClient

from app import audit as audit_module
from app import unsaved_pages as uv
from app.main import app
from app.routers import reviews
from app.security import require_staff
from tests.test_reviews import (
    ADMIN, BATCH, IT101, JUAN, OTHER_BATCH, OUTSIDER, Q4, STAFF, STATION, STATION_STAFF, TERM, _ago, seed, user,
)
from tests.test_unsaved_pages import DB, Q

B_OLD = "e0000000-0000-0000-0000-000000000001"
B_ADMIN_OLD = "e0000000-0000-0000-0000-000000000002"


class LQ(Q):
    def lt(self, col, val):
        from tests.test_reviews import _cmp_value
        return self._filter(col, lambda v: v is not None and _cmp_value(v) < _cmp_value(val))


class LDB(DB):
    calls = 0

    def table(self, name):
        self.calls += 1
        return LQ(self, name)


def _batch(bid, by, **kw):
    return {"id": bid, "station_id": STATION, "instructor_id": JUAN, "subject_id": IT101, "term_id": TERM,
            "status": "closed", "created_by": by, "created_at": _ago(hours=2), "closed_at": _ago(hours=1), **kw}


@pytest.fixture
def env(monkeypatch):
    def _setup(as_user=STAFF, role="SAO_STAFF", migrated=True, unsaved_table=True):
        db = seed(LDB(migrated=migrated, unsaved_table=unsaved_table))
        db.tables["scan_batches"] += [_batch(B_OLD, STAFF), _batch(B_ADMIN_OLD, ADMIN, created_at=_ago(hours=3))]
        db.tables.setdefault("scan_unsaved_pages", [])
        monkeypatch.setattr(reviews, "get_supabase", lambda: db)
        monkeypatch.setattr(audit_module, "get_supabase", lambda: (_ for _ in ()).throw(AssertionError("no real DB")))
        reviews._mig.update(missing_until=0.0, warned=False)
        uv.reset_state()
        app.dependency_overrides[require_staff] = lambda: user(as_user, role)
        return db, TestClient(app, raise_server_exceptions=False, headers={"X-Station-Key": "test-station-key"})

    yield _setup
    app.dependency_overrides.clear()
    uv.reset_state()


def _ids(r):
    assert r.status_code == 200, r.text
    return [b["id"] for b in r.json()["batches"]]


def _by_id(r, bid):
    return next(b for b in r.json()["batches"] if b["id"] == bid)


def test_admin_sees_all_work_first_then_newest(env):
    db, c = env(ADMIN, "SAO_ADMIN")
    r = c.get("/reviews/batches")
    assert _ids(r) == [BATCH, OTHER_BATCH, B_OLD, B_ADMIN_OLD]
    b = _by_id(r, BATCH)
    assert b["counts"] == {"queue_pending": 2, "import_pending": 1, "unsaved_pending": 0}
    assert b["to_review"] == 3 and b["access"] == "full" and b["status"] == "open"
    assert b["instructor"]["name"] and b["subject"]["code"] and b["term"]["label"] and b["station"]["label"] == "Laptop 1"
    assert b["started_by"]["id"] == STAFF and b["started_by"]["name"]
    assert _by_id(r, OTHER_BATCH)["counts"]["queue_pending"] == 1
    d = r.json()
    assert d["next_before"] is None and d["migration_ready"] and d["unsaved_ready"]


def test_staff_sees_only_batches_they_started(env):
    db, c = env()
    assert _ids(c.get("/reviews/batches")) == [BATCH, B_OLD]


def test_own_rows_and_laptop_grant_no_more(env):
    db, c = env(OUTSIDER)
    r = c.get("/reviews/batches")
    assert _ids(r) == [OTHER_BATCH]
    b = _by_id(r, OTHER_BATCH)
    assert b["access"] == "own" and b["counts"] == {"queue_pending": 1, "import_pending": 0, "unsaved_pending": 0}
    next(q for q in db.tables["failed_scan_queue"] if q["id"] == Q4)["status"] = "validated"
    assert _ids(c.get("/reviews/batches")) == []
    db, c = env(STATION_STAFF)
    assert _ids(c.get("/reviews/batches")) == []


def test_only_pending_hides_finished_batches(env):
    db, c = env(ADMIN, "SAO_ADMIN")
    r = c.get("/reviews/batches?only_pending=true")
    assert _ids(r) == [BATCH, OTHER_BATCH] and r.json()["next_before"] is None
    for t in ("failed_scan_queue", "import_errors"):
        for row in db.tables[t]:
            row["status"] = "validated"
    assert _ids(c.get("/reviews/batches?only_pending=true")) == []
    assert _ids(c.get("/reviews/batches"))[0] == BATCH


def test_paging_with_before(env):
    db, c = env(ADMIN, "SAO_ADMIN")
    r = c.get("/reviews/batches?limit=1")
    assert _ids(r) == [BATCH, OTHER_BATCH, B_OLD]
    nb = r.json()["next_before"]
    assert nb
    r2 = c.get("/reviews/batches", params={"limit": 1, "before": nb})
    assert _ids(r2) == [B_ADMIN_OLD] and r2.json()["next_before"] is None
    bad = c.get("/reviews/batches?before=yesterday")
    assert bad.status_code == 400 and "invalid_cursor" in bad.text
    assert c.get("/reviews/batches?limit=500").status_code == 422


def test_couldnt_be_saved_counts_and_missing_table(env):
    db, c = env()
    db.tables["scan_unsaved_pages"] += [
        {"id": "f0000000-0000-0000-0000-000000000001", "batch_id": B_OLD, "status": "needs_review"},
        {"id": "f0000000-0000-0000-0000-000000000002", "batch_id": B_OLD, "status": "saved"},
    ]
    r = c.get("/reviews/batches")
    assert _ids(r) == [BATCH, B_OLD]
    assert _by_id(r, B_OLD)["counts"]["unsaved_pending"] == 1
    db, c = env(unsaved_table=False)
    r = c.get("/reviews/batches")
    assert r.status_code == 200 and r.json()["unsaved_ready"] is False


def test_works_before_migration_005(env):
    db, c = env(ADMIN, "SAO_ADMIN", migrated=False)
    r = c.get("/reviews/batches")
    assert r.status_code == 200 and r.json()["migration_ready"] is False
    assert _by_id(r, BATCH)["counts"]["queue_pending"] == 2


def test_database_unreachable_is_503(env):
    db, c = env()

    def boom(name):
        raise httpx.ConnectError("down")
    db.table = boom
    r = c.get("/reviews/batches")
    assert r.status_code == 503 and "database_unreachable" in r.text


def test_requires_login():
    app.dependency_overrides.clear()
    r = TestClient(app, raise_server_exceptions=False).get("/reviews/batches")
    assert r.status_code in (401, 422)


def test_query_count_does_not_grow_with_batches(env):
    db, c = env(ADMIN, "SAO_ADMIN")
    for i in range(30):
        bid = f"e1000000-0000-0000-0000-{i:012d}"
        db.tables["scan_batches"].append(_batch(bid, STAFF, created_at=_ago(hours=10 + i)))
        db.tables["failed_scan_queue"].append({**db.tables["failed_scan_queue"][0], "id": f"e2000000-0000-0000-0000-{i:012d}",
                                               "batch_id": bid, "status": "pending"})
    db.calls = 0
    assert c.get("/reviews/batches").status_code == 200
    assert db.calls <= 12
