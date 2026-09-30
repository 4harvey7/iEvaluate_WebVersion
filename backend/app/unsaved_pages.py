"""Pages marking could not save (migration 008, table scan_unsaved_pages).

When n8n answers "flagged + internal_error_not_saved" (or fails in another way
that counts toward MAX_PIPELINE_ATTEMPTS), nothing was stored anywhere. The
backend keeps its own copy of the page exactly as it sent it to n8n, so the
page never silently drops out:

  retrying      the laptop is still resending it automatically
  needs_review  the automatic tries are used up; shown on the review page
                ("Couldn't be saved") with two actions:
                  Try again           -> re-sent to n8n from this copy
                  Type in the answers   -> a normal failed_scan_queue row is
                                         written (reason not_saved_by_pipeline)
                                         and the existing review/correction
                                         flow takes over
  sending       a "Try again" is in flight
  saved         marking stored it (outcome success/flagged/rejected/duplicate)
  manual        moved to failed_scan_queue (fsq_id) for hand entry

Why not a failed_scan_queue row straight away: that table has a unique index
on task_id and n8n's duplicate check answers `duplicate` for any task_id in
it, so a backend-written row would block every later retry. See migration 008.

Everything here uses the backend's own service key; nothing depends on the
Supabase credential inside n8n. Until migration 008 is applied every function
is a no-op (the table is re-tried every 5 minutes) and pages end as "Failed"
exactly as before.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any

from postgrest.exceptions import APIError

from app.logic import (
    PERMANENT_NOT_SAVED_REASON,
    UNSAVED_REASON,
    UNSAVED_REVIEW_MSG,
    PipelineResponseError,
    build_n8n_payload,
    is_permanent_not_saved,
    is_unsaved_internal_error,
    parse_ts,
    validate_n8n_response,
)

log = logging.getLogger("ievaluate.unsaved")

TABLE = "scan_unsaved_pages"
FSQ = "failed_scan_queue"
RAW = "sast_all_raw_data_survey"

RETRYING, NEEDS_REVIEW, SENDING, SAVED, MANUAL = "retrying", "needs_review", "sending", "saved", "manual"
STATUSES = (RETRYING, NEEDS_REVIEW, SENDING, SAVED, MANUAL)
OPEN_STATUSES = (RETRYING, NEEDS_REVIEW, SENDING)

# Reason written on the failed_scan_queue row made by "Type in the answers".
NOT_SAVED_BY_PIPELINE = "not_saved_by_pipeline"
HIDDEN_REASONS = {UNSAVED_REASON, PERMANENT_NOT_SAVED_REASON, NOT_SAVED_BY_PIPELINE}

# scan_batch_items.message of a page waiting here is logic.UNSAVED_REVIEW_MSG
# (item_error_code and the batch counters recognise it by that prefix).
MOVED_TO_REVIEW_MSG = "Not saved by marking; moved to Needs review to type in the answers."

# "Try again" clicks allowed per page (on top of the laptop's automatic tries).
MAX_WEBSITE_ATTEMPTS = 5
# A "Try again" still 'sending' this long after the n8n timeout is given up on.
SENDING_GRACE_SECONDS = 120.0
# n8n's own body limit is 16 MB; a bigger page is not kept (it can't be re-sent anyway).
MAX_KEEP_BASE64_CHARS = 16_000_000

# Written when a kept page is settled (saved, or copied into scan_error_images
# for hand entry): the ~1 MB copy is no longer needed. image_base64 is NOT NULL.
CLEARED_IMAGE = {"image_base64": "", "header_image_base64": None}

LIST_COLUMNS = (
    "id, item_id, batch_id, task_id, filename, review_reasons, message, status, outcome, "
    "watcher_failures, website_attempts, last_error, fsq_id, acted_by, sending_started_at, "
    "version, created_at, updated_at"
)

# Plain-English reasons a website "Try again" did not save the page.
RETRY_TIMEOUT_MSG = ("Marking didn't answer in time. It may still save the page: wait a minute and check this list "
                     "before trying again.")
RETRY_UNREACHABLE_MSG = "Marking couldn't be reached. Try again in a few minutes."
RETRY_AUTH_MSG = "Marking refused the server's credentials (a setup problem). Ask IT to check N8N_WEBHOOK_SECRET."
RETRY_BAD_REPLY_MSG = "Marking gave an unexpected answer. Try again in a few minutes, or type in the answers."
RETRY_REFUSED_MSG = "Marking refused this page as unreadable. Type in the answers."
RETRY_NOT_SAVED_MSG = ("Marking still couldn't save this page. If this keeps happening, ask IT to check the marking "
                       "system, or type in the answers.")
RETRY_STALE_MSG = "The last try didn't finish. Try again, or type in the answers."
RETRY_INTERNAL_MSG = "Something went wrong on the server while sending the page. Try again in a minute."


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- migration 008 state
_RETRY_TABLE_SECONDS = 300.0
_state = {"missing_until": 0.0, "warned": False}
_lock = threading.Lock()


def is_missing_table_error(exc: Exception) -> bool:
    if not isinstance(exc, APIError):
        return False
    code = str(getattr(exc, "code", "") or "")
    msg = str(getattr(exc, "message", "") or "").lower()
    if TABLE not in msg:
        return False
    return code in ("42P01", "PGRST205", "PGRST204", "42703") or "does not exist" in msg or "schema cache" in msg


def available() -> bool:
    with _lock:
        return time.monotonic() >= _state["missing_until"]


def mark_missing(exc: Exception) -> None:
    with _lock:
        first = not _state["warned"]
        _state["missing_until"] = time.monotonic() + _RETRY_TABLE_SECONDS
        _state["warned"] = True
    if first:
        log.warning("table %s is missing (%s). Apply migrations/008_scan_unsaved_pages.sql; until then pages "
                    "marking can't save end as 'Failed'.", TABLE, getattr(exc, "code", None) or type(exc).__name__)


def mark_present() -> None:
    with _lock:
        if _state["missing_until"] or _state["warned"]:
            log.info("table %s (migration 008) is available.", TABLE)
        _state["missing_until"] = 0.0
        _state["warned"] = False


def reset_state() -> None:
    with _lock:
        _state.update(missing_until=0.0, warned=False)


# ---------------------------------------------------------------- small readers
def _first(res) -> dict | None:
    data = getattr(res, "data", None)
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    return None


def _text(value: Any, limit: int = 500) -> str:
    if value is None or isinstance(value, (dict, list, bool)):
        return ""
    s = " ".join(str(value).split())
    return s[:limit]


def as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def reasons_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [s for s in (_text(r, 300) for r in value) if s][:50]
    if isinstance(value, str):
        s = value.strip()
        if s.startswith("{") and s.endswith("}"):
            return [p.strip().strip('"') for p in s[1:-1].split(",") if p.strip()][:50]
        return [s[:300]] if s else []
    return []


def shown_reasons(value: Any) -> list[str]:
    """Reasons worth showing a person (the 'not saved' codes are the headline instead)."""
    return [r for r in reasons_list(value) if r.strip().lower() not in HIDDEN_REASONS]


def is_unsaved_item_message(message: Any) -> bool:
    return str(message or "").startswith(UNSAVED_REVIEW_MSG)


# ---------------------------------------------------------------- recording (ingest)
def page_meta(page, file_sha256: str, station_id: str | None) -> dict:
    """What build_n8n_payload needs besides the image, from a PreparedPage."""
    meta: dict = {"file_sha256": file_sha256, "station_id": station_id}
    if page is not None:
        meta.update({
            "orientation_applied": getattr(page, "rotation_cw", 0),
            "orientation_confidence": getattr(page, "orientation_confidence", 0.0),
            "preprocess": getattr(page, "preprocess", "none"),
        })
        if getattr(page, "paper_size", None) in ("SHORT", "LONG"):
            meta["paper_size"] = page.paper_size
    return meta


def record(sb, *, item_id: str, batch_id: str, task_id: str, filename: str | None, image_b64: str,
           header_b64: str | None, meta: dict, reasons: list | None, message: str | None,
           failures: int, final: bool) -> bool:
    """Keep (or refresh) the copy of a page marking did not save. The image
    is written once; later failures only update the reasons / counters.
    Best effort: False when it could not be kept (then the page ends as
    'Failed' exactly as before). Never raises."""
    if not available() or not image_b64 or len(image_b64) > MAX_KEEP_BASE64_CHARS:
        return False
    vals = {
        "filename": (filename or "")[:200] or None,
        "review_reasons": reasons_list(reasons),
        "message": _text(message, 1000) or None,
        "watcher_failures": int(failures or 0),
        "status": NEEDS_REVIEW if final else RETRYING,
    }
    try:
        existing = _first(sb.table(TABLE).select("id, status").eq("item_id", item_id).limit(1).execute())
        if existing is None:
            try:
                sb.table(TABLE).insert({
                    **vals, "item_id": item_id, "batch_id": batch_id, "task_id": task_id,
                    "image_base64": image_b64, "header_image_base64": header_b64 or None, "page_meta": meta or {},
                }).execute()
                mark_present()
                return True
            except APIError as e:
                if str(getattr(e, "code", "")) != "23505":
                    raise
                existing = _first(sb.table(TABLE).select("id, status").eq("item_id", item_id).limit(1).execute())
                if existing is None:
                    return False
        if existing.get("status") not in (RETRYING, NEEDS_REVIEW):
            return False  # a website try / hand entry owns it now
        res = sb.table(TABLE).update(vals).eq("id", existing["id"]).in_("status", [RETRYING, NEEDS_REVIEW]).execute()
        mark_present()
        return _first(res) is not None
    except APIError as e:
        if is_missing_table_error(e):
            mark_missing(e)
        else:
            log.exception("could not keep unsaved page for item %s", item_id)
        return False
    except Exception:
        log.exception("could not keep unsaved page for item %s", item_id)
        return False


def close_for_item(sb, item_id: str, outcome: str) -> None:
    """The laptop's own resend got a final answer: an open copy is settled.
    Best effort, never raises."""
    if not available():
        return
    try:
        sb.table(TABLE).update({"status": SAVED, "outcome": outcome, "last_error": None, **CLEARED_IMAGE}) \
            .eq("item_id", item_id).in_("status", [RETRYING, NEEDS_REVIEW]).execute()
    except APIError as e:
        if is_missing_table_error(e):
            mark_missing(e)
        else:
            log.exception("could not settle unsaved page for item %s", item_id)
    except Exception:
        log.exception("could not settle unsaved page for item %s", item_id)


# ---------------------------------------------------------------- scan_batch_items
def settle_item(sb, item_id: str, status: str, reasons: list | None = None, identity: dict | None = None,
                message: str | None = None, retryable: bool = True, count_failure: bool = False) -> bool:
    """Move a page's scan_batch_items row to its final status through
    complete_batch_item(), which only transitions 'processing' rows and bumps
    the batch counter in the same transaction, so a result is never counted
    twice. An 'error' row is first put back to 'processing' (conditional)."""
    sb.table("scan_batch_items").update({"status": "processing", "updated_at": now_utc().isoformat()}) \
        .eq("id", item_id).eq("status", "error").execute()
    res = sb.rpc("complete_batch_item", {
        "p_item_id": item_id, "p_status": status, "p_review_reasons": reasons_list(reasons), "p_identity": identity,
        "p_message": message, "p_simulated": False, "p_retryable": retryable, "p_count_failure": count_failure,
    }).execute()
    data = getattr(res, "data", None)
    if isinstance(data, list):
        data = data[0] if data else None
    return data is not False


def item_to_review(sb, item_id: str, reasons: list | None) -> None:
    """An 'error' (still retryable) or stuck 'processing' item now waits on the
    review page: permanent error with UNSAVED_REVIEW_MSG. Not counted as a failure."""
    sb.table("scan_batch_items").update({"status": "processing", "updated_at": now_utc().isoformat()}) \
        .eq("id", item_id).eq("status", "error").execute()
    item_back_to_review(sb, item_id, reasons, count_failure=False)


def item_back_to_review(sb, item_id: str, reasons: list | None, count_failure: bool = True) -> None:
    """A website try failed: the page waits on the review page again."""
    sb.rpc("complete_batch_item", {
        "p_item_id": item_id, "p_status": "error", "p_review_reasons": reasons_list(reasons), "p_identity": None,
        "p_message": UNSAVED_REVIEW_MSG, "p_simulated": False, "p_retryable": False, "p_count_failure": count_failure,
    }).execute()


# ---------------------------------------------------------------- retry
def retry_payload(batch: dict, row: dict, staff_id: str | None) -> dict:
    """The same body the ingest sent (contract in n8n/WEBSITE_BRANCH.md),
    rebuilt from the kept copy."""
    meta = as_dict(row.get("page_meta"))
    sha = str(meta.get("file_sha256") or "")
    b = {**batch, "created_by": batch.get("created_by") or staff_id}
    payload = build_n8n_payload(b, meta.get("station_id") or batch.get("station_id"), row.get("filename") or "scan.jpg",
                                sha, row.get("image_base64") or "")
    payload["task_id"] = row.get("task_id") or payload["task_id"]
    for k in ("orientation_applied", "orientation_confidence", "preprocess"):
        if k in meta:
            payload[k] = meta[k]
    if meta.get("paper_size") in ("SHORT", "LONG"):
        payload["paper_size"] = meta["paper_size"]
    if row.get("header_image_base64"):
        payload["header_image"] = row["header_image_base64"]
    return payload


def classify_response(resp) -> tuple[str, dict | None, str]:
    """n8n's answer to a website "Try again" -> ('saved', result, '') or
    ('failed', result | None, plain-English reason)."""
    if resp.status_code in (401, 403):
        log.error("n8n rejected the webhook secret on a retry (HTTP %s)", resp.status_code)
        return "failed", None, RETRY_AUTH_MSG
    if resp.status_code != 200:
        log.warning("n8n returned HTTP %s on a retry", resp.status_code)
        return "failed", None, f"Marking answered with an error (HTTP {resp.status_code}). Try again in a few minutes."
    try:
        result = validate_n8n_response(resp.json())
    except (ValueError, PipelineResponseError) as e:
        log.warning("n8n response invalid on a retry: %s", str(e)[:200])
        return "failed", None, RETRY_BAD_REPLY_MSG
    if is_permanent_not_saved(result):
        return "failed", result, RETRY_REFUSED_MSG
    if is_unsaved_internal_error(result):
        return "failed", result, RETRY_NOT_SAVED_MSG
    return "saved", result, ""


def stored_outcome(sb, task_id: str | None) -> str | None:
    """Where a page with this task_id is stored now: 'flagged' (review queue),
    'success' (raw results), or None."""
    if not task_id:
        return None
    if sb.table(FSQ).select("id").eq("task_id", task_id).limit(1).execute().data:
        return "flagged"
    if sb.table(RAW).select("task_id").eq("task_id", task_id).limit(1).execute().data:
        return "success"
    return None


def sending_is_stale(row: dict, timeout_seconds: float, now: datetime | None = None) -> bool:
    if row.get("status") != SENDING:
        return False
    at = parse_ts(row.get("sending_started_at")) or parse_ts(row.get("updated_at"))
    if at is None:
        return True
    return ((now or now_utc()) - at).total_seconds() > float(timeout_seconds) + SENDING_GRACE_SECONDS


# ---------------------------------------------------------------- hand entry
def queue_row(row: dict, batch: dict, ctx: dict, scan_image_id: str | None) -> dict:
    """A failed_scan_queue row for "Type in the answers", shaped like the
    rows n8n queues after an internal error (flagged_record.js): the batch's
    instructor/subject as the suggestion, no ratings, the form check
    'unchecked'. The Flutter correction screen and the website review page
    both read it like any other website row."""
    inst = ctx.get("instructor") or {}
    subj = ctx.get("subject") or {}
    reasons = shown_reasons(row.get("review_reasons")) + [NOT_SAVED_BY_PIPELINE]
    detail = ", ".join(shown_reasons(row.get("review_reasons")))
    note = ("Marking read this page but couldn't save it"
            + (f" (it also noted: {detail})" if detail else "")
            + ". Nothing was stored, so enter the student ID, remarks and every rating from the scan.")
    meta = as_dict(row.get("page_meta"))
    partial = {
        "instructor": inst.get("name") or "",
        "subject": subj.get("code") or "",
        "ay_semester": "",
        "remarks": "",
        "date": "",
        "student_id": "",
        "ratings": None,
        "summary": "not scored — flagged: " + NOT_SAVED_BY_PIPELINE,
        "python_raw_ratings": None,
        "comparisons": None,
        "agreement_percent": None,
        "review_note": note,
        "ocr_instructor": "",
        "ocr_subject": "",
        "source": "website_batch",
        "batch_id": batch.get("id"),
        "batch_instructor_id": batch.get("instructor_id"),
        "batch_subject_id": batch.get("subject_id"),
        "station_id": meta.get("station_id") or batch.get("station_id"),
        "filename": row.get("filename"),
        "form_check_version": 1,
        "instructor_match": "unchecked",
        "subject_match": "unchecked",
        "suggested_instructor_id": batch.get("instructor_id"),
        "suggested_instructor_name": inst.get("name"),
        "suggested_subject_id": batch.get("subject_id"),
        "suggested_subject_code": subj.get("code"),
        "suggested_subject_name": subj.get("name"),
        "instructor_match_confidence": None,
        "subject_match_confidence": None,
        "instructor_candidates": [],
        "subject_candidates": [],
        "batch_instructor_name": inst.get("name"),
        "batch_subject_code": subj.get("code"),
        "batch_subject_name": subj.get("name"),
    }
    return {
        "task_id": row.get("task_id"),
        "user_id": batch.get("created_by"),
        "term_id": batch.get("term_id"),
        "table_found": None,
        "grid_source": "website_unsaved",
        "partial_data": partial,
        "status": "pending",
        "review_reasons": reasons,
        "omr_comparison": None,
        "validation_status": "flagged",
        "scan_image_id": scan_image_id,
        "batch_id": batch.get("id"),
    }


# ---------------------------------------------------------------- serializer
def serialize(row: dict, names: dict | None = None, retry_ready: bool = True,
              max_attempts: int = MAX_WEBSITE_ATTEMPTS) -> dict:
    names = names or {}
    status = row.get("status") if row.get("status") in STATUSES else NEEDS_REVIEW
    attempts = int(row.get("website_attempts") or 0)
    acted_by = row.get("acted_by")
    return {
        "id": row.get("id"),
        "item_id": row.get("item_id"),
        "task_id": row.get("task_id"),
        "filename": _text(row.get("filename"), 200) or None,
        "status": status,
        "outcome": _text(row.get("outcome"), 30) or None,
        "version": int(row.get("version") or 0),
        "review_reasons": shown_reasons(row.get("review_reasons")),
        "message": _text(row.get("message"), 500) or None,
        "last_error": _text(row.get("last_error"), 500) or None,
        "watcher_failures": int(row.get("watcher_failures") or 0),
        "website_attempts": attempts,
        "max_website_attempts": max_attempts,
        "can_retry": status == NEEDS_REVIEW and retry_ready and attempts < max_attempts,
        "can_enter_by_hand": status == NEEDS_REVIEW,
        "fsq_id": row.get("fsq_id"),
        # settled pages no longer keep their copy (the saved form / review form has it)
        "has_image": status not in (SAVED, MANUAL),
        "acted_by_name": (names.get(str(acted_by)) if acted_by else None) or None,
        "sending_started_at": row.get("sending_started_at"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at") or row.get("created_at"),
    }
