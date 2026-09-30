"""Pictures + score summaries of scanned pages for the page viewer
(migration 010).

Every page the ingest prepares for marking (upright, cropped, grey border:
exactly what n8n was sent) is kept as a smaller *viewing copy* (long side
SCAN_IMAGE_MAX_SIDE px, JPEG quality SCAN_IMAGE_JPEG_QUALITY) in the PRIVATE
Supabase Storage bucket SCAN_IMAGE_BUCKET ('scan-pages'), at
'<batch_id>/<item_id>.jpg', uploaded with the backend's service key. The
bucket has no storage policies, so nothing is readable with the anon or a
user key; staff see the picture only through
GET /batches/{batch_id}/items/{item_id}/image (login + batch access).

The work runs AFTER marking answered, in a small background pool: it never
delays or fails a scan. A failure is only logged (type/status, never the
image or a key). The same job stores the page's score summary
(app/page_scores.py) on scan_batch_items.result_summary.

Retention: pictures older than SCAN_IMAGE_RETENTION_DAYS (default 90; 0 =
keep) are removed by maybe_cleanup(), started at most once an hour from the
batch page's poll, also in the background pool.

Until migration 010 is applied (columns missing / bucket missing) every
function here is a no-op that is tried again every 5 minutes; the batch page
works as before and the viewer says pictures aren't kept yet."""
from __future__ import annotations

import io
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any

from postgrest.exceptions import APIError

from app import page_scores
from app.db import is_valid_uuid
from app.logic import parse_ts, sniff_image_type

log = logging.getLogger("ievaluate.page_images")

ITEMS = "scan_batch_items"
NEW_COLUMNS = ("result_summary", "page_image_path", "page_image_saved_at")
DEFAULT_BUCKET = "scan-pages"
DEFAULT_RETENTION_DAYS = 90
DEFAULT_MAX_SIDE = 1800
DEFAULT_QUALITY = 75
# Same limit as the bucket's file_size_limit in migration 010.
MAX_VIEW_BYTES = 5 * 1024 * 1024

CLEANUP_INTERVAL_SECONDS = 3600.0
CLEANUP_BATCH = 200
CLEANUP_MAX_ROUNDS = 5

_RETRY_SECONDS = 300.0


# ---------------------------------------------------------------- settings
def bucket_name(settings) -> str:
    return str(getattr(settings, "scan_image_bucket", "") or "").strip()


def retention_days(settings) -> int:
    try:
        return max(0, int(getattr(settings, "scan_image_retention_days", DEFAULT_RETENTION_DAYS)))
    except (TypeError, ValueError):
        return DEFAULT_RETENTION_DAYS


def _max_side(settings) -> int:
    try:
        return max(400, min(4000, int(getattr(settings, "scan_image_max_side", DEFAULT_MAX_SIDE))))
    except (TypeError, ValueError):
        return DEFAULT_MAX_SIDE


def _quality(settings) -> int:
    try:
        return max(30, min(95, int(getattr(settings, "scan_image_jpeg_quality", DEFAULT_QUALITY))))
    except (TypeError, ValueError):
        return DEFAULT_QUALITY


# ---------------------------------------------------------------- migration 010 state
class _Gate:
    """'Missing' flag re-tried every 5 minutes, warned once."""

    def __init__(self, what: str):
        self.what = what
        self.missing_until = 0.0
        self.warned = False
        self.lock = threading.Lock()

    def available(self) -> bool:
        with self.lock:
            return time.monotonic() >= self.missing_until

    def mark_missing(self, detail: str) -> None:
        with self.lock:
            first = not self.warned
            self.missing_until = time.monotonic() + _RETRY_SECONDS
            self.warned = True
        if first:
            log.warning("%s is missing (%s). Apply migrations/010_scan_page_images.sql; until then page "
                        "pictures and scores are not kept.", self.what, detail)

    def mark_present(self) -> None:
        with self.lock:
            if self.warned:
                log.info("%s (migration 010) is available.", self.what)
            self.missing_until = 0.0
            self.warned = False

    def reset(self) -> None:
        with self.lock:
            self.missing_until, self.warned = 0.0, False


