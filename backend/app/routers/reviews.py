"""Website review page: the forms of one batch that need a person
(failed_scan_queue rows with batch_id) and the website import errors
(import_errors rows with task_id 'web:<batch_id>:...'), refreshed live while
pages are still arriving, and the actions on them.

Corrections reuse the n8n correction webhooks the Flutter app already uses
('sast-manual-correction', 'import-error-correction'); nothing is written to
the raw results table from here.

Every edit carries the review_version the reviewer was looking at
(migration 005 bumps it on every content/status change, whoever makes it),
so an outdated edit gets 409 stale_row instead of overwriting someone else's
work. Before migration 005 the lists still load (read-only, always in full)
and every action answers 503 migration_required.

Access: SAO_ADMIN everything; SAO_STAFF the rows they own, the batches they
started and the batch of the laptop they are logged in at.
"""
from __future__ import annotations

import base64
import binascii
import logging
import threading
import time
from typing import Any, Callable

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from postgrest.exceptions import APIError
from pydantic import BaseModel, Field

from app import review_logic as rl
from app.audit import audit
from app.db import get_settings, get_supabase, is_valid_uuid
from app.logic import parse_ts, sniff_image_type
from app.security import INSTRUCTOR_ROLES, CurrentUser, require_staff, role_maps

router = APIRouter()
log = logging.getLogger("ievaluate.reviews")

FSQ = "failed_scan_queue"
IE = "import_errors"
MAX_ROWS = 1000
BATCH_COLUMNS = "id, station_id, instructor_id, subject_id, term_id, status, created_by, created_at"

NOT_YOUR_BATCH_MSG = (
    "Only the staff member who started this batch, the person logged in at its laptop, "
    "or an SAO admin can review these forms."
)
NOT_WEBSITE_MSG = "This form isn't from a website batch. Review it in the iEvaluate app."


# ------------------------------------------------------------------ migration 005 state
# The list is polled every few seconds: after a "column does not exist" the
# review columns are only tried again every 5 minutes (actions always try).
_RETRY_SECONDS = 300.0
_mig = {"missing_until": 0.0, "warned": False}
_mig_lock = threading.Lock()


def migration_available() -> bool:
    with _mig_lock:
        return time.monotonic() >= _mig["missing_until"]


def mark_migration_missing(exc: Exception) -> None:
    with _mig_lock:
        first = not _mig["warned"]
        _mig["missing_until"] = time.monotonic() + _RETRY_SECONDS
        _mig["warned"] = True
    if first:
        log.warning("review columns are missing (%s). Apply migrations/005_scan_review_queue.sql; "
                    "until then the review page is read-only.", getattr(exc, "code", None) or exc)


def mark_migration_present() -> None:
    with _mig_lock:
        if _mig["missing_until"] or _mig["warned"]:
            log.info("review columns (migration 005) are available.")
        _mig["missing_until"] = 0.0
        _mig["warned"] = False


# ------------------------------------------------------------------ n8n client
_client: httpx.Client | None = None
_client_lock = threading.Lock()


def _n8n() -> httpx.Client:
    global _client
    with _client_lock:
        if _client is None:
            s = get_settings()
            _client = httpx.Client(
                http2=False,
                follow_redirects=False,
                timeout=httpx.Timeout(s.n8n_correction_timeout_seconds, connect=10.0),
            )
        return _client


# ------------------------------------------------------------------ small helpers
def _err(status: int, code: str, message: str, **extra) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message, **extra})


def _conflict(c: rl.ReviewConflict) -> HTTPException:
    return HTTPException(status_code=c.status, detail=c.detail())


def _migration_required() -> HTTPException:
    return _err(503, "migration_required", rl.MIGRATION_REQUIRED_MSG)


def _first(res) -> dict | None:
    data = getattr(res, "data", None)
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    return None


def _cols(base: str, review: str, ready: bool) -> str:
    return f"{base}, {review}" if ready else base


# ------------------------------------------------------------------ batches + access
def _load_batch(sb, batch_id: str) -> dict:
    if not is_valid_uuid(batch_id):
        raise _err(404, "batch_not_found", "Batch not found.")
    rows = sb.table("scan_batches").select(BATCH_COLUMNS).eq("id", batch_id).limit(1).execute().data
    if not rows:
        raise _err(404, "batch_not_found", "Batch not found.")
    return rows[0]


def _has_batch_access(sb, batch: dict, user: CurrentUser) -> bool:
    """Admin, the staff member who started the batch, or the one logged in at its laptop."""
    if user.is_admin or str(batch.get("created_by") or "") == user.id:
        return True
    if not batch.get("station_id"):
        return False
    st = sb.table("stations").select("current_staff_id").eq("id", batch["station_id"]).limit(1).execute().data
    return bool(st and str(st[0].get("current_staff_id") or "") == user.id)


