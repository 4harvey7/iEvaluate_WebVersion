"""Watcher status + command columns on `stations` (migrations/004_watcher_folder_status.sql).

The backend must keep working before that migration is applied: reads fall
back to the base columns (the new fields come back as null) and heartbeat
writes fall back to last_seen_at / watcher_version only. After a failure the
new columns are re-tried every few minutes, so applying the migration takes
effect without a restart.

Command channel (watcher 2.4+): a staff member asks, on the website, to add
the pages that were scanned before the batch started (or to set them aside).
The request is stored as a pending command on the station; the next heartbeat
hands it to the watcher, which does it and reports `command_done` on the
heartbeat after that, which clears it.
"""
from __future__ import annotations

import logging
import threading
import time
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable

from postgrest.exceptions import APIError

from app.logic import parse_ts

log = logging.getLogger("ievaluate.stations")

WATCHER_STATUS_FIELDS = (
    "watcher_folder",
    "watcher_folder_exists",
    "watcher_folder_error",
    "watcher_queued_files",
    "watcher_last_file_at",
    "watcher_skipped_files",
)
# Internal: never returned as-is (station objects get `watcher_command` only).
WATCHER_COMMAND_FIELDS = ("watcher_command", "watcher_command_id", "watcher_command_at")
ALL_WATCHER_FIELDS = WATCHER_STATUS_FIELDS + WATCHER_COMMAND_FIELDS
FOLDER_ERRORS = {"not_found", "access_denied", "unsafe_path"}

CMD_RESEND = "resend_skipped"
CMD_DISCARD = "discard_skipped"
COMMANDS = {CMD_RESEND, CMD_DISCARD}
# A command the watcher has not picked up within this time is dropped (the
# laptop was off); the staff member can click again.
COMMAND_TTL_SECONDS = 900.0

# Online light. Watcher 2.4.4+ sends a heartbeat every 15 s (plus a keep-alive
# while a slow page upload blocks it) and tells the server when it is stopped
# by UNINSTALL; WATCHER_ONLINE_SECONDS (default 40 s = two missed heartbeats +
# 10 s) applies to it. Older watchers send one every 30 s and none during an
# upload, so they keep the old 90 s to avoid a false "Offline" mid-scan.
FAST_HEARTBEAT_VERSION = (2, 4, 4)
LEGACY_ONLINE_SECONDS = 90.0

# Undefined column / PostgREST "column not in schema cache".
_MISSING_COLUMN_CODES = {"42703", "PGRST204"}
_RETRY_SECONDS = 300.0

_state = {"missing_until": 0.0}
_lock = threading.Lock()


class StatusColumnsMissing(RuntimeError):
    """Migration 004 has not been applied."""


def is_missing_column_error(exc: Exception) -> bool:
    if not isinstance(exc, APIError):
        return False
    code = str(getattr(exc, "code", "") or "")
    msg = str(getattr(exc, "message", "") or "").lower()
    if code in _MISSING_COLUMN_CODES:
        return not msg or "watcher_" in msg
    return "watcher_" in msg and ("column" in msg or "schema cache" in msg)


def status_columns_available() -> bool:
    with _lock:
        return time.monotonic() >= _state["missing_until"]


def mark_status_columns_missing(exc: Exception) -> None:
    with _lock:
        first = _state["missing_until"] == 0.0
        _state["missing_until"] = time.monotonic() + _RETRY_SECONDS
    if first:
        log.warning(
            "stations.watcher_* columns are missing (%s). Apply migrations/004_watcher_folder_status.sql; "
            "until then watcher folder status is not stored.", getattr(exc, "code", None) or exc,
        )


def mark_status_columns_present() -> None:
    with _lock:
        if _state["missing_until"]:
            _state["missing_until"] = 0.0
            log.info("stations.watcher_* columns are available again.")


def select_stations(sb, base_columns: str, build: Callable) -> list[dict]:
    """Run `build(sb.table("stations").select(cols))` with the watcher columns
    if available, else with base_columns only (watcher fields filled with None)."""
    rows = None
    if status_columns_available():
        cols = base_columns + ", " + ", ".join(ALL_WATCHER_FIELDS)
        try:
            rows = build(sb.table("stations").select(cols)).execute().data or []
        except APIError as e:
            if not is_missing_column_error(e):
                raise
            mark_status_columns_missing(e)
    if rows is None:
        rows = build(sb.table("stations").select(base_columns)).execute().data or []
    for r in rows:
        for f in ALL_WATCHER_FIELDS:
            r.setdefault(f, None)
    return rows


def update_station_status(sb, station_id: str, base: dict, status: dict) -> dict | None:
    """Update base fields plus the watcher status fields, falling back to the
    base fields alone if the status columns do not exist yet. Returns the
    updated row (all columns) when the database sends it back."""
    if status and status_columns_available():
        try:
            res = sb.table("stations").update({**base, **status}).eq("id", station_id).execute()
            return _first(res)
        except APIError as e:
            if not is_missing_column_error(e):
                raise
            mark_status_columns_missing(e)
    res = sb.table("stations").update(base).eq("id", station_id).execute()
    return _first(res)


