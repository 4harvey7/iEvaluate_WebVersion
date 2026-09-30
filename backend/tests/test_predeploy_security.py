"""Pre-deployment security fixes (Render + Vercel): client IP behind the proxy,
plaintext n8n refused, station takeover, network folders, ingest resource
limits. No network, no DB."""
import asyncio
import io
import json
import os
import zipfile
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from starlette.requests import Request

from app import body_limit, logic, security
from app.batch_access import can_manage_station, is_batch_owner
from app.db import ConfigError, Settings, is_safe_webhook_url, validate_settings
from app.folder_safety import MESSAGES, check_folder_for_server, is_network_path
from app.main import app
from app.routers import batches, reviews, scans, stations
from app.routers.watcher_download import ZIP_PATH
from app.security import CurrentUser, require_staff, require_station
from tests.test_watcher_client import FakeServer, Resp, fw, make  # noqa: F401  (make is a fixture)
from tests.test_watcher_contract import BID, SID, MemSB, _open_batch, _station


def _user(uid, role="SAO_STAFF"):
    return CurrentUser(id=uid, email=None, first_name=None, last_name=None, role=role, token="t")


# ================================================================ a. client IP
def _request(headers=(), peer="10.0.0.5"):
    raw = [(k.lower().encode(), v.encode()) for k, v in headers]
    return Request({"type": "http", "method": "POST", "path": "/auth/login", "headers": raw,
                    "client": (peer, 1234), "query_string": b""})


@pytest.fixture
def trust(monkeypatch):
    def _set(value: bool):
        monkeypatch.setattr(security, "get_settings", lambda: SimpleNamespace(trust_proxy_headers=value))
    return _set


def test_client_ip_ignores_headers_unless_trusted(trust):
    trust(False)
    req = _request([("CF-Connecting-IP", "1.2.3.4"), ("X-Forwarded-For", "5.6.7.8")])
    assert security.client_ip(req) == "10.0.0.5"  # a local run can't be spoofed


def test_client_ip_prefers_cf_connecting_ip(trust):
    trust(True)
    req = _request([("CF-Connecting-IP", "203.0.113.9"), ("X-Forwarded-For", "6.6.6.6, 198.51.100.1")])
    assert security.client_ip(req) == "203.0.113.9"
    req = _request([("CF-Connecting-IP", "2001:db8::1")])
    assert security.client_ip(req) == "2001:db8::1"


def test_client_ip_uses_rightmost_valid_forwarded_for(trust):
    trust(True)
    # Leftmost entries are whatever the client sent: never trusted.
    req = _request([("CF-Connecting-IP", "not-an-ip"), ("X-Forwarded-For", "1.1.1.1, 198.51.100.7, garbage")])
    assert security.client_ip(req) == "198.51.100.7"
    req = _request([("X-Forwarded-For", "1.1.1.1"), ("X-Forwarded-For", "198.51.100.8")])  # repeated header
    assert security.client_ip(req) == "198.51.100.8"
    req = _request([("X-Forwarded-For", "[2001:db8::2]:443")])
    assert security.client_ip(req) == "2001:db8::2"


def test_client_ip_falls_back_to_peer(trust):
    trust(True)
    assert security.client_ip(_request([("X-Forwarded-For", "nonsense, ")])) == "10.0.0.5"
    assert security.client_ip(_request()) == "10.0.0.5"


def test_spoofed_forwarded_for_does_not_escape_login_rate_limit(trust, monkeypatch):
    trust(True)
    buckets = []
    monkeypatch.setattr(security.login_limiter, "hit", lambda key, *a: buckets.append(key) or False)
    c = TestClient(app, raise_server_exceptions=False)
    for fake in ("1.1.1.1", "2.2.2.2"):
        r = c.post("/auth/login", json={"email": "a@b.c", "password": "x"},
                   headers={"X-Forwarded-For": f"{fake}, 198.51.100.50"})
        assert r.status_code == 429
    assert buckets == ["ip:198.51.100.50", "ip:198.51.100.50"]


