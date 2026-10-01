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
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from postgrest.exceptions import APIError
from pydantic import BaseModel, Field

from app import page_images, page_scores
from app import review_logic as rl
from app import unsaved_pages as uv
from app.audit import audit
from app.batch_access import is_batch_owner
from app.db import get_settings, get_supabase, is_valid_uuid
from app.logic import COUNTER_FOR_STATUS, parse_ts, sniff_image_type
from app.routers import scans as scans_router
from app.security import INSTRUCTOR_ROLES, CurrentUser, require_staff, role_maps

router = APIRouter()
log = logging.getLogger("ievaluate.reviews")

FSQ = "failed_scan_queue"
IE = "import_errors"
MAX_ROWS = 1000
BATCH_COLUMNS = "id, station_id, instructor_id, subject_id, term_id, status, created_by, created_at"

NOT_YOUR_BATCH_MSG = (
    "Only the staff member who started this batch, or an SAO admin, can review these forms."
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
    """Admin, or the staff member who started the batch (app/batch_access.py).
    Being logged in at the batch's laptop no longer grants access: anyone can
    take over a free laptop, and that must not open someone else's batch."""
    return is_batch_owner(batch, user)


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
    # Pages marking couldn't save (migration 008): whoever may act on the batch.
    unsaved_rows, unsaved_pending, unsaved_ready = [], 0, uv.available()
    if full_access:
        unsaved_rows, open_rows, unsaved_ready = _load_unsaved(sb, batch_id, since_dt)
        if unsaved_ready:
            unsaved_rows, open_rows = _settle_unsaved(sb, batch, unsaved_rows, open_rows, settings, now)
        unsaved_pending = sum(1 for r in open_rows if r.get("status") in (uv.NEEDS_REVIEW, uv.SENDING))
    ctx = _batch_context(sb, batch)
    names = _names(sb, sorted(set(rl.people_ids(queue, imports)) | {
        str(r["acted_by"]) for r in unsaved_rows if is_valid_uuid(str(r.get("acted_by") or ""))}))
    stamp = rl.iso(now)
    retry_ready = bool(getattr(settings, "n8n_batch_webhook_url", ""))
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
        # "Couldn't be saved": pages marking didn't store (Try again / Type in the answers).
        "unsaved_ready": unsaved_ready,
        "retry_ready": retry_ready,
        "unsaved_pending": unsaved_pending,
        "unsaved": [uv.serialize(r, names, retry_ready, _max_website_attempts()) for r in unsaved_rows],
    }


# ------------------------------------------------------------------ past batches list (/review)
LIST_BATCH_COLUMNS = f"{BATCH_COLUMNS}, closed_at"
MAX_PENDING_ROWS = 5000   # pending rows read per request (all batches together)
MAX_TOP_BATCHES = 200     # batches with work (or still open) shown above the history
ID_CHUNK = 100            # ids per in_() filter (URL length)


def _review_columns_ready(sb) -> bool:
    """Migration 005 applied? One tiny probe, skipped for 5 minutes after a miss."""
    if not migration_available():
        return False
    try:
        sb.table(FSQ).select("id, review_version").limit(1).execute()
    except APIError as e:
        if not rl.is_missing_review_column(e):
            raise
        mark_migration_missing(e)
        return False
    mark_migration_present()
    return True


def _pending_work(sb) -> dict:
    """Everything still waiting for a person, grouped by batch, in 3 queries
    (same rules as list_batch_reviews): Needs review (pending queue rows + who
    scanned them), Import errors (distinct pending web:<batch>: task_ids),
    Couldn't be saved (needs_review/sending, migration 008)."""
    queue: dict[str, list[str]] = {}
    for r in (sb.table(FSQ).select("batch_id, user_id").eq("status", rl.QUEUE_PENDING)
              .limit(MAX_PENDING_ROWS).execute().data or []):
        if r.get("batch_id"):
            queue.setdefault(str(r["batch_id"]).lower(), []).append(str(r.get("user_id") or ""))
    imports: dict[str, set[str]] = {}
    for r in (sb.table(IE).select("task_id").like("task_id", "web:%").eq("status", rl.IMPORT_PENDING)
              .limit(MAX_PENDING_ROWS).execute().data or []):
        bid = rl.batch_id_from_task(r.get("task_id"))
        if bid:
            imports.setdefault(bid, set()).add(str(r["task_id"]))
    unsaved: dict[str, int] = {}
    ready = uv.available()
    if ready:
        try:
            rows = (sb.table(uv.TABLE).select("batch_id").in_("status", [uv.NEEDS_REVIEW, uv.SENDING])
                    .limit(MAX_PENDING_ROWS).execute().data or [])
        except APIError as e:
            if not uv.is_missing_table_error(e):
                raise
            uv.mark_missing(e)
            rows, ready = [], False
        else:
            uv.mark_present()
        for r in rows:
            if r.get("batch_id"):
                bid = str(r["batch_id"]).lower()
                unsaved[bid] = unsaved.get(bid, 0) + 1
    return {"queue": queue, "imports": imports, "unsaved": unsaved, "unsaved_ready": ready}