def _require_queue_access(sb, row: dict, user: CurrentUser) -> dict:
    batch = _load_batch(sb, str(row.get("batch_id")))
    if user.is_admin or str(row.get("user_id") or "") == user.id or _has_batch_access(sb, batch, user):
        return batch
    raise _err(403, "not_your_batch", NOT_YOUR_BATCH_MSG)


def _require_import_access(sb, row: dict, user: CurrentUser) -> dict:
    batch_id = rl.batch_id_from_task(row.get("task_id"))
    if not batch_id:
        raise _err(404, "not_website", NOT_WEBSITE_MSG)
    batch = _load_batch(sb, batch_id)
    if _has_batch_access(sb, batch, user):
        return batch
    raise _err(403, "not_your_batch", NOT_YOUR_BATCH_MSG)


# Names shown with the batch (instructor, subject, term, laptop) change rarely;
# the list is polled every 3 s per viewer, so they are cached for a minute.
_CTX_TTL_SECONDS = 60.0
_ctx_cache: dict[tuple, tuple[float, dict]] = {}
_ctx_lock = threading.Lock()


def _batch_context(sb, batch: dict) -> dict:
    key = tuple(batch.get(k) for k in ("id", "instructor_id", "subject_id", "term_id", "station_id"))
    now = time.monotonic()
    with _ctx_lock:
        hit = _ctx_cache.get(key)
        if hit and hit[0] > now:
            return hit[1]
    ctx = _load_batch_context(sb, batch)
    with _ctx_lock:
        if len(_ctx_cache) > 500:
            _ctx_cache.clear()
        _ctx_cache[key] = (now + _CTX_TTL_SECONDS, ctx)
    return ctx


def _load_batch_context(sb, batch: dict) -> dict:
    inst = {}
    if batch.get("instructor_id"):
        r = sb.table("user_info").select("id, first_name, last_name, is_placeholder").eq("id", batch["instructor_id"]).limit(1).execute().data
        inst = r[0] if r else {}
    subj = {}
    if batch.get("subject_id"):
        r = sb.table("subjects").select("id, subject_code, subject_name").eq("id", batch["subject_id"]).limit(1).execute().data
        subj = r[0] if r else {}
    term_label = None
    if batch.get("term_id"):
        t = sb.table("academic_terms").select("semester, academic_year").eq("id", batch["term_id"]).limit(1).execute().data
        if t:
            term_label = f"{t[0].get('semester') or ''} {t[0].get('academic_year') or ''}".strip() or None
    station_label = None
    if batch.get("station_id"):
        s = sb.table("stations").select("label").eq("id", batch["station_id"]).limit(1).execute().data
        station_label = s[0].get("label") if s else None
    return {
        "instructor": {"id": batch.get("instructor_id"), "name": rl.full_name(inst) or None,
                       "is_placeholder": rl._truthy(inst.get("is_placeholder"))},
        "subject": {"id": batch.get("subject_id"), "code": subj.get("subject_code"), "name": subj.get("subject_name")},
        "term": {"id": batch.get("term_id"), "label": term_label},
        "station": {"id": batch.get("station_id"), "label": station_label},
    }


def _names(sb, ids: list[str]) -> dict[str, str]:
    if not ids:
        return {}
    rows = sb.table("user_info").select("id, first_name, last_name").in_("id", ids).execute().data or []
    return {str(r["id"]): rl.full_name(r) for r in rows if r.get("id")}


# ------------------------------------------------------------------ lookups used by actions
def _creditable_instructor(sb, instructor_id: str) -> dict | None:
    """Same rule the n8n correction flow uses to credit a form: a placeholder
    account, or an approved account with a FULL-TIME / PART-TIME /
    DEPARTMENT_HEAD role in department_table."""
    if not is_valid_uuid(instructor_id):
        return None
    rows = (sb.table("user_info").select("id, first_name, last_name, account_status, is_placeholder")
            .eq("id", instructor_id).limit(1).execute().data)
    if not rows:
        return None
    ui = rows[0]
    if rl._truthy(ui.get("is_placeholder")):
        return ui
    if str(ui.get("account_status") or "").strip().lower() != "approved":
        return None
    by_name, _ = role_maps()
    role_ids = [by_name[r] for r in INSTRUCTOR_ROLES if r in by_name]
    if not role_ids:
        return None
    dt = sb.table("department_table").select("id").eq("user_id", instructor_id).in_("roles", role_ids).limit(1).execute().data
    return ui if dt else None


def _subject(sb, subject_id: str) -> dict | None:
    if not is_valid_uuid(subject_id):
        return None
    rows = sb.table("subjects").select("id, subject_code, subject_name").eq("id", subject_id).limit(1).execute().data
    return rows[0] if rows else None


def _other_raw_row(sb, instructor_id: str, subject_id: str, term_id: str | None, student_id: str,
                   task_id: str | None) -> dict | None:
    """A stored result of ANOTHER form (different or no task_id) for the same
    instructor, subject, term and student; crediting would overwrite it."""
    if not student_id or not term_id:
        return None
    rows = (sb.table("sast_all_raw_data_survey").select("task_id")
            .eq("instructor_ID", instructor_id).eq("subject_id", subject_id)
            .eq("term_id", term_id).eq("student_id", student_id).limit(5).execute().data or [])
    for r in rows:
        if r.get("task_id") != task_id:
            return r
    return None