def test_trust_proxy_headers_defaults_to_false(monkeypatch):
    monkeypatch.delenv("TRUST_PROXY_HEADERS", raising=False)
    monkeypatch.setenv("SUPABASE_URL", os.environ.get("SUPABASE_URL") or "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", os.environ.get("SUPABASE_SERVICE_KEY") or "k")
    assert Settings().trust_proxy_headers is False
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "true")
    assert Settings().trust_proxy_headers is True


# ================================================================ b. plaintext n8n
def _cfg(url, **extra):
    base = dict(n8n_batch_webhook_url=url, n8n_webhook_secret="s", n8n_correction_webhook_url="",
                n8n_import_error_webhook_url="")
    base.update(extra)
    return SimpleNamespace(**base)


@pytest.mark.parametrize("url", [
    "https://ievaluate-n8n.duckdns.org/webhook/sast-batch-scan",
    "http://localhost:5678/webhook/sast-batch-scan",
    "http://127.0.0.1:5678/webhook/x", "http://127.9.9.9/x", "http://[::1]:5678/x",
    "http://192.168.1.20:5678/x", "http://10.1.2.3/x", "http://172.16.0.9/x", "http://172.31.255.1/x",
])
def test_n8n_url_allowed(url):
    validate_settings(_cfg(url))


@pytest.mark.parametrize("url", [
    "http://ievaluate-n8n.duckdns.org/webhook/sast-batch-scan",
    "http://8.8.8.8/x", "http://172.32.0.1/x", "http://100.64.0.1/x", "http://192.168.1.20.nip.io/x",
    "ftp://10.0.0.1/x", "n8n.example/x",
])
def test_plaintext_or_odd_n8n_url_refused_without_echoing_it(url):
    with pytest.raises(ConfigError) as e:
        validate_settings(_cfg(url))
    assert "N8N_BATCH_WEBHOOK_URL" in str(e.value) and url not in str(e.value)


def test_plaintext_correction_webhook_refused():
    with pytest.raises(ConfigError) as e:
        validate_settings(_cfg("https://n8n.example/webhook/sast-batch-scan",
                               n8n_correction_webhook_url="http://n8n.example/webhook/sast-manual-correction"))
    assert "N8N_CORRECTION_WEBHOOK_URL" in str(e.value)
    assert not is_safe_webhook_url("http://n8n.example/x") and is_safe_webhook_url("https://n8n.example/x")


# ================================================================ c. station takeover
BOUND, OTHER, ADMIN = "bound-staff", "other-staff", "the-admin"


@pytest.fixture
def st_env(monkeypatch):
    audits = []

    def _env(sb, user):
        for mod in (stations, batches):
            monkeypatch.setattr(mod, "get_supabase", lambda: sb)
        monkeypatch.setattr(stations, "audit", lambda uid, action, meta=None: audits.append((uid, action, meta or {})))
        app.dependency_overrides[require_staff] = lambda: user
        return TestClient(app, raise_server_exceptions=False)

    _env.audits = audits
    yield _env
    app.dependency_overrides.clear()


def _bind(st_env, station, user, batches_=(), people=()):
    sb = MemSB({"stations": [station], "scan_batches": list(batches_), "user_info": list(people)})
    r = st_env(sb, user).post(f"/stations/{SID}/bind")
    return r, sb


def test_bind_free_station(st_env):
    r, sb = _bind(st_env, _station(), _user(OTHER))
    assert r.status_code == 200 and sb.tables["stations"][0]["current_staff_id"] == OTHER
    assert st_env.audits[-1][:2] == (OTHER, "station_bound")
    assert st_env.audits[-1][2]["previous_staff_id"] is None


def test_rebind_own_station_is_audited(st_env):
    r, sb = _bind(st_env, _station(current_staff_id=BOUND), _user(BOUND), [_open_batch(created_by=BOUND)])
    assert r.status_code == 200
    assert st_env.audits[-1][1] == "station_bound" and st_env.audits[-1][2]["rebind"] is True


def test_bind_over_someone_without_an_open_batch(st_env):
    # Shared laptop: the last person forgot to log out but has nothing open.
    r, sb = _bind(st_env, _station(current_staff_id=BOUND), _user(OTHER),
                  [_open_batch(created_by=BOUND, status="closed")])
    assert r.status_code == 200 and sb.tables["stations"][0]["current_staff_id"] == OTHER
    assert st_env.audits[-1][2]["previous_staff_id"] == BOUND