_columns = _Gate("scan_batch_items.result_summary / page_image_path")
_bucket = _Gate("the storage bucket for page pictures")


def columns_available() -> bool:
    return _columns.available()


def bucket_available(settings) -> bool:
    return bool(bucket_name(settings)) and _bucket.available()


def is_missing_column_error(exc: Exception) -> bool:
    if not isinstance(exc, APIError):
        return False
    code = str(getattr(exc, "code", "") or "")
    msg = str(getattr(exc, "message", "") or "")
    return code in ("42703", "PGRST204") and any(c in msg for c in NEW_COLUMNS)


def mark_columns_missing(exc: Exception) -> None:
    _columns.mark_missing(str(getattr(exc, "code", None) or type(exc).__name__))


def mark_columns_present() -> None:
    _columns.mark_present()


def reset_state() -> None:
    """Tests only."""
    _columns.reset()
    _bucket.reset()
    with _cleanup_lock:
        _cleanup["next_at"] = 0.0
    with _since_lock:
        _since.update(at=0.0, value=None)


def _storage_label(exc: Exception) -> str:
    """Type + status/code only: never the message body (could echo a URL)."""
    parts = [type(exc).__name__]
    for attr in ("status", "code"):
        v = getattr(exc, attr, None)
        if v not in (None, ""):
            parts.append(str(v)[:40])
    return " ".join(parts)


def _is_bucket_missing(exc: Exception) -> bool:
    msg = str(getattr(exc, "message", "") or "").lower()
    return "bucket" in msg and ("not found" in msg or "does not exist" in msg)


def _is_object_missing(exc: Exception) -> bool:
    msg = str(getattr(exc, "message", "") or "").lower()
    status = str(getattr(exc, "status", "") or "")
    return ("not found" in msg and "bucket" not in msg) or status == "404"


# ---------------------------------------------------------------- the picture
def object_path(batch_id: Any, item_id: Any) -> str | None:
    b, i = str(batch_id or ""), str(item_id or "")
    if not is_valid_uuid(b) or not is_valid_uuid(i):
        return None
    return f"{b.lower()}/{i.lower()}.jpg"


def viewing_copy(jpeg: bytes, max_side: int = DEFAULT_MAX_SIDE, quality: int = DEFAULT_QUALITY) -> bytes:
    """A smaller JPEG of the page for people to look at (never sent to marking)."""
    from PIL import Image

    with Image.open(io.BytesIO(jpeg)) as src:
        src.load()
        img = src
        if img.mode not in ("L", "RGB"):
            img = img.convert("L" if img.mode in ("1", "LA", "I", "I;16", "F") else "RGB")
        w, h = img.size
        scale = min(1.0, float(max_side) / float(max(w, h)))
        if scale < 1.0:
            img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=quality, optimize=True, progressive=True)
    data = out.getvalue()
    if sniff_image_type(data) != "jpeg" or len(data) > MAX_VIEW_BYTES:
        raise ValueError("viewing copy is not a usable JPEG")
    return data


def store_image(sb, settings, batch_id: str, item_id: str, jpeg: bytes) -> str | None:
    """Upload the viewing copy. Returns its path, or None (logged, never raises)."""
    bucket, path = bucket_name(settings), object_path(batch_id, item_id)
    if not jpeg or not path or not bucket_available(settings):
        return None
    try:
        data = viewing_copy(jpeg, _max_side(settings), _quality(settings))
    except Exception as e:
        log.warning("could not make the viewing copy of item %s (%s)", item_id, type(e).__name__)
        return None
    try:
        sb.storage.from_(bucket).upload(path, data, {"content-type": "image/jpeg", "upsert": "true",
                                                     "cache-control": "3600"})
    except Exception as e:
        if _is_bucket_missing(e):
            _bucket.mark_missing(_storage_label(e))
        else:
            log.warning("could not store the picture of item %s (%s)", item_id, _storage_label(e))
        return None
    _bucket.mark_present()
    return path


