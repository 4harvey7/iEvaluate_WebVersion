"""Watcher 2.4 contract: ingest early return without a batch, pages scanned
before the batch (skipped-pages endpoint + heartbeat command round trip),
batch detail extras and the database_unreachable 503. No network, no DB."""
import base64
import hashlib
import io
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from postgrest.exceptions import APIError

from app import logic, security, station_status
from app.main import app
from app.routers import batches, scans, stations
from app.security import CurrentUser, require_staff, require_station

SID = "33333333-3333-3333-3333-333333333333"
BID = "44444444-4444-4444-4444-444444444444"
NOW = datetime.now(timezone.utc)


# ---------------------------------------------------------------- tiny in-memory PostgREST
class _Q:
    def __init__(self, sb, table):
        self.sb, self.table, self.filters, self.op, self.values = sb, table, [], "select", None
        self.cols, self.lim = None, None

    def select(self, cols="*", count=None, head=None):
        self.cols = cols
        return self

    def update(self, values):
        self.op, self.values = "update", dict(values)
        return self

    def insert(self, values):
        self.op, self.values = "insert", dict(values)
        return self

    def eq(self, col, val):
        self.filters.append(lambda r: r.get(col) == val)
        return self

    def in_(self, col, vals):
        vals = list(vals)
        self.filters.append(lambda r: r.get(col) in vals)
        return self

    def is_(self, col, val):
        self.filters.append(lambda r: r.get(col) is None if val == "null" else r.get(col) == val)
        return self

    def limit(self, n):
        self.lim = n
        return self

    def __getattr__(self, name):  # order / or_ ...: no-ops
        return lambda *a, **k: self

    def _check_columns(self, names):
        missing = [c for c in names if c in self.sb.missing_columns]
        if missing:
            raise APIError({"code": "42703", "message": f"column {self.table}.{missing[0]} does not exist",
                            "hint": None, "details": None})

    def execute(self):
        rows = self.sb.tables.setdefault(self.table, [])
        if self.op == "insert":
            rows.append(dict(self.values))
            return SimpleNamespace(data=[dict(self.values)], count=1)
        if self.cols and self.cols != "*":
            self._check_columns([c.strip() for c in self.cols.split(",")])
        hit = [r for r in rows if all(f(r) for f in self.filters)]
        if self.lim is not None:
            hit = hit[: self.lim]
        if self.op == "update":
            self._check_columns(self.values)
            self.sb.updates.append((self.table, dict(self.values)))
            for r in hit:
                r.update(self.values)
        return SimpleNamespace(data=[dict(r) for r in hit], count=len(hit))


class MemSB:
    def __init__(self, tables=None, missing_columns=()):
        self.tables = tables or {}
        self.missing_columns = set(missing_columns)
        self.updates = []
        self.rpcs = []

    def table(self, name):
        return _Q(self, name)

    def rpc(self, name, params):
        self.rpcs.append((name, params))
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=[]))


def _station(**extra):
    row = {"id": SID, "label": "Laptop 1", "folder_path": "C:/Scans", "current_staff_id": None,
           "updated_at": None, "last_seen_at": NOW.isoformat(), "key_created_at": NOW.isoformat(),
           "watcher_version": "2.4.0", "key_hash": "h"}
    row.update(extra)
    return row


def _open_batch(**extra):
    b = {"id": BID, "station_id": SID, "status": "open", "created_by": "u", "instructor_id": "i", "subject_id": "s",
         "term_id": "t", "created_at": (NOW - timedelta(minutes=10)).isoformat(), "last_scan_at": None}
    b.update(extra)
    return b


USER = CurrentUser(id="staff-1", email=None, first_name="A", last_name="B", role="SAO_STAFF", token="t")


@pytest.fixture
def env(monkeypatch):
    station_status._state["missing_until"] = 0.0

    def _env(sb):
        for mod in (stations, scans, batches):
            monkeypatch.setattr(mod, "get_supabase", lambda: sb)
        monkeypatch.setattr(stations, "audit", lambda *a, **k: None)
        app.dependency_overrides[require_staff] = lambda: USER
        app.dependency_overrides[require_station] = lambda: {"id": SID, "label": "Laptop 1", "folder_path": "C:/Scans"}
        # The body-limit middleware answers 401 to ingest calls without a key.
        return TestClient(app, raise_server_exceptions=False, headers={"X-Station-Key": "test-station-key"})

    yield _env
    app.dependency_overrides.clear()
    station_status._state["missing_until"] = 0.0