def _visible_counts(sb, batch: dict, user: CurrentUser, work: dict) -> tuple[str, dict] | None:
    """The review page's own rule: 'full' (admin / started it) or 'own' (only
    their own pending Needs review rows). None = they couldn't open it."""
    bid = str(batch["id"]).lower()
    users = work["queue"].get(bid, [])
    if _has_batch_access(sb, batch, user):
        return "full", {"queue_pending": len(users), "import_pending": len(work["imports"].get(bid, ())),
                        "unsaved_pending": work["unsaved"].get(bid, 0)}
    own = sum(1 for u in users if u == user.id)
    return ("own", {"queue_pending": own, "import_pending": 0, "unsaved_pending": 0}) if own else None


def _batches_by_id(sb, ids: list[str]) -> list[dict]:
    out: list[dict] = []
    for i in range(0, len(ids), ID_CHUNK):
        out += sb.table("scan_batches").select(LIST_BATCH_COLUMNS).in_("id", ids[i:i + ID_CHUNK]).execute().data or []
    return out


def _names_for_batches(sb, batches: list[dict]) -> dict:
    """Instructor, starter, subject, term and laptop names of many batches in <= 4 queries."""
    def ids(*keys):
        return sorted({str(b[k]) for b in batches for k in keys if b.get(k)})

    def rows(table, cols, wanted):
        out = {}
        for i in range(0, len(wanted), ID_CHUNK):
            for r in sb.table(table).select(cols).in_("id", wanted[i:i + ID_CHUNK]).execute().data or []:
                out[str(r["id"])] = r
        return out

    return {"people": rows("user_info", "id, first_name, last_name", ids("instructor_id", "created_by")),
            "subjects": rows("subjects", "id, subject_code, subject_name", ids("subject_id")),
            "terms": rows("academic_terms", "id, semester, academic_year", ids("term_id")),
            "stations": rows("stations", "id, label", ids("station_id"))}


def _person_name(names: dict, pid) -> str | None:
    return (rl.full_name(names["people"].get(str(pid))) or None) if pid else None


def _serialize_list_batch(b: dict, access: str, counts: dict, names: dict) -> dict:
    subj = names["subjects"].get(str(b.get("subject_id")), {})
    term = names["terms"].get(str(b.get("term_id")), {})
    return {
        "id": b["id"], "status": b.get("status"), "created_at": b.get("created_at"),
        "closed_at": b.get("closed_at"), "access": access,
        "instructor": {"id": b.get("instructor_id"), "name": _person_name(names, b.get("instructor_id"))},
        "subject": {"id": b.get("subject_id"), "code": subj.get("subject_code"), "name": subj.get("subject_name")},
        "term": {"id": b.get("term_id"),
                 "label": f"{term.get('semester') or ''} {term.get('academic_year') or ''}".strip() or None},
        "station": {"id": b.get("station_id"),
                    "label": names["stations"].get(str(b.get("station_id")), {}).get("label")},
        "started_by": {"id": b.get("created_by"), "name": _person_name(names, b.get("created_by"))},
        "counts": counts,
        "to_review": sum(counts.values()),
    }


