import io
import json
import os
import re
import zipfile
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import Response
from postgrest.exceptions import APIError
from pydantic import BaseModel, Field

from app.audit import audit
from app.batch_access import (
    NOT_YOUR_STATION_CODE,
    NOT_YOUR_STATION_MSG,
    STATION_IN_USE_CODE,
    can_manage_station,
    station_in_use_message,
)
from app.db import get_settings, get_supabase, is_valid_uuid
from app.folder_safety import DEFAULT_FOLDER, check_folder_for_server, is_network_path
from app.logic import generate_station_key, hash_station_key, parse_ts
from app.routers.watcher_download import ZIP_PATH
from app.security import CurrentUser, require_staff
from app.station_status import (
    ALL_WATCHER_FIELDS,
    CMD_DISCARD,
    CMD_RESEND,
    StatusColumnsMissing,
    apply_public_watcher_fields,
    select_stations,
    set_command,
)

router = APIRouter()

STATION_COLUMNS = "id, label, folder_path, current_staff_id, updated_at, last_seen_at, key_created_at, watcher_version"
INSTALLER_TOP = "ScanWatcher/"
# Never ship these from the prebuilt package (secrets / per-laptop state).
_PACKAGE_EXCLUDE = {"watcher_config.json", "watcher_state.db", "watcher.log"}


class FolderUpdate(BaseModel):
    folder_path: str = Field(max_length=500)


class InstallerRequest(BaseModel):
    # Optional override of the backend URL written into watcher_config.json.
    api_base: str | None = Field(default=None, max_length=300)


class RegisterStation(BaseModel):
    label: str = Field(min_length=1, max_length=60)


class SkippedPagesAction(BaseModel):
    action: Literal["add_to_batch", "discard"]


NEEDS_MIGRATION_004 = "This needs a database update first. Ask IT to apply migration 004."
START_BATCH_FIRST = "Start a batch first, then add these pages."


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decorate(stations: list[dict]) -> list[dict]:
    """Add watcher_online, has_key, bound staff name and open batch id. Never
    returns key_hash."""
    if not stations:
        return stations
    sb = get_supabase()
    settings = get_settings()
    now = datetime.now(timezone.utc)

    staff_ids = list({s["current_staff_id"] for s in stations if s.get("current_staff_id")})
    names: dict[str, str] = {}
    if staff_ids:
        rows = sb.table("user_info").select("id, first_name, last_name").in_("id", staff_ids).execute().data or []
        names = {r["id"]: " ".join(p for p in (r.get("first_name"), r.get("last_name")) if p) for r in rows}

    ids = [s["id"] for s in stations]
    open_rows = sb.table("scan_batches").select("id, station_id").eq("status", "open").in_("station_id", ids).execute().data or []
    open_by_station = {r["station_id"]: r["id"] for r in open_rows}

    for s in stations:
        seen = parse_ts(s.get("last_seen_at"))
        s["watcher_online"] = bool(seen and (now - seen).total_seconds() <= settings.watcher_online_seconds)
        s["has_key"] = bool(s.pop("key_created_at", None))
        s["current_staff_name"] = names.get(s.get("current_staff_id"))
        s["open_batch_id"] = open_by_station.get(s["id"])
        s.pop("key_hash", None)
        apply_public_watcher_fields(s, now)  # watcher_skipped_files, watcher_command
    return stations


def _get_station_row(station_id: str) -> dict:
    if not is_valid_uuid(station_id):
        raise HTTPException(status_code=404, detail="Station not found")
    rows = select_stations(get_supabase(), STATION_COLUMNS, lambda q: q.eq("id", station_id).limit(1))
    if not rows:
        raise HTTPException(status_code=404, detail="Station not found")
    return rows[0]


def _require_station_manager(station: dict, user: CurrentUser, action: str) -> None:
    """Folder / key / installer: only the staff bound to this station, or an admin."""
    if not can_manage_station(station, user):
        audit(user.id, "station_change_refused", {"station_id": station.get("id"), "action": action,
                                                  "bound_staff_id": station.get("current_staff_id")})
        raise HTTPException(status_code=403, detail={"code": NOT_YOUR_STATION_CODE, "message": NOT_YOUR_STATION_MSG})


def _rotate_key(station_id: str) -> str:
    key = generate_station_key()
    get_supabase().table("stations").update({
        "key_hash": hash_station_key(key),
        "key_created_at": _now_iso(),
        "updated_at": _now_iso(),
    }).eq("id", station_id).execute()
    return key


