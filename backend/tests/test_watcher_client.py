"""Watcher 2.4 (backend/watcher/folder_watcher.py) against a fake server:
capture time, pages scanned before the batch, heartbeat commands, no ingest
without a batch, marking-off backoff. No network."""
import importlib
import json
import os
import sys
import time
import types
from datetime import datetime, timezone

import pytest

WATCHER_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "watcher")


def _load_watcher():
    try:
        import requests  # noqa: F401  (the watcher's only non-Pillow dependency)
    except ImportError:
        # The backend venv doesn't need requests; a stub is enough because
        # every test replaces the HTTP session.
        stub = types.ModuleType("requests")

        class RequestException(Exception):
            pass

        class Session:
            def __init__(self):
                self.headers = {}

        stub.RequestException = RequestException
        stub.Session = Session
        stub.Response = object
        sys.modules["requests"] = stub
    if WATCHER_DIR not in sys.path:
        sys.path.insert(0, WATCHER_DIR)
    return importlib.import_module("folder_watcher")


fw = _load_watcher()


# ---------------------------------------------------------------- fake server
class Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


class FakeServer:
    """Answers /scans/heartbeat and /scans/ingest like the backend does."""

    def __init__(self, open_batch=True, batch_created=None, grace=30.0):
        self.open_batch = open_batch
        self.batch_created = batch_created or time.time() - 60
        self.grace = grace
        self.command = None
        self.command_id = None
        self.folder_path = None  # what the website tells the watcher to watch
        self.heartbeats, self.ingests = [], []
        self.ingest_reply = None  # (status, body) to force a reply

    def post(self, url, json=None, timeout=None):
        if url.endswith("/scans/heartbeat"):
            self.heartbeats.append(json)
            if json.get("command_done") and json["command_done"] == self.command_id:
                self.command = self.command_id = None
            ob = {"id": "b1", "created_at": "x"} if self.open_batch else None
            return Resp(200, {"ok": True, "station_label": "L1", "open_batch": ob, "folder_path": self.folder_path,
                              "command": self.command, "command_id": self.command_id})
        assert url.endswith("/scans/ingest")
        self.ingests.append(json)
        if self.ingest_reply:
            return Resp(*self.ingest_reply)
        if not self.open_batch:
            return Resp(409, {"detail": {"code": "no_open_batch", "message": "no batch"}})
        captured = datetime.fromisoformat(json["captured_at"]).timestamp()
        if captured < self.batch_created - self.grace:
            return Resp(409, {"detail": {"code": "file_predates_batch", "message": "old"}})
        return Resp(200, {"status": "success", "batch_id": "b1", "review_reasons": [], "simulated": True})


@pytest.fixture
def make(tmp_path, monkeypatch):
    monkeypatch.setattr(fw, "SCRIPT_DIR", str(tmp_path))  # watcher_state.db goes here, not into the repo
    scans = tmp_path / "Scans"
    scans.mkdir()
    cfg_path = tmp_path / "watcher_config.json"
    cfg_path.write_text(json.dumps({"api_base": "http://server", "station_key": "k", "folder": str(scans)}))

    def _make(server):
        w = fw.Watcher(fw.load_config(str(cfg_path)))
        w.session = server
        return w
    return _make, scans


def _jpeg(path, age_seconds=0):
    from PIL import Image
    shade = 60 + sum(map(ord, os.path.basename(str(path)))) % 180  # different pages -> different bytes
    Image.new("L", (40, 40), shade).save(path, format="JPEG")
    if age_seconds:
        old = time.time() - age_seconds
        os.utime(path, (old, old))
    return path


def _settle(w, rounds=4):
    for _ in range(rounds):
        w.poll()


# ---------------------------------------------------------------- capture time
def test_capture_time_takes_latest_known_time():
    assert fw.capture_time(100.0) == 100.0
    assert fw.capture_time(100.0, 250.0) == 250.0             # copy: new creation time
    assert fw.capture_time(100.0, 90.0, 300.0) == 300.0       # moved in while running
    assert fw.capture_time(100.0, None, None) == 100.0