@router.get("/batches")
def list_review_batches(limit: int = Query(default=20, ge=1, le=50),
                        before: str | None = Query(default=None, max_length=64),
                        only_pending: bool = False,
                        user: CurrentUser = Depends(require_staff)):
    """Batches this user may review (the /review page). First answer: every
    batch with forms left, or still open (work first, then newest), then the
    rest newest first, `limit` at a time; pass next_before as `before` for more."""
    before_dt = parse_ts(before) if before else None
    if before and before_dt is None:
        raise _err(400, "invalid_cursor", "This list is out of date. Reload the page.")
    sb = get_supabase()
    now = rl.now_utc()
    migration_ready = _review_columns_ready(sb)
    work = _pending_work(sb)

    ids = set(work["queue"]) | set(work["imports"]) | set(work["unsaved"])
    candidates = {str(b["id"]).lower(): b for b in _batches_by_id(sb, sorted(ids))}
    if not only_pending:
        q = sb.table("scan_batches").select(LIST_BATCH_COLUMNS).eq("status", "open")
        if not user.is_admin:
            q = q.eq("created_by", user.id)
        for b in q.limit(50).execute().data or []:
            candidates.setdefault(str(b["id"]).lower(), b)
    top = []
    for b in candidates.values():
        seen = _visible_counts(sb, b, user, work)
        if seen and (sum(seen[1].values()) > 0 or (not only_pending and b.get("status") == "open")):
            top.append((b, *seen))
    top.sort(key=lambda t: str(t[0].get("created_at") or ""), reverse=True)
    top.sort(key=lambda t: sum(t[2].values()) == 0)  # work first; stable, so newest first within
    top = top[:MAX_TOP_BATCHES]
    shown = {str(t[0]["id"]).lower() for t in top}

    picked = [] if before_dt else list(top)  # "Show more" pages carry only the history
    next_before = None
    if not only_pending:
        q = sb.table("scan_batches").select(LIST_BATCH_COLUMNS)
        if not user.is_admin:
            q = q.eq("created_by", user.id)
        if before_dt:
            q = q.lt("created_at", rl.iso(before_dt))
        fetched = q.order("created_at", desc=True).limit(limit + 1 + len(shown)).execute().data or []
        rest = [b for b in fetched if str(b["id"]).lower() not in shown]
        if len(rest) > limit:
            rest = rest[:limit]
            next_before = rl.iso(parse_ts(rest[-1]["created_at"]))
        for b in rest:
            seen = _visible_counts(sb, b, user, work)
            if seen:
                picked.append((b, *seen))
    names = _names_for_batches(sb, [t[0] for t in picked])
    return {"server_time": rl.iso(now), "migration_ready": migration_ready,
            "unsaved_ready": work["unsaved_ready"],
            "batches": [_serialize_list_batch(b, a, c, names) for b, a, c in picked],
            "next_before": next_before}


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
    if kind is None:
        raise _err(404, "no_image", "No picture was saved for this form.")
    return Response(content=raw, media_type="image/png" if kind == "png" else "image/jpeg",
                    headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff",
                             "Content-Disposition": "inline"})


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
    # The live batch page shows what was credited (migration 010; best effort).
    page_images.set_summary_for_task(sb, str(row.get("batch_id")), row.get("task_id"),
                                     page_scores.from_correction(ratings, instructor, subject, student_id))
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
    page_images.set_summary_for_task(sb, str(batch["id"]), row.get("task_id"),
                                     page_scores.from_correction(rl.extract_import_ratings(raw), instructor, subject,
                                                                 check_student))
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


# ------------------------------------------------------------------ couldn't be saved (migration 008)
# Pages marking could not store (app/unsaved_pages.py). Same access rule as
# the import errors: the batch's staff member, the person at its laptop, or
# an SAO admin. Every action carries the row's `version`.
UNSAVED_MIGRATION_MSG = "This needs a database update first. Ask IT to apply migrations/008_scan_unsaved_pages.sql."
UNSAVED_NOT_FOUND_MSG = "This page was not found. It may have been removed."
MOVE_FAILED_MSG = "The page couldn't be moved to Needs review. Try again in a minute."
# A kept page waiting for review whose item is still 'processing' this long after its last change is repaired.
ITEM_REPAIR_GRACE_SECONDS = 120.0


def _max_website_attempts() -> int:
    return uv.MAX_WEBSITE_ATTEMPTS


def _batch_n8n() -> httpx.Client:
    """The scoring webhook's client (long timeout), shared with the ingest."""
    return scans_router._n8n()


# "Try again" waits on n8n for up to N8N_TIMEOUT_SECONDS. Those calls run in
# their own small pool, never on the request threads, so an n8n outage plus
# many clicks can't stall the other routes (watcher ingest included). No
# queueing: when every worker is busy the click gets 429 and nothing changes.
RETRY_WORKERS = 3
RETRY_BUSY_MSG = "Marking is busy with other pages that were sent again. Try again in a minute."
_retry_pool = ThreadPoolExecutor(max_workers=RETRY_WORKERS, thread_name_prefix="unsaved-retry")
_retry_lock = threading.Lock()
_retry_inflight = {"n": 0}