def _pending_import_error_for_task(sb, task_id: str | None) -> bool:
    if not task_id:
        return False
    try:
        rows = sb.table(IE).select("id").eq("task_id", task_id).eq("status", "pending").limit(1).execute().data
    except APIError:
        log.exception("could not check import_errors for task %s", task_id)
        return False
    return bool(rows)


def _ratings_or_400(value: Any) -> dict:
    try:
        return rl.validate_ratings(value)
    except ValueError as e:
        raise _err(400, "invalid_ratings", f"Each rating must be 1 to 5, or left blank ({e}).")


def _check_people(sb, instructor_id: str, subject_id: str) -> tuple[dict, dict]:
    instructor = _creditable_instructor(sb, instructor_id)
    if not instructor:
        raise _err(400, "invalid_instructor",
                   "That person can't be credited: they are not an active instructor. Pick someone else.")
    subject = _subject(sb, subject_id)
    if not subject:
        raise _err(400, "invalid_subject", "Subject not found. Pick it again from the list.")
    return instructor, subject


def _would_overwrite(existing: dict, student_id: str) -> HTTPException:
    task = existing.get("task_id")
    return _err(409, "would_overwrite",
                f"A form from student {student_id} is already stored for this instructor, subject and term"
                + (" (another scanned page)." if task else " (from the phone app or a sheet import).")
                + " Saving would replace it. Confirm only if this form should replace that one.",
                existing_task_id=task, student_id=student_id)


# ------------------------------------------------------------------ row loaders
def _get_queue_row(sb, row_id: str, need_review: bool) -> dict:
    if not is_valid_uuid(row_id):
        raise _err(404, "not_found", "This form was not found. It may have been removed.")
    try:
        rows = (sb.table(FSQ).select(_cols(rl.QUEUE_BASE_COLUMNS, rl.QUEUE_REVIEW_COLUMNS, need_review))
                .eq("id", row_id).limit(1).execute().data)
    except APIError as e:
        if need_review and rl.is_missing_review_column(e):
            mark_migration_missing(e)
            raise _migration_required()
        raise
    if need_review:
        mark_migration_present()
    if not rows:
        raise _err(404, "not_found", "This form was not found. It may have been removed.")
    if not rows[0].get("batch_id"):
        raise _err(404, "not_website", NOT_WEBSITE_MSG)
    return rows[0]


def _get_import_error(sb, error_id: int, need_review: bool) -> dict:
    try:
        rows = (sb.table(IE).select(_cols(rl.IMPORT_BASE_COLUMNS, rl.IMPORT_REVIEW_COLUMNS, need_review))
                .eq("id", error_id).limit(1).execute().data)
    except APIError as e:
        if need_review and rl.is_missing_review_column(e):
            mark_migration_missing(e)
            raise _migration_required()
        raise
    if need_review:
        mark_migration_present()
    if not rows:
        raise _err(404, "not_found", "This import error was not found. It may have been removed.")
    if not rl.batch_id_from_task(rows[0].get("task_id")):
        raise _err(404, "not_website", NOT_WEBSITE_MSG)
    return rows[0]


def _serialize_queue(sb, row: dict, batch: dict) -> dict:
    return rl.serialize_queue_row(row, _batch_context(sb, batch), _names(sb, rl.people_ids([row], [])))


def _serialize_import(sb, row: dict) -> dict:
    return rl.serialize_import_error(row, _names(sb, rl.people_ids([], [row])))


# ------------------------------------------------------------------ list
def _scoped_queue(q, batch_id: str, user: CurrentUser, full_access: bool):
    q = q.eq("batch_id", batch_id)
    return q if full_access else q.eq("user_id", user.id)