def load_image(sb, settings, path: str | None) -> bytes | None:
    """The stored viewing copy, or None when it isn't there (any more)."""
    bucket = bucket_name(settings)
    if not bucket or not path or not _bucket.available():
        return None
    try:
        data = sb.storage.from_(bucket).download(path)
    except Exception as e:
        if _is_bucket_missing(e):
            _bucket.mark_missing(_storage_label(e))
        elif not _is_object_missing(e):
            log.warning("could not read a page picture (%s)", _storage_label(e))
        return None
    return data if isinstance(data, (bytes, bytearray)) and sniff_image_type(bytes(data)) == "jpeg" else None


# ---------------------------------------------------------------- background pool
WORKERS = 2
# Each queued job holds one prepared page (~0.5-2 MB); beyond this the page's
# picture is simply not kept (logged) rather than letting memory grow.
MAX_PENDING = 16
_pool = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="page-images")
_pending = {"n": 0}
_pending_lock = threading.Lock()


def _pool_submit(fn, *args):
    return _pool.submit(fn, *args)


def submit(fn, *args) -> bool:
    with _pending_lock:
        if _pending["n"] >= MAX_PENDING:
            return False
        _pending["n"] += 1

    def run():
        try:
            fn(*args)
        except Exception:
            log.exception("page picture / score job failed")
        finally:
            with _pending_lock:
                _pending["n"] = max(0, _pending["n"] - 1)

    try:
        _pool_submit(run)
    except RuntimeError:  # pool shut down (server stopping)
        with _pending_lock:
            _pending["n"] = max(0, _pending["n"] - 1)
        return False
    return True


def _first(res) -> dict | None:
    data = getattr(res, "data", None)
    return data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def after_scan(sb, settings, *, batch_id: str, item_id: str, task_id: str | None,
               jpeg: bytes | None = None) -> bool:
    """Called once marking answered (whatever the outcome): keep the page's
    picture and score in the background. Never raises, never blocks."""
    if not columns_available():
        return False
    try:
        ok = submit(record_page, sb, settings, batch_id, item_id, task_id, jpeg)
    except Exception:
        log.exception("could not schedule the page picture of item %s", item_id)
        return False
    if not ok:
        log.warning("page picture of item %s not kept: too many pages waiting", item_id)
    return ok


def record_page(sb, settings, batch_id: str, item_id: str, task_id: str | None, jpeg: bytes | None) -> None:
    """Fill in whatever the item is still missing: the score summary (from
    marking's own tables) and the picture."""
    try:
        item = _first(sb.table(ITEMS).select("id, status, task_id, result_summary, page_image_path")
                      .eq("id", item_id).limit(1).execute())
    except APIError as e:
        if is_missing_column_error(e):
            mark_columns_missing(e)
            return
        raise
    mark_columns_present()
    if item is None:
        return
    vals: dict = {}
    status = item.get("status")
    if not item.get("result_summary") and status in page_scores.SCORED_STATUSES:
        try:
            summary = page_scores.lookup(sb, item.get("task_id") or task_id, status)
        except Exception as e:
            log.warning("could not read the score of item %s (%s)", item_id, type(e).__name__)
            summary = None
        if summary:
            vals["result_summary"] = summary
    if jpeg and not item.get("page_image_path"):
        path = store_image(sb, settings, batch_id, item_id, jpeg)
        if path:
            vals.update(page_image_path=path, page_image_saved_at=_now().isoformat())
    if vals:
        save_item(sb, item_id, vals)


def save_item(sb, item_id: str, vals: dict) -> bool:
    """Best-effort update of the migration-010 columns. Never raises."""
    if not columns_available():
        return False
    try:
        sb.table(ITEMS).update(vals).eq("id", item_id).execute()
        return True
    except APIError as e:
        if is_missing_column_error(e):
            mark_columns_missing(e)
        else:
            log.warning("could not save the page summary of item %s (%s)", item_id, getattr(e, "code", None))
    except Exception as e:
        log.warning("could not save the page summary of item %s (%s)", item_id, type(e).__name__)
    return False