@router.post("")
def register_station(body: RegisterStation, user: CurrentUser = Depends(require_staff)):
    label = " ".join(body.label.split())
    if not label:
        raise HTTPException(status_code=400, detail="Label is required")
    key = generate_station_key()
    try:
        res = get_supabase().table("stations").insert({
            "label": label,
            "key_hash": hash_station_key(key),
            "key_created_at": _now_iso(),
        }).execute()
    except APIError as e:
        if getattr(e, "code", None) == "23505":
            raise HTTPException(status_code=409, detail={"code": "duplicate_station", "message": f"A station named '{label}' already exists."})
        raise
    station = res.data[0]
    audit(user.id, "station_registered", {"station_id": station["id"], "label": label})
    fields = STATION_COLUMNS.replace(" ", "").split(",") + list(ALL_WATCHER_FIELDS)
    out = _decorate([{k: station.get(k) for k in fields}])[0]
    # The plain key is returned exactly once; only its hash is stored.
    out["station_key"] = key
    return out


@router.post("/{station_id}/regenerate-key")
def regenerate_key(station_id: str, user: CurrentUser = Depends(require_staff)):
    _require_station_manager(_get_station_row(station_id), user, "regenerate_key")
    key = _rotate_key(station_id)
    audit(user.id, "station_key_regenerated", {"station_id": station_id})
    station = _decorate([_get_station_row(station_id)])[0]
    station["station_key"] = key
    return station


def _api_base_for_watcher(request: Request, override: str | None) -> str:
    """Backend URL the watcher should call: explicit override, else the
    WATCHER_API_BASE setting, else this request's own base URL."""
    for candidate in (override, get_settings().watcher_api_base):
        c = (candidate or "").strip().rstrip("/")
        if c:
            if not re.match(r"^https?://[^\s/]+", c, re.I) or re.search(r"\s", c):
                raise HTTPException(status_code=400, detail="The server address must start with https:// (or http://).")
            return c
    base = str(request.base_url).rstrip("/")
    proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    if proto == "https" and base.startswith("http://"):
        base = "https://" + base[len("http://"):]
    return base


def _installer_folder(raw: str | None) -> str:
    folder = (raw or "").strip()
    return folder if folder and not is_network_path(folder) else DEFAULT_FOLDER


def _safe_label(label: str | None) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", label or "").strip("-._")
    return s[:60] or "Station"


def _read_package() -> list[tuple[str, bytes]]:
    """Files of the prebuilt watcher package (backend/app/static/watcher-package.zip,
    built by backend/watcher/build_package.py), re-rooted under ScanWatcher/."""
    if not os.path.isfile(ZIP_PATH):
        raise HTTPException(status_code=503, detail="The watcher package has not been built on the server yet. Ask the administrator to run watcher/build_package.py.")
    out: list[tuple[str, bytes]] = []
    with zipfile.ZipFile(ZIP_PATH) as z:
        for info in z.infolist():
            name = info.filename.replace("\\", "/").lstrip("/")
            if info.is_dir() or not name or ".." in name.split("/"):
                continue
            rel = name[len(INSTALLER_TOP):] if name.startswith(INSTALLER_TOP) else name
            if not rel or rel.rsplit("/", 1)[-1].lower() in _PACKAGE_EXCLUDE:
                continue
            out.append((INSTALLER_TOP + rel, z.read(info)))
    if not out:
        raise HTTPException(status_code=503, detail="The watcher package on the server is empty.")
    return out