def _page(captured_at=None):
    buf = io.BytesIO()
    Image.new("L", (30, 30), 200).save(buf, format="JPEG")
    raw = buf.getvalue()
    return {
        "filename": "p1.jpg",
        "image_base64": base64.b64encode(raw).decode(),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "captured_at": (captured_at or datetime.now(timezone.utc)).isoformat(),
    }


# ---------------------------------------------------------------- 3. ingest checks the batch first
def _no_decode(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("image must not be decoded")
    monkeypatch.setattr(scans, "decode_image_base64", boom)
    monkeypatch.setattr(scans, "prepare_page", boom)


def test_ingest_without_open_batch_returns_before_decoding(env, monkeypatch):
    sb = MemSB({"scan_batches": [], "stations": [_station()]})
    client = env(sb)
    _no_decode(monkeypatch)
    r = client.post("/scans/ingest", json=_page())
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "no_open_batch"
    assert sb.rpcs == []  # nothing claimed


def test_ingest_predating_file_returns_before_decoding(env, monkeypatch):
    sb = MemSB({"scan_batches": [_open_batch()], "stations": [_station()]})
    client = env(sb)
    _no_decode(monkeypatch)
    r = client.post("/scans/ingest", json=_page(NOW - timedelta(hours=1)))
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "file_predates_batch"
    assert sb.rpcs == []


def test_ingest_still_validates_image_after_batch_check(env):
    sb = MemSB({"scan_batches": [_open_batch()], "stations": [_station()]})
    client = env(sb)
    p = _page()
    p["sha256"] = "0" * 64  # wrong hash
    r = client.post("/scans/ingest", json=p)
    assert r.status_code == 400 and r.json()["detail"]["code"] == "hash_mismatch"


# ---------------------------------------------------------------- 2. skipped-pages endpoint
def test_add_to_batch_needs_open_batch(env):
    sb = MemSB({"scan_batches": [], "stations": [_station()]})
    client = env(sb)
    r = client.post(f"/stations/{SID}/skipped-pages", json={"action": "add_to_batch"})
    assert r.status_code == 409
    assert r.json() == {"detail": "Start a batch first, then add these pages."}
    assert not any(t == "stations" for t, _ in sb.updates)


@pytest.mark.parametrize("action,command", [("add_to_batch", "resend_skipped"), ("discard", "discard_skipped")])
def test_skipped_pages_sets_command_and_returns_station(env, action, command):
    sb = MemSB({"scan_batches": [_open_batch()], "stations": [_station(watcher_skipped_files=3)], "user_info": []})
    client = env(sb)
    r = client.post(f"/stations/{SID}/skipped-pages", json={"action": action})
    assert r.status_code == 200, r.text
    st = r.json()
    assert st["id"] == SID
    assert st["watcher_command"] == command
    assert st["watcher_skipped_files"] == 3
    assert "watcher_command_id" not in st and "watcher_command_at" not in st and "key_hash" not in st
    row = sb.tables["stations"][0]
    assert row["watcher_command"] == command and row["watcher_command_id"] and row["watcher_command_at"]


def test_discard_works_without_open_batch(env):
    sb = MemSB({"scan_batches": [], "stations": [_station()], "user_info": []})
    client = env(sb)
    r = client.post(f"/stations/{SID}/skipped-pages", json={"action": "discard"})
    assert r.status_code == 200 and r.json()["watcher_command"] == "discard_skipped"


def test_skipped_pages_without_migration_004_is_503(env):
    sb = MemSB({"scan_batches": [_open_batch()], "stations": [_station()], "user_info": []},
               missing_columns=station_status.ALL_WATCHER_FIELDS)
    client = env(sb)
    r = client.post(f"/stations/{SID}/skipped-pages", json={"action": "add_to_batch"})
    assert r.status_code == 503
    assert r.json() == {"detail": "This needs a database update first. Ask IT to apply migration 004."}
    # Station reads still work (fallback), with the new fields as null.
    s = client.get(f"/stations/{SID}").json()
    assert s["watcher_skipped_files"] is None and s["watcher_command"] is None


def test_skipped_pages_rejects_unknown_action_and_station(env):
    sb = MemSB({"scan_batches": [_open_batch()], "stations": [_station()]})
    client = env(sb)
    assert client.post(f"/stations/{SID}/skipped-pages", json={"action": "delete"}).status_code == 422
    other = "55555555-5555-5555-5555-555555555555"
    assert client.post(f"/stations/{other}/skipped-pages", json={"action": "discard"}).status_code == 404


def test_station_list_exposes_new_fields(env):
    sb = MemSB({"scan_batches": [], "stations": [_station(watcher_skipped_files=2)], "user_info": []})
    client = env(sb)
    (s,) = client.get("/stations").json()
    assert s["watcher_skipped_files"] == 2 and s["watcher_command"] is None


# ---------------------------------------------------------------- 2. heartbeat command round trip
def _hb(client, **body):
    base = {"version": "2.4.0", "folder": "C:/Scans", "folder_exists": True, "skipped_files": 2}
    base.update(body)
    r = client.post("/scans/heartbeat", json=base)
    assert r.status_code == 200, r.text
    return r.json()


def test_heartbeat_command_round_trip(env):
    sb = MemSB({"scan_batches": [_open_batch()], "stations": [_station()], "user_info": []})
    client = env(sb)
    d = _hb(client)
    assert d["command"] is None and d["command_id"] is None
    assert sb.tables["stations"][0]["watcher_skipped_files"] == 2

    assert client.post(f"/stations/{SID}/skipped-pages", json={"action": "add_to_batch"}).status_code == 200
    d = _hb(client)
    assert d["command"] == "resend_skipped" and d["command_id"]
    cid = d["command_id"]
    # Not done yet -> handed out again with the same id.
    assert _hb(client)["command_id"] == cid
    # Done -> cleared, next answer is null.
    d = _hb(client, command_done=cid, skipped_files=0)
    assert d["command"] is None and d["command_id"] is None
    row = sb.tables["stations"][0]
    assert row["watcher_command"] is None and row["watcher_command_id"] is None
    assert row["watcher_skipped_files"] == 0
    assert client.get(f"/stations/{SID}").json()["watcher_command"] is None


def test_command_done_for_old_id_keeps_newer_command(env):
    sb = MemSB({"scan_batches": [_open_batch()], "stations": [_station()], "user_info": []})
    client = env(sb)
    client.post(f"/stations/{SID}/skipped-pages", json={"action": "discard"})
    d = _hb(client, command_done="some-older-id")
    assert d["command"] == "discard_skipped"


def test_expired_command_is_dropped(env):
    old = (NOW - timedelta(seconds=station_status.COMMAND_TTL_SECONDS + 60)).isoformat()
    sb = MemSB({"scan_batches": [_open_batch()], "user_info": [],
                "stations": [_station(watcher_command="discard_skipped", watcher_command_id="x", watcher_command_at=old)]})
    client = env(sb)
    assert client.get(f"/stations/{SID}").json()["watcher_command"] is None
    assert _hb(client)["command"] is None
    assert sb.tables["stations"][0]["watcher_command"] is None


def test_resend_dropped_when_batch_closed_before_pickup(env):
    sb = MemSB({"scan_batches": [], "user_info": [],
                "stations": [_station(watcher_command="resend_skipped", watcher_command_id="x",
                                      watcher_command_at=NOW.isoformat())]})
    client = env(sb)
    assert _hb(client)["command"] is None
    assert sb.tables["stations"][0]["watcher_command"] is None


def test_heartbeat_without_migration_004_still_works(env):
    sb = MemSB({"scan_batches": [_open_batch()], "stations": [_station()]},
               missing_columns=station_status.ALL_WATCHER_FIELDS)
    client = env(sb)
    d = _hb(client, command_done="abc")
    assert d["command"] is None and d["open_batch"]["id"] == BID
    assert sb.tables["stations"][0]["watcher_version"] == "2.4.0"


# ---------------------------------------------------------------- 4/5. batch detail extras
def _items():
    t0 = NOW - timedelta(minutes=5)
    return [
        {"id": "a", "batch_id": BID, "status": "success", "message": None, "retryable": True, "review_reasons": [],
         "created_at": t0.isoformat(), "updated_at": (t0 + timedelta(seconds=5)).isoformat()},
        {"id": "b", "batch_id": BID, "status": "error", "message": logic.PIPELINE_NOT_CONFIGURED_MSG, "retryable": True,
         "review_reasons": [], "created_at": t0.isoformat(), "updated_at": (t0 + timedelta(seconds=90)).isoformat()},
    ]


def _batch_env(env, monkeypatch, webhook="", simulate=False, items=None):
    sb = MemSB({"scan_batches": [_open_batch()], "stations": [_station(watcher_skipped_files=4)], "user_info": [],
                "subjects": [], "academic_terms": [], "failed_scan_queue": [],
                "scan_batch_items": _items() if items is None else items})
    client = env(sb)
    settings = SimpleNamespace(n8n_batch_webhook_url=webhook, simulate_scans=simulate, abandoned_batch_hours=3,
                               watcher_online_seconds=90, simulation_mode=bool(simulate and not webhook))
    monkeypatch.setattr(batches, "get_settings", lambda: settings)
    return client


@pytest.mark.parametrize("webhook,simulate,ready", [("", False, False), ("", True, True), ("https://n8n/x", False, True)])
def test_batch_detail_marking_ready(env, monkeypatch, webhook, simulate, ready):
    d = _batch_env(env, monkeypatch, webhook, simulate).get(f"/batches/{BID}").json()
    assert d["marking_ready"] is ready


def test_batch_detail_last_item_at_station_fields_and_error_codes(env, monkeypatch):
    d = _batch_env(env, monkeypatch).get(f"/batches/{BID}").json()
    assert logic.parse_ts(d["last_item_at"]) == logic.parse_ts(_items()[1]["updated_at"])
    assert d["station"]["watcher_skipped_files"] == 4 and d["station"]["watcher_command"] is None
    codes = {it["id"]: it["error_code"] for it in d["recent_items"]}
    assert codes == {"a": None, "b": "pipeline_not_configured"}
    assert all("status" in it for it in d["recent_items"])


def test_batch_detail_last_item_at_null_without_items(env, monkeypatch):
    d = _batch_env(env, monkeypatch, items=[]).get(f"/batches/{BID}").json()
    assert d["last_item_at"] is None


def test_batch_detail_flagged_items_carry_review_status(env, monkeypatch):
    t = (NOW - timedelta(minutes=1)).isoformat()
    flagged = lambda i: {"id": f"f{i}", "batch_id": BID, "status": "flagged", "task_id": f"web:{BID}:{i}",
                         "message": "Flagged for manual review (instructor_mismatch_not_found). Not in the database.",
                         "retryable": True, "review_reasons": ["instructor_mismatch_not_found"],
                         "created_at": t, "updated_at": t}
    client = _batch_env(env, monkeypatch, items=[flagged(1), flagged(2), flagged(3)])
    sb = batches.get_supabase()
    sb.tables["failed_scan_queue"] = [
        {"id": "q1", "batch_id": BID, "task_id": f"web:{BID}:1", "status": "validated"},
        {"id": "q2", "batch_id": BID, "task_id": f"web:{BID}:2", "status": "pending"},
    ]
    d = client.get(f"/batches/{BID}").json()
    status = {it["id"]: it.get("review_status") for it in d["attention_items"]}
    assert status == {"f1": "validated", "f2": "pending", "f3": None}
    assert all(it["error_code"] is None for it in d["attention_items"])  # flagged is never an error


def test_item_error_codes():
    f = logic.item_error_code
    assert f({"status": "success"}) is None
    assert f({"status": "processing"}) is None
    assert f({"status": "error", "message": logic.PIPELINE_NOT_CONFIGURED_MSG, "retryable": True}) == "pipeline_not_configured"
    assert f({"status": "error", "message": logic.PIPELINE_MISCONFIGURED_MSG, "retryable": True}) == "pipeline_misconfigured"
    assert f({"status": "error", "message": "Scoring pipeline returned HTTP 500.", "retryable": True}) == "pipeline_error"
    assert f({"status": "error", "message": "Failed after 5 attempts: x", "retryable": False}) == "failed_after_retries"
    assert f({"status": "error", "message": logic.PIPELINE_REJECTED_MSG, "retryable": False}) == "pipeline_rejected_request"
    assert f({"status": "error", "message": logic.UNSAVED_REVIEW_MSG + " (5 automatic tries: x)",
              "retryable": False}) == "not_saved_needs_review"
    assert set(logic.ITEM_ERROR_CODES) == {"pipeline_not_configured", "pipeline_misconfigured", "pipeline_error",
                                            "not_saved_needs_review", "failed_after_retries", "pipeline_rejected_request"}


# ---------------------------------------------------------------- 6. network errors -> 503
UNREACHABLE = {"code": "database_unreachable", "detail": "The server can't reach the database right now. It will try again."}


@pytest.mark.parametrize("exc", [httpx.ConnectError("dns"), httpx.ReadTimeout("t"), httpx.RemoteProtocolError("x")])
def test_transport_error_in_station_auth_is_503(exc):
    def broken():
        raise exc
    app.dependency_overrides[require_station] = broken
    try:
        r = TestClient(app, raise_server_exceptions=False).post("/scans/heartbeat", json={})
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 503
    assert r.json() == UNREACHABLE


def test_transport_error_in_route_is_503(env, monkeypatch):
    client = env(MemSB())

    class Down:
        def table(self, name):
            raise httpx.ConnectError("getaddrinfo failed")
    monkeypatch.setattr(stations, "get_supabase", lambda: Down())
    r = client.get("/stations")
    assert r.status_code == 503 and r.json() == UNREACHABLE


def test_gateway_api_error_is_503(env, monkeypatch):
    client = env(MemSB())

    class Gateway:
        def table(self, name):
            raise APIError({"message": "JSON could not be generated", "code": 522, "hint": None, "details": "b'<html>'"})
    monkeypatch.setattr(stations, "get_supabase", lambda: Gateway())
    r = client.get("/stations")
    assert r.status_code == 503 and r.json() == UNREACHABLE


def test_auth_network_error_is_503_not_session_expired(monkeypatch):
    class Auth:
        def get_user(self, token):
            raise httpx.ConnectError("dns")
    monkeypatch.setattr(security, "get_supabase", lambda: SimpleNamespace(auth=Auth()))
    r = TestClient(app, raise_server_exceptions=False).get("/stations", headers={"Authorization": "Bearer abc.def.ghi"})
    assert r.status_code == 503 and r.json() == UNREACHABLE


def test_real_auth_failure_is_still_401(monkeypatch):
    class Auth:
        def get_user(self, token):
            raise ValueError("invalid JWT")
    monkeypatch.setattr(security, "get_supabase", lambda: SimpleNamespace(auth=Auth()))
    r = TestClient(app, raise_server_exceptions=False).get("/stations", headers={"Authorization": "Bearer abc.def.ghi"})
    assert r.status_code == 401


# ---------------------------------------------------------------- website folder: full paths only
@pytest.mark.parametrize("folder", [r"%USERPROFILE%\Scans", r"~\Scans", "~/Scans", r"%OneDrive%\Scans", r"%FOO%\Scans"])
def test_set_folder_refuses_shortcuts(env, folder):
    from app.folder_safety import MESSAGES
    sb = MemSB({"stations": [_station(current_staff_id=USER.id)]})  # only the bound staff may change it
    client = env(sb)
    r = client.put(f"/stations/{SID}/folder", json={"folder_path": folder})
    assert r.status_code == 400 and r.json() == {"detail": MESSAGES["variables"]}
    assert sb.tables["stations"][0]["folder_path"] == "C:/Scans"
    assert not [u for u in sb.updates if u[0] == "stations"]


def test_set_folder_saves_full_path(env):
    sb = MemSB({"stations": [_station(current_staff_id=USER.id)]})  # only the bound staff may change it
    client = env(sb)
    r = client.put(f"/stations/{SID}/folder", json={"folder_path": r"C:\scan"})
    assert r.status_code == 200 and r.json()["folder_path"] == "C:/scan"
    assert sb.tables["stations"][0]["folder_path"] == "C:/scan"
