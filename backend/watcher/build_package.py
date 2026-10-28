"""Rebuild backend/app/static/watcher-package.zip from this folder.

    backend\\venv\\Scripts\\python watcher\\build_package.py     (from backend/)

The website's "Download installer" endpoint takes this zip, puts it under a
ScanWatcher/ top folder and adds the station's watcher_config.json.

Never add watcher_config.json (it holds a station secret), watcher_state.db or
logs to the package - FILES is a whitelist and a safety check enforces it.
.bat and .txt files are written with Windows (CRLF) line endings.
"""
import os
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "app", "static", "watcher-package.zip")

FILES = [
    # what the user sees first
    "INSTALL.bat",
    "HOW TO INSTALL.txt",
    "CHECK.bat",
    "UNINSTALL.bat",
    "TECHNICAL NOTES.txt",
    # the program
    "folder_watcher.py",
    "requirements.txt",
    "setup_helper.py",
    # what the Start / Stop shortcuts open (made by shortcuts.bat)
    "start_watcher.bat",
    "STOP.bat",
    # helpers used by the .bat files above
    "shortcuts.bat",
    "find_python.bat",
    "watcher_process.bat",
    "install_task.bat",
    "uninstall_task.bat",
]

FORBIDDEN = {"watcher_config.json", "watcher_state.db"}
CRLF_EXTENSIONS = (".bat", ".cmd", ".txt")


def _is_forbidden(name: str) -> bool:
    low = os.path.basename(name).lower()
    return low in FORBIDDEN or low.startswith("watcher.log") or low.endswith((".db", ".log"))


def _data(path: str) -> bytes:
    with open(path, "rb") as f:
        data = f.read()
    if path.lower().endswith(CRLF_EXTENSIONS):
        # cmd.exe misreads labels/goto in LF-only batch files; Notepad wants CRLF too.
        data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    return data


def main() -> None:
    missing = [n for n in FILES if not os.path.isfile(os.path.join(HERE, n))]
    if missing:
        raise SystemExit(f"missing files: {', '.join(missing)}")
    bad = [n for n in FILES if _is_forbidden(n)]
    if bad:
        raise SystemExit(f"refusing to package secrets/state: {', '.join(bad)}")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    tmp = OUT + ".tmp"
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for name in FILES:
            src = os.path.join(HERE, name)
            info = zipfile.ZipInfo(name, date_time=time.localtime(os.path.getmtime(src))[:6])
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, _data(src))
    os.replace(tmp, OUT)

    with zipfile.ZipFile(OUT) as z:
        names = z.namelist()
        assert not any(_is_forbidden(n) for n in names)
        print(f"wrote {OUT} ({len(names)} files)")
        for i in z.infolist():
            print(f"  {i.file_size:>8}  {i.filename}")


if __name__ == "__main__":
    main()
