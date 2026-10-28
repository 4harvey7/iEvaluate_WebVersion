"""Watcher 2.5.0: started by hand (Start / Stop shortcuts, no auto-start at
log-on) and only one copy at a time (watcher.lock, exit code 7)."""
import argparse
import os
import sys

import pytest

from tests.test_watcher_client import WATCHER_DIR, fw

if WATCHER_DIR not in sys.path:
    sys.path.insert(0, WATCHER_DIR)
import setup_helper as sh  # noqa: E402


def _read(name: str) -> str:
    with open(os.path.join(WATCHER_DIR, name), "r", encoding="ascii") as f:
        return f.read()


# ---------------------------------------------------------------- CHECK report
@pytest.fixture
def report(tmp_path, monkeypatch, capsys):
    folder = tmp_path / "scans"
    folder.mkdir()
    monkeypatch.setattr(sh, "read_config", lambda _p: ("https://api.example.com", "k", str(folder), None))

    def run(running: str, autostart: str):
        code = sh.cmd_report(argparse.Namespace(dir=str(tmp_path), running=running, autostart=autostart))
        return code, capsys.readouterr().out

    return run


def test_report_running_without_autostart_is_all_good(report):
    code, out = report("1", "0")
    assert code == 0
    assert "[OK]  The Scan Watcher is running." in out
    assert "[!!]" not in out and "log-on" not in out and "start by itself" not in out.lower()


def test_report_turned_off_is_info_not_a_failure(report):
    code, out = report("0", "0")
    assert code == 2  # CHECK.bat: "Nothing is broken, just turned off"
    assert "The Scan Watcher is turned off. Double-click Start Scan Watcher." in out
    assert "[!!]" not in out and "restart the laptop" not in out.lower()


def test_report_autostart_is_only_a_note(report):
    code, out = report("1", "1")
    assert code == 0
    assert "It also starts by itself at log-on; turn this off with uninstall_task.bat" in out
    assert "[!!]" not in out


def test_report_real_problem_still_fails_when_turned_off(tmp_path, capsys):
    # no watcher_config.json in the folder
    code = sh.cmd_report(argparse.Namespace(dir=str(tmp_path), running="0", autostart="0"))
    assert code == 1 and "settings file is missing" in capsys.readouterr().out


# ---------------------------------------------------------------- single instance
def test_lock_is_exclusive_and_released_on_close(tmp_path):
    path = str(tmp_path / fw.LOCK_FILE_NAME)
    first = fw.acquire_instance_lock(path)
    assert first is not None
    try:
        assert fw.acquire_instance_lock(path) is None  # a second watcher
    finally:
        first.close()
    again = fw.acquire_instance_lock(path)
    assert again is not None
    again.close()


def test_second_copy_exits_with_code_7(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(fw, "SCRIPT_DIR", str(tmp_path))
    monkeypatch.setattr(fw, "_instance_lock", None)
    logged = []
    monkeypatch.setattr(fw, "setup_logging", lambda: logged.append(1))
    held = fw.acquire_instance_lock(os.path.join(str(tmp_path), fw.LOCK_FILE_NAME))
    assert held is not None
    try:
        monkeypatch.setattr(sys, "argv", ["folder_watcher.py"])
        with pytest.raises(SystemExit) as e:
            fw.main()
        assert e.value.code == fw.EXIT_ALREADY_RUNNING == 7
        assert "Scan Watcher is already running" in capsys.readouterr().out
        assert not logged  # never touches watcher.log
    finally:
        held.close()


def test_offline_mode_ignores_the_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(fw, "SCRIPT_DIR", str(tmp_path))
    monkeypatch.setattr(fw, "_instance_lock", None)
    monkeypatch.setattr(fw, "setup_logging", lambda: None)
    monkeypatch.setattr(fw, "load_config", lambda _p: object())
    sent = []
    monkeypatch.setattr(fw, "send_offline", lambda cfg: sent.append(cfg) or True)
    held = fw.acquire_instance_lock(os.path.join(str(tmp_path), fw.LOCK_FILE_NAME))
    try:
        monkeypatch.setattr(sys, "argv", ["folder_watcher.py", "--offline"])
        with pytest.raises(SystemExit) as e:
            fw.main()
        assert e.value.code == 0 and len(sent) == 1  # STOP.bat works while the watcher still runs
        assert fw._instance_lock is None
    finally:
        held.close()


# ---------------------------------------------------------------- scripts
def test_installer_makes_shortcuts_not_autostart():
    install = _read("INSTALL.bat")
    assert r"\install_task.bat" not in install  # no auto-start any more
    assert 'call "%DEST%\\uninstall_task.bat" /quiet' in install
    assert 'call "%DEST%\\shortcuts.bat" create' in install
    for name in ("STOP.bat", "shortcuts.bat"):
        assert install.count(name) >= 2  # required-files list and copy list
    uninstall = _read("UNINSTALL.bat")
    assert "shortcuts.bat" in uninstall and 'call "%SW_SHORTCUTS%" remove /quiet' in uninstall
    assert "STOP.bat" in uninstall and 'call "%SW_STOPPER%" /quiet' in uninstall
    assert ":tell_website" not in uninstall  # lives in STOP.bat only
    assert "--offline" in _read("STOP.bat") and "watcher_process.bat\" stop" in _read("STOP.bat")


def test_start_watcher_stops_retrying_when_already_running():
    bat = _read("start_watcher.bat")
    assert 'if "%SW_CODE%"=="7" goto already_running' in bat


def test_package_ships_the_new_scripts():
    import build_package
    assert {"STOP.bat", "shortcuts.bat"} <= set(build_package.FILES)
    assert all(os.path.isfile(os.path.join(WATCHER_DIR, n)) for n in build_package.FILES)