def _load_lists(sb, batch_id: str, user: CurrentUser, full_access: bool, since_dt):
    """Queue rows + import errors (changed since the cursor when possible) and
    the rows with a website correction in flight (always, so they can be
    finalised). Falls back to the base columns before migration 005."""
    web_like = f"web:{batch_id}:%"
    attempts = (True, False) if migration_available() else (False,)
    for ready in attempts:
        try:
            def queue_q(q):
                q = _scoped_queue(q, batch_id, user, full_access)
                if ready and since_dt is not None:
                    q = q.gte("updated_at", rl.since_filter_value(since_dt))
                return q.order("created_at").limit(MAX_ROWS)

            def import_q(q):
                q = q.like("task_id", web_like)
                if ready and since_dt is not None:
                    q = q.gte("updated_at", rl.since_filter_value(since_dt))
                return q.order("created_at").limit(MAX_ROWS)

            queue = queue_q(sb.table(FSQ).select(_cols(rl.QUEUE_BASE_COLUMNS, rl.QUEUE_REVIEW_COLUMNS, ready))).execute().data or []
            imports = []
            if full_access:
                imports = import_q(sb.table(IE).select(_cols(rl.IMPORT_BASE_COLUMNS, rl.IMPORT_REVIEW_COLUMNS, ready))).execute().data or []
            flight_q, flight_ie = [], []
            if ready:
                flight_q = (_scoped_queue(sb.table(FSQ).select(f"{rl.QUEUE_BASE_COLUMNS}, {rl.QUEUE_REVIEW_COLUMNS}"), batch_id, user, full_access)
                            .in_("review_state", list(rl.REVIEW_STATES)).limit(200).execute().data or [])
                if full_access:
                    flight_ie = (sb.table(IE).select(f"{rl.IMPORT_BASE_COLUMNS}, {rl.IMPORT_REVIEW_COLUMNS}")
                                 .like("task_id", web_like).in_("review_state", list(rl.REVIEW_STATES))
                                 .limit(200).execute().data or [])
                mark_migration_present()
            return queue, imports, flight_q, flight_ie, ready
        except APIError as e:
            if not ready or not rl.is_missing_review_column(e):
                raise
            mark_migration_missing(e)
    return [], [], [], [], False  # pragma: no cover


def _finalise_queue_row(sb, row: dict, now) -> dict | None:
    """Settle a website correction once n8n's own update is visible, or give
    up on it after 10 minutes. Returns the updated row, or None (unchanged)."""
    state, status = row.get("review_state"), row.get("status")
    if state not in rl.REVIEW_STATES:
        return None
    corr = rl.as_dict(row.get("review_correction"))
    if status == "validated":
        vals: dict = {"review_state": None, "review_error": None}
        if state == "submitting":
            if not row.get("review_action"):
                vals["review_action"] = corr.get("action") if corr.get("action") in ("approved", "rerouted") else "approved"
            if not row.get("validated_by") and is_valid_uuid(str(corr.get("reviewed_by") or "")):
                vals["validated_by"] = corr["reviewed_by"]
    elif status != rl.QUEUE_PENDING:
        vals = {"review_state": None}
    elif state == "submitting" and rl.submission_stale(row, now):
        vals = {"review_state": "submit_failed", "review_error": rl.NOT_CONFIRMED_MSG}
    else:
        return None
    try:
        res = sb.table(FSQ).update(vals).eq("id", row["id"]).eq("review_state", state).execute()
    except APIError:
        log.exception("could not finalise review of queue row %s", row.get("id"))
        return None
    return _first(res) or {**row, **vals}


def _finalise_import_row(sb, row: dict, now) -> dict | None:
    state, status = row.get("review_state"), row.get("status")
    if state not in rl.REVIEW_STATES:
        return None
    if status != rl.IMPORT_PENDING:
        vals = {"review_state": None, "review_error": None}
    elif state == "submitting" and rl.submission_stale(row, now):
        vals = {"review_state": "submit_failed", "review_error": rl.NOT_CONFIRMED_MSG}
    else:
        return None
    try:
        res = sb.table(IE).update(vals).eq("id", row["id"]).eq("review_state", state).execute()
    except APIError:
        log.exception("could not finalise review of import error %s", row.get("id"))
        return None
    return _first(res) or {**row, **vals}


def _merge_rows(rows: list[dict], extra: list[dict], finalise: Callable) -> list[dict]:
    by_id = {r.get("id"): r for r in rows}
    for r in extra:
        by_id.setdefault(r.get("id"), r)
    for rid, r in list(by_id.items()):
        done = finalise(r)
        if done:
            by_id[rid] = done
    return sorted(by_id.values(), key=lambda r: (str(r.get("created_at") or ""), str(r.get("id"))))


def _count(q) -> int:
    return q.execute().count or 0