def test_file_created_time(tmp_path):
    p = _jpeg(tmp_path / "a.jpg", age_seconds=3600)
    st = os.stat(p)
    created = fw.file_created_time(st)
    if os.name == "nt":
        assert created is not None and created > st.st_mtime  # just written, mtime pushed back an hour
    iso = fw.capture_time_iso(fw.Tracked(size=1, mtime=st.st_mtime, created=created))
    assert datetime.fromisoformat(iso).tzinfo is not None


def test_files_at_startup_have_no_first_seen_but_new_ones_do(make):
    make_w, scans = make
    _jpeg(scans / "old.jpg", age_seconds=7200)
    w = make_w(FakeServer(open_batch=False))
    w.poll()
    assert w.tracked[str(scans / "old.jpg")].first_seen is None
    before = time.time()
    _jpeg(scans / "copied.jpg", age_seconds=7200)  # old mtime, like a copied/moved file
    w.poll()
    t = w.tracked[str(scans / "copied.jpg")]
    assert t.first_seen is not None and t.first_seen >= before - 1
    assert datetime.fromisoformat(fw.capture_time_iso(t)).timestamp() >= before - 1


def test_moved_in_old_file_is_accepted_while_running(make):
    make_w, scans = make
    server = FakeServer(open_batch=True, batch_created=time.time() - 60)
    w = make_w(server)
    w.heartbeat(force=True)
    w.poll()  # initial scan (empty)
    _jpeg(scans / "late.jpg", age_seconds=7200)
    _settle(w)
    assert len(server.ingests) == 1
    assert os.listdir(scans / "processed")  # delivered


# ---------------------------------------------------------------- before-batch + commands
def test_predating_file_goes_to_before_batch_and_is_counted(make):
    make_w, scans = make
    _jpeg(scans / "yesterday.jpg", age_seconds=86400)
    # A just-written test file has a creation time of "now", so start the
    # batch in the future to make the file older than the batch.
    server = FakeServer(open_batch=True, batch_created=time.time() + 3600)
    w = make_w(server)
    w.heartbeat(force=True)
    _settle(w)
    bb = scans / "skipped" / "before-batch"
    assert os.listdir(bb) == ["yesterday.jpg"]
    assert w.skipped_count() == 1
    w.heartbeat(force=True)
    assert server.heartbeats[-1]["skipped_files"] == 1


def test_other_rejects_still_go_to_plain_skipped(make):
    make_w, scans = make
    server = FakeServer(open_batch=True)
    server.ingest_reply = (422, {"detail": {"code": "failed_after_retries", "message": "x"}})
    w = make_w(server)
    w.heartbeat(force=True)
    w.poll()
    _jpeg(scans / "bad.jpg")
    _settle(w)
    assert "bad.jpg" in os.listdir(scans / "skipped")
    assert w.skipped_count() == 0


def test_resend_command_round_trip(make):
    make_w, scans = make
    bb = scans / "skipped" / "before-batch"
    bb.mkdir(parents=True)
    _jpeg(bb / "p1.jpg", age_seconds=86400)
    _jpeg(bb / "p2.jpg", age_seconds=86400)
    # Batch started just after the pages were written: they predate it.
    server = FakeServer(open_batch=True, batch_created=time.time() + 0.5, grace=0.0)
    time.sleep(1.0)
    server.command, server.command_id = "resend_skipped", "cmd-1"
    w = make_w(server)
    w.poll()  # initial scan
    w.heartbeat(force=True)  # gets the command, carries it out
    assert sorted(os.listdir(bb)) == []
    assert {"p1.jpg", "p2.jpg"} <= set(os.listdir(scans))
    assert w.command_done == "cmd-1"
    w.heartbeat()  # sent right away (last_heartbeat was reset)
    assert server.heartbeats[-1]["command_done"] == "cmd-1"
    assert server.heartbeats[-1]["skipped_files"] == 0
    assert w.command_done is None and server.command is None
    _settle(w)
    assert len(server.ingests) == 2  # both pass the predate check now
    assert len(os.listdir(scans / "processed" / datetime.now().strftime("%Y-%m-%d"))) == 2


