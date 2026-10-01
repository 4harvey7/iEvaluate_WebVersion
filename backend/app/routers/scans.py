"""Watcher-facing endpoints. Authenticated by the X-Station-Key header only;
the station is derived from the key, never from a client-supplied id."""
import base64
import logging
import threading
from datetime import datetime, timezone
from typing import Callable

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import page_images
from app import unsaved_pages as unsaved
from app.db import get_settings, get_supabase
from app.folder_safety import is_network_path
from app.logic import (
    PIPELINE_MISCONFIGURED_MSG,
    PIPELINE_NOT_CONFIGURED_MSG,
    PIPELINE_REJECTED_MSG,
    PIPELINE_TIMEOUT_MSG,
    PIPELINE_UNAVAILABLE_MSG,
    SHA256_RE,
    UNSAVED_REVIEW_MSG,
    ImageValidationError,
    PipelineResponseError,
    build_n8n_payload,
    corrected_capture_time,
    decode_image_base64,
    file_predates_batch,
    is_permanent_not_saved,
    is_unsaved_internal_error,
    item_error_code,
    make_task_id,
    prepare_page,
    parse_ts,
    retries_exhausted,
    safe_filename,
    sha256_hex,
    validate_n8n_response,
)
from app.omr_prep import OmrPrepConfig
from app.security import require_station
from app.station_status import FOLDER_ERRORS, command_for_heartbeat, update_station_status

router = APIRouter()
log = logging.getLogger("ievaluate.scans")

_n8n_client: httpx.Client | None = None
_n8n_lock = threading.Lock()


def _n8n() -> httpx.Client:
    global _n8n_client
    with _n8n_lock:
        if _n8n_client is None:
            s = get_settings()
            _n8n_client = httpx.Client(
                http2=False,
                follow_redirects=False,
                timeout=httpx.Timeout(s.n8n_timeout_seconds, connect=10.0),
            )
        return _n8n_client


def _err(status: int, code: str, message: str, **extra) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message, **extra})


class IngestScan(BaseModel):
    filename: str = Field(min_length=1, max_length=300)
    # 12 MB decoded -> ~16.8M base64 chars; the body cap (17 MB) is enforced earlier.
    image_base64: str = Field(min_length=8, max_length=16_800_000)
    sha256: str = Field(min_length=64, max_length=64)
    captured_at: str = Field(max_length=64)  # file mtime (ISO 8601)
    client_time: str | None = Field(default=None, max_length=64)  # watcher's "now", used to cancel clock skew


def _result_payload(item: dict, batch_id: str, idempotent: bool) -> dict:
    status = item.get("status")
    return {
        "batch_id": batch_id,
        "item_id": item.get("id"),
        "task_id": item.get("task_id"),
        "status": status,
        "flagged": status == "flagged",
        "review_reasons": item.get("review_reasons") or [],
        "identity_check": item.get("identity"),
        "message": item.get("message"),
        "simulated": bool(item.get("simulated")),
        "idempotent": idempotent,
    }


def _mark_error(item_id: str, message: str, retryable: bool = True, reasons: list | None = None,
                count_failure: bool = False) -> None:
    try:
        get_supabase().rpc("complete_batch_item", {
            "p_item_id": item_id, "p_status": "error", "p_review_reasons": reasons or [], "p_identity": None,
            "p_message": message, "p_simulated": False, "p_retryable": retryable, "p_count_failure": count_failure,
        }).execute()
    except Exception:
        log.exception("failed to mark item %s as error", item_id)


FAILED_AFTER_RETRIES_MSG = "Scoring failed after repeated attempts; the file was set aside. Rescan it if needed."
KEPT_FOR_REVIEW_MSG = ("Marking couldn't save this page after several tries. A copy is waiting on the website's "
                       "review page (try again or type in the answers there); no rescan needed.")


def _failed_after_retries(attempts: int | None = None) -> HTTPException:
    # 422 + this code tells the watcher to move the file to skipped/ (no more retries).
    return _err(422, "failed_after_retries", FAILED_AFTER_RETRIES_MSG, attempts=attempts)


def _kept_for_review(attempts: int | None, batch_id: str) -> HTTPException:
    # Same code (watchers <= 2.4.0 set the file aside as before); 2.4.1+ see
    # kept_for_review and count the page as delivered.
    return _err(422, "failed_after_retries", KEPT_FOR_REVIEW_MSG, attempts=attempts,
                kept_for_review=True, batch_id=batch_id)