@router.get("/batch/{batch_id}")
def list_batch_reviews(batch_id: str, since: str | None = Query(default=None, max_length=64),
                       user: CurrentUser = Depends(require_staff)):
    sb = get_supabase()
    settings = get_settings()
    now = rl.now_utc()  # the next cursor: taken before any query
    batch = _load_batch(sb, batch_id)
    full_access = _has_batch_access(sb, batch, user)
    since_dt = parse_ts(since) if since else None

    queue, imports, flight_q, flight_ie, ready = _load_lists(sb, batch_id, user, full_access, since_dt)
    if not full_access and since_dt is None and not queue:
        raise _err(403, "not_your_batch", NOT_YOUR_BATCH_MSG)
    if ready:
        queue = _merge_rows(queue, flight_q, lambda r: _finalise_queue_row(sb, r, now))
        imports = _merge_rows(imports, flight_ie, lambda r: _finalise_import_row(sb, r, now))

    def fsq_count():
        return _scoped_queue(sb.table(FSQ).select("id", count="exact", head=True), batch_id, user, full_access)

    # Pending website import errors of this batch, by task. A credited form can
    # land here a little after its correction was answered (n8n answers before
    # the shared flow runs), so the page matches them on every poll instead of
    # trusting the approve call's outcome.
    pending_tasks = sorted({
        str(r["task_id"]) for r in (
            sb.table(IE).select("task_id").like("task_id", f"web:{batch_id}:%")
            .eq("status", rl.IMPORT_PENDING).limit(MAX_ROWS).execute().data or []
        ) if r.get("task_id")
    })
    if not full_access:
        own = {str(r.get("task_id")) for r in (
            _scoped_queue(sb.table(FSQ).select("task_id"), batch_id, user, False).limit(MAX_ROWS).execute().data or [])}
        pending_tasks = [t for t in pending_tasks if t in own]

    counts = {
        "queue_pending": _count(fsq_count().eq("status", rl.QUEUE_PENDING)),
        "queue_done": _count(fsq_count().neq("status", rl.QUEUE_PENDING)),
        "import_pending": len(pending_tasks) if full_access else 0,
    }
    ctx = _batch_context(sb, batch)
    names = _names(sb, rl.people_ids(queue, imports))
    stamp = rl.iso(now)
    return {
        "server_time": stamp,
        "cursor": stamp,
        "full": since_dt is None or not ready,
        "migration_ready": ready,
        "correction_ready": bool(getattr(settings, "n8n_correction_webhook_url", "")),
        "import_correction_ready": bool(getattr(settings, "n8n_import_error_webhook_url", "")),
        "access": "full" if full_access else "own",
        "batch": {
            "id": batch["id"],
            "status": batch.get("status"),
            "created_at": batch.get("created_at"),
            "instructor": {"id": ctx["instructor"]["id"], "name": ctx["instructor"]["name"]},
            "subject": ctx["subject"],
            "term": ctx["term"],
            "station": ctx["station"],
        },
        "counts": counts,
        # task_ids of forms that were credited but are now waiting in Import errors.
        "import_pending_tasks": pending_tasks,
        "queue": [rl.serialize_queue_row(r, ctx, names, now) for r in queue],
        "import_errors": [rl.serialize_import_error(r, names, now) for r in imports],
    }


# ------------------------------------------------------------------ images
def _image_bytes(sb, scan_image_id: Any, task_id: Any) -> bytes | None:
    """The stored scan picture: by scan_image_id, else the oldest non-empty
    image saved for the task (the same preference the n8n lookup uses)."""
    candidates = []
    if is_valid_uuid(str(scan_image_id or "")):
        candidates += sb.table("scan_error_images").select("image_base64").eq("id", str(scan_image_id)).limit(1).execute().data or []
    for c in candidates:
        raw = _decode_image(c.get("image_base64"))
        if raw:
            return raw
    if task_id:
        rows = (sb.table("scan_error_images").select("image_base64").eq("task_id", str(task_id))
                .order("created_at").limit(3).execute().data or [])
        for c in rows:
            raw = _decode_image(c.get("image_base64"))
            if raw:
                return raw
    return None


def _decode_image(b64: Any) -> bytes | None:
    if not isinstance(b64, str) or not b64.strip():
        return None
    s = b64.strip()
    if s.startswith("data:"):
        s = s.split(",", 1)[-1]
    try:
        raw = base64.b64decode(s, validate=False)
    except (binascii.Error, ValueError):
        return None
    return raw if sniff_image_type(raw) else None


def _image_response(raw: bytes | None) -> Response:
    if not raw:
        raise _err(404, "no_image", "No picture was saved for this form.")
    kind = sniff_image_type(raw)
    return Response(content=raw, media_type="image/png" if kind == "png" else "image/jpeg",
                    headers={"Cache-Control": "private, max-age=300"})


@router.get("/queue/{row_id}/image")
def queue_image(row_id: str, user: CurrentUser = Depends(require_staff)):
    sb = get_supabase()
    row = _get_queue_row(sb, row_id, need_review=False)
    _require_queue_access(sb, row, user)
    return _image_response(_image_bytes(sb, row.get("scan_image_id"), row.get("task_id")))


@router.get("/import-errors/{error_id}/image")
def import_error_image(error_id: int, user: CurrentUser = Depends(require_staff)):
    sb = get_supabase()
    row = _get_import_error(sb, error_id, need_review=False)
    _require_import_access(sb, row, user)
    return _image_response(_image_bytes(sb, row.get("scan_image_id"), row.get("task_id")))


# ------------------------------------------------------------------ claim
class ClaimBody(BaseModel):
    release: bool = False


