import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from postgrest.exceptions import APIError
from pydantic import BaseModel

from app.audit import audit
from app.db import get_settings, get_supabase, is_valid_uuid
from app.logic import item_error_code, last_activity, parse_ts
from app.routers.instructors import get_instructor_if_eligible
from app.security import CurrentUser, require_staff
from app.station_status import public_watcher_fields, select_stations

router = APIRouter()
log = logging.getLogger("ievaluate.batches")

ITEM_COLUMNS = "id, filename, status, review_reasons, identity, message, simulated, task_id, attempts, retryable, orientation_applied, orientation_confidence, orientation_uncertain, created_at, updated_at"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _full_name(r: dict | None) -> str | None:
    if not r:
        return None
    return " ".join(p for p in (r.get("first_name"), r.get("last_name")) if p) or None


def _current_term_id() -> str | None:
    res = get_supabase().table("system_settings").select("current_term_id").eq("id", 1).limit(1).execute()
    return res.data[0].get("current_term_id") if res.data else None


class StartBatch(BaseModel):
    station_id: str
    instructor_id: str
    subject_id: str


@router.post("/start")
def start_batch(body: StartBatch, user: CurrentUser = Depends(require_staff)):
    if not is_valid_uuid(body.station_id):
        raise HTTPException(status_code=400, detail="Invalid station ID")
    if not is_valid_uuid(body.instructor_id) or not is_valid_uuid(body.subject_id):
        raise HTTPException(status_code=400, detail="Invalid instructor or subject ID")
    sb = get_supabase()

    if not sb.table("stations").select("id").eq("id", body.station_id).limit(1).execute().data:
        raise HTTPException(status_code=404, detail={"code": "station_not_found", "message": "Station not found."})
    if not get_instructor_if_eligible(body.instructor_id):
        raise HTTPException(status_code=400, detail={"code": "invalid_instructor", "message": "That person is not an active instructor."})
    if not sb.table("subjects").select("id").eq("id", body.subject_id).limit(1).execute().data:
        raise HTTPException(status_code=400, detail={"code": "invalid_subject", "message": "Subject not found."})
    term_id = _current_term_id()
    if not term_id:
        raise HTTPException(status_code=409, detail={"code": "no_current_term", "message": "No current academic term is set. Ask an SAO admin to set it first."})

    try:
        res = sb.table("scan_batches").insert({
            "station_id": body.station_id,
            "instructor_id": body.instructor_id,
            "subject_id": body.subject_id,
            "term_id": term_id,
            "created_by": user.id,
            "status": "open",
        }).execute()
    except APIError as e:
        # Partial unique index one_open_batch_per_station = the hard block.
        if getattr(e, "code", None) == "23505":
            raise HTTPException(status_code=409, detail={"code": "batch_already_open", "message": "A batch is already open on this station. Close it first."})
        raise
    batch = res.data[0]

    try:
        sb.table("instructor_subjects").upsert(
            {"instructor_id": body.instructor_id, "subject_id": body.subject_id, "term_id": term_id},
            on_conflict="subject_id,instructor_id,term_id",
            ignore_duplicates=True,
        ).execute()
    except Exception:
        log.exception("instructor_subjects upsert failed (batch still started)")

    audit(user.id, "scan_batch_started", {"batch_id": batch["id"], "station_id": body.station_id})
    return {"id": batch["id"], "status": batch["status"], "created_at": batch["created_at"]}


@router.get("/station/{station_id}/open")
def get_open_batch(station_id: str, user: CurrentUser = Depends(require_staff)):
    if not is_valid_uuid(station_id):
        raise HTTPException(status_code=404, detail="Station not found")
    res = (
        get_supabase().table("scan_batches")
        .select("id, status, created_at, instructor_id, subject_id")
        .eq("station_id", station_id).eq("status", "open").limit(1).execute()
    )
    return res.data[0] if res.data else None


def _load_batch(batch_id: str) -> dict:
    if not is_valid_uuid(batch_id):
        raise HTTPException(status_code=404, detail="Batch not found")
    res = get_supabase().table("scan_batches").select("*").eq("id", batch_id).limit(1).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Batch not found")
    return res.data[0]


def _with_error_codes(items: list[dict]) -> list[dict]:
    """Add error_code (see logic.ITEM_ERROR_CODES; null unless status is 'error')."""
    for it in items:
        it["error_code"] = item_error_code(it)
    return items


def _with_review_status(sb, batch_id: str, items: list[dict]) -> list[dict]:
    """Add review_status to flagged items: the status of their failed_scan_queue
    row (pending / validated / discarded), null when there is none."""
    if not any(it.get("status") == "flagged" for it in items):
        return items
    try:
        rows = (sb.table("failed_scan_queue").select("task_id, status")
                .eq("batch_id", batch_id).limit(5000).execute().data or [])
    except APIError:
        log.exception("could not read review status for batch %s", batch_id)
        return items
    by_task = {r.get("task_id"): r.get("status") for r in rows if r.get("task_id")}
    for it in items:
        if it.get("status") == "flagged":
            it["review_status"] = by_task.get(it.get("task_id"))
    return items


