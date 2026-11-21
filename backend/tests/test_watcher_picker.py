"""Watcher 2.5.2 local Browse picker (folder_watcher.py): origin allowlist,
Private Network Access preflight, 127.0.0.1-only, one folder window at a time.
The folder window itself is mocked."""
import http.client
import json
import os
import socket
import threading

import pytest

from tests.test_watcher_client import fw, make  # noqa: F401  (make is a fixture)

SITE = "https://ievaluate.example.com"
PORT = 47615
OK_HEADERS = {"Origin": SITE, "Host": f"127.0.0.1:{PORT}"}


def _reply(method="GET", path="/ping", headers=OK_HEADERS, ip="127.0.0.1", pick=lambda: (200, {"path": "C:\\Scans"})):
    return fw.picker_reply(method, path, headers, ip, [SITE], PORT, pick)


def test_site_origins_are_exact_and_never_wildcard():
    assert fw.parse_site_origins([SITE + "/", "*", "http://localhost:5173", "https://a.com/path", 5, ""]) == [
        SITE, "http://localhost:5173"]
    assert fw.parse_site_origins("https://a.com") == []
    assert fw.parse_site_origins(None) == []


@pytest.mark.parametrize("headers,ip", [
    ({"Origin": "https://evil.example.com", "Host": f"127.0.0.1:{PORT}"}, "127.0.0.1"),  # other website
    ({"Host": f"127.0.0.1:{PORT}"}, "127.0.0.1"),                                          # no Origin
    ({"Origin": SITE, "Host": f"evil.example.com:{PORT}"}, "127.0.0.1"),                   # DNS rebinding
    (OK_HEADERS, "192.168.1.20"),                                                          # not this laptop
])
def test_refused_without_cors_headers_and_never_opens_the_window(headers, ip):
    opened = []
    for method, path in (("GET", "/ping"), ("POST", "/pick-folder"), ("OPTIONS", "/pick-folder")):
        status, hdrs, body = _reply(method, path, headers, ip, pick=lambda: opened.append(1))
        assert status == 403 and hdrs == {} and body == {"error": "forbidden"}
    assert opened == []


def test_preflight_allows_private_network_for_the_website_only():
    h = dict(OK_HEADERS, **{"Access-Control-Request-Method": "POST", "Access-Control-Request-Private-Network": "true"})
    status, hdrs, body = _reply("OPTIONS", "/pick-folder", h)
    assert status == 204 and body is None
    assert hdrs["Access-Control-Allow-Origin"] == SITE and hdrs["Access-Control-Allow-Private-Network"] == "true"
    assert "*" not in hdrs.values()
    _, hdrs, _ = _reply("OPTIONS", "/ping")
    assert "Access-Control-Allow-Private-Network" not in hdrs


def test_ping_pick_and_unknown_route():
    status, hdrs, body = _reply("GET", "/ping")
    assert status == 200 and body["version"] == fw.VERSION == "2.5.2" and hdrs["Access-Control-Allow-Origin"] == SITE
    assert _reply("POST", "/pick-folder")[::2] == (200, {"path": "C:\\Scans"})
    assert _reply("GET", "/pick-folder")[0] == 404  # opening the window needs POST
    assert _reply("GET", "/files")[0] == 404


def test_one_folder_window_at_a_time(make):
    make_w, _ = make
    cfg = make_w(None).cfg
    inside, release = threading.Event(), threading.Event()

    def slow_ask(initial):
        inside.set()
        release.wait(5)
        return ""

    picker = fw.FolderPicker(cfg, ask=slow_ask)
    first = []
    t = threading.Thread(target=lambda: first.append(picker()))
    t.start()
    assert inside.wait(5)
    assert picker() == (409, {"error": "busy"})
    release.set()
    t.join(5)
    assert first == [(200, {"path": None})]  # cancelled
    assert fw.FolderPicker(cfg, ask=lambda i: "")() == (200, {"path": None})  # lock released again


def test_no_folder_window_available(make):
    make_w, _ = make
    assert fw.FolderPicker(make_w(None).cfg, ask=lambda i: None)() == (503, {"error": "no_dialog"})


def test_picked_folder_is_added_to_allowed_roots(make, tmp_path):
    make_w, _ = make
    cfg = make_w(None).cfg
    other = tmp_path.parent / (tmp_path.name + "-picked")  # outside allowed_roots (= tmp_path)
    other.mkdir()
    assert not fw.in_allowed_roots(str(other), cfg.allowed_roots)
    status, body = fw.FolderPicker(cfg, ask=lambda i: str(other).replace("\\", "/"))()
    assert status == 200 and body == {"path": os.path.normpath(str(other))}
    assert fw.in_allowed_roots(str(other), cfg.allowed_roots)
    saved = json.loads((tmp_path / "watcher_config.json").read_text())
    assert fw.normalize_folder(str(other)) in saved["allowed_roots"] and saved["station_key"] == "k"


def test_unsafe_picked_folder_is_returned_but_not_allowed(make):
    make_w, _ = make
    cfg = make_w(None).cfg
    before = list(cfg.allowed_roots)
    drive_root = "C:/" if os.name == "nt" else "/"
    assert fw.FolderPicker(cfg, ask=lambda i: drive_root)()[0] == 200
    assert fw.FolderPicker(cfg, ask=lambda i: "//server/share/scans")()[0] == 200
    assert cfg.allowed_roots == before


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_server_binds_127_0_0_1_only_and_checks_origin(make):
    make_w, _ = make
    cfg = make_w(None).cfg
    assert fw.start_picker_server(cfg) is None  # no site_origins: Browse off
    cfg.site_origins = [SITE]
    port = _free_port()
    srv = fw.start_picker_server(cfg, port=port, pick=lambda: (200, {"path": "C:\\Scans"}))
    try:
        assert srv.server_address[0] == "127.0.0.1"
        assert fw.start_picker_server(cfg, port=port) is None  # port taken: no second server

        def call(method, path, origin):
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            c.request(method, path, headers={"Origin": origin})
            r = c.getresponse()
            return r.status, r.getheader("Access-Control-Allow-Origin"), r.read()

        status, acao, data = call("GET", "/ping", SITE)
        assert status == 200 and acao == SITE and json.loads(data)["ok"] is True
        status, acao, data = call("POST", "/pick-folder", SITE)
        assert status == 200 and json.loads(data) == {"path": "C:\\Scans"}
        status, acao, _ = call("POST", "/pick-folder", "https://evil.example.com")
        assert status == 403 and acao is None
    finally:
        srv.shutdown()
        srv.server_close()
