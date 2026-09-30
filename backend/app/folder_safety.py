r"""Scan-folder safety checks shared by the website API and the folder watcher.

The watcher (backend/watcher/folder_watcher.py) is a standalone script, so it
carries a copy of this logic. Keep the two in sync.

A watched folder is scanned recursively every couple of seconds and every
image in it is sent for scoring, so it must be a folder just for scans:
never a drive root, the user profile, Windows / Program Files, the root of
Desktop / Documents / Downloads / Pictures / OneDrive, or a network share root.
Subfolders of Desktop, Documents, etc. are fine.

Folders set on the website must be full paths on the laptop itself: no
%NAME% variables, no ~, and no network shares (\\server\share or
//server/share). A folder on the website is pushed to the laptop on its next
heartbeat, so a network path there could make the laptop read (and send for
scoring) files from any server the website user picks. Only a hand-edited
watcher_config.json on the laptop may still use %VARS% or a folder inside a
network share.
"""
from __future__ import annotations

import posixpath
import re

DEFAULT_FOLDER = "C:/Scans"

# Folders whose ROOT is too big to watch (subfolders are fine).
SPECIAL_ROOT_NAMES = {"desktop", "documents", "my documents", "downloads", "pictures", "my pictures"}
# Top-level folders that must never be watched, nor anything inside them.
SYSTEM_TREES = {"windows", "program files", "program files (x86)"}
# Top-level folders whose root is too big.
TOP_ROOTS = {"users", "documents and settings", "programdata"}

_EXAMPLE = "C:\\Scans"
MESSAGES = {
    "empty": f"Please enter the folder the scanner saves to, like {_EXAMPLE}.",
    "relative": f"Please enter a full folder path that starts with a drive letter, like {_EXAMPLE}.",
    "variables": f"Please type the full folder path instead of using shortcuts like %NAME% or ~, for example {_EXAMPLE}.",
    "drive_root": f"A whole drive cannot be watched. Please pick or create a folder just for scans, like {_EXAMPLE}.",
    "unc_root": "A whole network share cannot be watched. Please pick a folder inside the share that is just for scans.",
    "network": f"Network folders (like \\\\server\\share) cannot be set from the website. Please use a folder on the laptop itself, like {_EXAMPLE}. "
               "(IT can still set a network folder in watcher_config.json on the laptop.)",
    "system": f"Windows and Program Files folders cannot be watched. Please pick or create a folder just for scans, like {_EXAMPLE}.",
    "too_big": f"That folder is too big to watch. Please pick or create a folder just for scans, like {_EXAMPLE}.",
    "invalid": f"That does not look like a valid folder path. Please use something like {_EXAMPLE}.",
}


def normalize_folder(raw: str | None) -> str:
    """Trim quotes/whitespace, use forward slashes, collapse duplicate slashes,
    resolve . and .., drop the trailing slash, upper-case the drive letter.
    Does NOT expand variables."""
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
    """Return None if `path` (already expanded) is safe to watch, else a
    problem code (see MESSAGES). `forbidden_*` are extra normalized paths
    (the watcher adds the laptop's real profile / Windows / OneDrive dirs)."""
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
        return "relative"  # "C:" or drive-relative "C:Scans"
    parts = [x.casefold() for x in rest.split("/") if x]
    if not parts:
        return "drive_root"
    if parts[0] in SYSTEM_TREES:
        return "system"
    if len(parts) == 1 and parts[0] in TOP_ROOTS:
        return "too_big"
    if parts[0] in ("users", "documents and settings"):
        if len(parts) == 2:
            return "too_big"  # a user profile root (also Public, Default)
        if len(parts) == 3 and (parts[2] in SPECIAL_ROOT_NAMES or parts[2].startswith("onedrive")):
            return "too_big"
        if len(parts) == 4 and parts[2].startswith("onedrive") and parts[3] in SPECIAL_ROOT_NAMES:
            return "too_big"
    return None


def uses_shortcut(raw) -> bool:
    """True for folder shortcuts like %USERPROFILE%/Scans or ~/Scans."""
    p = normalize_folder(raw)
    return "%" in p or p.startswith("~")


def is_network_path(raw) -> bool:
    """True for UNC / network-share paths (\\\\server\\share, //server/share, and
    the \\\\?\\UNC\\... and \\\\.\\ device forms): all start with two slashes."""
    p = str(raw or "").strip().strip('"').strip("'").strip().replace("\\", "/")
    return p.startswith("//")


def check_folder_for_server(raw: str | None) -> tuple[str, str | None]:
    """(normalized path to store, plain-English error or None). Website folders
    must be full local paths: shortcuts like %NAME% or ~ ("variables") and
    network shares ("network") are refused."""
    normalized = normalize_folder(raw)
    if not normalized:
        code = "empty"
    elif is_network_path(raw) or is_network_path(normalized):
        code = "network"
    else:
        code = folder_problem(normalized)
    return normalized, (MESSAGES.get(code, MESSAGES["too_big"]) if code else None)