def test_bind_refused_while_bound_staff_has_an_open_batch(st_env):
    r, sb = _bind(st_env, _station(current_staff_id=BOUND), _user(OTHER), [_open_batch(created_by=BOUND)],
                  [{"id": BOUND, "first_name": "Sara", "last_name": "Staff"}])
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["code"] == "station_in_use" and "Sara Staff" in detail["message"]
    assert sb.tables["stations"][0]["current_staff_id"] == BOUND
    assert not [u for u in sb.updates if u[0] == "stations"]
    assert st_env.audits[-1][:2] == (OTHER, "station_bind_refused")


def test_admin_may_bind_over_an_open_batch(st_env):
    r, sb = _bind(st_env, _station(current_staff_id=BOUND), _user(ADMIN, "SAO_ADMIN"), [_open_batch(created_by=BOUND)])
    assert r.status_code == 200 and sb.tables["stations"][0]["current_staff_id"] == ADMIN
    assert st_env.audits[-1][2]["admin_override"] is True


def test_bind_is_conditional_on_nobody_else_binding_meanwhile(st_env):
    sb = MemSB({"stations": [_station()], "scan_batches": [], "user_info": []})
    client = st_env(sb, _user(OTHER))
    real_get = stations._get_station_row
    stations_row = {"n": 0}

    def racing_get(station_id):  # someone binds between our check and our update
        row = real_get(station_id)
        stations_row["n"] += 1
        if stations_row["n"] == 1:
            sb.tables["stations"][0]["current_staff_id"] = BOUND
        return row
    stations._get_station_row = racing_get
    try:
        r = client.post(f"/stations/{SID}/bind")
    finally:
        stations._get_station_row = real_get
    assert r.status_code == 409 and sb.tables["stations"][0]["current_staff_id"] == BOUND


@pytest.mark.parametrize("who,ok", [(_user(BOUND), True), (_user(ADMIN, "SAO_ADMIN"), True), (_user(OTHER), False)])
def test_set_folder_only_bound_staff_or_admin(st_env, who, ok):
    sb = MemSB({"stations": [_station(current_staff_id=BOUND)]})
    r = st_env(sb, who).put(f"/stations/{SID}/folder", json={"folder_path": r"D:\Scans\Office"})
    if ok:
        assert r.status_code == 200 and sb.tables["stations"][0]["folder_path"] == "D:/Scans/Office"
        assert st_env.audits[-1][1] == "station_folder_changed"
        assert st_env.audits[-1][2]["previous_folder_path"] == "C:/Scans"
    else:
        assert r.status_code == 403 and r.json()["detail"]["code"] == "not_your_station"
        assert sb.tables["stations"][0]["folder_path"] == "C:/Scans"
        assert st_env.audits[-1][1] == "station_change_refused"


def test_set_folder_refused_on_unbound_station(st_env):
    sb = MemSB({"stations": [_station()]})
    assert st_env(sb, _user(OTHER)).put(f"/stations/{SID}/folder", json={"folder_path": "C:/Scans2"}).status_code == 403


@pytest.mark.parametrize("who,ok", [(_user(BOUND), True), (_user(ADMIN, "SAO_ADMIN"), True), (_user(OTHER), False)])
def test_regenerate_key_only_bound_staff_or_admin(st_env, who, ok):
    sb = MemSB({"stations": [_station(current_staff_id=BOUND)], "scan_batches": [], "user_info": []})
    r = st_env(sb, who).post(f"/stations/{SID}/regenerate-key")
    if ok:
        assert r.status_code == 200 and r.json()["station_key"]
        assert sb.tables["stations"][0]["key_hash"] != "h"
    else:
        assert r.status_code == 403 and sb.tables["stations"][0]["key_hash"] == "h"