def test_discard_command_moves_never_deletes(make):
    make_w, scans = make
    bb = scans / "skipped" / "before-batch"
    bb.mkdir(parents=True)
    _jpeg(bb / "p1.jpg", age_seconds=86400)
    server = FakeServer(open_batch=False)
    server.command, server.command_id = "discard_skipped", "cmd-2"
    w = make_w(server)
    w.heartbeat(force=True)
    dest = scans / "skipped" / "discarded" / datetime.now().strftime("%Y-%m-%d")
    assert os.listdir(dest) == ["p1.jpg"]
    assert os.listdir(bb) == []
    w.heartbeat(force=True)
    assert server.heartbeats[-1]["command_done"] == "cmd-2"


def test_same_command_id_is_never_run_twice(make):
    make_w, scans = make
    bb = scans / "skipped" / "before-batch"
    bb.mkdir(parents=True)
    server = FakeServer(open_batch=False)
    server.command, server.command_id = "discard_skipped", "cmd-3"
    w = make_w(server)
    w.heartbeat(force=True)

    _jpeg(bb / "new.jpg", age_seconds=10)  # arrives after the command was carried out
    # Server "missed" the done report and hands out the same id again.
    w.command_done = None
    w.heartbeat(force=True)
    assert os.listdir(bb) == ["new.jpg"]  # not discarded by a replay


# ---------------------------------------------------------------- no batch: no ingest
def test_no_ingest_while_no_open_batch(make):
    make_w, scans = make
    server = FakeServer(open_batch=False)
    w = make_w(server)
    w.heartbeat(force=True)
    w.poll()
    _jpeg(scans / "p.jpg")
    _settle(w, 6)
    assert server.ingests == []
    assert (scans / "p.jpg").exists()
    server.open_batch = True
    w.heartbeat(force=True)
    _settle(w)
    assert len(server.ingests) == 1


# ---------------------------------------------------------------- marking switched off
def test_marking_off_backs_off_and_logs_once(make, caplog):
    make_w, scans = make
    server = FakeServer(open_batch=True)
    server.ingest_reply = (503, {"detail": {"code": "pipeline_not_configured", "message": "x"}})
    w = make_w(server)
    w.heartbeat(force=True)
    w.poll()
    _jpeg(scans / "p.jpg")
    with caplog.at_level("INFO", logger="watcher"):
        _settle(w)
        t = w.tracked[str(scans / "p.jpg")]
        first_wait = t.next_try - time.monotonic()
        assert 20 < first_wait <= fw.MARKING_OFF_BASE_SECONDS
        for _ in range(8):
            t.next_try = 0.0
            w.process(str(scans / "p.jpg"), t)
        assert t.next_try - time.monotonic() <= fw.MARKING_OFF_MAX_SECONDS
        assert t.next_try - time.monotonic() > fw.MARKING_OFF_MAX_SECONDS - 5
    msgs = [r.getMessage() for r in caplog.records if "marking is switched off" in r.getMessage()]
    assert len(msgs) == 1
    assert (scans / "p.jpg").exists()


def test_database_unreachable_code_is_read_from_top_level():
    code, msg = fw.Watcher._detail(Resp(503, {"code": "database_unreachable", "detail": "The server can't reach the database"}))
    assert code == "database_unreachable" and "database" in msg


def test_kept_for_review_counts_as_delivered(make):
    """Server kept a copy for its review page: the file goes to processed/, not skipped/."""
    make_w, scans = make
    server = FakeServer(open_batch=True)
    server.ingest_reply = (422, {"detail": {"code": "failed_after_retries", "message": "kept",
                                            "kept_for_review": True, "batch_id": "b1"}})
    w = make_w(server)
    w.heartbeat(force=True)
    w.poll()
    _jpeg(scans / "unsaved.jpg")
    _settle(w)
    assert len(server.ingests) == 1
    assert os.listdir(scans / "processed" / datetime.now().strftime("%Y-%m-%d")) == ["unsaved.jpg"]
    assert not (scans / "skipped").exists() or "unsaved.jpg" not in os.listdir(scans / "skipped")
    _settle(w)
    assert len(server.ingests) == 1  # never sent again


def test_version():
    assert fw.VERSION == "2.5.0"


