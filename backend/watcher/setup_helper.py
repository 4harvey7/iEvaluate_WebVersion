"""Small helper for INSTALL.bat and CHECK.bat. Not needed to run the watcher.

    python setup_helper.py config --config watcher_config.json [--out folder.txt]
        Checks the settings file, creates the scan folder, writes the folder
        path to --out (console code page, for the .bat to show).
        Exit: 0 OK | 2 file missing | 3 file broken (download again)
              4 folder could not be created | 5 folder not allowed
    python setup_helper.py online --config watcher_config.json
        Exit 0 if the server in the settings file answers at all, 1 if not.
    python setup_helper.py report --dir <install folder> --running 0|1 --autostart 0|1
        Prints an "is it working?" report in plain words (CHECK.bat).
        Exit: 0 all fine, running | 2 all fine, but turned off (started by hand
              since 2.5.0 - not a problem) | 1 something needs fixing.
        --autostart 1 (IT turned on install_task.bat) is only a note.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_FOLDER = r"C:\Scans"


class BadConfig(Exception):
    pass


# --------------------------------------------------------------------------- config
def _simple_read(path: str) -> tuple[str, str, str]:
    """Fallback parser (used only if folder_watcher.load_config can't be used)."""
    with open(path, "r", encoding="utf-8-sig") as f:
        text = f.read()
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        try:
            raw = json.loads(text.replace("\\", "/"))  # tolerate "C:\Scans"
        except json.JSONDecodeError as e:
            raise BadConfig(f"not valid JSON: {e}")
    if not isinstance(raw, dict):
        raise BadConfig("not a JSON object")
    api = str(raw.get("api_base") or "").strip().rstrip("/")
    key = str(raw.get("station_key") or "").strip()
    if not api or not key:
        raise BadConfig("api_base or station_key is missing")
    folder = str(raw.get("folder") or "").strip().strip('"') or DEFAULT_FOLDER
    folder = os.path.expandvars(os.path.expanduser(folder)).replace("/", os.sep)
    return api, key, os.path.abspath(folder)


def read_config(path: str) -> tuple[str, str, str, str | None]:
    """Returns (api_base, station_key, folder, folder_problem_or_None).
    Uses the watcher's own loader so the installer and the watcher agree."""
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    fw = None
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(path)))
        sys.path.insert(1, HERE)
        import folder_watcher as fw  # noqa: F401
    except Exception:
        fw = None
    if fw is not None and hasattr(fw, "load_config"):
        try:
            cfg = fw.load_config(path)
        except SystemExit as e:
            raise BadConfig(str(e))
        except Exception as e:
            if type(e).__name__ == "ConfigError":
                raise BadConfig(str(e))
            cfg = None
        if cfg is not None:
            problem = None
            checker = getattr(fw, "local_folder_problem", None)
            if checker:
                try:
                    problem = checker(cfg.folder)
                except Exception:
                    problem = None
            return cfg.api_base, cfg.station_key, os.path.abspath(cfg.folder), problem
    api, key, folder = _simple_read(path)
    return api, key, folder, None


def _write_console_text(path: str, text: str) -> None:
    enc = "oem" if os.name == "nt" else "utf-8"
    with open(path, "w", encoding=enc, errors="replace") as f:
        f.write(text + "\n")


def cmd_config(args) -> int:
    try:
        _api, _key, folder, problem = read_config(args.config)
    except FileNotFoundError:
        return 2
    except BadConfig as e:
        print(f"settings file problem: {e}", file=sys.stderr)
        return 3
    if args.out:
        _write_console_text(args.out, folder)
    if problem:
        return 5
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError:
        return 4
    return 0 if os.path.isdir(folder) else 4


def cmd_online(args) -> int:
    try:
        api, _key, _folder, _p = read_config(args.config)
        import requests
        requests.get(api + "/", timeout=15)
    except Exception:
        return 1
    return 0  # any HTTP answer (even 404) means the server is reachable


