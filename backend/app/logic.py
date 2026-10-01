"""Pure helpers (no DB / network) so they can be unit tested in isolation."""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import io
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# Decompression-bomb / memory protection. Pillow's warning becomes a hard error
# process-wide. What reaches /scans/ingest is small: the watcher converts every
# scan (whatever its dpi) to a ~200 dpi greyscale JPEG with a long edge of at
# most 2200 px, i.e. <= ~3.7 MP for a long-bond (8.5x13 / 8.5x14 in) page, so a
# 600 dpi long-bond scan (5100x8400 = ~43 MP) is shrunk on the laptop and keeps
# working. 12 MP still accepts an un-shrunk 300 dpi long-bond page
# (2550x4200 = 10.7 MP) from an older/other client, while one decoded RGB page
# costs <= ~36 MB instead of ~120 MB at the old 40 MP cap (x the ingest
# concurrency, x the copies orientation/OMR prep make).
MAX_IMAGE_PIXELS = 12_000_000
try:
    import warnings as _warnings

    from PIL import Image as _PILImage

    _PILImage.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    _warnings.simplefilter("error", _PILImage.DecompressionBombWarning)
except ImportError:  # pragma: no cover
    pass

N8N_STATUSES = ("success", "flagged", "rejected", "duplicate")
IDENTITY_VALUES = ("match", "unreadable", "mismatch", "unverified")


# ---------------------------------------------------------------- station keys
def generate_station_key() -> str:
    return secrets.token_urlsafe(32)


def hash_station_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def station_key_matches(presented_key: str, stored_hash: str | None) -> bool:
    if not presented_key or not stored_hash:
        return False
    return hmac.compare_digest(hash_station_key(presented_key), stored_hash)


# ---------------------------------------------------------------- task ids
def make_task_id(batch_id: str, file_sha256: str) -> str:
    return f"web:{batch_id}:{file_sha256[:16]}"