def _reserve_retry_slot() -> bool:
    with _retry_lock:
        if _retry_inflight["n"] >= RETRY_WORKERS:
            return False
        _retry_inflight["n"] += 1
        return True


def _release_retry_slot() -> None:
    with _retry_lock:
        _retry_inflight["n"] = max(0, _retry_inflight["n"] - 1)


def _retry_pool_submit(fn, *args):
    return _retry_pool.submit(fn, *args)


def _run_retry_and_release(row_id: str, batch: dict, user_id: str) -> None:
    try:
        _run_unsaved_retry(row_id, batch, user_id)
    finally:
        _release_retry_slot()


def _load_unsaved(sb, batch_id: str, since_dt) -> tuple[list[dict], list[dict], bool]:
    """(rows changed since the cursor, every still-open row, table available)."""
    if not uv.available():
        return [], [], False

    def base():
        return sb.table(uv.TABLE).select(uv.LIST_COLUMNS).eq("batch_id", batch_id)

    try:
        q = base()
        if since_dt is not None:
            q = q.gte("updated_at", rl.since_filter_value(since_dt))
        changed = q.order("created_at").limit(MAX_ROWS).execute().data or []
        open_rows = base().in_("status", list(uv.OPEN_STATUSES)).order("created_at").limit(MAX_ROWS).execute().data or []
    except APIError as e:
        if uv.is_missing_table_error(e):
            uv.mark_missing(e)
            return [], [], False
        raise
    uv.mark_present()
    return changed, open_rows, True


def _unsaved_back_to_review(sb, row: dict, message: str, result: dict | None, count_failure: bool,
                            from_status: str = uv.SENDING) -> dict | None:
    """A website try did not save the page: it waits on the review page again."""
    vals: dict = {"status": uv.NEEDS_REVIEW, "last_error": message, "sending_started_at": None}
    reasons = row.get("review_reasons")
    if result:
        reasons = result.get("review_reasons") or reasons
        vals["review_reasons"] = uv.reasons_list(reasons)
        if result.get("message"):
            vals["message"] = str(result["message"])[:1000]
    done = _first(sb.table(uv.TABLE).update(vals).eq("id", row["id"]).eq("status", from_status).execute())
    if done is None:
        return None
    try:
        uv.item_back_to_review(sb, row["item_id"], reasons, count_failure=count_failure)
    except APIError:
        log.exception("could not put item %s back to review", row.get("item_id"))
    return done


def _unsaved_saved(sb, row: dict, status: str, result: dict | None, from_status: str) -> dict | None:
    """Marking stored the page: settle the copy and the batch item (counted once)."""
    done = _first(sb.table(uv.TABLE).update({
        "status": uv.SAVED, "outcome": status, "last_error": None, "sending_started_at": None,
        **uv.CLEARED_IMAGE,  # marking has its own copy now
    }).eq("id", row["id"]).eq("status", from_status).execute())
    if done is None:
        return None
    if result:
        reasons, identity, message = result.get("review_reasons") or [], result.get("identity_check"), result.get("message")
    else:
        reasons = uv.shown_reasons(row.get("review_reasons")) if status == "flagged" else []
        identity, message = None, "Saved by marking on a later try."
    try:
        uv.settle_item(sb, row["item_id"], status, reasons, identity, message)
    except APIError:
        log.exception("could not settle item %s", row.get("item_id"))
    # Now that marking stored it, its score can be shown (background, best effort).
    page_images.after_scan(sb, get_settings(), batch_id=str(row.get("batch_id")), item_id=str(row.get("item_id")),
                           task_id=row.get("task_id"))
    return done


def _idle_seconds(row: dict, now) -> float:
    at = parse_ts(row.get("updated_at"))
    return (now - at).total_seconds() if at else 1e9


