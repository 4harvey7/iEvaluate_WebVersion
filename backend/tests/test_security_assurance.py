"""Security assurance review fixes: station ownership (M5, M6), webhook secret
(L2), early station-key check (L1), login lockout per (email, IP) (L3),
trusted client-IP header (L4), watcher hardening (M7, L6, L7). No network, no DB."""
import asyncio
import json
import os
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import body_limit, security
from app.db import ConfigError, validate_settings
from app.main import app
from app.routers import auth, batches, reviews
from tests.test_predeploy_security import ADMIN, BOUND, OTHER, _ok_app, _request, _run, _status, _user, st_env  # noqa: F401
from tests.test_watcher_client import FakeServer, fw, make  # noqa: F401
from tests.test_watcher_contract import SID, MemSB, _station

WHO = [(_user(BOUND), True), (_user(ADMIN, "SAO_ADMIN"), True), (_user(OTHER), False)]


# ================================================================ M5 skipped pages
@pytest.mark.parametrize("who,ok", WHO)
def test_skipped_pages_only_bound_staff_or_admin(st_env, who, ok):
    sb = MemSB({"stations": [_station(current_staff_id=BOUND)], "scan_batches": [], "user_info": []})
    r = st_env(sb, who).post(f"/stations/{SID}/skipped-pages", json={"action": "discard"})
    if ok:
        assert r.status_code == 200 and sb.tables["stations"][0]["watcher_command"] == "discard_skipped"
    else:
        assert r.status_code == 403 and r.json()["detail"]["code"] == "not_your_station"
        assert not [u for u in sb.updates if u[0] == "stations"]
        assert st_env.audits[-1][1] == "station_change_refused"


# ================================================================ M6 batch start
@pytest.mark.parametrize("who,ok", WHO)
def test_batch_start_only_on_own_station_or_admin(st_env, monkeypatch, who, ok):
    monkeypatch.setattr(batches, "get_instructor_if_eligible", lambda _id: {"id": _id})
    sb = MemSB({"stations": [_station(current_staff_id=BOUND)], "scan_batches": [], "subjects": []})
    body = {"station_id": SID, "instructor_id": "55555555-5555-5555-5555-555555555555",
            "subject_id": "66666666-6666-6666-6666-666666666666"}
    r = st_env(sb, who).post("/batches/start", json=body)
    if ok:  # past the ownership check; stops at the next one (no such subject)
        assert r.status_code == 400 and r.json()["detail"]["code"] == "invalid_subject"
    else:
        assert r.status_code == 403 and r.json()["detail"]["code"] == "not_your_station"
        assert st_env.audits[-1][1] == "station_change_refused"
    assert sb.tables["scan_batches"] == []


# ================================================================ L2 webhook secret
def _hooks(secret, **urls):
    base = dict(n8n_batch_webhook_url="", n8n_correction_webhook_url="", n8n_import_error_webhook_url="")
    base.update(urls)
    return SimpleNamespace(n8n_webhook_secret=secret, **base)


@pytest.mark.parametrize("url_attr", ["n8n_correction_webhook_url", "n8n_import_error_webhook_url"])
def test_any_webhook_url_requires_a_long_secret(url_attr):
    url = {url_attr: "https://n8n.example/webhook/x"}
    for bad in ("", "s" * 31):
        with pytest.raises(ConfigError) as e:
            validate_settings(_hooks(bad, **url))
        assert "N8N_WEBHOOK_SECRET" in str(e.value) and "n8n.example" not in str(e.value)
    validate_settings(_hooks("s" * 32, **url))
    validate_settings(_hooks(""))  # no URL: no secret needed


def test_correction_never_sent_without_secret(monkeypatch):
    monkeypatch.setattr(reviews, "get_settings", lambda: SimpleNamespace(n8n_webhook_secret=""))
    monkeypatch.setattr(reviews, "_n8n", lambda: pytest.fail("must not call n8n without the secret"))
    result, message, _ = reviews._send_correction("https://n8n.example/x", {"task_id": "t"})
    assert result == "failed" and "N8N_WEBHOOK_SECRET" in message