def _latest_item_at(items: list[dict]) -> str | None:
    stamps = [t for it in items for t in (parse_ts(it.get("created_at")), parse_ts(it.get("updated_at"))) if t]
    return max(stamps).isoformat() if stamps else None


def _item_counts(batch_id: str) -> dict:
    """Counts for statuses that have no counter column (in-flight / errors)."""
    sb = get_supabase()
    out = {}
    for status in ("processing", "error"):
        r = sb.table("scan_batch_items").select("id", count="exact", head=True).eq("batch_id", batch_id).eq("status", status).execute()
        out[status] = r.count or 0
    r = (sb.table("scan_batch_items").select("id", count="exact", head=True)
         .eq("batch_id", batch_id).eq("status", "error").eq("retryable", False).execute())
    out["failed"] = r.count or 0  # permanent: retry cap reached, file moved to skipped/ by the watcher
    return out


@router.get("/{batch_id}")
def get_batch(batch_id: str, user: CurrentUser = Depends(require_staff)):
    b = _load_batch(batch_id)
    sb = get_supabase()
    settings = get_settings()

    people_ids = [i for i in {b.get("instructor_id"), b.get("created_by"), b.get("closed_by")} if i]
    people = {r["id"]: r for r in (sb.table("user_info").select("id, first_name, last_name, is_placeholder").in_("id", people_ids).execute().data or [])}
    subj = sb.table("subjects").select("id, subject_code, subject_name").eq("id", b["subject_id"]).limit(1).execute().data
    subj = subj[0] if subj else {}
    term_label = None
    if b.get("term_id"):
        t = sb.table("academic_terms").select("semester, academic_year").eq("id", b["term_id"]).limit(1).execute().data
        if t:
            term_label = f"{t[0].get('semester') or ''} {t[0].get('academic_year') or ''}".strip()
    st = select_stations(sb, "id, label, last_seen_at, folder_path, watcher_version", lambda q: q.eq("id", b["station_id"]).limit(1))
    st = st[0] if st else {}

    counts = _item_counts(batch_id)
    attention = (
        sb.table("scan_batch_items").select(ITEM_COLUMNS)
        .eq("batch_id", batch_id).or_("status.in.(flagged,rejected,error),orientation_uncertain.is.true")
        .order("created_at", desc=True).limit(200).execute().data or []
    )
    recent = (
        sb.table("scan_batch_items").select(ITEM_COLUMNS)
        .eq("batch_id", batch_id).order("updated_at", desc=True).limit(15).execute().data or []
    )
    queue_pending = (
        sb.table("failed_scan_queue").select("id", count="exact", head=True)
        .eq("batch_id", batch_id).eq("status", "pending").execute().count or 0
    )
    # Website import errors of this batch (instructor/subject not found when a
    # correction was credited); they are handled on the review page.
    try:
        import_pending = (
            sb.table("import_errors").select("id", count="exact", head=True)
            .like("task_id", f"web:{batch_id}:%").eq("status", "pending").execute().count or 0
        )
    except APIError:
        log.exception("could not count import errors for batch %s", batch_id)
        import_pending = 0
    simulated_items = (
        sb.table("scan_batch_items").select("id", count="exact", head=True)
        .eq("batch_id", batch_id).eq("simulated", True).execute().count or 0
    )

    now = _now()
    seen = parse_ts(st.get("last_seen_at"))
    instructor = people.get(b["instructor_id"], {})
    c = {
        "processed": b.get("processed_count") or 0,
        "flagged": b.get("flagged_count") or 0,
        "rejected": b.get("rejected_count") or 0,
        "duplicates": b.get("duplicate_count") or 0,
        "errors": counts["error"] - counts["failed"],
        "failed": counts["failed"],
        "processing": counts["processing"],
    }
    c["total"] = sum(c.values())
    return {
        "id": b["id"],
        "status": b["status"],
        "force_closed": b.get("force_closed"),
        "created_at": b["created_at"],
        "closed_at": b.get("closed_at"),
        "last_scan_at": b.get("last_scan_at"),
        "last_activity_at": (last_activity(b) or now).isoformat(),
        "abandoned_after_hours": settings.abandoned_batch_hours,
        "server_time": now.isoformat(),
        "created_by_name": _full_name(people.get(b.get("created_by"))),
        "closed_by_name": _full_name(people.get(b.get("closed_by"))),
        "instructor": {"id": b["instructor_id"], "name": _full_name(instructor) or "Unknown instructor", "is_placeholder": bool(instructor.get("is_placeholder"))},
        "subject": {"id": b["subject_id"], "code": subj.get("subject_code"), "name": subj.get("subject_name")},
        "term": {"id": b.get("term_id"), "label": term_label},
        "station": {
            "id": b["station_id"],
            "label": st.get("label"),
            "last_seen_at": st.get("last_seen_at"),
            "watcher_online": bool(seen and (now - seen).total_seconds() <= settings.watcher_online_seconds),
            "watcher_version": st.get("watcher_version"),
            "folder_path": st.get("folder_path"),
            **public_watcher_fields(st, now),  # incl. watcher_skipped_files, watcher_command
        },
        "counts": c,
        "queue_pending": queue_pending,
        "import_pending": import_pending,
        "simulated": settings.simulation_mode or simulated_items > 0,
        "simulation_mode": settings.simulation_mode,
        # False = pages can't be marked right now (no n8n webhook and no
        # practice mode); they wait and are retried.
        "marking_ready": bool(settings.n8n_batch_webhook_url) or bool(settings.simulate_scans),
        # Latest page activity for this batch (item created/updated), null if none.
        "last_item_at": _latest_item_at(recent + attention),
        "attention_items": _with_review_status(sb, batch_id, _with_error_codes(attention)),
        "recent_items": _with_review_status(sb, batch_id, _with_error_codes(recent)),
    }