def _repair_unsaved(sb, batch: dict, by_id: dict, timeout: float, now) -> None:
    """Keep the kept pages and their batch items in step (runs with every
    list poll; complete_batch_item only moves a 'processing' item once, so
    repeating a settle is safe):
      * saved / moved to Needs review, but the item is still error/processing
        (the second write failed): settle the item again;
      * waiting for review, but the item was left 'processing': back to error;
      * the laptop was still retrying when the batch closed (it can't send to a
        closed batch any more): the page now waits for a person."""
    try:
        items = (sb.table("scan_batch_items").select("id, status, updated_at").eq("batch_id", batch["id"])
                 .in_("status", ["error", "processing"]).limit(MAX_ROWS).execute().data or [])
        if not items:
            return
        by_item = {str(i["id"]): i for i in items}
        rows = (sb.table(uv.TABLE).select(uv.LIST_COLUMNS).eq("batch_id", batch["id"])
                .in_("item_id", list(by_item)).in_("status", [uv.SAVED, uv.MANUAL, uv.NEEDS_REVIEW, uv.RETRYING])
                .limit(MAX_ROWS).execute().data or [])
    except APIError:
        log.exception("could not check kept pages against their batch items")
        return
    batch_open = batch.get("status") == "open"
    for r in rows:
        r = by_id.get(r.get("id")) or r
        item = by_item.get(str(r.get("item_id")))
        if not item:
            continue
        status, idle = r.get("status"), _idle_seconds(item, now)
        try:
            if status == uv.SAVED and r.get("outcome") in COUNTER_FOR_STATUS:
                reasons = uv.shown_reasons(r.get("review_reasons")) if r["outcome"] == "flagged" else []
                uv.settle_item(sb, r["item_id"], r["outcome"], reasons, None, "Saved by marking on a later try.")
            elif status == uv.MANUAL and r.get("fsq_id"):
                uv.settle_item(sb, r["item_id"], "flagged",
                               uv.shown_reasons(r.get("review_reasons")) + [uv.NOT_SAVED_BY_PIPELINE], None,
                               uv.MOVED_TO_REVIEW_MSG)
            elif status == uv.NEEDS_REVIEW and item["status"] == "processing" and idle > ITEM_REPAIR_GRACE_SECONDS:
                uv.item_back_to_review(sb, r["item_id"], r.get("review_reasons"), count_failure=False)
            elif status == uv.RETRYING and not batch_open and (item["status"] == "error" or idle > timeout + 60):
                done = _first(sb.table(uv.TABLE).update({"status": uv.NEEDS_REVIEW}).eq("id", r["id"])
                              .eq("status", uv.RETRYING).execute())
                if done:
                    uv.item_to_review(sb, r["item_id"], r.get("review_reasons"))
                    by_id[r["id"]] = done
        except APIError:
            log.exception("could not repair kept page %s", r.get("id"))


def _settle_unsaved(sb, batch: dict, rows: list[dict], open_rows: list[dict], settings, now) -> tuple[list[dict], list[dict]]:
    """Runs with every list poll: give up on a "Try again" that never
    finished, settle pages that marking stored after all (a timed-out try
    n8n finished later), and repair items left behind (_repair_unsaved).
    Returns (rows to send, rows still open)."""
    timeout = float(getattr(settings, "n8n_timeout_seconds", 360.0) or 360.0)
    by_id = {r.get("id"): r for r in rows}
    for r in open_rows:
        by_id.setdefault(r.get("id"), r)
    for rid, r in list(by_id.items()):
        if uv.sending_is_stale(r, timeout, now):
            done = _unsaved_back_to_review(sb, r, uv.RETRY_STALE_MSG, None, count_failure=False)
            if done:
                by_id[rid] = done
    waiting = [r for r in by_id.values() if r.get("status") == uv.NEEDS_REVIEW and r.get("task_id")]
    if waiting:
        tasks = sorted({str(r["task_id"]) for r in waiting})
        try:
            in_fsq = {x.get("task_id") for x in (
                sb.table(FSQ).select("task_id").in_("task_id", tasks).limit(MAX_ROWS).execute().data or [])}
            rest = [t for t in tasks if t not in in_fsq]
            in_raw = {x.get("task_id") for x in (
                sb.table(uv.RAW).select("task_id").in_("task_id", rest).limit(MAX_ROWS).execute().data or [])} if rest else set()
        except APIError:
            log.exception("could not check whether unsaved pages were stored meanwhile")
            in_fsq, in_raw = set(), set()
        for r in waiting:
            outcome = "flagged" if r["task_id"] in in_fsq else "success" if r["task_id"] in in_raw else None
            if outcome:
                done = _unsaved_saved(sb, r, outcome, None, from_status=uv.NEEDS_REVIEW)
                if done:
                    by_id[r["id"]] = done
    _repair_unsaved(sb, batch, by_id, timeout, now)
    merged = sorted(by_id.values(), key=lambda r: (str(r.get("created_at") or ""), str(r.get("id"))))
    return merged, [r for r in merged if r.get("status") in uv.OPEN_STATUSES]