@pytest.mark.skipif(not os.path.isfile(ZIP_PATH), reason="watcher package not built")
@pytest.mark.parametrize("who,ok", [(_user(BOUND), True), (_user(ADMIN, "SAO_ADMIN"), True), (_user(OTHER), False)])
def test_installer_only_bound_staff_or_admin(st_env, who, ok):
    sb = MemSB({"stations": [_station(current_staff_id=BOUND)]})
    r = st_env(sb, who).post(f"/stations/{SID}/installer", json={"api_base": "https://api.example.com"})
    if ok:
        assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
        assert st_env.audits[-1][1] == "station_key_regenerated"
    else:
        assert r.status_code == 403 and sb.tables["stations"][0]["key_hash"] == "h"  # key not rotated


@pytest.mark.skipif(not os.path.isfile(ZIP_PATH), reason="watcher package not built")
def test_installer_never_writes_a_network_folder(st_env):
    sb = MemSB({"stations": [_station(current_staff_id=BOUND, folder_path="//fileserver/share/scans")]})
    r = st_env(sb, _user(BOUND)).post(f"/stations/{SID}/installer", json={"api_base": "https://api.example.com"})
    cfg = json.loads(zipfile.ZipFile(io.BytesIO(r.content)).read("ScanWatcher/watcher_config.json"))
    assert cfg["folder"] == "C:/Scans"


BATCH = {"id": BID, "station_id": SID, "status": "open", "created_by": BOUND}


def test_batch_access_is_creator_or_admin_not_station_binding():
    assert is_batch_owner(BATCH, _user(BOUND))
    assert is_batch_owner(BATCH, _user(ADMIN, "SAO_ADMIN"))
    assert not is_batch_owner(BATCH, _user(OTHER))
    assert not is_batch_owner({**BATCH, "created_by": None}, _user(OTHER))
    sb = MemSB({"stations": [_station(current_staff_id=OTHER)]})  # OTHER took over the laptop
    assert not reviews._has_batch_access(sb, BATCH, _user(OTHER))
    assert reviews._has_batch_access(sb, BATCH, _user(BOUND))  # creator keeps access after being replaced
    assert not batches.has_page_access(BATCH, _user(OTHER), {"current_staff_id": OTHER})
    assert batches.has_page_access(BATCH, _user(BOUND), {"current_staff_id": OTHER})
    assert not batches.can_close_normally(BATCH, _user(OTHER))
    assert batches.can_close_normally(BATCH, _user(BOUND))


def test_can_manage_station():
    assert can_manage_station({"current_staff_id": BOUND}, _user(BOUND))
    assert can_manage_station({"current_staff_id": None}, _user(ADMIN, "SAO_ADMIN"))
    assert not can_manage_station({"current_staff_id": BOUND}, _user(OTHER))
    assert not can_manage_station({"current_staff_id": None}, _user(OTHER))


# ================================================================ d. network folders
@pytest.mark.parametrize("folder", [
    "\\\\fileserver\\share\\scans", "//fileserver/share/scans", "\\\\fileserver\\share",
    "\\\\?\\UNC\\fileserver\\share\\scans", "\\\\.\\C:\\Scans", "\\\\localhost\\c$\\Scans",
])
def test_website_refuses_network_folders(folder):
    assert is_network_path(folder)
    normalized, problem = check_folder_for_server(folder)
    assert problem == MESSAGES["network"]


def test_local_folders_still_accepted():
    assert check_folder_for_server("C:\\Scans") == ("C:/Scans", None)
    assert check_folder_for_server("D:/Scans/Room 2") == ("D:/Scans/Room 2", None)
    assert not is_network_path("C:/Scans") and not is_network_path("/srv/scans")


def test_set_folder_endpoint_refuses_network_share(st_env):
    sb = MemSB({"stations": [_station(current_staff_id=BOUND)]})
    r = st_env(sb, _user(BOUND)).put(f"/stations/{SID}/folder", json={"folder_path": "\\\\srv\\share\\scans"})
    assert r.status_code == 400 and r.json() == {"detail": MESSAGES["network"]}
    assert sb.tables["stations"][0]["folder_path"] == "C:/Scans"


def test_heartbeat_never_sends_a_network_folder(monkeypatch):
    sb = MemSB({"stations": [_station(folder_path="//srv/share/scans")], "scan_batches": []})
    monkeypatch.setattr(scans, "get_supabase", lambda: sb)
    app.dependency_overrides[require_station] = lambda: {"id": SID, "label": "L", "folder_path": "//srv/share/scans"}
    try:
        r = TestClient(app, raise_server_exceptions=False).post("/scans/heartbeat", json={})
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 200 and r.json()["folder_path"] is None
    assert scans._heartbeat_folder("C:/Scans") == "C:/Scans"