# ---------------------------------------------------------------- time
def parse_ts(value: Any) -> datetime | None:
    """Parse an ISO timestamp (Supabase / watcher). Naive values are UTC."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            s = str(value).strip().replace("Z", "+00:00")
            # Python < 3.11 can't parse >6 fractional digits; trim them.
            s = re.sub(r"(\.\d{6})\d+", r"\1", s)
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def corrected_capture_time(captured_at: datetime, client_time: datetime | None, server_now: datetime) -> datetime:
    """Shift the watcher's file mtime onto the server clock using the watcher's
    reported 'now' (removes laptop clock skew; network latency is negligible)."""
    if client_time is None:
        return captured_at
    skew = server_now - client_time
    # Ignore absurd values (e.g. a wildly wrong client_time field).
    if abs(skew) > timedelta(days=2):
        return captured_at
    return captured_at + skew


def file_predates_batch(captured_at: datetime, batch_created_at: datetime, grace_seconds: float = 0.0) -> bool:
    return captured_at < batch_created_at - timedelta(seconds=grace_seconds)


def last_activity(batch: dict) -> datetime | None:
    candidates = [parse_ts(batch.get("last_scan_at")), parse_ts(batch.get("created_at"))]
    candidates = [c for c in candidates if c is not None]
    return max(candidates) if candidates else None


# ---------------------------------------------------------------- images
class ImageValidationError(ValueError):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def decode_image_base64(data: str, max_bytes: int) -> bytes:
    if not isinstance(data, str) or not data:
        raise ImageValidationError("invalid_image", "image_base64 is empty.")
    if data.startswith("data:"):
        data = data.split(",", 1)[-1]
    # Cheap pre-check before decoding: base64 is ~4/3 the decoded size.
    if len(data) > (max_bytes * 4) // 3 + 16:
        raise ImageValidationError("image_too_large", f"Image exceeds {max_bytes // (1024 * 1024)} MB.", 413)
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        raise ImageValidationError("invalid_image", "image_base64 is not valid base64.")
    if len(raw) > max_bytes:
        raise ImageValidationError("image_too_large", f"Image exceeds {max_bytes // (1024 * 1024)} MB.", 413)
    if not raw:
        raise ImageValidationError("invalid_image", "Image is empty.")
    return raw


def sniff_image_type(raw: bytes) -> str | None:
    if raw[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    return None


@dataclass
class PreparedPage:
    jpeg: bytes
    rotation_cw: int = 0          # clockwise degrees applied (after any EXIF transpose)
    exif_transposed: bool = False
    orientation_confidence: float = 0.0
    orientation_uncertain: bool = False
    preprocess: str = "none"      # OMR preprocessing applied, e.g. "longbond_crop+grey_pad_5"
    paper_size: str | None = None  # "SHORT"/"LONG" bond detected by omr_prep (None: not run)
    header_jpeg: bytes | None = None  # cropped/upscaled header crop for OCR (website pages only)


def normalize_to_jpeg(raw: bytes) -> bytes:
    return prepare_page(raw).jpeg


def prepare_page(raw: bytes, orient: bool = True, omr_prep=None) -> PreparedPage:
    """Validate the bytes are a real JPEG/PNG, turn the page upright/portrait
    (see app/orientation.py) and return JPEG bytes for n8n. An upright
    RGB/greyscale JPEG passes through byte-for-byte; anything rotated, PNG,
    CMYK, etc. is re-encoded."""
    kind = sniff_image_type(raw)
    if kind is None:
        raise ImageValidationError("unsupported_image", "Only JPEG or PNG images are accepted.", 415)
    from PIL import Image, UnidentifiedImageError

    too_big = ImageValidationError(
        "image_too_large", f"Image dimensions exceed {MAX_IMAGE_PIXELS // 1_000_000} megapixels.", 413
    )
    try:
        # Header-only open: check dimensions BEFORE any pixel data is decoded.
        with Image.open(io.BytesIO(raw)) as probe:
            w, h = probe.size
            if w <= 0 or h <= 0 or w * h > MAX_IMAGE_PIXELS:
                raise too_big
            probe.verify()
        img = Image.open(io.BytesIO(raw))
        if img.size[0] * img.size[1] > MAX_IMAGE_PIXELS:
            raise too_big
        img.load()
    except ImageValidationError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise too_big
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        raise ImageValidationError("invalid_image", "The image could not be decoded (corrupt or truncated).")

    page = PreparedPage(jpeg=b"")
    if orient:
        from app.orientation import normalize_orientation

        res = normalize_orientation(img)
        page.rotation_cw = res.rotation_cw
        page.exif_transposed = res.exif_transposed
        page.orientation_confidence = res.confidence
        page.orientation_uncertain = res.uncertain
        changed = res.changed
        img = res.image
    else:
        changed = False

    if omr_prep is not None:
        # Scanner/website pages only: bond-size detection + crop + grey border
        # (app/omr_prep.py).
        from app.omr_prep import HEADER_JPEG_QUALITY, crop_header, prepare_for_omr

        # Header crop for OCR, taken from the oriented image before the bond crop/grey
        # border below (which would shift its coordinates). Best-effort: a crop failure
        # never blocks the main page.
        try:
            header_img = crop_header(img)
            hbuf = io.BytesIO()
            header_img.save(hbuf, format="JPEG", quality=HEADER_JPEG_QUALITY, optimize=True)
            page.header_jpeg = hbuf.getvalue()
        except Exception:
            page.header_jpeg = None

        prep = prepare_for_omr(img, omr_prep)
        page.preprocess = prep.label
        page.paper_size = prep.paper_size
        if prep.steps:
            img = prep.image
            changed = True

    if kind == "jpeg" and img.mode in ("L", "RGB") and not changed:
        page.jpeg = raw
        return page
    if img.mode in ("1", "L", "LA", "I", "I;16", "F"):
        img = img.convert("L")
    elif img.mode != "RGB":
        img = img.convert("RGB")
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=88, optimize=True)
    page.jpeg = out.getvalue()
    return page


def sha256_hex(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def safe_filename(name: str) -> str:
    name = (name or "").replace("\\", "/").split("/")[-1]
    name = re.sub(r"[\x00-\x1f]", "", name).strip()
    return name[:200] or "scan.jpg"


# ---------------------------------------------------------------- n8n response
class PipelineResponseError(ValueError):
    pass


def _clean_str(v: Any, limit: int = 500) -> str | None:
    if v is None:
        return None
    if not isinstance(v, (str, int, float)):
        return None
    s = str(v).strip()
    return s[:limit] if s else None


def validate_n8n_response(body: Any) -> dict:
    """Defensively validate n8n's reply. Raises PipelineResponseError when the
    shape is unusable (non-object, array, unknown status...)."""
    if isinstance(body, list) and len(body) == 1 and isinstance(body[0], dict):
        # n8n "respond with all items" wraps single objects in an array; accept
        # exactly-one-item arrays only.
        body = body[0]
    if not isinstance(body, dict):
        raise PipelineResponseError("response is not a JSON object")

    status = body.get("status")
    if not isinstance(status, str) or status.strip().lower() not in N8N_STATUSES:
        raise PipelineResponseError("missing or unknown status")
    status = status.strip().lower()

    reasons_raw = body.get("review_reasons")
    if reasons_raw is None:
        reasons: list[str] = []
    elif isinstance(reasons_raw, list):
        reasons = [s for s in (_clean_str(r, 300) for r in reasons_raw) if s][:50]
    elif isinstance(reasons_raw, str):
        reasons = [reasons_raw.strip()[:300]] if reasons_raw.strip() else []
    else:
        raise PipelineResponseError("review_reasons is not a list")

    identity = None
    ic = body.get("identity_check")
    if ic is not None:
        if not isinstance(ic, dict):
            raise PipelineResponseError("identity_check is not an object")

        def _verdict(v: Any) -> str:
            v = (str(v).strip().lower() if isinstance(v, str) else "")
            return v if v in IDENTITY_VALUES else "unverified"

        identity = {
            "instructor": _verdict(ic.get("instructor")),
            "subject": _verdict(ic.get("subject")),
            "ocr_instructor": _clean_str(ic.get("ocr_instructor"), 200),
            "ocr_subject": _clean_str(ic.get("ocr_subject"), 200),
        }

    flagged_raw = body.get("flagged")
    flagged = flagged_raw if isinstance(flagged_raw, bool) else status == "flagged"
    # A "success" that n8n marks flagged is treated as flagged.
    if status == "success" and flagged:
        status = "flagged"

    return {
        "status": status,
        "flagged": status == "flagged",
        "task_id": _clean_str(body.get("task_id"), 200),
        "review_reasons": reasons,
        "identity_check": identity,
        "message": _clean_str(body.get("message"), 1000),
    }


def build_n8n_payload(batch: dict, station_id: str, filename: str, file_sha256: str, jpeg_b64: str,
                      orientation: "PreparedPage | None" = None) -> dict:
    payload = {
        "batch_id": batch["id"],
        "instructor_id": batch["instructor_id"],
        "subject_id": batch["subject_id"],
        "term_id": batch.get("term_id"),
        "station_id": station_id,
        "staff_id": batch.get("created_by"),
        "filename": filename,
        "file_sha256": file_sha256,
        "task_id": make_task_id(batch["id"], file_sha256),
        "image": jpeg_b64,
        "mime_type": "image/jpeg",
    }
    if orientation is not None:
        # Optional extras (n8n ignores unknown fields): clockwise degrees the
        # backend rotated the page, and how sure it was that it is now upright.
        payload["orientation_applied"] = orientation.rotation_cw
        payload["orientation_confidence"] = orientation.orientation_confidence
        payload["preprocess"] = orientation.preprocess
        if orientation.paper_size in ("SHORT", "LONG"):
            # Bond size for the Python OMR; n8n falls back to LONG without it.
            payload["paper_size"] = orientation.paper_size
        if orientation.header_jpeg:
            # Optional: cropped/upscaled header for a clearer OCR read of instructor/subject.
            # Backward compatible -- n8n ignores it when absent.
            payload["header_image"] = base64.b64encode(orientation.header_jpeg).decode("ascii")
    return payload


# ---------------------------------------------------------------- subjects
def normalize_code(code: str) -> str:
    """'Math 101' and 'MATH-101' both become 'MATH101'."""
    return re.sub(r"[^A-Z0-9]", "", (code or "").upper())


def clean_search_text(q: str, limit: int = 60) -> str:
    """Keep only characters that are safe inside a PostgREST or=() filter."""
    q = re.sub(r"[^A-Za-z0-9 .\-&']", " ", q or "")
    q = q.replace("'", "")
    return re.sub(r"\s+", " ", q).strip()[:limit]


def code_like_pattern(norm: str) -> str:
    """'MATH101' -> '%M%A%T%H%1%0%1%' so 'MATH 101' / 'Math-101' also match
    in SQL; results are re-ranked by exact normalized comparison in Python."""
    return "%" + "%".join(norm) + "%" if norm else "%"


def rank_subjects(rows: list[dict], q: str) -> list[dict]:
    norm = normalize_code(q)
    ql = q.lower()

    def key(s: dict):
        code_n = normalize_code(s.get("subject_code") or "")
        name = (s.get("subject_name") or "").lower()
        if norm and code_n == norm:
            r = 0
        elif norm and code_n.startswith(norm):
            r = 1
        elif norm and norm in code_n:
            r = 2
        elif ql and ql in name:
            r = 3
        else:
            r = 9
        return (r, code_n)

    ranked = sorted(rows, key=key)
    return [s for s in ranked if key(s)[0] < 9]


# ---------------------------------------------------------------- pipeline outcomes
UNSAVED_REASON = "internal_error_not_saved"

# Mirrors complete_batch_item() in migration 002.
COUNTER_FOR_STATUS = {
    "success": "processed_count",
    "flagged": "flagged_count",
    "rejected": "rejected_count",
    "duplicate": "duplicate_count",
}


def is_unsaved_internal_error(result: dict) -> bool:
    """n8n answered 'flagged' but the page was NOT stored anywhere; treat the
    reply as a pipeline failure (retry), not as a flagged sheet."""
    if result.get("status") != "flagged":
        return False
    return any(str(r).strip().lower() == UNSAVED_REASON for r in (result.get("review_reasons") or []))


PERMANENT_NOT_SAVED_REASON = "not_saved_permanent"


def is_permanent_not_saved(result: dict) -> bool:
    """n8n rejected the request as malformed ('invalid_request' +
    'not_saved_permanent'): nothing was stored and a retry can't help."""
    if result.get("status") != "flagged":
        return False
    return any(str(r).strip().lower() == PERMANENT_NOT_SAVED_REASON for r in (result.get("review_reasons") or []))