def _get_unsaved(sb, row_id: str, columns: str | None = None) -> dict:
    if not is_valid_uuid(row_id):
        raise _err(404, "not_found", UNSAVED_NOT_FOUND_MSG)
    try:
        rows = (sb.table(uv.TABLE).select(columns or f"{uv.LIST_COLUMNS}, page_meta")
                .eq("id", row_id).limit(1).execute().data)
    except APIError as e:
        if uv.is_missing_table_error(e):
            uv.mark_missing(e)
            raise _err(503, "migration_required", UNSAVED_MIGRATION_MSG)
        raise
    uv.mark_present()
    if not rows:
        raise _err(404, "not_found", UNSAVED_NOT_FOUND_MSG)
    return rows[0]


def _unsaved_access(sb, row: dict, user: CurrentUser) -> dict:
    batch = _load_batch(sb, str(row.get("batch_id")))
    if _has_batch_access(sb, batch, user):
        return batch
    raise _err(403, "not_your_batch", NOT_YOUR_BATCH_MSG)


def _check_unsaved(row: dict, expected_version: int) -> None:
    status, current = row.get("status"), int(row.get("version") or 0)
    if status == uv.SENDING:
        raise _err(409, "in_progress", "This page is being sent to marking right now. Wait a moment, it updates by itself.",
                   current_version=current)
    if status != uv.NEEDS_REVIEW:
        raise _err(409, "already_resolved", "This page was already dealt with, so nothing was changed.",
                   page_status=status, current_version=current)
    if int(expected_version) != current:
        raise _err(409, "stale_row", "This page changed meanwhile. Look at the new version, then try again.",
                   current_version=current)


def _unsaved_conflict(sb, row_id: str, expected_version: int) -> HTTPException:
    """A conditional update matched nothing: say why, from the row as it is now."""
    try:
        _check_unsaved(_get_unsaved(sb, row_id), expected_version)
    except HTTPException as e:
        return e
    return _err(409, "stale_row", "This page changed meanwhile. Look at the new version, then try again.")


class UnsavedActionBody(BaseModel):
    expected_version: int = Field(ge=0, le=2_000_000_000)


@router.get("/unsaved/{row_id}/image")
def unsaved_image(row_id: str, user: CurrentUser = Depends(require_staff)):
    sb = get_supabase()
    row = _get_unsaved(sb, row_id, columns="id, batch_id, image_base64")
    _unsaved_access(sb, row, user)
    return _image_response(_decode_image(row.get("image_base64")))


def _run_unsaved_retry(row_id: str, batch: dict, user_id: str) -> None:
    """Background part of "Try again": re-send the kept copy to n8n and settle
    the outcome. Never raises (runs after the response)."""
    sb = get_supabase()
    settings = get_settings()
    row = None
    try:
        rows = sb.table(uv.TABLE).select("*").eq("id", row_id).limit(1).execute().data or []
        row = rows[0] if rows else None
        if not row or row.get("status") != uv.SENDING:
            return
        payload = uv.retry_payload(batch, row, user_id)
        headers = {"X-Webhook-Secret": getattr(settings, "n8n_webhook_secret", "") or ""}
        meta = {"unsaved_id": row_id, "task_id": row.get("task_id"), "batch_id": row.get("batch_id")}
        try:
            resp = _batch_n8n().post(settings.n8n_batch_webhook_url, json=payload, headers=headers)
        except httpx.TimeoutException:
            log.warning("retry of unsaved page timed out (task %s)", row.get("task_id"))
            _unsaved_back_to_review(sb, row, uv.RETRY_TIMEOUT_MSG, None, count_failure=False)
            audit(user_id, "scan_unsaved_retry_result", {**meta, "outcome": "timeout"})
            return
        except httpx.HTTPError as e:
            log.warning("retry of unsaved page: n8n transport error %s (task %s)", type(e).__name__, row.get("task_id"))
            _unsaved_back_to_review(sb, row, uv.RETRY_UNREACHABLE_MSG, None, count_failure=False)
            audit(user_id, "scan_unsaved_retry_result", {**meta, "outcome": "unreachable"})
            return
        kind, result, message = uv.classify_response(resp)
        if kind == "failed":
            _unsaved_back_to_review(sb, row, message, result, count_failure=True)
            audit(user_id, "scan_unsaved_retry_result", {**meta, "outcome": "not_saved"})
            return
        status = result["status"]
        if status == "duplicate":
            # Already stored by an earlier try whose answer was lost: count it where it is.
            status = uv.stored_outcome(sb, row.get("task_id")) or "duplicate"
        _unsaved_saved(sb, row, status, result, from_status=uv.SENDING)
        audit(user_id, "scan_unsaved_retry_result", {**meta, "outcome": status})
    except Exception:
        log.exception("retry of unsaved page %s failed", row_id)
        if row and row.get("item_id"):
            try:
                _unsaved_back_to_review(sb, row, uv.RETRY_INTERNAL_MSG, None, count_failure=False)
            except Exception:
                log.exception("could not put unsaved page %s back to review", row_id)