def _first(res) -> dict | None:
    data = getattr(res, "data", None)
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    return None


# ------------------------------------------------------------------ online light
def _version_tuple(version) -> tuple[int, ...] | None:
    m = re.match(r"^\s*(\d+)\.(\d+)(?:\.(\d+))?", str(version or ""))
    return tuple(int(p or 0) for p in m.groups()) if m else None


def online_seconds_for(version, configured: float) -> float:
    """Seconds since the last heartbeat for which a watcher counts as online."""
    v = _version_tuple(version)
    if v is None or v < FAST_HEARTBEAT_VERSION:
        return max(float(configured), LEGACY_ONLINE_SECONDS)
    return float(configured)


def watcher_presence(row: dict, now: datetime, configured: float) -> dict:
    """watcher_online plus what the website needs to let a cached answer expire
    by itself: the heartbeat's age (server clock, so browser clock skew does not
    matter) and the threshold that applies to this watcher."""
    limit = online_seconds_for(row.get("watcher_version"), configured)
    seen = parse_ts(row.get("last_seen_at"))
    if seen is None:
        return {"watcher_online": False, "watcher_seen_seconds_ago": None, "watcher_online_seconds": limit}
    age = max(0.0, (now - seen).total_seconds())
    return {"watcher_online": age <= limit, "watcher_seen_seconds_ago": round(age, 1), "watcher_online_seconds": limit}


def offline_last_seen(now: datetime, configured: float, version) -> datetime:
    """A last_seen_at that already reads as offline: used when the watcher says
    it is stopping, so the light turns red now instead of after the timeout."""
    return now - timedelta(seconds=online_seconds_for(version, configured) + 1.0)


# ------------------------------------------------------------------ commands
def _now() -> datetime:
    return datetime.now(timezone.utc)


def live_command(row: dict | None, now: datetime | None = None) -> str | None:
    """The pending command on a station row, or None (none / unknown / expired)."""
    if not row:
        return None
    cmd = row.get("watcher_command")
    if cmd not in COMMANDS:
        return None
    at = parse_ts(row.get("watcher_command_at"))
    if at is not None and ((now or _now()) - at).total_seconds() > COMMAND_TTL_SECONDS:
        return None
    return cmd


def public_watcher_fields(row: dict, now: datetime | None = None) -> dict:
    """Watcher fields for station objects returned to the website."""
    out = {f: row.get(f) for f in WATCHER_STATUS_FIELDS}
    out["watcher_command"] = live_command(row, now)
    return out


def apply_public_watcher_fields(row: dict, now: datetime | None = None) -> dict:
    """In place: replace the internal command columns with `watcher_command`."""
    fields = public_watcher_fields(row, now)
    for f in WATCHER_COMMAND_FIELDS:
        row.pop(f, None)
    row.update(fields)
    return row


def set_command(sb, station_id: str, command: str) -> str:
    """Store a pending command for the station's watcher. Returns its id.
    Raises StatusColumnsMissing when migration 004 has not been applied."""
    if command not in COMMANDS:
        raise ValueError(f"unknown command {command!r}")
    command_id = uuid.uuid4().hex
    try:
        sb.table("stations").update({
            "watcher_command": command,
            "watcher_command_id": command_id,
            "watcher_command_at": _now().isoformat(),
        }).eq("id", station_id).execute()
    except APIError as e:
        if not is_missing_column_error(e):
            raise
        mark_status_columns_missing(e)
        raise StatusColumnsMissing() from e
    mark_status_columns_present()
    return command_id


def clear_command(sb, station_id: str, command_id: str | None) -> None:
    """Clear the pending command, but only if it is still the one given (a
    newer click must survive). Best effort."""
    try:
        q = sb.table("stations").update({
            "watcher_command": None, "watcher_command_id": None, "watcher_command_at": None,
        }).eq("id", station_id)
        q = q.eq("watcher_command_id", command_id) if command_id else q.is_("watcher_command_id", "null")
        q.execute()
    except APIError as e:
        if is_missing_column_error(e):
            mark_status_columns_missing(e)
            return
        log.exception("could not clear watcher command for station %s", station_id)


def command_for_heartbeat(sb, station_id: str, row: dict | None, command_done: str | None,
                          has_open_batch: bool, now: datetime | None = None) -> tuple[str | None, str | None]:
    """Decide which command (if any) this heartbeat hands to the watcher.
    `row` is the station row after the heartbeat update. Clears the command
    when the watcher reports it done, when it expired, or when it is a resend
    while no batch is open (the pages would only be set aside again)."""
    if not row or not row.get("watcher_command"):
        return None, None
    cid = row.get("watcher_command_id")
    cmd = live_command(row, now)
    if command_done and cid and command_done == cid:
        clear_command(sb, station_id, cid)
        return None, None
    if cmd is None or (cmd == CMD_RESEND and not has_open_batch):
        clear_command(sb, station_id, cid)
        return None, None
    return cmd, cid