@router.post("/queue/{row_id}/claim")
def claim_queue_row(row_id: str, body: ClaimBody | None = None, user: CurrentUser = Depends(require_staff)):
    """Advisory "I'm looking at this form" marker (expires after 5 minutes;
    the editor refreshes it every minute). Never changes review_version and
    never takes over someone else's live claim."""
    sb = get_supabase()
    now = rl.now_utc()
    row = _get_queue_row(sb, row_id, need_review=True)
    batch = _require_queue_access(sb, row, user)
    release = bool(body and body.release)
    claimed = False
    if release:
        if str(row.get("review_claimed_by") or "") == user.id:
            row = _first(sb.table(FSQ).update({"review_claimed_by": None, "review_claimed_at": None})
                         .eq("id", row["id"]).eq("review_claimed_by", user.id).execute()) or row
    elif row.get("status") == rl.QUEUE_PENDING:
        other = str(row.get("review_claimed_by") or "")
        if not other or other == user.id or not rl.claim_is_live(row, now):
            row = _first(sb.table(FSQ).update({"review_claimed_by": user.id, "review_claimed_at": rl.iso(now)})
                         .eq("id", row["id"]).execute()) or {**row, "review_claimed_by": user.id, "review_claimed_at": rl.iso(now)}
            claimed = True
    out = _serialize_queue(sb, row, batch)
    return {"ok": True, "claimed": claimed, "claimed_by": out["claimed_by"], "row": out}


# ------------------------------------------------------------------ sending a correction
def _post_correction(url: str, payload: dict) -> tuple[str, str]:
    """-> ('ok', '') | ('unknown', '') when n8n may still be working on it
    (timeout / dropped connection) | ('failed', plain-English reason)."""
    secret = getattr(get_settings(), "n8n_webhook_secret", "") or ""
    headers = {"X-Webhook-Secret": secret} if secret else {}
    try:
        resp = _n8n().post(url, json=payload, headers=headers)
    except (httpx.ConnectError, httpx.ConnectTimeout) as e:
        log.warning("correction webhook unreachable: %s", type(e).__name__)
        return "failed", "Marking could not be reached, so the correction was not sent. Try again in a minute."
    except httpx.TimeoutException:
        log.warning("correction webhook timed out (task %s); left submitting", payload.get("task_id"))
        return "unknown", ""
    except httpx.HTTPError as e:
        log.warning("correction webhook connection dropped (%s, task %s); left submitting", type(e).__name__, payload.get("task_id"))
        return "unknown", ""
    if resp.status_code in (401, 403):
        log.error("correction webhook rejected the secret (HTTP %s)", resp.status_code)
        return "failed", "Marking refused the server's credentials (a setup problem). Ask IT to check N8N_WEBHOOK_SECRET."
    if not 200 <= resp.status_code < 300:
        log.warning("correction webhook returned HTTP %s (task %s)", resp.status_code, payload.get("task_id"))
        return "failed", (f"Marking answered with an error (HTTP {resp.status_code}), so the correction may not be saved. "
                          "Check the form and send it again.")
    try:
        body = resp.json()
    except ValueError:
        body = None
    if isinstance(body, list) and len(body) == 1:
        body = body[0]
    if isinstance(body, dict) and body.get("success") is False:
        return "failed", "Marking could not process the correction. Check the form and send it again."
    return "ok", ""


def _mark_submit_failed(sb, table: str, row_id, message: str) -> dict | None:
    try:
        return _first(sb.table(table).update({"review_state": "submit_failed", "review_error": message})
                      .eq("id", row_id).eq("review_state", "submitting").execute())
    except APIError:
        log.exception("could not mark %s %s as submit_failed", table, row_id)
        return None


def _raise_current_conflict(fresh: dict, expected_version: int, pending_status: str) -> None:
    """The conditional update matched nothing: say why, from the row as it is now."""
    try:
        rl.check_can_act(fresh, expected_version, pending_status=pending_status)
    except rl.ReviewConflict as c:
        raise _conflict(c)
    raise _err(409, "stale_row", "This form changed while you were editing it. Look at the new version, then try again.",
               current_version=rl.version_of(fresh))


# ------------------------------------------------------------------ queue: approve / reject
class ApproveBody(BaseModel):
    expected_version: int = Field(ge=0, le=2_000_000_000)
    instructor_id: str = Field(min_length=1, max_length=64)
    subject_id: str = Field(min_length=1, max_length=64)
    student_id: str = Field(default="", max_length=64)
    remarks: str = Field(default="", max_length=4000)
    ratings: dict[str, Any] = Field(default_factory=dict)
    confirm_overwrite: bool = False