# keep(final, reasons, note, failures) -> True when a copy of the page was kept
KeepFn = Callable[[bool, list | None, str, int], bool]


def _pipeline_failure(item_id: str, prior_failures: int, code: str, client_msg: str, item_msg: str,
                      reasons: list | None = None, keep: KeepFn | None = None,
                      batch_id: str | None = None, note: str | None = None) -> HTTPException:
    """A REAL pipeline failure (n8n didn't store / errored / bad reply /
    timed out / couldn't be reached). Counts toward MAX_PIPELINE_ATTEMPTS. Retryable -> 502 (watcher
    backs off and retries); cap reached -> permanent error + 422. A copy of
    the page is kept (app/unsaved_pages.py) so that, once the cap is reached,
    it waits on the review page instead of silently dropping out."""
    max_attempts = get_settings().max_pipeline_attempts
    failures = prior_failures + 1
    final = retries_exhausted(failures, max_attempts)
    kept = bool(keep and keep(final, reasons, note or item_msg, failures))
    if final:
        if kept:
            _mark_error(item_id, f"{UNSAVED_REVIEW_MSG} ({failures} automatic tries: {item_msg})", retryable=False,
                        reasons=reasons, count_failure=True)
            log.warning("item %s not saved after %s pipeline failures (%s); kept for review", item_id, failures, code)
            return _kept_for_review(failures, batch_id or "")
        _mark_error(item_id, f"Failed after {failures} attempts: {item_msg}", retryable=False, reasons=reasons, count_failure=True)
        log.warning("item %s failed permanently after %s pipeline failures (%s)", item_id, failures, code)
        return _failed_after_retries(failures)
    _mark_error(item_id, item_msg, retryable=True, reasons=reasons, count_failure=True)
    return _err(502, code, client_msg, failures=failures, max_attempts=max_attempts)


def _keep_failed_page(sb, item_id: str, keep: KeepFn) -> bool:
    """A page that gave up BEFORE copies were kept (or while migration 008
    was missing) is sent again, e.g. the file was put back into the scan
    folder: keep it now so it shows on the review page. True when a copy
    exists (then the item message says so)."""
    if not unsaved.available():
        return False
    try:
        rows = sb.table("scan_batch_items").select("id, status, retryable, message, review_reasons, pipeline_failures") \
            .eq("id", item_id).limit(1).execute().data or []
    except Exception:
        log.exception("could not read item %s", item_id)
        return False
    item = rows[0] if rows and isinstance(rows[0], dict) else None
    code = item_error_code(item) if item else None
    if code == "not_saved_needs_review":
        return True
    if code != "failed_after_retries":
        return False
    if not keep(True, item.get("review_reasons") or [], str(item.get("message") or ""),
                int(item.get("pipeline_failures") or 0)):
        return False
    try:
        sb.table("scan_batch_items").update({
            "message": f"{UNSAVED_REVIEW_MSG} ({item.get('message') or 'automatic tries used up'})"[:1000],
        }).eq("id", item_id).eq("status", "error").eq("retryable", False).execute()
    except Exception:
        log.exception("could not update item %s after keeping it", item_id)
    return True


def _omr_prep_config(settings) -> OmrPrepConfig | None:
    cfg = OmrPrepConfig(
        pad=bool(getattr(settings, "pad_for_omr", True)),
        pad_percent=float(getattr(settings, "pad_percent", 5.0)),
        crop_long_bond=bool(getattr(settings, "crop_long_bond", True)),
    )
    return cfg if (cfg.pad or cfg.crop_long_bond) else None


def _record_orientation(item_id: str, page) -> None:
    """Store what the orientation step did (best effort; never blocks scoring)."""
    if page.orientation_uncertain:
        log.info("orientation uncertain for item %s (confidence %.2f)", item_id, page.orientation_confidence)
    try:
        get_supabase().table("scan_batch_items").update({
            "orientation_applied": page.rotation_cw,
            "orientation_confidence": page.orientation_confidence,
            "orientation_uncertain": page.orientation_uncertain,
        }).eq("id", item_id).execute()
    except Exception:
        log.exception("could not record orientation for item %s", item_id)