# --------------------------------------------------------------------------- report
HINTS = [
    # (text found in the log, plain-words meaning) - first match wins, newest line first
    ("Told the website this laptop is offline", "The Scan Watcher was stopped. Double-click Start Scan Watcher "
                                                "before scanning."),
    ("Station key rejected", "The website does not accept this laptop's key any more (a new installer was "
                             "downloaded for this station). Download the installer again and run INSTALL."),
    ("Config problem", "The settings file is broken. Download the installer from the website and run INSTALL."),
    ("too big to watch", "The scan folder is not allowed (it is too big, like a whole drive or the Desktop). "
                         "On the website, set a folder just for scans, like C:\\Scans. The laptop waits until then."),
    ("No permission to read the scan folder", "The laptop is not allowed to open the scan folder. "
                                              "On the website, choose another folder, like C:\\Scans."),
    ("Scan folder not found", "The scan folder does not exist. If it is on a USB stick or another drive, "
                              "plug it in - or set the folder to C:\\Scans on the website."),
    ("heartbeat failed", "The laptop cannot reach the website. Check the internet connection. "
                         "It will keep trying by itself."),
    ("network error", "The laptop cannot reach the website. Check the internet connection. "
                      "It will keep trying by itself."),
    ("can't reach the database", "The website has a connection problem right now. Nothing to do on this laptop: "
                                 "it keeps trying by itself."),
    ("marking is switched off", "The website can't mark pages right now. The pages wait on this laptop "
                                "and are sent again by themselves."),
    ("before the current batch started", "Some pages were scanned before the batch was started. They wait in "
                                         "skipped\\before-batch. On the website, open the batch and add them to it "
                                         "(or scan them again)."),
    ("Website asked to", "Everything is fine. The laptop did what was asked on the website."),
    ("No open batch", "Everything is fine. Scans wait on the laptop until a batch is started on the website."),
    ("-> processed", "Everything is fine. Scans are being sent to the website."),
    ("Open batch detected", "Everything is fine. A batch is open and scans will be sent."),
]


def _tail(path: str, n: int) -> list[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return []
    return [ln.rstrip("\r\n") for ln in lines[-n:] if ln.strip()]


def _plain(line: str) -> str:
    # "2026-09-27 10:15:03,123 INFO message"  ->  "27 Sep 10:15  message"
    parts = line.split(" ", 3)
    if len(parts) == 4 and len(parts[0]) == 10 and parts[0][4] == "-":
        date, time_, level, msg = parts
        mark = "  (problem)" if level in ("WARNING", "ERROR", "CRITICAL") else ""
        return f"{date} {time_[:5]}  {msg}{mark}"
    return line


TURNED_OFF = "The Scan Watcher is turned off. Double-click Start Scan Watcher."
AUTOSTART_NOTE = "It also starts by itself at log-on; turn this off with uninstall_task.bat"


def cmd_report(args) -> int:
    """Exit 0 = all fine and running, 2 = all fine but turned off, 1 = something to fix."""
    app_dir = os.path.abspath(args.dir)
    cfg_path = os.path.join(app_dir, "watcher_config.json")
    ok = True
    running = args.running == "1"
    print()
    # Since 2.5.0 the watcher is started by hand ("Start Scan Watcher"), so
    # "turned off" is normal, not a problem - and so is "no auto-start".
    if running:
        print("  [OK]  The Scan Watcher is running.")
    else:
        print(f"  [i]   {TURNED_OFF}")
        print("        (The shortcut is on the desktop. Start it before scanning.)")
    if args.autostart == "1":
        print(f"  [i]   {AUTOSTART_NOTE}")

    folder = None
    try:
        _api, _key, folder, problem = read_config(cfg_path)
    except FileNotFoundError:
        ok = False
        print("  [!!]  The settings file is missing. Download the installer from the website and run INSTALL.")
        problem = None
    except BadConfig:
        ok = False
        print("  [!!]  The settings file is broken. Download the installer from the website and run INSTALL.")
        problem = None
    if folder:
        if problem:
            ok = False
            print(f"  [!!]  The scan folder {folder} is not allowed. Set a folder just for scans on the website.")
        elif os.path.isdir(folder):
            print(f"  [OK]  The scan folder exists: {folder}")
        else:
            ok = False
            print(f"  [!!]  The scan folder is missing: {folder}")
            print("        It is made again by itself within a minute (if its drive is there).")
        print("        (Your scanner must save into this folder.)")

    log_path = os.path.join(app_dir, "watcher.log")
    lines = _tail(log_path, 200)
    print()
    if not lines:
        print("  No messages from the Scan Watcher yet.")
    else:
        # Only look at messages since the last start.
        start = 0
        for i, ln in enumerate(lines):
            if "iEvaluate watcher " in ln:
                start = i
        recent = lines[start:]
        meaning = None
        for ln in reversed(recent[-30:]):
            for needle, text in HINTS:
                if needle in ln:
                    meaning = text
                    break
            if meaning:
                break
        print("  Last messages from the Scan Watcher:")
        for ln in recent[-8:]:
            print("    " + _plain(ln)[:150])
        if meaning:
            print()
            print("  What this means:")
            print("    " + meaning)
    print()
    print(f"  (Full log: {log_path})")
    if not ok:
        return 1
    return 0 if running else 2


def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("config")
    c.add_argument("--config", required=True)
    c.add_argument("--out")
    o = sub.add_parser("online")
    o.add_argument("--config", required=True)
    r = sub.add_parser("report")
    r.add_argument("--dir", required=True)
    r.add_argument("--running", default="0")
    r.add_argument("--autostart", default="0")
    a = p.parse_args()
    return {"config": cmd_config, "online": cmd_online, "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