def test_watcher_version():
    assert fw.VERSION == "2.4.3"


@pytest.mark.parametrize("remote", ["//srv/share/scans", "\\\\srv\\share\\scans", "\\\\?\\UNC\\srv\\share\\x"])
def test_watcher_refuses_network_folder_from_website(make, remote):
    make_w, scans_dir = make
    server = FakeServer()
    w = make_w(server)
    server.folder_path = remote
    w.heartbeat(force=True)
    assert w.cfg.folder == str(scans_dir)  # old folder kept
    assert w.folder_error == "unsafe_path"
    w.heartbeat()
    assert server.heartbeats[-1]["folder_error"] == "unsafe_path"


def test_watcher_local_config_may_still_use_a_network_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(fw, "SCRIPT_DIR", str(tmp_path))
    cfg_path = tmp_path / "watcher_config.json"
    cfg_path.write_text(json.dumps({"api_base": "https://server", "station_key": "k",
                                    "folder": "//fileserver/scans/office1"}))
    cfg = fw.load_config(str(cfg_path))
    assert fw.is_network_path(cfg.folder)
    assert fw.folder_problem(fw.normalize_folder(cfg.folder)) is None  # allowed locally (not a share root)
    assert fw.folder_problem("//fileserver/scans") == "unc_root"


# ================================================================ e. resource limits
async def _run(mw, headers, body_chunks, path="/scans/ingest"):
    sent, received = [], {"n": 0}
    chunks = list(body_chunks)

    async def receive():
        received["n"] += 1
        if not chunks:
            return {"type": "http.request", "body": b"", "more_body": False}
        c = chunks.pop(0)
        return {"type": "http.request", "body": c, "more_body": bool(chunks)}

    async def send(msg):
        sent.append(msg)

    scope = {"type": "http", "method": "POST", "path": path,
             "headers": [(k.lower().encode(), v.encode()) for k, v in headers]}
    await mw(scope, receive, send)
    return sent, received["n"]


def _status(sent):
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


async def _ok_app(scope, receive, send):
    await receive()
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"{}"})


def test_ingest_without_station_key_is_401_before_reading_the_body():
    mw = body_limit.BodySizeLimitMiddleware(_ok_app, ingest_concurrency=4)
    sent, reads = asyncio.run(_run(mw, [("content-type", "application/json")], [b"x" * 1024] * 50))
    assert _status(sent) == 401 and reads == 0
    assert json.loads(sent[1]["body"])["detail"]["code"] == "invalid_station_key"
    sent, reads = asyncio.run(_run(mw, [("x-station-key", "   ")], [b"{}"]))
    assert _status(sent) == 401 and reads == 0
    sent, _ = asyncio.run(_run(mw, [("x-station-key", "k")], [b"{}"]))
    assert _status(sent) == 200
    sent, _ = asyncio.run(_run(mw, [], [b"{}"], path="/auth/login"))  # other endpoints unaffected
    assert _status(sent) == 200


def test_ingest_without_station_key_via_app_is_401():
    r = TestClient(app, raise_server_exceptions=False).post("/scans/ingest", json={"x": 1})
    assert r.status_code == 401 and r.json()["detail"]["code"] == "invalid_station_key"


def test_ingest_concurrency_cap_returns_503_with_retry_after():
    async def scenario():
        gate = asyncio.Event()
        started = asyncio.Event()

        async def slow_app(scope, receive, send):
            await receive()
            started.set()
            await gate.wait()
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"{}"})

        mw = body_limit.BodySizeLimitMiddleware(slow_app, ingest_concurrency=1)
        first = asyncio.create_task(_run(mw, [("x-station-key", "k")], [b"{}"]))
        await started.wait()
        busy, reads = await _run(mw, [("x-station-key", "k")], [b"x" * 1000])
        assert _status(busy) == 503 and reads == 0
        hdrs = dict(busy[0]["headers"])
        assert hdrs[b"retry-after"] == str(body_limit.BUSY_RETRY_AFTER_SECONDS).encode()
        assert json.loads(busy[1]["body"])["detail"]["code"] == "server_busy"
        gate.set()
        done, _ = await first
        assert _status(done) == 200
        assert mw.ingest_slots.active == 0  # slot released
        again, _ = await _run(mw, [("x-station-key", "k")], [b"{}"])
        assert _status(again) == 200
    asyncio.run(scenario())