def retries_exhausted(attempts: int | None, max_attempts: int) -> bool:
    return (attempts or 1) >= max_attempts


# ---------------------------------------------------------------- item messages / error codes
# Messages the backend stores on scan_batch_items with status 'error'.
PIPELINE_NOT_CONFIGURED_MSG = "Scoring pipeline not configured."
PIPELINE_MISCONFIGURED_MSG = "Scoring pipeline rejected the server's credentials (configuration error)."
PIPELINE_REJECTED_MSG = "Rejected by pipeline (invalid request); the file was set aside."
# n8n didn't answer in time / couldn't be reached. Retried; counts toward the cap.
PIPELINE_TIMEOUT_MSG = "Scoring pipeline timed out."
PIPELINE_UNAVAILABLE_MSG = "Scoring pipeline is unavailable."
# Automatic tries used up, but a copy is kept for the review page (app/unsaved_pages.py).
UNSAVED_REVIEW_MSG = "Not saved by marking; waiting on the review page."

# item.error_code values (batch detail). None for every status except 'error'.
ITEM_ERROR_CODES = (
    "pipeline_not_configured",    # marking is switched off on the server; retried automatically
    "pipeline_misconfigured",     # server's n8n secret rejected; retried automatically
    "pipeline_error",             # n8n failed / bad reply / not saved; retried automatically
    "pipeline_timeout",           # n8n took too long to answer; retried automatically
    "pipeline_unavailable",       # n8n couldn't be reached; retried automatically
    "not_saved_needs_review",     # gave up automatically; a copy waits on the review page (Try again / Type in the answers)
    "failed_after_retries",       # gave up after MAX_PIPELINE_ATTEMPTS; file set aside, rescan
    "pipeline_rejected_request",  # n8n refused the request; file set aside, rescan
)


def item_error_code(item: dict) -> str | None:
    """Stable machine code for why a page is in 'error' (derived from the
    message/retryable the backend stored), or None for any other status."""
    if (item or {}).get("status") != "error":
        return None
    msg = str(item.get("message") or "")
    if msg == PIPELINE_REJECTED_MSG:
        return "pipeline_rejected_request"
    if msg.startswith(UNSAVED_REVIEW_MSG) and item.get("retryable") is False:
        return "not_saved_needs_review"
    if item.get("retryable") is False:
        return "failed_after_retries"
    if msg.startswith(PIPELINE_NOT_CONFIGURED_MSG):
        return "pipeline_not_configured"
    if msg.startswith(PIPELINE_MISCONFIGURED_MSG):
        return "pipeline_misconfigured"
    if msg.startswith(PIPELINE_TIMEOUT_MSG):
        return "pipeline_timeout"
    if msg.startswith(PIPELINE_UNAVAILABLE_MSG):
        return "pipeline_unavailable"
    return "pipeline_error"