@router.post("/ingest")
def ingest_scan(body: IngestScan, station: dict = Depends(require_station)):
    settings = get_settings()
    sb = get_supabase()
    station_id = station["id"]
    now = datetime.now(timezone.utc)

    # 1. Validate the payload before touching the DB / pipeline.
    sha = body.sha256.strip().lower()
    if not SHA256_RE.match(sha):
        raise _err(400, "invalid_sha256", "sha256 must be 64 lowercase hex characters.")
    captured_at = parse_ts(body.captured_at)
    if captured_at is None:
        raise _err(400, "invalid_captured_at", "captured_at must be an ISO 8601 timestamp.")

    # 2. Must have an open batch on this station. Checked BEFORE the image is
    # decoded / rotated, so a watcher waiting for a batch costs one query.
    res = sb.table("scan_batches").select("*").eq("station_id", station_id).eq("status", "open").limit(1).execute()
    if not res.data:
        raise _err(409, "no_open_batch", "No open batch on this station. Start a batch on the website first.")
    batch = res.data[0]
    if not batch.get("created_by"):
        # staff_id must always reach n8n (Flutter shows queued rows by staff);
        # legacy batches without created_by fall back to the station's staff.
        batch = {**batch, "created_by": station.get("current_staff_id")}

    # 3. Files that existed before the batch started belong to someone else
    # (the watcher sets them aside in skipped/before-batch/).
    batch_created = parse_ts(batch["created_at"])
    effective = corrected_capture_time(captured_at, parse_ts(body.client_time), now)
    if batch_created and file_predates_batch(effective, batch_created, settings.predate_grace_seconds):
        raise _err(409, "file_predates_batch", "This file was created before the current batch started; it will not be scored.",
                   batch_created_at=batch_created.isoformat())

    # 4. Decode + validate the image, then orient it.
    max_bytes = int(settings.max_upload_mb * 1024 * 1024)
    try:
        raw = decode_image_base64(body.image_base64, max_bytes)
        if sha256_hex(raw) != sha:
            raise ImageValidationError("hash_mismatch", "sha256 does not match the image bytes.")
        # Orientation is decided HERE (single source of truth), whatever the
        # watcher or scanner did: every page reaches n8n upright + portrait.
        page = prepare_page(raw, omr_prep=_omr_prep_config(settings))
        jpeg = page.jpeg
    except ImageValidationError as e:
        raise _err(e.status, e.code, e.message)
    filename = safe_filename(body.filename)

    # 5. Idempotency: claim (batch, sha256). Same page resent -> stored result.
    task_id = make_task_id(batch["id"], sha)
    claim = sb.rpc("claim_batch_item", {
        "p_batch_id": batch["id"], "p_station_id": station_id, "p_file_sha256": sha,
        "p_filename": filename, "p_task_id": task_id, "p_stale_seconds": int(settings.n8n_timeout_seconds) + 60,
    }).execute()
    row = claim.data[0] if claim.data else None
    if not row:
        raise _err(500, "claim_failed", "Could not record the scan. Will retry.")
    item_id = row["item_id"]
    prior_failures = int(row.get("item_failures") or 0)

    # A copy of the page for the review page, kept when marking can't save it.
    jpeg_b64 = base64.b64encode(jpeg).decode("ascii")
    header_b64 = base64.b64encode(page.header_jpeg).decode("ascii") if page.header_jpeg else None

    def keep(final: bool, reasons: list | None, note: str, failures: int) -> bool:
        return unsaved.record(
            sb, item_id=item_id, batch_id=batch["id"], task_id=task_id, filename=filename, image_b64=jpeg_b64,
            header_b64=header_b64, meta=unsaved.page_meta(page, sha, station_id), reasons=reasons or [],
            message=note, failures=failures, final=final,
        )

    if not row["claimed"]:
        if row["item_status"] == "processing":
            raise _err(409, "in_progress", "This page is already being processed. Will retry shortly.")
        if row["item_status"] == "error":
            # Only non-retryable errors are left unclaimed: retry cap reached.
            if _keep_failed_page(sb, item_id, keep):
                raise _kept_for_review(row.get("item_failures"), batch["id"])
            raise _failed_after_retries(row.get("item_failures"))
        item = sb.table("scan_batch_items").select("*").eq("id", item_id).limit(1).execute().data[0]
        if not item.get("page_image_path") or not item.get("result_summary"):
            # e.g. the first try's picture couldn't be kept: fill it in now
            page_images.after_scan(sb, settings, batch_id=batch["id"], item_id=item_id, task_id=task_id, jpeg=jpeg)
        return _result_payload(item, batch["id"], idempotent=True)

    _record_orientation(item_id, page)
    try:
        # 6a. Simulation (explicit opt-in, no webhook configured).
        if not settings.n8n_batch_webhook_url:
            if settings.simulate_scans:
                msg = "SIMULATED result - no scoring pipeline configured."
                sb.rpc("complete_batch_item", {
                    "p_item_id": item_id, "p_status": "success", "p_review_reasons": [], "p_identity": None,
                    "p_message": msg, "p_simulated": True,
                }).execute()
                return {
                    "batch_id": batch["id"], "item_id": item_id, "task_id": task_id, "status": "success", "flagged": False,
                    "review_reasons": [], "identity_check": None, "message": msg, "simulated": True, "idempotent": False,
                }
            _mark_error(item_id, PIPELINE_NOT_CONFIGURED_MSG)
            log.error("N8N_BATCH_WEBHOOK_URL is not set and SIMULATE_SCANS is false")
            raise _err(503, "pipeline_not_configured", "The scoring pipeline is not configured on the server. Will retry.")

        # 6b. Forward to n8n. URL + raw errors stay server-side.
        payload = build_n8n_payload(batch, station_id, filename, sha, jpeg_b64, orientation=page)
        headers = {"X-Webhook-Secret": settings.n8n_webhook_secret}  # required at startup when a URL is set
        try:
            resp = _n8n().post(settings.n8n_batch_webhook_url, json=payload, headers=headers)
        except httpx.TimeoutException:
            # Shown as "Trying again" (not "Working on it") and counted toward the
            # retry cap, so a page can't wait forever. The resend uses the same
            # task_id, so n8n can tell if it stored the page after all.
            log.warning("n8n call timed out (task %s)", task_id)
            raise _pipeline_failure(item_id, prior_failures, "pipeline_timeout", "The scoring pipeline timed out. Will retry.",
                                    PIPELINE_TIMEOUT_MSG, keep=keep, batch_id=batch["id"])
        except httpx.HTTPError as e:
            log.warning("n8n transport error (task %s): %s", task_id, type(e).__name__)
            raise _pipeline_failure(item_id, prior_failures, "pipeline_unavailable",
                                    "The scoring pipeline is unavailable. Will retry.",
                                    PIPELINE_UNAVAILABLE_MSG, keep=keep, batch_id=batch["id"])

        if resp.status_code in (401, 403):
            # Wrong/missing webhook secret: a configuration problem, not the page's fault.
            log.error("n8n rejected the webhook secret (HTTP %s) - check N8N_WEBHOOK_SECRET", resp.status_code)
            _mark_error(item_id, PIPELINE_MISCONFIGURED_MSG, retryable=True)
            raise _err(503, "pipeline_misconfigured", "The scoring pipeline is misconfigured on the server. Will retry.")

        if resp.status_code != 200:
            log.warning("n8n returned HTTP %s (task %s)", resp.status_code, task_id)
            raise _pipeline_failure(item_id, prior_failures, "pipeline_error", "The scoring pipeline returned an error. Will retry.",
                                    f"Scoring pipeline returned HTTP {resp.status_code}.", keep=keep, batch_id=batch["id"])
        try:
            result = validate_n8n_response(resp.json())
        except (ValueError, PipelineResponseError) as e:
            log.warning("n8n response invalid (task %s): %s", task_id, str(e)[:200])
            raise _pipeline_failure(item_id, prior_failures, "pipeline_bad_response",
                                    "The scoring pipeline returned an unexpected response. Will retry.",
                                    "Scoring pipeline returned an unexpected response.", keep=keep, batch_id=batch["id"])

        # n8n rejected the request itself (invalid_request + not_saved_permanent):
        # nothing stored, retrying can't help -> permanent error, watcher skips the file.
        if is_permanent_not_saved(result):
            log.warning("n8n rejected request permanently (task %s)", task_id)
            _mark_error(item_id, PIPELINE_REJECTED_MSG, retryable=False, reasons=result["review_reasons"], count_failure=True)
            if prior_failures:
                unsaved.close_for_item(sb, item_id, "refused")
            raise _err(422, "pipeline_rejected_request", PIPELINE_REJECTED_MSG)

        # n8n says "flagged" + internal_error_not_saved: the page was NOT stored.
        # Retry instead of counting it as flagged.
        if is_unsaved_internal_error(result):
            log.warning("n8n internal error, page not saved (task %s, prior failures %s)", task_id, prior_failures)
            raise _pipeline_failure(item_id, prior_failures, "pipeline_not_saved",
                                    "The scoring pipeline could not save this page. Will retry.",
                                    "Scoring pipeline internal error; page not saved.",
                                    reasons=result["review_reasons"], keep=keep, batch_id=batch["id"],
                                    note=result.get("message"))

        sb.rpc("complete_batch_item", {
            "p_item_id": item_id,
            "p_status": result["status"],
            "p_review_reasons": result["review_reasons"],
            "p_identity": result["identity_check"],
            "p_message": result["message"],
            "p_simulated": False,
        }).execute()
        if prior_failures:
            # an automatic resend worked after all: the kept copy is no longer needed
            unsaved.close_for_item(sb, item_id, result["status"])

        return {
            "batch_id": batch["id"],
            "item_id": item_id,
            "task_id": task_id,
            "status": result["status"],
            "flagged": result["flagged"],
            "review_reasons": result["review_reasons"],
            "identity_check": result["identity_check"],
            "message": result["message"],
            "simulated": False,
            "idempotent": False,
        }
    finally:
        # Whatever marking answered (or not): keep the page's picture + score for the
        # page viewer, in the background (app/page_images.py). Never blocks or fails the scan.
        page_images.after_scan(sb, settings, batch_id=batch["id"], item_id=item_id, task_id=task_id, jpeg=jpeg)