@router.post("/queue/{row_id}/approve")
def approve_queue_row(row_id: str, body: ApproveBody, user: CurrentUser = Depends(require_staff)):
    sb = get_supabase()
    settings = get_settings()
    now = rl.now_utc()
    row = _get_queue_row(sb, row_id, need_review=True)
    batch = _require_queue_access(sb, row, user)
    try:
        rl.check_can_act(row, body.expected_version, now)
    except rl.ReviewConflict as c:
        raise _conflict(c)
    ratings = _ratings_or_400(body.ratings)
    instructor, subject = _check_people(sb, body.instructor_id, body.subject_id)
    url = getattr(settings, "n8n_correction_webhook_url", "")
    if not url:
        raise _err(503, "correction_not_configured",
                   "Sending corrections isn't set up on this server yet. Ask IT to set N8N_CORRECTION_WEBHOOK_URL.")
    student_id = rl.clean_student_id(body.student_id)
    remarks = rl.clean_remarks(body.remarks)
    term_id = row.get("term_id") or batch.get("term_id")
    if student_id and not body.confirm_overwrite:
        existing = _other_raw_row(sb, instructor["id"], subject["id"], term_id, student_id, row.get("task_id"))
        if existing is not None:
            raise _would_overwrite(existing, student_id)

    action = rl.review_action_for(instructor["id"], subject["id"], batch)
    stamp = rl.iso(now)
    correction = {
        "action": action,
        "instructor_id": instructor["id"], "instructor_name": rl.full_name(instructor),
        "subject_id": subject["id"], "subject_code": subject.get("subject_code"),
        "student_id": student_id, "remarks": remarks, "ratings": ratings, "term_id": term_id,
        "confirm_overwrite": bool(body.confirm_overwrite),
        "reviewed_by": user.id, "reviewed_by_name": user.full_name, "submitted_at": stamp,
    }
    started = _first(sb.table(FSQ).update({
        "review_state": "submitting", "review_submitted_at": stamp, "review_error": None,
        "review_correction": correction, "review_claimed_by": user.id, "review_claimed_at": stamp,
    }).eq("id", row["id"]).eq("review_version", body.expected_version).eq("status", rl.QUEUE_PENDING).execute())
    if not started:
        _raise_current_conflict(_get_queue_row(sb, row_id, need_review=True), body.expected_version, rl.QUEUE_PENDING)

    payload = rl.correction_payload(row, instructor, subject, ratings, student_id, remarks, term_id, user.id, stamp)
    result, message = _post_correction(url, payload)
    meta = {"failed_scan_id": row["id"], "task_id": row.get("task_id"), "batch_id": row.get("batch_id"),
            "action": action, "instructor_id": instructor["id"], "subject_id": subject["id"]}
    if result == "failed":
        failed = _mark_submit_failed(sb, FSQ, row["id"], message) or {**started, "review_state": "submit_failed", "review_error": message}
        audit(user.id, "scan_review_submit_failed", meta)
        raise _err(502, "correction_failed", message, row=_serialize_queue(sb, failed, batch))
    if result == "unknown":
        audit(user.id, "scan_review_submitted", meta)
        return {"ok": True, "outcome": "submitted", "row": _serialize_queue(sb, started, batch)}

    done = _first(sb.table(FSQ).update({
        "status": "validated", "review_state": None, "review_error": None, "review_action": action,
        "validated_by": user.id, "validated_at": rl.iso(rl.now_utc()),
        "review_claimed_by": None, "review_claimed_at": None,
    }).eq("id", row["id"]).eq("review_state", "submitting").execute())
    if not done:
        done = _get_queue_row(sb, row_id, need_review=True)
    # n8n answers right after marking the row validated and runs the shared flow
    # (raw row, or an import error when the instructor/subject can't be stored)
    # afterwards, so an import error usually shows up a little later: the list
    # reports it in import_pending_tasks. This only catches one that is already there.
    outcome = "import_error" if _pending_import_error_for_task(sb, row.get("task_id")) else "credited"
    audit(user.id, "scan_review_approved", {**meta, "outcome": outcome})
    return {"ok": True, "outcome": outcome, "row": _serialize_queue(sb, done, batch)}


class RejectBody(BaseModel):
    expected_version: int = Field(ge=0, le=2_000_000_000)
    note: str | None = Field(default=None, max_length=500)


@router.post("/queue/{row_id}/reject")
def reject_queue_row(row_id: str, body: RejectBody, user: CurrentUser = Depends(require_staff)):
    sb = get_supabase()
    now = rl.now_utc()
    row = _get_queue_row(sb, row_id, need_review=True)
    batch = _require_queue_access(sb, row, user)
    try:
        rl.check_can_act(row, body.expected_version, now)
    except rl.ReviewConflict as c:
        raise _conflict(c)
    note = " ".join((body.note or "").split())[:500] or None
    done = _first(sb.table(FSQ).update({
        "status": "discarded", "review_action": "rejected", "review_state": None, "review_error": None,
        "review_correction": {"action": "rejected", "note": note, "reviewed_by": user.id,
                              "reviewed_by_name": user.full_name, "at": rl.iso(now)},
        "review_claimed_by": None, "review_claimed_at": None,
    }).eq("id", row["id"]).eq("review_version", body.expected_version).eq("status", rl.QUEUE_PENDING).execute())
    if not done:
        _raise_current_conflict(_get_queue_row(sb, row_id, need_review=True), body.expected_version, rl.QUEUE_PENDING)
    audit(user.id, "scan_review_rejected", {"failed_scan_id": row["id"], "task_id": row.get("task_id"), "batch_id": row.get("batch_id")})
    return {"ok": True, "outcome": "rejected", "row": _serialize_queue(sb, done, batch)}