@router.post("/unsaved/{row_id}/retry")
def retry_unsaved(row_id: str, body: UnsavedActionBody, user: CurrentUser = Depends(require_staff)):
    """Try again: re-send the kept copy through n8n (no re-scan). Answers at
    once with outcome 'sending'; the list shows the result when it is in."""
    sb = get_supabase()
    settings = get_settings()
    row = _get_unsaved(sb, row_id)
    batch = _unsaved_access(sb, row, user)
    _check_unsaved(row, body.expected_version)
    if not getattr(settings, "n8n_batch_webhook_url", ""):
        raise _err(503, "pipeline_not_configured",
                   "Marking isn't set up on this server, so the page can't be sent again. Type in the answers instead.")
    attempts = int(row.get("website_attempts") or 0)
    if attempts >= _max_website_attempts():
        raise _err(409, "too_many_tries",
                   f"This page was already sent again {attempts} times. Type in the answers instead.")
    if not _reserve_retry_slot():
        raise _err(429, "retry_busy", RETRY_BUSY_MSG)  # row untouched: same status, same version
    try:
        started = _start_retry(sb, row, batch, body.expected_version, attempts, user)
    except BaseException:
        _release_retry_slot()  # not handed to the pool
        raise
    # From here the pool's worker releases the slot.
    audit(user.id, "scan_unsaved_retry", {"unsaved_id": row["id"], "task_id": row.get("task_id"),
                                          "batch_id": row.get("batch_id"), "attempt": attempts + 1})
    return {"ok": True, "outcome": "sending",
            "row": uv.serialize(started, {user.id: user.full_name}, True, _max_website_attempts())}


def _start_retry(sb, row: dict, batch: dict, expected_version: int, attempts: int, user: CurrentUser) -> dict:
    """Claim the page (conditional on its version) and hand it to the retry
    pool. Raises HTTPException when the page changed meanwhile."""
    row_id = str(row["id"])
    now = rl.now_utc()
    started = _first(sb.table(uv.TABLE).update({
        "status": uv.SENDING, "sending_started_at": rl.iso(now), "acted_by": user.id,
        "website_attempts": attempts + 1, "last_error": None,
    }).eq("id", row["id"]).eq("version", expected_version).eq("status", uv.NEEDS_REVIEW).execute())
    if not started:
        raise _unsaved_conflict(sb, row_id, expected_version)
    # The batch item goes back to 'processing' (only from 'error'): the result
    # is then recorded through complete_batch_item exactly once.
    claimed = _first(sb.table("scan_batch_items").update({"status": "processing", "updated_at": rl.iso(now)})
                     .eq("id", row["item_id"]).eq("status", "error").execute())
    if not claimed:
        sb.table(uv.TABLE).update({
            "status": uv.NEEDS_REVIEW, "sending_started_at": None, "website_attempts": attempts,
            "last_error": "This page changed on the batch meanwhile. Look at it again.",
        }).eq("id", row["id"]).eq("status", uv.SENDING).execute()
        raise _err(409, "item_changed", "This page changed meanwhile (for example it was sent again from the laptop). "
                                        "The list refreshes by itself.")
    try:
        _retry_pool_submit(_run_retry_and_release, row["id"], batch, user.id)
    except RuntimeError:  # pool shut down (server stopping)
        log.exception("could not start the retry of unsaved page %s", row_id)
        _unsaved_back_to_review(sb, started, uv.RETRY_INTERNAL_MSG, None, count_failure=False)
        raise _err(503, "retry_unavailable", "The server is restarting. Try again in a minute.")
    return started


def _queue_row_for_task(sb, task_id: str | None) -> dict | None:
    if not task_id:
        return None
    return _first(sb.table(FSQ).select("*").eq("task_id", task_id).limit(1).execute())