def test_ingest_slot_released_when_the_app_fails_or_body_too_big():
    async def boom(scope, receive, send):
        raise RuntimeError("x")

    mw = body_limit.BodySizeLimitMiddleware(boom, ingest_concurrency=1)
    with pytest.raises(RuntimeError):
        asyncio.run(_run(mw, [("x-station-key", "k")], [b"{}"]))
    assert mw.ingest_slots.active == 0
    mw = body_limit.BodySizeLimitMiddleware(_ok_app, ingest_concurrency=1)
    big = [b"x" * (1024 * 1024)] * 18
    sent, _ = asyncio.run(_run(mw, [("x-station-key", "k")], big))
    assert _status(sent) == 413 and mw.ingest_slots.active == 0


def test_ingest_concurrency_setting_default(monkeypatch):
    monkeypatch.delenv("INGEST_MAX_CONCURRENCY", raising=False)
    monkeypatch.setenv("SUPABASE_URL", os.environ.get("SUPABASE_URL") or "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", os.environ.get("SUPABASE_SERVICE_KEY") or "k")
    assert Settings().ingest_max_concurrency == 4


class _HResp(Resp):
    def __init__(self, status, body, headers=None):
        super().__init__(status, body)
        self.headers = headers or {}


def test_watcher_treats_server_busy_as_retry(make):
    make_w, _ = make
    server = FakeServer()
    w = make_w(server)
    busy = {"detail": {"code": "server_busy", "message": "busy"}}
    server.post = lambda url, json=None, timeout=None: _HResp(503, busy, {"Retry-After": "7"})
    assert w.send_page("p.jpg", b"\xff\xd8\xff", "0" * 64, "2026-09-30T00:00:00+00:00") == 7.0
    server.post = lambda url, json=None, timeout=None: _HResp(503, busy)
    assert w.send_page("p.jpg", b"\xff\xd8\xff", "0" * 64, "2026-09-30T00:00:00+00:00") == 10.0
    server.post = lambda url, json=None, timeout=None: _HResp(503, {"detail": "Service Unavailable"})
    assert w.send_page("p.jpg", b"\xff\xd8\xff", "0" * 64, "2026-09-30T00:00:00+00:00") is None  # backoff
    assert fw.retry_after_seconds(_HResp(503, {}, {"Retry-After": "9999"}), 10.0) == 120.0


def _jpeg_bytes(w, h, dpi=None):
    buf = io.BytesIO()
    Image.new("L", (w, h), 255).save(buf, format="JPEG", **({"dpi": (dpi, dpi)} if dpi else {}))
    return buf.getvalue()


def test_pixel_cap_keeps_real_long_bond_scans_working():
    # What the watcher really sends: a 600 dpi long-bond (8.5 x 14 in) scan shrunk on the laptop.
    big = Image.new("L", (5100, 8400), 255)
    sent = fw._normalize_page(big, 600.0)
    with Image.open(io.BytesIO(sent)) as im:
        assert max(im.size) <= fw.MAX_LONG_EDGE
    assert logic.prepare_page(sent).jpeg
    # An un-shrunk 300 dpi long-bond page (older / other clients) still fits.
    assert logic.prepare_page(_jpeg_bytes(2550, 4200, 300)).jpeg
    # Sample scans on disk: 2550x3300 @300 dpi and 2803x1700 @200 dpi.
    assert logic.prepare_page(_jpeg_bytes(2550, 3300, 300)).jpeg


def test_pixel_cap_rejects_raw_600_dpi_pages():
    with pytest.raises(logic.ImageValidationError) as e:
        logic.prepare_page(_jpeg_bytes(5100, 7800))  # 39.8 MP: fine at the old 40 MP cap, not any more
    assert e.value.status == 413 and "12 megapixels" in e.value.message