class Heartbeat(BaseModel):
    version: str | None = Field(default=None, max_length=40)
    client_time: str | None = Field(default=None, max_length=64)
    queued_files: int | None = Field(default=None, ge=0, le=10_000_000)
    # Watcher 2.3+: the folder actually being watched and its state.
    folder: str | None = Field(default=None, max_length=500)
    folder_exists: bool | None = None
    folder_error: str | None = Field(default=None, max_length=40)
    last_file_at: str | None = Field(default=None, max_length=64)
    # Watcher 2.4+: files waiting in skipped/before-batch/, and the id of the
    # last command it carried out (clears the pending command).
    skipped_files: int | None = Field(default=None, ge=0, le=10_000_000)
    command_done: str | None = Field(default=None, max_length=64)


def _watcher_status(body: Heartbeat) -> dict:
    """Station columns from a heartbeat. Only watchers that report a folder
    (2.3+) update them, so older watchers leave them untouched."""
    if body.folder is None and body.folder_exists is None:
        return {}
    last = parse_ts(body.last_file_at) if body.last_file_at else None
    return {
        "watcher_folder": (body.folder or "").strip()[:500] or None,
        "watcher_folder_exists": body.folder_exists,
        "watcher_folder_error": body.folder_error if body.folder_error in FOLDER_ERRORS else None,
        "watcher_queued_files": body.queued_files,
        "watcher_last_file_at": last.isoformat() if last else None,
        "watcher_skipped_files": body.skipped_files,
    }