# ================================================================ L1 early station-key check
def test_unknown_station_key_is_401_before_slot_or_body(monkeypatch):
    seen = []
    monkeypatch.setattr(security, "station_key_known", lambda key: seen.append(key) or False)
    mw = body_limit.BodySizeLimitMiddleware(_ok_app, ingest_concurrency=1)
    sent, reads = asyncio.run(_run(mw, [("x-station-key", "fake-key")], [b"x" * 1024] * 50))
    assert _status(sent) == 401 and reads == 0 and mw.ingest_slots.active == 0
    assert seen == ["fake-key"]
    monkeypatch.setattr(security, "station_key_known", lambda key: True)
    sent, _ = asyncio.run(_run(mw, [("x-station-key", "real-key")], [b"{}"]))
    assert _status(sent) == 200


def test_station_key_lookup_failure_falls_through_to_route(monkeypatch):
    def down(key):
        raise RuntimeError("database unreachable")
    monkeypatch.setattr(security, "station_key_known", down)
    mw = body_limit.BodySizeLimitMiddleware(_ok_app, ingest_concurrency=1)
    sent, _ = asyncio.run(_run(mw, [("x-station-key", "k")], [b"{}"]))
    assert _status(sent) == 200  # require_station answers (503) in the real app


def test_station_by_key_matches_hash_only(monkeypatch):
    from app.logic import hash_station_key
    sb = MemSB({"stations": [{"id": SID, "label": "L", "key_hash": hash_station_key("good"),
                              "folder_path": None, "current_staff_id": None}]})
    monkeypatch.setattr(security, "get_supabase", lambda: sb)
    assert security.station_by_key("good")["id"] == SID and "key_hash" not in security.station_by_key("good")
    assert security.station_by_key("bad") is None and security.station_by_key("") is None
    assert security.station_by_key("x" * 201) is None


# ================================================================ L3 login lockout
def test_login_lockout_is_per_email_and_ip(monkeypatch):
    monkeypatch.setattr(auth, "login_limiter", security.RateLimiter())
    monkeypatch.setattr(security, "get_settings", lambda: SimpleNamespace(trust_proxy_headers=True,
                                                                           trusted_ip_header="cf-connecting-ip"))

    def bad_password(*_a):
        raise RuntimeError("Invalid login credentials")
    monkeypatch.setattr(auth, "new_auth_client", lambda: SimpleNamespace(
        auth=SimpleNamespace(sign_in_with_password=bad_password)))
    c = TestClient(app, raise_server_exceptions=False)

    def login(ip):
        return c.post("/auth/login", json={"email": "Victim@school.edu", "password": "x"},
                      headers={"CF-Connecting-IP": ip}).status_code
    assert [login("203.0.113.1") for _ in range(8)] == [401] * 8
    assert login("203.0.113.1") == 429           # the attacker's IP is cooled down...
    assert login("198.51.100.2") == 401          # ...the real user elsewhere is not locked out