def set_summary_for_task(sb, batch_id: str, task_id: str | None, summary: dict) -> None:
    """A reviewer credited this page: show what was credited. Never raises."""
    if not task_id or not columns_available():
        return
    try:
        sb.table(ITEMS).update({"result_summary": summary}).eq("batch_id", batch_id).eq("task_id", task_id).execute()
    except APIError as e:
        if is_missing_column_error(e):
            mark_columns_missing(e)
        else:
            log.warning("could not update the page summary for task %s (%s)", task_id, getattr(e, "code", None))
    except Exception as e:
        log.warning("could not update the page summary for task %s (%s)", task_id, type(e).__name__)


# ---------------------------------------------------------------- retention
_cleanup = {"next_at": 0.0}
_cleanup_lock = threading.Lock()


def maybe_cleanup(sb, settings) -> bool:
    """Start the retention cleanup in the background, at most once an hour."""
    if retention_days(settings) <= 0 or not bucket_available(settings) or not columns_available():
        return False
    now = time.monotonic()
    with _cleanup_lock:
        if now < _cleanup["next_at"]:
            return False
        _cleanup["next_at"] = now + CLEANUP_INTERVAL_SECONDS
    return submit(cleanup_expired, sb, settings)


def cleanup_expired(sb, settings, now: datetime | None = None) -> int:
    """Remove pictures older than the retention period (the item keeps
    page_image_saved_at, so the viewer can say why the picture is gone)."""
    days = retention_days(settings)
    bucket = bucket_name(settings)
    if days <= 0 or not bucket:
        return 0
    cutoff = ((now or _now()) - timedelta(days=days)).isoformat()
    removed = 0
    for _ in range(CLEANUP_MAX_ROUNDS):
        try:
            rows = (sb.table(ITEMS).select("id, page_image_path").lt("page_image_saved_at", cutoff)
                    .neq("page_image_path", "").order("page_image_saved_at").limit(CLEANUP_BATCH).execute().data or [])
        except APIError as e:
            if is_missing_column_error(e):
                mark_columns_missing(e)
            else:
                log.warning("page picture cleanup: could not list old pictures (%s)", getattr(e, "code", None))
            break
        rows = [r for r in rows if r.get("page_image_path")]
        if not rows:
            break
        try:
            sb.storage.from_(bucket).remove([r["page_image_path"] for r in rows])
        except Exception as e:
            if _is_bucket_missing(e):
                _bucket.mark_missing(_storage_label(e))
            else:
                log.warning("page picture cleanup: could not remove pictures (%s)", _storage_label(e))
            break
        try:
            sb.table(ITEMS).update({"page_image_path": None}).in_("id", [r["id"] for r in rows]).execute()
        except Exception as e:
            log.warning("page picture cleanup: could not clear %s paths (%s)", len(rows), type(e).__name__)
            break
        removed += len(rows)
        if len(rows) < CLEANUP_BATCH:
            break
    if removed:
        log.info("page picture cleanup: removed %s pictures older than %s days", removed, days)
    return removed


# ---------------------------------------------------------------- "kept since"
# The oldest picture ever kept = when this feature started on this server
# ("Image not kept for pages scanned before <date>"). Cached for an hour.
_since = {"at": 0.0, "value": None}
_since_lock = threading.Lock()


def images_since(sb) -> datetime | None:
    now = time.monotonic()
    with _since_lock:
        ttl = CLEANUP_INTERVAL_SECONDS if _since["value"] else _RETRY_SECONDS
        if _since["at"] and now - _since["at"] < ttl:
            return _since["value"]
    if not columns_available():
        return None
    try:
        # the oldest kept picture (the gte filter also leaves out NULLs)
        row = _first(sb.table(ITEMS).select("page_image_saved_at").gte("page_image_saved_at", "2000-01-01T00:00:00+00:00")
                     .order("page_image_saved_at").limit(1).execute())
        value = parse_ts((row or {}).get("page_image_saved_at"))
    except Exception as e:
        log.warning("could not read when page pictures started (%s)", type(e).__name__)
        return None
    with _since_lock:
        _since.update(at=now, value=value)
    return value