# ------------------------------------------------------------------ import errors: resolve / discard
class ResolveBody(BaseModel):
    expected_version: int = Field(ge=0, le=2_000_000_000)
    instructor_id: str = Field(min_length=1, max_length=64)
    subject_id: str = Field(min_length=1, max_length=64)
    student_id: str | None = Field(default=None, max_length=64)
    remarks: str | None = Field(default=None, max_length=4000)
    ratings: dict[str, Any] | None = None
    confirm_overwrite: bool = False  # same "replace a stored form" guard as approve


@router.post("/import-errors/{error_id}/resolve")
def resolve_import_error(error_id: int, body: ResolveBody, user: CurrentUser = Depends(require_staff)):
    sb = get_supabase()
    settings = get_settings()
    now = rl.now_utc()
    row = _get_import_error(sb, error_id, need_review=True)
    batch = _require_import_access(sb, row, user)
    try:
        rl.check_can_act(row, body.expected_version, now, pending_status=rl.IMPORT_PENDING)
    except rl.ReviewConflict as c:
        raise _conflict(c)
    ratings = _ratings_or_400(body.ratings) if body.ratings is not None else None
    instructor, subject = _check_people(sb, body.instructor_id, body.subject_id)
    url = getattr(settings, "n8n_import_error_webhook_url", "")
    if not url:
        raise _err(503, "correction_not_configured",
                   "Sending corrections isn't set up on this server yet. Ask IT to set N8N_IMPORT_ERROR_WEBHOOK_URL.")
    student_id = rl.clean_student_id(body.student_id) if body.student_id is not None else None
    remarks = rl.clean_remarks(body.remarks) if body.remarks is not None else None
    term_id = row.get("raw_term_id") or batch.get("term_id")
    check_student = student_id if student_id is not None else rl.serialize_import_error(row)["student_id"]
    if check_student and not body.confirm_overwrite:
        existing = _other_raw_row(sb, instructor["id"], subject["id"], term_id, check_student, row.get("task_id"))
        if existing is not None:
            raise _would_overwrite(existing, check_student)

    stamp = rl.iso(now)
    started = _first(sb.table(IE).update({
        "review_state": "submitting", "review_submitted_at": stamp, "review_error": None,
        "review_claimed_by": user.id, "review_claimed_at": stamp,
    }).eq("id", row["id"]).eq("review_version", body.expected_version).eq("status", rl.IMPORT_PENDING).execute())
    if not started:
        _raise_current_conflict(_get_import_error(sb, error_id, need_review=True), body.expected_version, rl.IMPORT_PENDING)

    raw = rl.corrected_raw_data(row.get("raw_data"), student_id, remarks, ratings)
    payload = rl.import_error_payload(row, instructor, subject, raw, term_id, user.id, stamp)
    result, message = _post_correction(url, payload)
    meta = {"import_error_id": row["id"], "task_id": row.get("task_id"), "instructor_id": instructor["id"], "subject_id": subject["id"]}
    if result == "failed":
        failed = _mark_submit_failed(sb, IE, row["id"], message) or {**started, "review_state": "submit_failed", "review_error": message}
        audit(user.id, "import_error_review_submit_failed", meta)
        raise _err(502, "correction_failed", message, row=_serialize_import(sb, failed))
    if result == "unknown":
        audit(user.id, "import_error_review_submitted", meta)
        return {"ok": True, "outcome": "submitted", "row": _serialize_import(sb, started)}

    done = _first(sb.table(IE).update({
        "status": "resolved", "resolved_at": rl.iso(rl.now_utc()), "resolved_by": user.id,
        "review_state": None, "review_error": None, "review_claimed_by": None, "review_claimed_at": None,
    }).eq("id", row["id"]).eq("review_state", "submitting").execute())
    if not done:
        done = _get_import_error(sb, error_id, need_review=True)
    audit(user.id, "import_error_review_resolved", meta)
    return {"ok": True, "outcome": "credited", "row": _serialize_import(sb, done)}


class DiscardBody(BaseModel):
    expected_version: int = Field(ge=0, le=2_000_000_000)


@router.post("/import-errors/{error_id}/discard")
def discard_import_error(error_id: int, body: DiscardBody, user: CurrentUser = Depends(require_staff)):
    sb = get_supabase()
    now = rl.now_utc()
    row = _get_import_error(sb, error_id, need_review=True)
    _require_import_access(sb, row, user)
    try:
        rl.check_can_act(row, body.expected_version, now, pending_status=rl.IMPORT_PENDING)
    except rl.ReviewConflict as c:
        raise _conflict(c)
    done = _first(sb.table(IE).update({
        "status": "discarded", "review_state": None, "review_error": None,
        "review_claimed_by": None, "review_claimed_at": None,
    }).eq("id", row["id"]).eq("review_version", body.expected_version).eq("status", rl.IMPORT_PENDING).execute())
    if not done:
        _raise_current_conflict(_get_import_error(sb, error_id, need_review=True), body.expected_version, rl.IMPORT_PENDING)
    audit(user.id, "import_error_review_discarded", {"import_error_id": row["id"], "task_id": row.get("task_id")})
    return {"ok": True, "outcome": "discarded", "row": _serialize_import(sb, done)}