@router.get("/{batch_id}/flags")
def get_batch_flags(batch_id: str, user: CurrentUser = Depends(require_staff)):
    _load_batch(batch_id)
    sb = get_supabase()
    return _with_review_status(sb, batch_id, _with_error_codes(
        sb.table("scan_batch_items").select(ITEM_COLUMNS)
        .eq("batch_id", batch_id).eq("status", "flagged").order("created_at", desc=True).execute().data or []
    ))


class CloseBatch(BaseModel):
    force: bool = False  # close even though pages are still processing


def _close(batch_id: str, user: CurrentUser, force_closed: bool) -> dict:
    res = get_supabase().table("scan_batches").update({
        "status": "closed",
        "closed_at": _now().isoformat(),
        "closed_by": user.id,
        "force_closed": force_closed,
    }).eq("id", batch_id).eq("status", "open").execute()
    if not res.data:
        raise HTTPException(status_code=409, detail={"code": "not_open", "message": "Batch is already closed."})
    audit(user.id, "scan_batch_closed", {"batch_id": batch_id, "force_closed": force_closed})
    return {"id": batch_id, "status": "closed"}


def can_close_normally(batch: dict, user: CurrentUser) -> bool:
    """Creator, the staff member currently bound to the batch's station, or an admin."""
    if user.is_admin or batch.get("created_by") == user.id:
        return True
    st = (
        get_supabase().table("stations").select("current_staff_id")
        .eq("id", batch["station_id"]).limit(1).execute().data
    )
    return bool(st and st[0].get("current_staff_id") == user.id)


@router.post("/{batch_id}/close")
def close_batch(batch_id: str, body: CloseBatch | None = None, user: CurrentUser = Depends(require_staff)):
    b = _load_batch(batch_id)
    if b["status"] != "open":
        raise HTTPException(status_code=409, detail={"code": "not_open", "message": "Batch is already closed."})
    if not can_close_normally(b, user):
        raise HTTPException(status_code=403, detail={
            "code": "not_your_batch",
            "message": "Only the staff member who started this batch, the person logged in at its station, or an SAO admin can close it. "
                       "Otherwise use force-close once it has been inactive long enough.",
        })
    processing = _item_counts(batch_id)["processing"]
    if processing and not (body and body.force):
        raise HTTPException(status_code=409, detail={
            "code": "items_processing",
            "message": f"{processing} page(s) are still being processed. Wait for them, or close anyway.",
            "processing": processing,
        })
    # Closing with flagged items is allowed; the UI confirms first.
    return _close(batch_id, user, force_closed=False)


@router.post("/{batch_id}/force-close")
def force_close_batch(batch_id: str, user: CurrentUser = Depends(require_staff)):
    """For batches abandoned on another station: allowed once there has been no
    activity (last scan, or creation if nothing was scanned) for
    ABANDONED_BATCH_HOURS."""
    b = _load_batch(batch_id)
    if b["status"] != "open":
        raise HTTPException(status_code=409, detail={"code": "not_open", "message": "Batch is already closed."})
    hours = get_settings().abandoned_batch_hours
    last = last_activity(b)
    if last and last > _now() - timedelta(hours=hours):
        raise HTTPException(status_code=409, detail={
            "code": "not_abandoned",
            "message": f"This batch had activity less than {hours:g}h ago, so it can't be force-closed yet.",
            "last_activity_at": last.isoformat(),
        })
    return _close(batch_id, user, force_closed=True)