@router.post("/{station_id}/installer")
def download_installer(
    station_id: str,
    request: Request,
    body: InstallerRequest | None = Body(default=None),
    user: CurrentUser = Depends(require_staff),
):
    """One-file setup: the watcher package plus a ready watcher_config.json.
    Rotates the station key (any watcher using the old key stops working)."""
    station = _get_station_row(station_id)
    _require_station_manager(station, user, "installer")
    api_base = _api_base_for_watcher(request, body.api_base if body else None)
    files = _read_package()  # before rotating, so a missing package changes nothing
    key = _rotate_key(station_id)
    audit(user.id, "station_key_regenerated", {"station_id": station_id, "via": "installer"})
    config = {
        "api_base": api_base,
        "station_key": key,
        # Never a network share from the website (IT may set one on the laptop by hand).
        "folder": _installer_folder(station.get("folder_path")),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for name, data in files:
            z.writestr(name, data)
        z.writestr(INSTALLER_TOP + "watcher_config.json", json.dumps(config, indent=2) + "\n")
    filename = f"ScanWatcher-{_safe_label(station.get('label'))}.zip"
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.get("")
def list_stations(user: CurrentUser = Depends(require_staff)):
    return _decorate(select_stations(get_supabase(), STATION_COLUMNS, lambda q: q.order("label")))


@router.get("/{station_id}")
def get_station(station_id: str, user: CurrentUser = Depends(require_staff)):
    return _decorate([_get_station_row(station_id)])[0]


@router.put("/{station_id}/folder")
def set_folder(station_id: str, body: FolderUpdate, user: CurrentUser = Depends(require_staff)):
    station = _get_station_row(station_id)
    _require_station_manager(station, user, "set_folder")
    folder, problem = check_folder_for_server(body.folder_path)
    if problem:
        raise HTTPException(status_code=400, detail=problem)
    get_supabase().table("stations").update({
        "folder_path": folder,
        "updated_at": _now_iso(),
    }).eq("id", station_id).execute()
    audit(user.id, "station_folder_changed", {"station_id": station_id, "folder_path": folder,
                                              "previous_folder_path": station.get("folder_path")})
    return _decorate([_get_station_row(station_id)])[0]


@router.post("/{station_id}/skipped-pages")
def skipped_pages(station_id: str, body: SkippedPagesAction, user: CurrentUser = Depends(require_staff)):
    """Pages scanned before the batch started wait on the laptop in
    skipped/before-batch/. add_to_batch: the watcher puts them back and sends
    them to the open batch; discard: it moves them to skipped/discarded/<date>/
    (never deleted). Carried out on the watcher's next heartbeat (~10-30 s)."""
    _get_station_row(station_id)
    sb = get_supabase()
    if body.action == "add_to_batch":
        open_rows = sb.table("scan_batches").select("id").eq("station_id", station_id).eq("status", "open").limit(1).execute().data
        if not open_rows:
            raise HTTPException(status_code=409, detail=START_BATCH_FIRST)
    command = CMD_RESEND if body.action == "add_to_batch" else CMD_DISCARD
    try:
        command_id = set_command(sb, station_id, command)
    except StatusColumnsMissing:
        raise HTTPException(status_code=503, detail=NEEDS_MIGRATION_004)
    audit(user.id, "station_skipped_pages", {"station_id": station_id, "action": body.action, "command_id": command_id})
    return _decorate([_get_station_row(station_id)])[0]


@router.post("/{station_id}/bind")
def bind_staff_to_station(station_id: str, user: CurrentUser = Depends(require_staff)):
    """Bind the *authenticated* staff member to this laptop's station.

    Staff share laptops, so this stays easy: allowed when the station is free,
    already ours, we are an admin, or whoever is bound there has no open batch
    on it. Otherwise 409 station_in_use (binding would not give access to their
    batch any more - see app/batch_access.py - but it would cut them off their
    own laptop setup mid-batch)."""
    station = _get_station_row(station_id)
    sb = get_supabase()
    previous = str(station.get("current_staff_id") or "") or None
    if previous and previous != user.id and not user.is_admin:
        busy = (sb.table("scan_batches").select("id").eq("station_id", station_id).eq("status", "open")
                .eq("created_by", previous).limit(1).execute().data)
        if busy:
            names = sb.table("user_info").select("first_name, last_name").eq("id", previous).limit(1).execute().data or []
            name = " ".join(p for p in ((names[0].get("first_name"), names[0].get("last_name")) if names else ()) if p)
            audit(user.id, "station_bind_refused", {"station_id": station_id, "bound_staff_id": previous,
                                                    "open_batch_id": busy[0]["id"]})
            raise HTTPException(status_code=409, detail={"code": STATION_IN_USE_CODE,
                                                         "message": station_in_use_message(name or None)})
    q = sb.table("stations").update({
        "current_staff_id": user.id,
        "updated_at": _now_iso(),
    }).eq("id", station_id)
    # Only if nobody else bound in the meantime (the check above is not atomic).
    if not user.is_admin:
        q = q.eq("current_staff_id", previous) if previous else q.is_("current_staff_id", "null")
    res = q.execute()
    if not user.is_admin and not (getattr(res, "data", None) or []):
        raise HTTPException(status_code=409, detail={"code": STATION_IN_USE_CODE,
                                                     "message": "Someone else just logged in on this laptop. Please try again."})
    audit(user.id, "station_bound", {"station_id": station_id, "previous_staff_id": previous,
                                     "rebind": previous == user.id,
                                     "admin_override": bool(previous and previous != user.id and user.is_admin)})
    return _decorate([_get_station_row(station_id)])[0]