@router.post("/unsaved/{row_id}/to-review")
def unsaved_to_review(row_id: str, body: UnsavedActionBody, user: CurrentUser = Depends(require_staff)):
    """Type in the answers: the page becomes a normal Needs review form
    (failed_scan_queue + scan_error_images, reason not_saved_by_pipeline),
    written with the backend's own key, and is approved with the usual
    correction flow. Idempotent: failed_scan_queue.task_id is unique."""
    sb = get_supabase()
    row = _get_unsaved(sb, row_id, columns="*")
    batch = _unsaved_access(sb, row, user)
    _check_unsaved(row, body.expected_version)
    retry_ready = bool(getattr(get_settings(), "n8n_batch_webhook_url", ""))
    if uv.stored_outcome(sb, row.get("task_id")) == "success":
        # Marking stored it after all (e.g. a try whose answer was lost): no form to type in.
        done = _unsaved_saved(sb, row, "success", None, from_status=uv.NEEDS_REVIEW)
        if done is None:
            raise _unsaved_conflict(sb, row_id, body.expected_version)
        audit(user.id, "scan_unsaved_already_saved", {"unsaved_id": row["id"], "task_id": row.get("task_id"),
                                                      "batch_id": row.get("batch_id")})
        return {"ok": True, "outcome": "already_saved", "queue_row": None,
                "row": uv.serialize(done, {user.id: user.full_name}, retry_ready, _max_website_attempts())}
    moving = _first(sb.table(uv.TABLE).update({"status": uv.MANUAL, "acted_by": user.id, "last_error": None})
                    .eq("id", row["id"]).eq("version", body.expected_version).eq("status", uv.NEEDS_REVIEW).execute())
    if not moving:
        raise _unsaved_conflict(sb, row_id, body.expected_version)
    try:
        fsq = _queue_row_for_task(sb, row.get("task_id"))
        if fsq is None:
            img = _first(sb.table("scan_error_images").insert(
                {"task_id": row.get("task_id"), "image_base64": row.get("image_base64")}).execute())
            new = uv.queue_row(row, batch, _batch_context(sb, batch), (img or {}).get("id"))
            try:
                fsq = _first(sb.table(FSQ).insert(new).execute())
            except APIError as e:
                if str(getattr(e, "code", "")) != "23505":
                    raise
                fsq = _queue_row_for_task(sb, row.get("task_id"))  # queued meanwhile (n8n or a double click)
                if img and img.get("id"):
                    try:
                        sb.table("scan_error_images").delete().eq("id", img["id"]).execute()
                    except APIError:
                        log.exception("could not remove the unused scan image %s", img.get("id"))
            if fsq is None:
                raise RuntimeError("the review queue row was not created")
        uv.settle_item(sb, row["item_id"], "flagged", fsq.get("review_reasons") or [uv.NOT_SAVED_BY_PIPELINE], None,
                       uv.MOVED_TO_REVIEW_MSG)
        # the scan now lives in scan_error_images (or on the existing queue row)
        done = _first(sb.table(uv.TABLE).update({"fsq_id": fsq["id"], **uv.CLEARED_IMAGE}).eq("id", row["id"]).execute()) \
            or {**moving, "fsq_id": fsq["id"]}
    except Exception:
        log.exception("could not move unsaved page %s to the review queue", row.get("id"))
        try:
            sb.table(uv.TABLE).update({"status": uv.NEEDS_REVIEW, "last_error": MOVE_FAILED_MSG}) \
                .eq("id", row["id"]).eq("status", uv.MANUAL).execute()
        except Exception:
            log.exception("could not put unsaved page %s back", row.get("id"))
        raise _err(502, "move_failed", MOVE_FAILED_MSG)
    audit(user.id, "scan_unsaved_to_review", {"unsaved_id": row["id"], "task_id": row.get("task_id"),
                                              "batch_id": row.get("batch_id"), "failed_scan_id": fsq["id"]})
    try:
        queue = _get_queue_row(sb, str(fsq["id"]), need_review=migration_available())
    except HTTPException:
        queue = _get_queue_row(sb, str(fsq["id"]), need_review=False)
    return {"ok": True, "outcome": "moved",
            "row": uv.serialize(done, {user.id: user.full_name}, bool(getattr(get_settings(), "n8n_batch_webhook_url", "")),
                                _max_website_attempts()),
            "queue_row": _serialize_queue(sb, queue, batch)}