# ---------------------------------------------------------------- online light (2.4.4)
def test_heartbeat_often_enough_for_the_online_light():
    # The website shows "Offline" 40 s after the last heartbeat (WATCHER_ONLINE_SECONDS).
    assert fw.HEARTBEAT_SECONDS * 2 + 10 <= 40


def test_keepalive_only_while_the_main_loop_is_busy(make):
    make_w, _ = make
    w = make_w(FakeServer())
    now = time.monotonic()
    w.last_heartbeat = now - 5
    assert not w.keepalive_due(now, 0.0)  # regular heartbeats are flowing
    w.last_heartbeat = now - 60           # stuck in a slow upload
    assert w.keepalive_due(now, 0.0)
    assert not w.keepalive_due(now, now - 3)  # one went out just now
    w.auth_failed_until = now + 30
    assert not w.keepalive_due(now, 0.0)  # key rejected: don't hammer the server


def test_send_offline(make, monkeypatch):
    make_w, _ = make
    cfg = make_w(FakeServer()).cfg
    calls = []

    def post(url, json=None, headers=None, timeout=None):
        calls.append((url, json, headers))
        return Resp(200, {"ok": True})
    monkeypatch.setattr(fw.requests, "post", post, raising=False)
    assert fw.send_offline(cfg) is True
    url, body, headers = calls[-1]
    assert url == "http://server/scans/offline" and body == {"version": fw.VERSION}
    assert headers["X-Station-Key"] == "k"

    monkeypatch.setattr(fw.requests, "post", lambda *a, **k: Resp(404, {}), raising=False)
    assert fw.send_offline(cfg) is False  # older server: nothing happens

    def down(*a, **k):
        raise fw.requests.RequestException("no network")
    monkeypatch.setattr(fw.requests, "post", down, raising=False)
    assert fw.send_offline(cfg) is False  # never raises


@pytest.mark.parametrize("shortcut", ["%USERPROFILE%/Scans", "~/Scans"])
def test_website_folder_with_shortcut_is_refused(make, shortcut):
    make_w, scans = make
    server = FakeServer()
    w = make_w(server)
    server.folder_path = shortcut
    w.heartbeat(force=True)
    assert w.cfg.folder == str(scans)
    assert w.folder_error == "unsafe_path"
    w.heartbeat()  # the refusal is reported right away, not a minute later
    assert server.heartbeats[-1]["folder_error"] == "unsafe_path"


def test_website_folder_that_is_missing_is_not_created_and_reported(make, tmp_path):
    make_w, scans = make
    server = FakeServer()
    w = make_w(server)
    w.heartbeat(force=True)
    typo = tmp_path / "scan"
    server.folder_path = str(typo).replace("\\", "/")
    w.heartbeat(force=True)
    assert not typo.exists()
    assert w.cfg.folder == os.path.normpath(str(typo))
    assert w.local_folder_error == "not_found"
    n = len(server.heartbeats)
    w.heartbeat()  # no force: sent right away
    assert len(server.heartbeats) == n + 1
    assert server.heartbeats[-1]["folder_exists"] is False
    assert server.heartbeats[-1]["folder_error"] == "not_found"
    typo.mkdir()
    w.poll()
    n = len(server.heartbeats)
    w.heartbeat()
    assert len(server.heartbeats) == n + 1
    assert server.heartbeats[-1]["folder_exists"] is True
    assert server.heartbeats[-1]["folder_error"] is None


def test_only_default_folder_is_created_and_only_at_startup(make, tmp_path, monkeypatch):
    make_w, scans = make
    default = tmp_path / "DefaultScans"
    monkeypatch.setattr(fw, "DEFAULT_FOLDER", fw.normalize_folder(str(default)))
    w = make_w(FakeServer())
    w.switch_folder(fw.normalize_folder(str(default)))
    assert not default.exists()
    w.check_folder(create=True)
    assert default.is_dir()
    other = tmp_path / "OtherScans"
    w.switch_folder(fw.normalize_folder(str(other)))
    w.check_folder(create=True)
    assert not other.exists()
    assert w.local_folder_error == "not_found"
