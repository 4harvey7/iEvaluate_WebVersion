"""
iEvaluate scan folder watcher - runs on each scanning laptop.

Watches the scanner's output folder (recursively), converts every finished
scan page to a grayscale JPEG and sends it to the backend's /scans/ingest
endpoint, authenticated with this station's key.

Configuration: watcher_config.json next to this script (download it,
prefilled, from the website's "Scanner setup" page):

    {
      "api_base":    "https://api.example.com",
      "station_key": "<secret shown once on the website>",
      "folder":      "C:/Scans"
    }

"folder" is optional (default C:/Scans). The folder set on the website for
this station is picked up automatically within ~15 s (no restart needed) and
saved back into watcher_config.json.

Optional keys: "processed_dir", "skipped_dir", "poll_seconds".

Pages scanned before the batch was started are set aside in
skipped/before-batch/. The website can then ask (through the heartbeat) to add
them to the open batch or to set them aside for good (skipped/discarded/<date>/,
never deleted).

Started by hand (2.5.0+): the "Start Scan Watcher" shortcut (desktop / Start
menu) runs start_watcher.bat; "Stop Scan Watcher" (STOP.bat) stops it and tells
the website. It no longer starts by itself at log-on (IT can still turn that on
with install_task.bat).

One copy at a time (2.5.0+): the running watcher holds an exclusive lock on
watcher.lock next to this script for as long as it runs. A second copy prints
"Scan Watcher is already running" and exits with code 7 (start_watcher.bat then
stops instead of retrying). One-shot modes (--offline) don't take the lock.

Run:  py -3 folder_watcher.py            (or: py -3 folder_watcher.py --config other.json)
      py -3 folder_watcher.py --offline  tell the website this laptop stopped (STOP / UNINSTALL do this)
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import logging
import logging.handlers
import os
import posixpath
import re
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import requests

try:
    from PIL import Image, ImageOps, ImageSequence, UnidentifiedImageError
except ImportError:  # pragma: no cover
    print("Pillow is not installed. Run:  py -3 -m pip install -r requirements.txt")
    raise

VERSION = "2.5.0"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOCK_FILE_NAME = "watcher.lock"    # single instance (2.5.0+), next to this script
EXIT_ALREADY_RUNNING = 7           # start_watcher.bat stops its restart loop on this code

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".pdf"}
STABLE_POLLS_REQUIRED = 2          # size+mtime unchanged for this many polls
DEFAULT_POLL_SECONDS = 2.0
HEARTBEAT_SECONDS = 15.0           # the website shows "Offline" ~40 s after the last one
HEARTBEAT_SECONDS_WAITING = 10.0   # faster while files wait for a batch
KEEPALIVE_CHECK_SECONDS = 5.0      # background keep-alive while a slow upload blocks heartbeats
OFFLINE_TIMEOUT = (5, 10)          # connect, read: "going offline" notice at UNINSTALL / Ctrl+C
TARGET_DPI = 200
MAX_LONG_EDGE = 2200
JPEG_QUALITY = 85
REQUEST_TIMEOUT = (10, 150)        # connect, read (backend waits up to ~120s on n8n)
MAX_CONVERT_ATTEMPTS = 3
PERMANENT_REJECT_CODES = {400, 413, 415, 422}
FINAL_STATUSES = {"success", "flagged", "rejected", "duplicate"}
BEFORE_BATCH_DIR = "before-batch"  # under skipped/: pages scanned before the batch started
DISCARDED_DIR = "discarded"        # under skipped/: pages the website said to set aside
CMD_RESEND = "resend_skipped"
CMD_DISCARD = "discard_skipped"
MARKING_OFF_BASE_SECONDS = 30.0    # marking switched off on the server: 30s, 60s, ... up to 5 min
MARKING_OFF_MAX_SECONDS = 300.0

Image.MAX_IMAGE_PIXELS = 200_000_000

log = logging.getLogger("watcher")


# --------------------------------------------------------------------------- setup
def setup_logging() -> None:
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fh = logging.handlers.RotatingFileHandler(
        os.path.join(SCRIPT_DIR, "watcher.log"), maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    log.addHandler(fh)
    log.addHandler(sh)


# --------------------------------------------------------------------------- folder safety
# Copy of backend/app/folder_safety.py (this script is standalone). Keep in sync.
DEFAULT_FOLDER = "C:/Scans"
SPECIAL_ROOT_NAMES = {"desktop", "documents", "my documents", "downloads", "pictures", "my pictures"}
SYSTEM_TREES = {"windows", "program files", "program files (x86)"}
TOP_ROOTS = {"users", "documents and settings", "programdata"}


def normalize_folder(raw: str | None) -> str:
    """Forward slashes, no duplicate/trailing slash, . and .. resolved,
    upper-case drive letter. Does NOT expand variables."""
    p = str(raw or "").strip().strip('"').strip("'").strip()
    if not p:
        return ""
    p = p.replace("\\", "/")
    unc = p.startswith("//")
    body = re.sub(r"/+", "/", p[2:] if unc else p)
    m = re.match(r"^([A-Za-z]):(.*)$", body) if not unc else None
    if m:
        rest = m.group(2)
        if rest.startswith("/"):
            rest = posixpath.normpath(rest)
            rest = "/" if rest in ("/", "//") else rest.rstrip("/")
        return m.group(1).upper() + ":" + rest
    if unc:
        body = posixpath.normpath("/" + body)[1:] if body else ""
        return "//" + body.strip("/")
    if body.startswith("/"):
        body = posixpath.normpath(body)
        return body if body == "/" else body.rstrip("/")
    return body


def folder_problem(path: str | None, forbidden_exact=(), forbidden_trees=(), allow_posix: bool = False) -> str | None:
    """None if `path` (already expanded) is safe to watch, else a problem code."""
    p = normalize_folder(path)
    if not p:
        return "empty"
    if "%" in p or p.startswith("~"):
        return "variables"
    key = p.casefold()
    for f in forbidden_trees:
        f = normalize_folder(f).casefold()
        if f and (key == f or key.startswith(f.rstrip("/") + "/")):
            return "system"
    for f in forbidden_exact:
        f = normalize_folder(f).casefold()
        if f and key == f:
            return "too_big"

    if p.startswith("//"):
        parts = [x for x in p[2:].split("/") if x]
        if not parts or parts[0] in ("?", "."):
            return "invalid"
        if len(parts) <= 2:
            return "unc_root"
        return None

    m = re.match(r"^([A-Z]):(.*)$", p)
    if not m:
        if allow_posix and p.startswith("/"):
            return "drive_root" if p == "/" else None
        return "relative"
    rest = m.group(2)
    if not rest.startswith("/"):
        return "relative"
    parts = [x.casefold() for x in rest.split("/") if x]
    if not parts:
        return "drive_root"
    if parts[0] in SYSTEM_TREES:
        return "system"
    if len(parts) == 1 and parts[0] in TOP_ROOTS:
        return "too_big"
    if parts[0] in ("users", "documents and settings"):
        if len(parts) == 2:
            return "too_big"
        if len(parts) == 3 and (parts[2] in SPECIAL_ROOT_NAMES or parts[2].startswith("onedrive")):
            return "too_big"
        if len(parts) == 4 and parts[2].startswith("onedrive") and parts[3] in SPECIAL_ROOT_NAMES:
            return "too_big"
    return None


def uses_shortcut(raw) -> bool:
    """True for folder shortcuts like %USERPROFILE%/Scans or ~/Scans (kept in sync with the server copy)."""
    p = normalize_folder(raw)
    return "%" in p or p.startswith("~")


def is_network_path(raw) -> bool:
    """True for UNC / network-share paths (\\\\server\\share, //server/share, and
    the \\\\?\\UNC\\... and \\\\.\\ device forms): all start with two slashes.
    Refused when they come from the website (heartbeat folder_path); only a
    hand-edited watcher_config.json on this laptop may use a network folder.
    Kept in sync with the server copy."""
    p = str(raw or "").strip().strip('"').strip("'").strip().replace("\\", "/")
    return p.startswith("//")


def _local_forbidden() -> tuple[list[str], list[str]]:
    """This laptop's real profile / OneDrive / Windows / Program Files dirs."""
    env = os.environ
    exact, trees = [], []
    homes = [env.get("USERPROFILE"), os.path.expanduser("~"), env.get("PUBLIC")]
    clouds = [env.get(k) for k in ("OneDrive", "OneDriveCommercial", "OneDriveConsumer")]
    for base in [h for h in homes + clouds if h]:
        exact.append(base)
        for name in ("Desktop", "Documents", "Downloads", "Pictures"):
            exact.append(os.path.join(base, name))
    for k in ("SystemRoot", "WINDIR", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        if env.get(k):
            trees.append(env[k])
    return exact, trees


def expand_folder(raw: str | None) -> str:
    """Expand %VARS% / ~ and normalize (forward slashes)."""
    return normalize_folder(os.path.expandvars(os.path.expanduser(str(raw or "").strip().strip('"'))))


def local_folder_problem(raw: str | None) -> str | None:
    exact, trees = _local_forbidden()
    return folder_problem(expand_folder(raw), exact, trees, allow_posix=(os.name != "nt"))


def to_os_path(normalized: str) -> str:
    return os.path.normpath(normalized) if normalized else normalized


# --------------------------------------------------------------------------- config
class ConfigError(Exception):
    pass


@dataclass
class Config:
    api_base: str
    station_key: str
    folder: str
    processed_dir: str
    skipped_dir: str
    poll_seconds: float = DEFAULT_POLL_SECONDS
    processed_dir_explicit: bool = False
    skipped_dir_explicit: bool = False
    path: str = ""


_PATH_KEYS = ("folder", "processed_dir", "skipped_dir")
_CONTROL_CHARS = re.compile(r"[\x00-\x1f]")


def _fix_backslashes(text: str) -> str:
    r"""Turn single backslashes (e.g. a hand-typed "C:\Scans") into forward
    slashes. Keeps \\ (an escaped backslash), \" and \uXXXX."""
    return re.sub(r'\\(\\|"|u[0-9a-fA-F]{4})|\\', lambda m: m.group(0) if m.group(1) else "/", text)


def read_config_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8-sig") as f:
        text = f.read()
    try:
        raw = json.loads(text)
        # "C:\temp\new" parses, but \t and \n became control characters.
        if isinstance(raw, dict) and any(_CONTROL_CHARS.search(str(raw.get(k) or "")) for k in _PATH_KEYS):
            raise json.JSONDecodeError("control characters in a path", text, 0)
    except json.JSONDecodeError as first:
        try:
            raw = json.loads(_fix_backslashes(text))
        except json.JSONDecodeError:
            raise ConfigError(
                f"watcher_config.json is not valid JSON: {first}. "
                "Tip: write folder paths with forward slashes, like C:/Scans."
            )
        log.warning("watcher_config.json: backslashes in paths were read as forward slashes (use C:/Scans style).")
    if not isinstance(raw, dict):
        raise ConfigError("watcher_config.json must be a JSON object ({ ... }).")
    return raw


def load_config(path: str) -> Config:
    if not os.path.exists(path):
        raise ConfigError(
            f"Config file not found: {path}\n"
            "Download the installer (or watcher_config.json) from the website's 'Scanner setup' page and put it next to this script."
        )
    raw = read_config_json(path)
    missing = [k for k in ("api_base", "station_key") if not str(raw.get(k) or "").strip()]
    if missing:
        raise ConfigError(f"watcher_config.json is missing: {', '.join(missing)}")
    folder = expand_folder(raw.get("folder")) or DEFAULT_FOLDER
    cfg = Config(
        api_base=str(raw["api_base"]).strip().rstrip("/"),
        station_key=str(raw["station_key"]).strip(),
        folder=to_os_path(folder),
        processed_dir="",
        skipped_dir="",
        poll_seconds=max(0.5, float(raw.get("poll_seconds") or DEFAULT_POLL_SECONDS)),
        processed_dir_explicit=bool(str(raw.get("processed_dir") or "").strip()),
        skipped_dir_explicit=bool(str(raw.get("skipped_dir") or "").strip()),
        path=path,
    )
    cfg.processed_dir = (
        os.path.abspath(to_os_path(expand_folder(raw["processed_dir"]))) if cfg.processed_dir_explicit
        else os.path.join(cfg.folder, "processed")
    )
    cfg.skipped_dir = (
        os.path.abspath(to_os_path(expand_folder(raw["skipped_dir"]))) if cfg.skipped_dir_explicit
        else os.path.join(cfg.folder, "skipped")
    )
    return cfg


def save_folder_to_config(path: str, folder: str) -> None:
    """Atomically rewrite watcher_config.json with a new "folder", keeping
    every other key."""
    raw = read_config_json(path)
    raw["folder"] = folder
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".watcher_config.", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(raw, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


# --------------------------------------------------------------------------- state
class State:
    """sqlite record of pages already delivered, keyed by (batch_id, sha256):
    a restart never resends a page to the SAME batch, but an identical page
    dropped in again while a NEW batch is open is sent to that batch."""

    def __init__(self, path: str):
        self.db = sqlite3.connect(path)
        self.db.execute(
            "create table if not exists sent_pages ("
            " batch_id text not null, sha256 text not null, filename text, status text, sent_at text,"
            " primary key (batch_id, sha256))"
        )
        self.db.commit()

    def is_sent(self, sha: str, batch_id: str | None) -> bool:
        if not batch_id:
            return False  # unknown batch context: let the server decide (it is idempotent)
        row = self.db.execute("select 1 from sent_pages where batch_id = ? and sha256 = ?", (batch_id, sha)).fetchone()
        return row is not None

    def mark_sent(self, sha: str, filename: str, status: str, batch_id: str | None) -> None:
        if not batch_id:
            return
        self.db.execute(
            "insert or replace into sent_pages (batch_id, sha256, filename, status, sent_at) values (?,?,?,?,?)",
            (batch_id, sha, filename, status, datetime.now(timezone.utc).isoformat()),
        )
        self.db.commit()

    def last_sent_at(self) -> str | None:
        try:
            row = self.db.execute("select max(sent_at) from sent_pages").fetchone()
        except sqlite3.Error:
            return None
        return row[0] if row and row[0] else None


# --------------------------------------------------------------------------- images
def _normalize_page(img: "Image.Image", source_dpi: float | None) -> bytes:
    """Grayscale JPEG, ~200 dpi, long edge <= 2200 px, quality 85."""
    img = ImageOps.exif_transpose(img) if hasattr(img, "getexif") else img
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
        img = Image.alpha_composite(bg, img)
    img = img.convert("L")
    scale = 1.0
    if source_dpi and source_dpi > TARGET_DPI * 1.1:
        scale = TARGET_DPI / source_dpi
    long_edge = max(img.size) * scale
    if long_edge > MAX_LONG_EDGE:
        scale *= MAX_LONG_EDGE / long_edge
    if scale < 0.999:
        img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True, dpi=(TARGET_DPI, TARGET_DPI))
    return out.getvalue()


def _image_dpi(img: "Image.Image") -> float | None:
    dpi = img.info.get("dpi")
    try:
        if dpi and float(dpi[0]) > 1:
            return float(dpi[0])
    except (TypeError, ValueError, IndexError):
        pass
    return None


def convert_file(path: str) -> list[bytes]:
    """Return one JPEG per page. Multi-page TIFF and PDF are split."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        try:
            import pymupdf as fitz  # PyMuPDF >= 1.24
        except ImportError:
            try:
                import fitz  # older PyMuPDF
            except ImportError:
                fitz = None
        if fitz is None:
            raise RuntimeError("PDF support needs PyMuPDF: py -3 -m pip install -r requirements.txt")
        pages = []
        with fitz.open(path) as doc:
            for page in doc:
                pix = page.get_pixmap(dpi=TARGET_DPI, colorspace=fitz.csGRAY, alpha=False)
                img = Image.frombytes("L", (pix.width, pix.height), pix.samples, "raw", "L", pix.stride)
                pages.append(_normalize_page(img, TARGET_DPI))
        if not pages:
            raise ValueError("PDF has no pages")
        return pages

    with Image.open(path) as img:
        dpi = _image_dpi(img)
        if ext in (".tif", ".tiff"):
            return [_normalize_page(frame.copy(), _image_dpi(frame) or dpi) for frame in ImageSequence.Iterator(img)]
        img.load()
        return [_normalize_page(img, dpi)]


# --------------------------------------------------------------------------- files
@dataclass
class Tracked:
    size: int
    mtime: float
    created: float | None = None     # file creation time (None if the OS doesn't say)
    first_seen: float | None = None  # wall clock when the watcher first saw it (None: found at start-up)
    stable: int = 0
    next_try: float = 0.0
    failures: int = 0
    convert_failures: int = 0
    marking_waits: int = 0           # times the server said marking is switched off
    pages: list[bytes] | None = field(default=None, repr=False)


def file_created_time(st: os.stat_result) -> float | None:
    """Creation time: st_birthtime where the OS has it (Windows with Python
    3.12+, macOS), else st_ctime on Windows (creation time there). None on
    Linux, where st_ctime is only the last metadata change."""
    bt = getattr(st, "st_birthtime", None)
    if bt:
        return float(bt)
    if os.name == "nt":
        return float(st.st_ctime)
    return None


def capture_time(mtime: float, created: float | None = None, first_seen: float | None = None) -> float:
    """When the page is considered scanned (epoch seconds): the latest of its
    modified time, its creation time (a COPIED file gets a new creation time
    but keeps its old modified time) and when this watcher first saw it (a
    MOVED file keeps both). first_seen is only known for files that appeared
    while the watcher was running."""
    return max(t for t in (mtime, created, first_seen) if t is not None)


def capture_time_iso(t: "Tracked") -> str:
    return datetime.fromtimestamp(capture_time(t.mtime, t.created, t.first_seen), tz=timezone.utc).isoformat()


def iter_candidates(cfg: Config):
    excluded = {os.path.normcase(cfg.processed_dir), os.path.normcase(cfg.skipped_dir)}
    for root, dirs, files in os.walk(cfg.folder):
        dirs[:] = [
            d for d in dirs
            if not d.startswith(".") and os.path.normcase(os.path.join(root, d)) not in excluded
        ]
        for name in files:
            if name.startswith((".", "~")) or name.lower().endswith((".tmp", ".part", ".crdownload")):
                continue
            if os.path.splitext(name)[1].lower() in SUPPORTED_EXTENSIONS:
                yield os.path.join(root, name)


def move_file(path: str, dest_root: str, subdir: str | None = None) -> str:
    """Move a file, never overwriting (adds " (1)", " (2)" ...). Returns the new path."""
    dest_dir = os.path.join(dest_root, subdir) if subdir else dest_root
    os.makedirs(dest_dir, exist_ok=True)
    base, ext = os.path.splitext(os.path.basename(path))
    dest = os.path.join(dest_dir, base + ext)
    n = 1
    while os.path.exists(dest):
        dest = os.path.join(dest_dir, f"{base} ({n}){ext}")
        n += 1
    shutil.move(path, dest)
    return dest


def list_page_files(folder: str) -> list[str]:
    """Scan files directly inside `folder` (not sub-folders), oldest name first."""
    try:
        with os.scandir(folder) as it:
            names = [
                e.path for e in it
                if e.is_file() and not e.name.startswith((".", "~"))
                and os.path.splitext(e.name)[1].lower() in SUPPORTED_EXTENSIONS
            ]
    except OSError:
        return []
    return sorted(names)


def retry_after_seconds(resp, default: float, low: float = 1.0, high: float = 120.0) -> float:
    """The server's Retry-After (seconds form), clamped; `default` if absent/bad."""
    try:
        value = float(str(resp.headers.get("Retry-After", "")).strip())
    except (TypeError, ValueError, AttributeError):
        return default
    return max(low, min(high, value)) if value == value else default  # NaN -> default


def backoff(failures: int, base: float = 5.0, cap: float = 300.0) -> float:
    return min(cap, base * (2 ** max(0, failures - 1)))


# --------------------------------------------------------------------------- watcher
class Watcher:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.state = State(os.path.join(SCRIPT_DIR, "watcher_state.db"))
        self.session = requests.Session()
        self.session.headers.update({"X-Station-Key": cfg.station_key, "User-Agent": f"ievaluate-watcher/{VERSION}"})
        self.tracked: dict[str, Tracked] = {}
        self.last_heartbeat = 0.0
        self.open_batch_id: str | None = None
        self.has_open_batch: bool | None = None  # unknown until first heartbeat
        self.auth_failed_until = 0.0
        # True until the first full look at the folder (at start-up, after a
        # folder switch or after the folder came back): files found then were
        # already there, so their "first seen" time says nothing.
        self.initial_scan = True
        self.resent: dict[str, float] = {}             # path put back by resend_skipped -> first_seen
        self.command_done: str | None = None           # id to report on the next heartbeat
        self.done_commands: list[str] = []             # recent ids already carried out (never twice)
        self.folder_exists = False
        self.local_folder_error: str | None = None      # None | not_found | access_denied
        self.folder_unsafe = local_folder_problem(cfg.folder) is not None  # folder in use is unsafe
        self.rejected_remote_folder: str | None = None  # unsafe folder offered by the website
        self.folder_logged: str | None = None           # last folder problem logged (log once)
        self.folder_generation = 0                      # bumped on every folder switch
        self.last_file_at: str | None = self.state.last_sent_at()

    # ------------------------------------------------------------ folder
    @property
    def folder_error(self) -> str | None:
        if self.folder_unsafe or self.rejected_remote_folder:
            return "unsafe_path"
        return self.local_folder_error

    def _note_folder(self, code: str | None, message: str = "") -> None:
        """Log a folder problem once (and once when it clears)."""
        if code == self.folder_logged:
            return
        if code:
            log.warning(message)
        elif self.folder_logged:
            log.info("Scan folder OK: %s", self.cfg.folder)
        self.folder_logged = code

    @staticmethod
    def _may_create(folder: str) -> bool:
        """Only the default folder (C:/Scans) is ever created by the watcher."""
        return normalize_folder(folder).casefold() == normalize_folder(DEFAULT_FOLDER).casefold()

    def check_folder(self, create: bool = False) -> None:
        """Refresh folder_exists / local_folder_error. With create=True (only
        at start-up) a missing default folder (C:/Scans) is created when its
        parent exists. A folder pushed from the website is never created: a
        typo must show up as "not found" instead of silently making a folder."""
        folder = self.cfg.folder
        if self.folder_unsafe:
            self.folder_exists = os.path.isdir(folder)
            self.local_folder_error = None
            self._note_folder(
                "unsafe_path",
                f"NOT watching {folder}: that folder is too big to watch (a drive, the user folder, the root of "
                "Desktop/Documents/Downloads/OneDrive, Windows or Program Files). Set a folder just for scans, "
                "like C:\\Scans, on the website's Scanner setup page.",
            )
            return
        error = None
        if not os.path.isdir(folder):
            parent = os.path.dirname(folder.rstrip("\\/"))
            if create and self._may_create(folder) and parent and parent != folder and os.path.isdir(parent):
                try:
                    os.makedirs(folder, exist_ok=True)
                    log.info("Created scan folder %s", folder)
                except PermissionError:
                    error = "access_denied"
                except OSError:
                    error = "not_found"
            else:
                error = "not_found"
        if error is None:
            try:
                with os.scandir(folder) as it:
                    next(it, None)
            except PermissionError:
                error = "access_denied"
            except OSError:
                error = "not_found"
        self.folder_exists = os.path.isdir(folder)
        if error != self.local_folder_error:
            self.last_heartbeat = 0.0  # tell the website right away
        self.local_folder_error = error
        if error == "not_found":
            self._note_folder(error, f"Scan folder not found (will keep checking): {folder}")
        elif error == "access_denied":
            self._note_folder(error, f"No permission to read the scan folder (will keep checking): {folder}")
        else:
            self._note_folder(None)

    def apply_remote_folder(self, remote) -> None:
        """Hot-switch to the folder set on the website (heartbeat folder_path)."""
        if not isinstance(remote, str) or not remote.strip():
            self.rejected_remote_folder = None
            return
        if uses_shortcut(remote):
            if self.rejected_remote_folder != remote:
                log.error(
                    "The website asks to watch %s, but folder shortcuts like %%NAME%% or ~ are not allowed - "
                    "still watching %s. Type the full folder path, like C:\\Scans.", remote, self.cfg.folder,
                )
                self.last_heartbeat = 0.0
            self.rejected_remote_folder = remote
            return
        if is_network_path(remote):
            # A folder pushed from the website must be on this laptop: a network
            # share there could make this laptop read and send files from any
            # server. IT can still set one in watcher_config.json by hand.
            if self.rejected_remote_folder != remote:
                log.error(
                    "The website asks to watch %s, but network folders can't be set from the website - "
                    "still watching %s. Use a folder on this laptop, like C:\\Scans (or set the network "
                    "folder in watcher_config.json).", remote, self.cfg.folder,
                )
                self.last_heartbeat = 0.0
            self.rejected_remote_folder = remote
            return
        new = expand_folder(remote)
        if new.casefold() == normalize_folder(self.cfg.folder).casefold():
            self.rejected_remote_folder = None
            return
        if local_folder_problem(remote):
            if self.rejected_remote_folder != remote:
                log.error(
                    "The website asks to watch %s, but that folder is too big to watch - still watching %s. "
                    "Pick a folder just for scans, like C:\\Scans.", remote, self.cfg.folder,
                )
                self.last_heartbeat = 0.0
            self.rejected_remote_folder = remote
            return
        self.rejected_remote_folder = None
        self.switch_folder(new)

    def switch_folder(self, new: str) -> None:
        cfg = self.cfg
        cfg.folder = to_os_path(new)
        if not cfg.processed_dir_explicit:
            cfg.processed_dir = os.path.join(cfg.folder, "processed")
        if not cfg.skipped_dir_explicit:
            cfg.skipped_dir = os.path.join(cfg.folder, "skipped")
        self.tracked.clear()
        self.initial_scan = True
        self.folder_unsafe = False
        self.folder_logged = None
        self.folder_generation += 1
        log.info("Now watching %s", cfg.folder)
        self.check_folder()
        self.last_heartbeat = 0.0  # report the new folder to the website right away
        if cfg.path:
            try:
                save_folder_to_config(cfg.path, new)
            except (OSError, ConfigError, ValueError) as e:
                log.warning("Could not save the new folder into %s (%s) - it will be picked up again after a restart.", cfg.path, e)

    # ------------------------------------------------------------ http
    def _post(self, path: str, payload: dict) -> requests.Response:
        return self.session.post(f"{self.cfg.api_base}{path}", json=payload, timeout=REQUEST_TIMEOUT)

    @staticmethod
    def _detail(resp: requests.Response) -> tuple[str | None, str]:
        try:
            body = resp.json()
        except Exception:
            return None, f"HTTP {resp.status_code}"
        if not isinstance(body, dict):
            return None, f"HTTP {resp.status_code}"
        d = body.get("detail")
        if isinstance(d, dict):
            return d.get("code"), str(d.get("message") or d.get("code") or resp.status_code)
        # e.g. {"code": "database_unreachable", "detail": "..."}
        return body.get("code"), str(d or f"HTTP {resp.status_code}")

    @staticmethod
    def _kept_for_review(resp: requests.Response) -> str | None:
        """Server 2026-09-30+: a page marking gave up on is kept for the website
        review page. Returns its batch id ('' if not sent), or None when the
        server did not keep a copy."""
        try:
            d = resp.json().get("detail")
        except Exception:
            return None
        if isinstance(d, dict) and d.get("kept_for_review") is True:
            return str(d.get("batch_id") or "")
        return None

    # ------------------------------------------------------------ pages scanned before the batch
    @property
    def before_batch_dir(self) -> str:
        return os.path.join(self.cfg.skipped_dir, BEFORE_BATCH_DIR)

    def skipped_count(self) -> int:
        return len(list_page_files(self.before_batch_dir))

    def run_command(self, command: str, command_id: str) -> None:
        """Carry out a command from the website (once per id), then report it
        done on the next heartbeat."""
        if command_id in self.done_commands:
            self.command_done = command_id  # already done; just report it again
            return
        files = list_page_files(self.before_batch_dir)
        moved = 0
        if command == CMD_RESEND:
            for path in files:
                try:
                    dest = move_file(path, self.cfg.folder)
                except OSError as e:
                    log.warning("Could not put %s back into the scan folder (%s)", os.path.basename(path), e)
                    continue
                now = time.time()
                try:
                    os.utime(dest, (now, now))  # survives a restart: the page now counts as new
                except OSError:
                    pass
                self.resent[dest] = now
                moved += 1
            log.info("Website asked to add the pages scanned before the batch: %d page file(s) put back and will be sent now.", moved)
            for t in self.tracked.values():
                t.next_try = 0.0
        elif command == CMD_DISCARD:
            dest_dir = os.path.join(self.cfg.skipped_dir, DISCARDED_DIR, datetime.now().strftime("%Y-%m-%d"))
            for path in files:
                try:
                    move_file(path, dest_dir)
                    moved += 1
                except OSError as e:
                    log.warning("Could not set aside %s (%s)", os.path.basename(path), e)
            log.info("Website asked to set aside the pages scanned before the batch: %d page file(s) moved to %s (not deleted).",
                     moved, dest_dir)
        else:
            log.warning("Unknown command from the website: %r (ignored)", command)
        self.done_commands = (self.done_commands + [command_id])[-20:]
        self.command_done = command_id
        self.last_heartbeat = 0.0  # report back right away

    def heartbeat(self, force: bool = False) -> None:
        waiting = any(t.stable >= STABLE_POLLS_REQUIRED for t in self.tracked.values())
        interval = HEARTBEAT_SECONDS_WAITING if (waiting and not self.has_open_batch) else HEARTBEAT_SECONDS
        now = time.monotonic()
        if not force and now - self.last_heartbeat < interval:
            return
        self.last_heartbeat = now
        payload = {
            "version": VERSION,
            "client_time": datetime.now(timezone.utc).isoformat(),
            "queued_files": len(self.tracked),
            "folder": normalize_folder(self.cfg.folder),
            "folder_exists": bool(self.folder_exists),
            "folder_error": self.folder_error,
            "last_file_at": self.last_file_at,
            "skipped_files": self.skipped_count(),
        }
        sent_done = self.command_done
        if sent_done:
            payload["command_done"] = sent_done
        try:
            resp = self._post("/scans/heartbeat", payload)
        except requests.RequestException as e:
            log.warning("heartbeat failed: %s", type(e).__name__)
            return
        if resp.status_code == 401:
            log.error("Station key rejected by the server. Download the installer again (website: Scanner setup) and run INSTALL.")
            self.auth_failed_until = now + 60
            return
        if resp.status_code != 200:
            code, _msg = self._detail(resp)
            if code == "database_unreachable":
                log.warning("heartbeat: the server can't reach the database right now - will try again")
            else:
                log.warning("heartbeat HTTP %s", resp.status_code)
            return
        try:
            data = resp.json()
        except ValueError:
            log.warning("heartbeat: server reply was not JSON")
            return
        if not isinstance(data, dict):
            return
        # Folder chosen on the website (servers older than 2.3 do not send it).
        self.apply_remote_folder(data.get("folder_path"))
        had = self.has_open_batch
        self.has_open_batch = bool(data.get("open_batch"))
        self.open_batch_id = (data.get("open_batch") or {}).get("id")
        if self.has_open_batch and not had:
            log.info("Open batch detected on station '%s' - sending scans.", data.get("station_label"))
            for t in self.tracked.values():
                t.next_try = 0.0
        elif had is not False and not self.has_open_batch:
            log.info("No open batch on this station - new scans will wait.")
        # The server got our command_done (it answers null or a NEWER command).
        if sent_done and self.command_done == sent_done and data.get("command_id") != sent_done:
            self.command_done = None
        command, command_id = data.get("command"), data.get("command_id")
        if isinstance(command, str) and isinstance(command_id, str) and command_id:
            self.run_command(command, command_id)

    # ------------------------------------------------------------ polling
    def poll(self) -> None:
        self.check_folder()
        if self.folder_unsafe or self.local_folder_error:
            # Nothing can be watched: drop stale entries so queued_files is accurate.
            self.tracked.clear()
            self.initial_scan = True  # files there when it comes back were not "seen arriving"
            return

        generation = self.folder_generation
        wall = time.time()
        seen = set()
        for path in iter_candidates(self.cfg):
            seen.add(path)
            try:
                st = os.stat(path)
            except OSError:
                continue
            t = self.tracked.get(path)
            if t is None or t.size != st.st_size or t.mtime != st.st_mtime:
                # New or still changing: (re)start the stability count. Keep
                # the first time it was seen; files already there at start-up
                # have none.
                if t is not None:
                    first_seen = t.first_seen
                elif path in self.resent:
                    first_seen = self.resent.pop(path)
                else:
                    first_seen = None if self.initial_scan else wall
                self.tracked[path] = Tracked(size=st.st_size, mtime=st.st_mtime,
                                             created=file_created_time(st), first_seen=first_seen)
                continue
            if st.st_size > 0:
                t.stable += 1
        for gone in set(self.tracked) - seen:
            self.tracked.pop(gone, None)
        self.initial_scan = False
        if self.resent:
            self.resent = {p: ts for p, ts in self.resent.items() if wall - ts < 3600}

        now = time.monotonic()
        if now < self.auth_failed_until:
            return
        if self.has_open_batch is False:
            # No batch: don't send anything (the server would only say "no
            # open batch"). Heartbeats every 10 s notice when one starts.
            return
        for path, t in list(self.tracked.items()):
            if generation != self.folder_generation:
                return  # folder switched mid-poll (heartbeat inside process())
            if self.has_open_batch is False:
                return  # the batch was closed (heartbeat inside process())
            if t.stable < STABLE_POLLS_REQUIRED or now < t.next_try:
                continue
            try:
                self.process(path, t)
            except Exception:
                log.exception("unexpected error processing %s", path)
                t.failures += 1
                t.next_try = time.monotonic() + backoff(t.failures)

    # ------------------------------------------------------------ one file
    def process(self, path: str, t: Tracked) -> None:
        name = os.path.relpath(path, self.cfg.folder).replace(os.sep, "/")
        if t.pages is None:
            try:
                t.pages = convert_file(path)
            except UnidentifiedImageError as e:
                t.convert_failures += 1
                if t.convert_failures >= MAX_CONVERT_ATTEMPTS:
                    log.error("%s: not a readable image (%s) - moving to skipped/", name, e)
                    self._skip(path)
                else:
                    t.next_try = time.monotonic() + backoff(t.convert_failures, 3, 60)
                return
            except (PermissionError, OSError) as e:
                # Often: scanner software still holds the file. Retry later.
                t.convert_failures += 1
                if t.convert_failures >= MAX_CONVERT_ATTEMPTS * 2:
                    log.error("%s: cannot read file (%s) - moving to skipped/", name, e)
                    self._skip(path)
                else:
                    t.next_try = time.monotonic() + backoff(t.convert_failures, 3, 60)
                return
            except Exception as e:
                t.convert_failures += 1
                if t.convert_failures >= MAX_CONVERT_ATTEMPTS:
                    log.error("%s: not a readable image/PDF (%s) - moving to skipped/", name, e)
                    self._skip(path)
                else:
                    t.next_try = time.monotonic() + backoff(t.convert_failures, 3, 60)
                return

        # Local de-duplication is per batch, so make sure we know the CURRENT
        # batch (a batch may have been closed and a new one opened since the
        # last heartbeat).
        if time.monotonic() - self.last_heartbeat > 5:
            generation = self.folder_generation
            self.heartbeat(force=True)
            if generation != self.folder_generation:
                return  # the website switched the folder; this file is no longer watched
            if self.has_open_batch is False:
                return  # no batch any more: wait (poll() skips until one is open)
        captured_at = capture_time_iso(t)
        total = len(t.pages)
        for idx, jpeg in enumerate(t.pages, start=1):
            sha = hashlib.sha256(jpeg).hexdigest()
            if self.state.is_sent(sha, self.open_batch_id):
                continue
            page_name = name if total == 1 else f"{name}#p{idx}"
            outcome = self.send_page(page_name, jpeg, sha, captured_at)
            if outcome == "sent":
                continue
            if outcome == "skip_file":
                self._skip(path)
                return
            if outcome == "before_batch":
                log.info("%s: scanned before the current batch started - set aside in skipped/%s/ "
                         "(it can be added to the batch on the website)", name, BEFORE_BATCH_DIR)
                self._skip(path, BEFORE_BATCH_DIR)
                self.last_heartbeat = 0.0  # tell the website soon
                return
            if outcome == "marking_off":
                t.marking_waits += 1
                if t.marking_waits == 1:
                    log.warning("%s: the website can't mark pages right now (marking is switched off on the server) - "
                                "the page waits here and is tried again every few minutes", name)
                t.next_try = time.monotonic() + backoff(t.marking_waits, MARKING_OFF_BASE_SECONDS, MARKING_OFF_MAX_SECONDS)
                return
            # retry later: send_page returned a delay, or None for exponential backoff
            t.failures += 1
            delay = outcome if isinstance(outcome, (int, float)) else backoff(t.failures)
            t.next_try = time.monotonic() + delay
            return

        # every page delivered
        try:
            move_file(path, self.cfg.processed_dir, datetime.now().strftime("%Y-%m-%d"))
            log.info("%s: done (%d page%s) -> processed/", name, total, "" if total == 1 else "s")
        except OSError as e:
            log.warning("%s: sent, but could not move to processed/ (%s)", name, e)
            t.next_try = time.monotonic() + 30
            return
        self.tracked.pop(path, None)

    def _skip(self, path: str, subdir: str | None = None) -> None:
        try:
            move_file(path, self.cfg.skipped_dir, subdir)
        except OSError as e:
            log.warning("could not move %s to skipped/ (%s)", path, e)
        self.tracked.pop(path, None)

    def send_page(self, page_name: str, jpeg: bytes, sha: str, captured_at: str):
        """Returns 'sent', 'skip_file', 'before_batch', 'marking_off', or a
        retry delay in seconds (None: exponential backoff)."""
        payload = {
            "filename": page_name,
            "image_base64": base64.b64encode(jpeg).decode("ascii"),
            "sha256": sha,
            "captured_at": captured_at,
            "client_time": datetime.now(timezone.utc).isoformat(),
        }
        try:
            resp = self._post("/scans/ingest", payload)
        except requests.RequestException as e:
            log.warning("%s: network error (%s) - will retry", page_name, type(e).__name__)
            return None  # caller applies exponential backoff

        if resp.status_code == 200:
            data = resp.json()
            status = str(data.get("status"))
            if status not in FINAL_STATUSES:
                log.warning("%s: unexpected server status %r - will retry", page_name, status)
                return None
            if data.get("batch_id"):
                self.open_batch_id = data["batch_id"]
            self.state.mark_sent(sha, page_name, status, data.get("batch_id"))
            self.last_file_at = datetime.now(timezone.utc).isoformat()
            extra = ""
            if data.get("review_reasons"):
                extra = " | " + "; ".join(map(str, data["review_reasons"]))[:200]
            sim = " [SIMULATED]" if data.get("simulated") else ""
            log.info("%s: %s%s%s", page_name, status.upper(), sim, extra)
            return "sent"

        code, message = self._detail(resp)
        if resp.status_code == 409 and code == "file_predates_batch":
            return "before_batch"
        if resp.status_code == 409 and code == "no_open_batch":
            if self.has_open_batch is not False:
                log.info("No open batch on this station - scans will wait until one is started.")
            self.has_open_batch = False  # poll() now waits for a heartbeat that sees a batch
            self.open_batch_id = None
            return 15.0
        if resp.status_code == 503 and code == "pipeline_not_configured":
            return "marking_off"
        if resp.status_code == 503 and code == "server_busy":
            # Server 2026-09-30+: too many pages being marked at once. Not the page's fault.
            log.info("%s: the server is busy with other pages - will retry", page_name)
            return retry_after_seconds(resp, 10.0)
        if resp.status_code == 503 and code == "database_unreachable":
            log.warning("%s: the server can't reach the database right now - will retry", page_name)
            return None
        if resp.status_code == 422 and code == "failed_after_retries":
            kept = self._kept_for_review(resp)
            if kept is not None:
                # The server kept a copy for its review page (Try again / enter by hand):
                # the page is delivered, nothing to rescan.
                log.warning("%s: marking couldn't save this page after several tries - a copy is waiting on the "
                            "website's review page (no rescan needed)", page_name)
                self.state.mark_sent(sha, page_name, "needs_review", kept or self.open_batch_id)
                self.last_file_at = datetime.now(timezone.utc).isoformat()
                return "sent"
            log.error("%s: scoring failed after repeated attempts - moving to skipped/ (rescan if needed)", page_name)
            return "skip_file"
        if resp.status_code == 422 and code == "pipeline_rejected_request":
            log.error("%s: rejected by the scoring pipeline (invalid request) - moving to skipped/", page_name)
            return "skip_file"
        if resp.status_code == 409 and code == "in_progress":
            return 15.0
        if resp.status_code == 401:
            log.error("Station key rejected. Download the installer again (website: Scanner setup) and run INSTALL.")
            self.auth_failed_until = time.monotonic() + 60
            return 60.0
        if resp.status_code in PERMANENT_REJECT_CODES:
            log.error("%s: rejected by server (%s) - moving to skipped/", page_name, message)
            return "skip_file"
        log.warning("%s: server error HTTP %s (%s) - will retry", page_name, resp.status_code, message)
        return None

    def keepalive_due(self, now: float, last_sent: float) -> bool:
        """True when the main loop has missed a heartbeat (busy) and no keep-alive
        went out in the last HEARTBEAT_SECONDS."""
        return (now - self.last_heartbeat >= HEARTBEAT_SECONDS + KEEPALIVE_CHECK_SECONDS
                and now - last_sent >= HEARTBEAT_SECONDS and now >= self.auth_failed_until)

    def _keepalive_loop(self) -> None:
        """Background thread. The main loop sends one page at a time and can wait
        minutes on the marking pipeline, with no heartbeat meanwhile; the website
        would show "Offline". While that happens, send a minimal heartbeat (only
        last_seen / version; no folder status, commands are left for the main
        loop). Does nothing while regular heartbeats flow. Own session: the main
        one is busy in the other thread."""
        session = requests.Session()
        session.headers.update({"X-Station-Key": self.cfg.station_key, "User-Agent": f"ievaluate-watcher/{VERSION}"})
        last_sent = 0.0
        while True:
            time.sleep(KEEPALIVE_CHECK_SECONDS)
            now = time.monotonic()
            if not self.keepalive_due(now, last_sent):
                continue
            last_sent = now
            try:
                session.post(f"{self.cfg.api_base}/scans/heartbeat", json={"version": VERSION}, timeout=(5, 15))
            except requests.RequestException:
                pass  # the main loop logs connection problems

    # ------------------------------------------------------------ main loop
    def run(self) -> None:
        log.info("iEvaluate watcher %s | folder=%s | server=%s", VERSION, self.cfg.folder, self.cfg.api_base)
        log.info("Processed files -> %s | skipped -> %s", self.cfg.processed_dir, self.cfg.skipped_dir)
        self.check_folder(create=True)
        self.heartbeat(force=True)
        threading.Thread(target=self._keepalive_loop, name="keepalive", daemon=True).start()
        while True:
            try:
                self.heartbeat()
                self.poll()
            except KeyboardInterrupt:
                raise
            except Exception:
                log.exception("watcher loop error (continuing)")
            time.sleep(self.cfg.poll_seconds)


_instance_lock = None  # open handle of watcher.lock; kept open for the life of the process


def acquire_instance_lock(path: str | None = None):
    """Take the single-instance lock: an exclusive, non-blocking lock on the
    first byte of watcher.lock. Returns the open file (keep it open - closing it
    or ending the process releases the lock, also after a crash or a kill), or
    None when another watcher already holds it."""
    path = path or os.path.join(SCRIPT_DIR, LOCK_FILE_NAME)
    f = open(path, "a+b")
    try:
        f.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:  # pragma: no cover - the watcher runs on Windows; this keeps tests portable
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f


def send_offline(cfg: Config) -> bool:
    """Tell the website this laptop stopped watching (STOP, UNINSTALL, Ctrl+C), so its
    light turns red now instead of ~40 s later. Best effort, never raises.
    Servers without this endpoint answer 404; nothing changes then."""
    try:
        resp = requests.post(
            f"{cfg.api_base}/scans/offline", json={"version": VERSION}, timeout=OFFLINE_TIMEOUT,
            headers={"X-Station-Key": cfg.station_key, "User-Agent": f"ievaluate-watcher/{VERSION}"},
        )
    except requests.RequestException as e:
        log.warning("could not tell the website this laptop is offline: %s", type(e).__name__)
        return False
    if resp.status_code != 200:
        log.warning("going offline: HTTP %s", resp.status_code)
        return False
    log.info("Told the website this laptop is offline.")
    return True


def main() -> None:
    global _instance_lock
    parser = argparse.ArgumentParser(description="iEvaluate scan folder watcher")
    parser.add_argument("--config", default=os.path.join(SCRIPT_DIR, "watcher_config.json"))
    parser.add_argument("--offline", action="store_true",
                        help="only tell the website this laptop stopped watching, then exit (used by STOP / UNINSTALL)")
    args = parser.parse_args()
    if not args.offline:
        # Before logging: a second copy must not write to (or rotate) watcher.log.
        _instance_lock = acquire_instance_lock()
        if _instance_lock is None:
            print("Scan Watcher is already running")
            sys.exit(EXIT_ALREADY_RUNNING)
    setup_logging()
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        log.error("Config problem: %s", e)  # console + watcher.log
        sys.exit(2)
    except Exception:
        log.exception("Could not read %s", args.config)
        sys.exit(2)
    if args.offline:
        sys.exit(0 if send_offline(cfg) else 1)
    try:
        Watcher(cfg).run()
    except KeyboardInterrupt:
        log.info("Stopped.")
        send_offline(cfg)


if __name__ == "__main__":
    main()
