"""Page viewer for the live batch page and the review page: one scanned
page's full result and its picture.

  GET /batches/{batch_id}/items/{item_id}         result detail (per-question ratings)
  GET /batches/{batch_id}/items/{item_id}/image   the page as it was sent for marking

Staff login on both, plus the review page's batch-access rule
(reviews._has_batch_access: SAO admin or the staff member who started the
batch; see app/batch_access.py). The picture is read with the
backend's service key from the private bucket (app/page_images.py), or, for
pages scanned before it existed, from the copies marking / the backend
already keep (scan_error_images for flagged pages, scan_unsaved_pages for
pages marking couldn't save). It is always answered as image/jpeg or
image/png after checking the bytes really are one."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from postgrest.exceptions import APIError

from app import page_images, page_scores
from app import unsaved_pages as uv
from app.db import get_settings, get_supabase, is_valid_uuid
from app.logic import item_error_code, parse_ts
from app.routers import reviews
from app.routers.batches import ITEM_COLUMNS, PAGE_COLUMNS
from app.security import CurrentUser, require_staff

router = APIRouter()
log = logging.getLogger("ievaluate.batch_pages")

ITEM_NOT_FOUND_MSG = "This page was not found in this batch."
NOT_YOUR_BATCH_MSG = "Only the staff member who started this batch, or an SAO admin, can open its pages."
# A page whose picture is still being saved (background job) for this long.
NOT_READY_SECONDS = 120.0

IMAGES_NOT_SET_UP_MSG = ("Pictures of scanned pages aren't switched on on this server yet "
                         "(ask IT to apply migrations/010_scan_page_images.sql).")
IMAGES_OFF_MSG = "This server is set up not to keep pictures of scanned pages."
IMAGE_NOT_READY_MSG = "The picture is still being saved. Try again in a moment."
IMAGE_UNAVAILABLE_MSG = "The picture couldn't be loaded right now. Try again in a minute."
IMAGE_NOT_SAVED_MSG = "The picture of this page couldn't be saved when it was scanned."


def _err(status: int, code: str, message: str, **extra) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message, **extra})


def _access(sb, batch_id: str, user: CurrentUser) -> dict:
    batch = reviews._load_batch(sb, batch_id)  # 404 batch_not_found
    if not reviews._has_batch_access(sb, batch, user):
        raise _err(403, "not_your_batch", NOT_YOUR_BATCH_MSG)
    return batch


def _load_item(sb, batch_id: str, item_id: str) -> tuple[dict, bool]:
    """(item, migration 010 columns present). 404 unless it belongs to the batch."""
    if not is_valid_uuid(item_id):
        raise _err(404, "not_found", ITEM_NOT_FOUND_MSG)

    def q(cols):
        return sb.table("scan_batch_items").select(cols).eq("id", item_id).eq("batch_id", batch_id).limit(1).execute().data

    rows, ready = None, False
    if page_images.columns_available():
        try:
            rows, ready = q(f"{ITEM_COLUMNS}, {PAGE_COLUMNS}"), True
            page_images.mark_columns_present()
        except APIError as e:
            if not page_images.is_missing_column_error(e):
                raise
            page_images.mark_columns_missing(e)
    if rows is None:
        rows = q(ITEM_COLUMNS)
    if not rows:
        raise _err(404, "not_found", ITEM_NOT_FOUND_MSG)
    return rows[0], ready


def _review_status(sb, task_id: str | None) -> str | None:
    if not task_id:
        return None
    try:
        rows = sb.table("failed_scan_queue").select("status").eq("task_id", task_id).limit(1).execute().data or []
    except APIError:
        log.exception("could not read the review status of task %s", task_id)
        return None
    return rows[0].get("status") if rows else None


def _fresh_summary(sb, item: dict, ready: bool) -> dict | None:
    """The stored summary; a flagged page is re-read from the review queue
    (it may have been credited since, on the website or in the app), and a
    page scanned before migration 010 gets its summary now (stored for the list)."""
    stored = item.get("result_summary") if isinstance(item.get("result_summary"), dict) else None
    status = item.get("status")
    if status not in page_scores.SCORED_STATUSES or (stored and status != "flagged"):
        return stored
    if stored and stored.get("source") == "review":
        return stored  # credited on the review page: that is the final result
    try:
        fresh = page_scores.lookup(sb, item.get("task_id"), status)
    except Exception as e:
        log.warning("could not read the score of item %s (%s)", item.get("id"), type(e).__name__)
        return stored
    if fresh and fresh != stored and ready:
        page_images.save_item(sb, item["id"], {"result_summary": fresh})
    return fresh or stored


@router.get("/{batch_id}/items/{item_id}")
def page_detail(batch_id: str, item_id: str, user: CurrentUser = Depends(require_staff)):
    sb = get_supabase()
    settings = get_settings()
    _access(sb, batch_id, user)
    item, ready = _load_item(sb, batch_id, item_id)
    summary = _fresh_summary(sb, item, ready)
    return {
        "id": item.get("id"),
        "batch_id": batch_id,
        "filename": item.get("filename"),
        "status": item.get("status"),
        "error_code": item_error_code(item),
        "retryable": item.get("retryable"),
        "review_reasons": item.get("review_reasons") or [],
        "message": item.get("message"),
        "identity": item.get("identity"),
        "simulated": bool(item.get("simulated")),
        "orientation_applied": item.get("orientation_applied"),
        "orientation_uncertain": bool(item.get("orientation_uncertain")),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
        "review_status": _review_status(sb, item.get("task_id")) if item.get("status") == "flagged" else None,
        # Full result incl. per-question ratings (m1..m10, p1..p10); null when there is none.
        "summary": summary,
        "image": "kept" if item.get("page_image_path") else "removed" if item.get("page_image_saved_at") else None,
        "page_images_enabled": bool(page_images.bucket_name(settings)) and ready,
        "retention_days": page_images.retention_days(settings),
    }


def _fallback_image(sb, item: dict) -> bytes | None:
    """Copies kept before the page viewer existed (or while its bucket is missing)."""
    status = item.get("status")
    try:
        if status in ("flagged", "duplicate", "error") and item.get("task_id"):
            raw = reviews._image_bytes(sb, None, item.get("task_id"))
            if raw:
                return raw
        if status == "error" and uv.available():
            rows = (sb.table(uv.TABLE).select("image_base64").eq("item_id", str(item["id"])).limit(1).execute().data or [])
            if rows:
                return reviews._decode_image(rows[0].get("image_base64"))
    except APIError as e:
        if uv.is_missing_table_error(e):
            uv.mark_missing(e)
        else:
            log.warning("could not read an older copy of item %s (%s)", item.get("id"), getattr(e, "code", None))
    return None


def _day(dt: datetime) -> str:
    return f"{dt.day} {dt:%b %Y}"


def _missing(sb, settings, item: dict, ready: bool) -> HTTPException:
    """Why there is no picture, in plain words (404 + a stable code)."""
    if not page_images.bucket_name(settings):
        return _err(404, "images_off", IMAGES_OFF_MSG)
    if not ready or not page_images.bucket_available(settings):
        return _err(404, "images_not_set_up", IMAGES_NOT_SET_UP_MSG)
    if item.get("page_image_path"):
        return _err(404, "image_unavailable", IMAGE_UNAVAILABLE_MSG)
    days = page_images.retention_days(settings)
    if item.get("page_image_saved_at"):
        msg = (f"The picture was removed {days} days after scanning, as set on this server."
               if days else "The picture was removed.")
        return _err(404, "image_removed", msg)
    now = datetime.now(timezone.utc)
    changed = parse_ts(item.get("updated_at")) or parse_ts(item.get("created_at"))
    if item.get("status") == "processing" or (changed and now - changed < timedelta(seconds=NOT_READY_SECONDS)):
        return _err(404, "image_not_ready", IMAGE_NOT_READY_MSG)
    since = page_images.images_since(sb)
    created = parse_ts(item.get("created_at"))
    if since is None or (created and created < since):
        msg = (f"Image not kept for pages scanned before {_day(since)}." if since
               else "Image not kept for pages scanned before the page viewer was switched on.")
        return _err(404, "image_not_kept", msg, kept_since=since.isoformat() if since else None)
    return _err(404, "image_not_saved", IMAGE_NOT_SAVED_MSG)


@router.get("/{batch_id}/items/{item_id}/image")
def page_image(batch_id: str, item_id: str, user: CurrentUser = Depends(require_staff)):
    sb = get_supabase()
    settings = get_settings()
    _access(sb, batch_id, user)
    item, ready = _load_item(sb, batch_id, item_id)
    raw = page_images.load_image(sb, settings, item.get("page_image_path")) if item.get("page_image_path") else None
    if raw is None:
        raw = _fallback_image(sb, item)
    if raw is None:
        raise _missing(sb, settings, item, ready)
    return reviews._image_response(raw)