def test_login_cooldown_expires(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(security, "time", SimpleNamespace(monotonic=lambda: now[0]))
    limiter = security.RateLimiter()
    assert all(limiter.hit("email:v|ip:a", 8, 300) for _ in range(8))
    assert not limiter.hit("email:v|ip:a", 8, 300)  # cooled down...
    now[0] += 301
    assert limiter.hit("email:v|ip:a", 8, 300)      # ...not locked for good


# ================================================================ L4 trusted IP header
def _ip_settings(monkeypatch, header):
    monkeypatch.setattr(security, "get_settings",
                        lambda: SimpleNamespace(trust_proxy_headers=True, trusted_ip_header=header))


def test_trusted_ip_header_is_configurable(monkeypatch):
    headers = [("CF-Connecting-IP", "6.6.6.6"), ("X-Real-IP", "203.0.113.4"), ("X-Forwarded-For", "1.1.1.1, 198.51.100.9")]
    _ip_settings(monkeypatch, "none")  # not behind Cloudflare: a client-sent CF-Connecting-IP is ignored
    assert security.client_ip(_request(headers)) == "198.51.100.9"
    _ip_settings(monkeypatch, "x-real-ip")
    assert security.client_ip(_request(headers)) == "203.0.113.4"
    _ip_settings(monkeypatch, "cf-connecting-ip")  # default (Render behind Cloudflare)
    assert security.client_ip(_request(headers)) == "6.6.6.6"


# ================================================================ watcher (M7, L6, L7)
def _write_cfg(tmp_path, **cfg):
    p = tmp_path / "watcher_config.json"
    p.write_text(json.dumps({"station_key": "k", "folder": str(tmp_path / "Scans"), **cfg}))
    return str(p)


@pytest.mark.parametrize("api,ok", [
    ("https://api.example.com", True), ("http://localhost:8000", True), ("http://127.0.0.1:8000", True),
    ("http://api.example.com", False), ("http://192.168.1.5:8000", False), ("ftp://localhost", False),
    ("https://", False),
])
def test_watcher_api_base_must_be_https(tmp_path, api, ok):
    path = _write_cfg(tmp_path, api_base=api)
    if ok:
        assert fw.load_config(path).api_base == api
    else:
        with pytest.raises(fw.ConfigError):
            fw.load_config(path)


def test_watcher_default_allowed_roots_keep_existing_folder(tmp_path):
    cfg = fw.load_config(_write_cfg(tmp_path, api_base="https://x"))
    assert cfg.allowed_roots == [os.path.normpath(fw.DEFAULT_FOLDER), os.path.normpath(str(tmp_path / "Scans"))]


def test_watcher_refuses_website_folder_outside_allowed_roots(make, tmp_path):
    make_w, scans = make  # allowed_roots = [tmp_path]
    outside = tmp_path.parent / (tmp_path.name + "-elsewhere")
    outside.mkdir()
    inside = tmp_path / "Room2"
    inside.mkdir()
    server = FakeServer()
    w = make_w(server)
    server.folder_path = str(outside).replace("\\", "/")
    w.heartbeat(force=True)
    assert w.cfg.folder == str(scans) and w.folder_error == "unsafe_path"
    server.folder_path = str(inside).replace("\\", "/")
    w.heartbeat(force=True)
    assert w.cfg.folder == os.path.normpath(str(inside)) and w.folder_error is None


def test_in_allowed_roots():
    assert fw.in_allowed_roots("C:/Scans/Room 2", ["C:/Scans"])
    assert fw.in_allowed_roots("C:/Scans", ["C:/Scans"])
    assert not fw.in_allowed_roots("C:/ScansX", ["C:/Scans"])
    assert not fw.in_allowed_roots("C:/Scans/../Users", ["C:/Scans"])
    assert not fw.in_allowed_roots("D:/Scans", ["C:/Scans"])  # other drive
    assert not fw.in_allowed_roots("C:/Scans", [])


def test_watcher_skips_symlinked_files(make, tmp_path):
    make_w, scans = make
    target = tmp_path / "secret.jpg"
    target.write_bytes(b"\xff\xd8\xff")
    (scans / "real.jpg").write_bytes(b"\xff\xd8\xff")
    try:
        os.symlink(target, scans / "link.jpg")
    except (OSError, NotImplementedError):
        pytest.skip("creating symlinks needs Developer Mode / admin on Windows")
    w = make_w(FakeServer())
    assert [os.path.basename(p) for p in fw.iter_candidates(w.cfg)] == ["real.jpg"]
    assert [os.path.basename(p) for p in fw.list_page_files(str(scans))] == ["real.jpg"]