def _heartbeat_folder(raw) -> str | None:
    folder = (raw or "").strip()
    if not folder or is_network_path(folder):
        return None
    return folder


@router.post("/heartbeat")
def heartbeat(body: Heartbeat | None = None, station: dict = Depends(require_station)):
    sb = get_supabase()
    now = datetime.now(timezone.utc)
    update = {"last_seen_at": now.isoformat()}
    if body and body.version:
        update["watcher_version"] = body.version
    row = update_station_status(sb, station["id"], update, _watcher_status(body) if body else {})
    ob = sb.table("scan_batches").select("id, created_at").eq("station_id", station["id"]).eq("status", "open").limit(1).execute().data
    command, command_id = command_for_heartbeat(
        sb, station["id"], row, body.command_done if body else None, has_open_batch=bool(ob), now=now,
    )
    return {
        "ok": True,
        "station_id": station["id"],
        "station_label": station.get("label"),
        "server_time": now.isoformat(),
        "open_batch": ob[0] if ob else None,
        # Folder chosen on the website; watcher 2.3+ switches to it live.
        # Never a network share (older watchers would follow it).
        "folder_path": _heartbeat_folder(station.get("folder_path")),
        # Watcher 2.4+: null | "resend_skipped" | "discard_skipped" (pages in
        # skipped/before-batch/). The watcher echoes command_id back as
        # command_done on its next heartbeat.
        "command": command,
        "command_id": command_id,
    }
