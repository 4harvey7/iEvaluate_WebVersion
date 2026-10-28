"""Pure helpers for the website review page (routers/reviews.py): turning
failed_scan_queue / import_errors rows into what the page shows, the checks an
edit must pass, and the payloads for the existing n8n correction webhooks.
No DB, no network, so everything here is unit tested directly.

Rows come from two writers the website does not control (n8n and the Flutter
app), so every field is read defensively: missing / odd values fall back to
the batch's own instructor and subject and a match of 'unchecked'.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from postgrest.exceptions import APIError

from app.db import is_valid_uuid
from app.logic import parse_ts
from app.station_status import is_missing_column_error

# ---------------------------------------------------------------- constants
MATCH_VALUES = ("same", "found", "not_found", "ambiguous", "unreadable", "unchecked")
INSTRUCTOR_MISMATCH_REASONS = (
    "instructor_mismatch_found", "instructor_mismatch_not_found", "instructor_mismatch_ambiguous",
)
SUBJECT_MISMATCH_REASONS = (
    "subject_mismatch_found", "subject_mismatch_not_found", "subject_mismatch_ambiguous",
)
MISMATCH_REASONS = INSTRUCTOR_MISMATCH_REASONS + SUBJECT_MISMATCH_REASONS
FORM_MATCH_FAILED = "form_match_failed"

RATING_KEYS = tuple(f"m{i}" for i in range(1, 11)) + tuple(f"p{i}" for i in range(1, 11))
LETTER_RATINGS = {"O": 5, "VS": 4, "S": 3, "F": 2, "US": 1}
# Teacher rule (2026-10-08): a form with fewer answered items is discarded, never credited
# (n8n stores such a scan as discarded; migration 014 leaves such a raw row out of the means).
MIN_ANSWERED = 5

QUEUE_PENDING = "pending"
IMPORT_PENDING = "pending"
REVIEW_STATES = ("submitting", "submit_failed")
REVIEW_ACTIONS = ("approved", "rerouted", "rejected")

# A correction that n8n has not confirmed after this long is shown as failed
# (the webhook timeout is 180 s, so n8n is long finished by then).
SUBMIT_STALE_SECONDS = 600.0
# "Someone is looking at this form" marker; the editor refreshes it every 60 s.
CLAIM_TTL_SECONDS = 300.0
# The `since` cursor re-sends rows changed a little before it (clock/commit races).
SINCE_OVERLAP_SECONDS = 5.0

QUEUE_BASE_COLUMNS = (
    "id, task_id, user_id, term_id, table_found, grid_source, partial_data, status, "
    "validated_by, validated_at, review_reasons, omr_comparison, validation_status, "
    "scan_image_id, batch_id, created_at"
)
QUEUE_REVIEW_COLUMNS = (
    "updated_at, review_version, review_state, review_action, review_error, "
    "review_submitted_at, review_claimed_by, review_claimed_at, review_correction"
)
IMPORT_BASE_COLUMNS = (
    "id, error_type, source, status, resolved_at, resolved_by, raw_instructor_name, "
    "raw_subject_name, raw_student_id, raw_term_id, raw_data, task_id, review_reasons, "
    "scan_image_id, created_at"
)
IMPORT_REVIEW_COLUMNS = (
    "updated_at, review_version, review_state, review_error, review_submitted_at, "
    "review_claimed_by, review_claimed_at"
)
REVIEW_COLUMN_NAMES = (
    "updated_at", "review_version", "review_state", "review_action", "review_error",
    "review_submitted_at", "review_claimed_by", "review_claimed_at", "review_correction",
)

WEB_TASK_RE = re.compile(r"^web:([0-9a-fA-F-]{36}):")

# Numbering-machine stamp (migration 015): digits only, 3-12 long, kept exactly as
# printed (leading zeros too). One stamp per term: (term_id, stamp_no) is unique in
# the raw results table.
STAMP_RE = re.compile(r"^[0-9]{3,12}$")
STAMP_SOURCES = ("gemini+python", "gemini", "staff", "override")
STAMP_STATUSES = ("ok", "unconfirmed", "missing", "unreadable", "multiple")
STAMP_REASONS = ("stamp_missing", "stamp_unreadable", "multiple_stamps", "stamp_unconfirmed")
DUPLICATE_STAMP = "duplicate_stamp"   # review reason and review_correction.rule of an auto-rejected form
QUEUE_STAMP_COLUMNS = "stamp_no, stamp_source"
STAMP_COLUMN_NAMES = ("stamp_no", "stamp_source")
STAMP_MIGRATION_MSG = (
    "Stamp numbers need a database update first. Ask IT to apply migrations/015_stamp_no.sql."
)

MIGRATION_REQUIRED_MSG = (
    "This needs a database update first. Ask IT to apply migrations/005_scan_review_queue.sql."
)
NOT_CONFIRMED_MSG = (
    "Marking did not confirm this correction within 10 minutes. Check the form and send it again."
)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    """UTC ISO string with a 'Z' (no '+' to escape in a query string)."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def since_filter_value(since: datetime) -> str:
    return iso(since - timedelta(seconds=SINCE_OVERLAP_SECONDS))


# ---------------------------------------------------------------- migration 005
def is_missing_review_column(exc: Exception) -> bool:
    """True when a query failed because migration 005 has not been applied
    (undefined column / PostgREST schema cache miss for a review column)."""
    if not isinstance(exc, APIError):
        return False
    code = str(getattr(exc, "code", "") or "")
    msg = str(getattr(exc, "message", "") or "").lower()
    if (code in ("42703", "PGRST204") or "column" in msg or "schema cache" in msg) and any(
        c in msg for c in REVIEW_COLUMN_NAMES
    ):
        return True
    # Same rule as the watcher columns for a bare "undefined column" error.
    return is_missing_column_error(exc) and "watcher_" not in msg


# ---------------------------------------------------------------- migration 015
def is_missing_stamp_column(exc: Exception) -> bool:
    """True when a query failed because migration 015 (stamp_no / stamp_source)
    has not been applied yet."""
    if not isinstance(exc, APIError):
        return False
    code = str(getattr(exc, "code", "") or "")
    msg = str(getattr(exc, "message", "") or "").lower()
    return (code in ("42703", "PGRST204") or "column" in msg or "schema cache" in msg) and any(
        c in msg for c in STAMP_COLUMN_NAMES)


# ---------------------------------------------------------------- errors
class ReviewConflict(Exception):
    """An action that cannot be done in the row's current state."""

    def __init__(self, http_status: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status = http_status
        self.code = code
        self.message = message
        self.extra = extra

    def detail(self) -> dict:
        return {"code": self.code, "message": self.message, **self.extra}


def version_of(row: dict | None) -> int:
    try:
        return int((row or {}).get("review_version") or 0)
    except (TypeError, ValueError):
        return 0


def submission_in_progress(row: dict, now: datetime | None = None) -> bool:
    """A website correction was sent less than 10 minutes ago and has not been
    answered yet (a second submit meanwhile could credit the form twice)."""
    if (row or {}).get("review_state") != "submitting":
        return False
    at = parse_ts(row.get("review_submitted_at")) or parse_ts(row.get("updated_at"))
    if at is None:
        return False
    return ((now or now_utc()) - at).total_seconds() < SUBMIT_STALE_SECONDS


def submission_stale(row: dict, now: datetime | None = None) -> bool:
    return (row or {}).get("review_state") == "submitting" and not submission_in_progress(row, now)


def check_can_act(row: dict, expected_version: int, now: datetime | None = None,
                  pending_status: str = QUEUE_PENDING) -> None:
    """Raise ReviewConflict unless the row is still waiting for review, no
    correction is in flight, and it is exactly the version the reviewer saw."""
    current = version_of(row)
    status = str(row.get("status") or "")
    if status != pending_status:
        raise ReviewConflict(409, "already_resolved",
                             "This form has already been dealt with, so nothing was changed.",
                             status=status, current_version=current)
    if submission_in_progress(row, now):
        raise ReviewConflict(409, "in_progress",
                             "This form is being sent to marking right now. Wait a moment and check again.",
                             current_version=current)
    if int(expected_version) != current:
        raise ReviewConflict(409, "stale_row",
                             "This form was changed while you were editing it (for example on the phone app). "
                             "Your edits are kept: look at the new version, then try again.",
                             current_version=current)


# ---------------------------------------------------------------- small readers
def as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip().startswith("{"):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except ValueError:
            return {}
    return {}


def _text(value: Any, limit: int = 500) -> str:
    if value is None or isinstance(value, (dict, list, bool)):
        return ""
    s = " ".join(str(value).split())
    return "" if s.lower() in ("null", "undefined", "none") else s[:limit]


def _uuid_or_none(value: Any) -> str | None:
    s = str(value).strip() if value is not None else ""
    return s.lower() if is_valid_uuid(s) else None


def _score(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return round(max(0.0, min(1.0, f)), 2)


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in ("true", "t", "1", "yes") if value is not None else False


def reasons_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [s for s in (_text(r, 300) for r in value) if s][:50]
    if isinstance(value, str):
        s = value.strip()
        if s.startswith("{") and s.endswith("}"):  # Postgres array literal
            return [p.strip().strip('"') for p in s[1:-1].split(",") if p.strip()][:50]
        return [s[:300]] if s else []
    return []


def full_name(row: dict | None) -> str:
    if not row:
        return ""
    return " ".join(p.strip() for p in (row.get("first_name"), row.get("last_name")) if isinstance(p, str) and p.strip())


def batch_id_from_task(task_id: Any) -> str | None:
    m = WEB_TASK_RE.match(str(task_id or ""))
    return m.group(1).lower() if m and is_valid_uuid(m.group(1)) else None


# ---------------------------------------------------------------- ratings
def normalize_rating(value: Any) -> int | None:
    """1..5, letter ratings (O/VS/S/F/US) mapped like the Flutter screen, else None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        f = float(value)
        return int(f) if f.is_integer() and 1 <= f <= 5 else None
    s = str(value).strip()
    if not s or s.lower() in ("blank", "null", "none"):
        return None
    mapped = LETTER_RATINGS.get(s.upper())
    if mapped:
        return mapped
    try:
        f = float(s)
    except ValueError:
        return None
    return int(f) if f.is_integer() and 1 <= f <= 5 else None


def _comparison_list(omr_comparison: Any) -> list:
    oc = omr_comparison
    if isinstance(oc, str):
        try:
            oc = json.loads(oc)
        except ValueError:
            oc = None
    if isinstance(oc, dict) and isinstance(oc.get("comparisons"), list):
        return oc["comparisons"]
    if isinstance(oc, list):
        return oc
    return []


def _from_map(m: Any, lower: str, upper: str) -> int | None:
    if not isinstance(m, dict):
        return None
    for k in (lower, upper):
        if m.get(k) is not None:
            v = normalize_rating(m.get(k))
            if v is not None:
                return v
    return None


def _one_rating(key: str, partial: dict, py_raw: dict, comps: list, row: dict) -> int | None:
    """Same search order as the Flutter correction screen (_extractScore)."""
    prefix, index = key[0], int(key[1:])
    lower, upper = key, key.upper()
    ratings = partial.get("ratings")
    if isinstance(ratings, dict):
        v = _from_map(ratings, lower, upper)
        if v is not None:
            return v
        if prefix == "m":
            sec = ratings.get("management") or ratings.get("Management")
        else:
            sec = ratings.get("performance") or ratings.get("Performance")
        if isinstance(sec, dict):
            v = _from_map(sec, lower, upper)
            if v is None and sec.get(str(index)) is not None:
                v = normalize_rating(sec.get(str(index)))
            if v is not None:
                return v
        elif isinstance(sec, list) and index - 1 < len(sec):
            item = sec[index - 1]
            if isinstance(item, dict):
                v = normalize_rating(item.get("score") or item.get("rating_name") or item.get("answer"))
            else:
                v = normalize_rating(item)
            if v is not None:
                return v
    for comp in comps:
        if isinstance(comp, dict) and str(comp.get("question") or "").lower() == lower:
            for f in ("used", "python", "gemini"):
                v = normalize_rating(comp.get(f))
                if v is not None:
                    return v
    for source in (partial, partial.get("scores"), py_raw, partial.get("gemini_scores"), row, row.get("raw_scores")):
        v = _from_map(source, lower, upper)
        if v is not None:
            return v
    return None


def extract_ratings(partial: Any, omr_comparison: Any = None, row: dict | None = None) -> dict:
    p = as_dict(partial)
    py_raw = as_dict(p.get("python_raw_ratings"))
    comps = _comparison_list(omr_comparison)
    return {k: _one_rating(k, p, py_raw, comps, row or {}) for k in RATING_KEYS}


def extract_import_ratings(raw_data: Any) -> dict:
    raw = as_dict(raw_data)
    out = {}
    for k in RATING_KEYS:
        v = normalize_rating(raw.get(k.upper()))
        out[k] = v if v is not None else normalize_rating(raw.get(k))
    return out


def clean_comparisons(value: Any) -> list[dict]:
    out = []
    for c in _comparison_list(value)[:20]:
        if not isinstance(c, dict):
            continue
        q = _text(c.get("question"), 10).lower()
        if q not in RATING_KEYS:
            continue
        agree = c.get("agree")
        out.append({
            "question": q,
            "python": normalize_rating(c.get("python")),
            "gemini": normalize_rating(c.get("gemini")),
            "used": normalize_rating(c.get("used")),
            "agree": agree if isinstance(agree, bool) else None,
            "python_weak": c.get("python_weak") is True,
        })
    return out


def validate_ratings(value: Any) -> dict:
    """Reviewer ratings -> {m1..p10: 1..5 | None}. Raises ValueError on bad input."""
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("ratings must be an object")
    unknown = [k for k in value if k not in RATING_KEYS]
    if unknown:
        raise ValueError(f"unknown rating {unknown[0]!r}")
    out = {}
    for k in RATING_KEYS:
        v = value.get(k)
        if v is None or v == "":
            out[k] = None
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float, str)):
            raise ValueError(f"rating {k} must be 1-5 or blank")
        n = normalize_rating(v)
        if n is None or (isinstance(v, str) and not v.strip().isdigit()):
            raise ValueError(f"rating {k} must be 1-5 or blank")
        out[k] = n
    return out


def answered_count(ratings: dict) -> int:
    """How many of the 20 items have a rating (validate_ratings output)."""
    return sum(1 for k in RATING_KEYS if ratings.get(k) is not None)


def clean_student_id(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or ""))[:50]


def clean_remarks(value: Any) -> str:
    return str(value or "").strip()[:4000]


# ---------------------------------------------------------------- stamp numbers
def normalize_stamp_no(value: Any) -> str | None:
    """A stamp number typed by staff -> digits string, None when blank.
    Spaces are ignored; anything else must be 3-12 digits (raises ValueError)."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("stamp number must be digits")
    s = re.sub(r"\s+", "", str(value))
    if not s:
        return None
    if not STAMP_RE.match(s):
        raise ValueError("stamp number must be 3 to 12 digits")
    return s


def stamp_no_or_none(value: Any) -> str | None:
    """A stored stamp number, or None when it is missing / not a valid stamp."""
    s = str(value).strip() if isinstance(value, (str, int)) and not isinstance(value, bool) else ""
    return s if STAMP_RE.match(s) else None


def stamp_source_or_none(value: Any, stamp_no: str | None) -> str | None:
    if not stamp_no:
        return None
    return value if value in STAMP_SOURCES else None


def stamp_candidates(partial: dict) -> list[str]:
    out: list[str] = []
    for c in (partial.get("stamp_candidates") if isinstance(partial.get("stamp_candidates"), list) else [])[:10]:
        s = stamp_no_or_none(c)
        if s and s not in out:
            out.append(s)
    return out


def has_stamp_reason(reasons: list[str]) -> bool:
    return any(r in STAMP_REASONS for r in reasons)


def stamp_source_for_approve(row: dict, stamp_no: str | None) -> str | None:
    """'override' when staff keep the stamp an override set, else 'staff' (None without a stamp)."""
    if not stamp_no:
        return None
    if row.get("stamp_source") == "override" and stamp_no_or_none(row.get("stamp_no")) == stamp_no:
        return "override"
    return "staff"


def clean_duplicate_of(value: Any) -> dict | None:
    """The 'already stored' row (DUP object of the stamp contract), read defensively."""
    d = as_dict(value)
    if not d:
        return None
    raw_id = d.get("raw_id")
    if isinstance(raw_id, bool):
        raw_id = None
    try:
        raw_id = int(raw_id) if raw_id is not None and str(raw_id).strip() != "" else None
    except (TypeError, ValueError):
        raw_id = None
    task_id = _text(d.get("task_id"), 200) or None
    out = {
        "raw_id": raw_id,
        "task_id": task_id,
        "batch_id": _uuid_or_none(d.get("batch_id")) or batch_id_from_task(task_id),
        "submitted_date": _text(d.get("submitted_date"), 30) or None,
        "instructor_id": _uuid_or_none(d.get("instructor_id")),
        "subject_id": _uuid_or_none(d.get("subject_id")),
        "term_id": _uuid_or_none(d.get("term_id")),
        "stamp_no": stamp_no_or_none(d.get("stamp_no")),
    }
    return out if any(v is not None for v in out.values()) else None


def duplicate_from_raw(raw: dict) -> dict:
    """DUP object from a sast_all_raw_data_survey row."""
    return clean_duplicate_of({
        "raw_id": raw.get("id"), "task_id": raw.get("task_id"), "submitted_date": raw.get("submitted_date"),
        "instructor_id": raw.get("instructor_ID"), "subject_id": raw.get("subject_id"),
        "term_id": raw.get("term_id"), "stamp_no": raw.get("stamp_no"),
    }) or {}


def enrich_duplicate(dup: dict | None, names: dict | None = None, dup_ctx: dict | None = None) -> dict | None:
    """DUP + what the page shows: instructor name, subject code and, for a page
    scanned on the website, the batch item that opens it in the page viewer."""
    if not dup:
        return None
    names = names or {}
    ctx = dup_ctx or {}
    subj = (ctx.get("subjects") or {}).get(dup.get("subject_id") or "") or {}
    item_id = (ctx.get("items") or {}).get(dup.get("task_id") or "")
    return {
        **dup,
        "instructor_name": names.get(str(dup.get("instructor_id"))) or (ctx.get("people") or {}).get(
            str(dup.get("instructor_id"))) or None,
        "subject_code": subj.get("code") or None,
        "subject_name": subj.get("name") or None,
        "from_website": bool(dup.get("batch_id")),
        "item_id": item_id if dup.get("batch_id") and item_id else None,
    }


def _stamp_note(value: Any) -> dict | None:
    """partial_data.stamp_override / stamp_fix -> what the page shows."""
    d = as_dict(value)
    if not d:
        return None
    return {
        "by_name": _text(d.get("by_name"), 200) or None,
        "note": _text(d.get("note"), 500) or None,
        "at": _text(d.get("at"), 40) or None,
        "previous_stamp": stamp_no_or_none(d.get("previous_stamp")),
        "new_stamp": stamp_no_or_none(d.get("new_stamp")),
    }


# ---------------------------------------------------------------- suggestions
def clean_instructor_candidates(value: Any) -> list[dict]:
    out = []
    for c in (value if isinstance(value, list) else [])[:5]:
        if not isinstance(c, dict):
            continue
        cid, name = _uuid_or_none(c.get("id")), _text(c.get("name"), 200)
        if not cid or not name:
            continue
        out.append({"id": cid, "name": name, "score": _score(c.get("score")),
                    "is_placeholder": c.get("is_placeholder") is True, "is_batch": c.get("is_batch") is True})
    return out


def clean_subject_candidates(value: Any) -> list[dict]:
    out = []
    for c in (value if isinstance(value, list) else [])[:5]:
        if not isinstance(c, dict):
            continue
        cid = _uuid_or_none(c.get("id"))
        code, name = _text(c.get("code"), 60), _text(c.get("name"), 200)
        if not cid or not (code or name):
            continue
        out.append({"id": cid, "code": code or None, "name": name or None,
                    "score": _score(c.get("score")), "is_batch": c.get("is_batch") is True})
    return out


def match_value(value: Any) -> str:
    v = str(value).strip().lower() if isinstance(value, str) else ""
    return v if v in MATCH_VALUES else "unchecked"


def suggestions(partial: Any, batch_ctx: dict) -> dict:
    """Suggested instructor/subject from n8n's form check (partial_data), or
    the batch's own values when the form was not checked (older n8n build,
    lookup error) - never a guess the reviewer can't see the reason for."""
    p = as_dict(partial)
    bi = batch_ctx.get("instructor") or {}
    bs = batch_ctx.get("subject") or {}
    batch_instructor = {
        "id": bi.get("id") or _uuid_or_none(p.get("batch_instructor_id")),
        "name": bi.get("name") or _text(p.get("batch_instructor_name"), 200) or None,
    }
    batch_subject = {
        "id": bs.get("id") or _uuid_or_none(p.get("batch_subject_id")),
        "code": bs.get("code") or _text(p.get("batch_subject_code"), 60) or None,
        "name": bs.get("name") or _text(p.get("batch_subject_name"), 200) or None,
    }
    im, sm = match_value(p.get("instructor_match")), match_value(p.get("subject_match"))

    sid = _uuid_or_none(p.get("suggested_instructor_id")) if im != "unchecked" else None
    if sid:
        name = _text(p.get("suggested_instructor_name"), 200) or (
            batch_instructor["name"] if sid == batch_instructor["id"] else _text(p.get("instructor"), 200))
        suggested_instructor = {"id": sid, "name": name or None, "confidence": _score(p.get("instructor_match_confidence"))}
    else:
        suggested_instructor = {**batch_instructor, "confidence": None}

    subid = _uuid_or_none(p.get("suggested_subject_id")) if sm != "unchecked" else None
    if subid:
        same = subid == batch_subject["id"]
        suggested_subject = {
            "id": subid,
            "code": _text(p.get("suggested_subject_code"), 60) or (batch_subject["code"] if same else _text(p.get("subject"), 60)) or None,
            "name": _text(p.get("suggested_subject_name"), 200) or (batch_subject["name"] if same else None) or None,
            "confidence": _score(p.get("subject_match_confidence")),
        }
    else:
        suggested_subject = {**batch_subject, "confidence": None}

    return {
        "instructor_match": im,
        "subject_match": sm,
        "batch_instructor": batch_instructor,
        "batch_subject": batch_subject,
        "suggested_instructor": suggested_instructor,
        "suggested_subject": suggested_subject,
        "instructor_candidates": clean_instructor_candidates(p.get("instructor_candidates")) if im != "unchecked" else [],
        "subject_candidates": clean_subject_candidates(p.get("subject_candidates")) if sm != "unchecked" else [],
    }


def review_action_for(instructor_id: str, subject_id: str, batch: dict) -> str:
    """'rerouted' when the form is credited to anything but the batch's own
    instructor + subject, else 'approved'."""
    same = (str(instructor_id).lower() == str(batch.get("instructor_id") or "").lower()
            and str(subject_id).lower() == str(batch.get("subject_id") or "").lower())
    return "approved" if same else "rerouted"


# ---------------------------------------------------------------- serializers
def claim_is_live(row: dict, now: datetime | None = None) -> bool:
    if not row.get("review_claimed_by"):
        return False
    at = parse_ts(row.get("review_claimed_at"))
    return at is not None and ((now or now_utc()) - at).total_seconds() < CLAIM_TTL_SECONDS


def _claimed(row: dict, names: dict, now: datetime) -> dict | None:
    if not claim_is_live(row, now):
        return None
    uid = str(row.get("review_claimed_by"))
    return {"id": uid, "name": names.get(uid) or None}


def _stamp(row: dict) -> str | None:
    return row.get("updated_at") or row.get("validated_at") or row.get("resolved_at") or row.get("created_at")


def is_auto_duplicate(row: dict) -> bool:
    """n8n rejected the form by itself: its stamp was already stored for the term."""
    corr = as_dict(row.get("review_correction"))
    return row.get("status") == "discarded" and corr.get("rule") == DUPLICATE_STAMP


def stamp_fields(row: dict, names: dict | None = None, stamp_ctx: dict | None = None) -> dict:
    """Stamp number part of a serialized queue row. Rows from before migration
    015 (no stamp columns / keys) come out as all-empty."""
    p = as_dict(row.get("partial_data"))
    stamp_no = stamp_no_or_none(row.get("stamp_no"))
    status = p.get("stamp_status") if p.get("stamp_status") in STAMP_STATUSES else None
    saved = ((stamp_ctx or {}).get("saved") or {}).get(row.get("task_id") or "")
    return {
        "stamp_no": stamp_no,
        "stamp_source": stamp_source_or_none(row.get("stamp_source"), stamp_no),
        "stamp_status": status,
        "stamp_candidates": stamp_candidates(p),
        "duplicate_of": enrich_duplicate(clean_duplicate_of(p.get("duplicate_of")), names, stamp_ctx),
        "auto_duplicate": is_auto_duplicate(row),
        "stamp_override": _stamp_note(p.get("stamp_override")),
        # The stored result of a credited form (admins only: "Edit stamp").
        "saved_raw": saved if isinstance(saved, dict) else None,
    }


def stamp_lookup_ids(rows: list[dict]) -> dict:
    """Ids the stamp part of the rows needs from the database (see stamp_fields)."""
    people, subjects, tasks = set(), set(), set()
    for r in rows:
        dup = clean_duplicate_of(as_dict(r.get("partial_data")).get("duplicate_of"))
        if not dup:
            continue
        if dup.get("instructor_id"):
            people.add(dup["instructor_id"])
        if dup.get("subject_id"):
            subjects.add(dup["subject_id"])
        if dup.get("batch_id") and dup.get("task_id"):
            tasks.add(dup["task_id"])
    return {"people": sorted(people), "subjects": sorted(subjects), "tasks": sorted(tasks)}


def serialize_queue_row(row: dict, batch_ctx: dict, names: dict | None = None, now: datetime | None = None,
                        stamp_ctx: dict | None = None) -> dict:
    names = names or {}
    now = now or now_utc()
    p = as_dict(row.get("partial_data"))
    corr = as_dict(row.get("review_correction"))
    omr = row.get("omr_comparison")
    agreement = p.get("agreement_percent")
    if not isinstance(agreement, (int, float)) or isinstance(agreement, bool):
        agreement = as_dict(omr).get("agreement_percent")
    if not isinstance(agreement, (int, float)) or isinstance(agreement, bool):
        agreement = None
    validated_by = row.get("validated_by") or corr.get("reviewed_by")
    state = row.get("review_state") if row.get("review_state") in REVIEW_STATES else None
    action = row.get("review_action") if row.get("review_action") in REVIEW_ACTIONS else None
    return {
        "id": row.get("id"),
        "task_id": row.get("task_id"),
        "status": row.get("status"),
        "review_state": state,
        "review_action": action,
        "review_error": _text(row.get("review_error"), 500) or None,
        "review_version": version_of(row),
        "updated_at": _stamp(row),
        "created_at": row.get("created_at"),
        "filename": _text(p.get("filename"), 200) or None,
        "review_reasons": reasons_list(row.get("review_reasons")),
        "review_note": _text(p.get("review_note"), 3000) or None,
        "student_id": clean_student_id(p.get("student_id")),
        "remarks": str(p.get("remarks") or "").strip()[:4000] if not isinstance(p.get("remarks"), (dict, list)) else "",
        "ratings": extract_ratings(p, omr, row),
        "agreement_percent": agreement,
        "comparisons": clean_comparisons(p.get("comparisons") if isinstance(p.get("comparisons"), list) else omr),
        "table_found": row.get("table_found") if isinstance(row.get("table_found"), bool) else None,
        "ocr_instructor": _text(p.get("ocr_instructor"), 200) or None,
        "ocr_subject": _text(p.get("ocr_subject"), 200) or None,
        **suggestions(p, batch_ctx),
        "has_image": bool(row.get("scan_image_id")),
        "claimed_by": _claimed(row, names, now),
        "claimed_at": row.get("review_claimed_at") if claim_is_live(row, now) else None,
        "validated_at": row.get("validated_at"),
        "validated_by_name": (names.get(str(validated_by)) if validated_by else None) or _text(corr.get("reviewed_by_name"), 200) or None,
        "review_note_by_reviewer": _text(corr.get("note"), 500) or None,
        # n8n stored it already discarded (min-answered rule), no person involved
        "auto_discarded": corr.get("auto") is True and corr.get("action") == "rejected",
        # What the website last sent for this form (prefills a retry after a
        # failed send; "credited to" once done). None if nothing was sent.
        "correction": _correction_summary(corr),
        # Numbering-machine stamp (migration 015; auto_duplicate = discarded by n8n as already scanned).
        **stamp_fields(row, names, stamp_ctx),
    }


def _correction_summary(corr: dict) -> dict | None:
    iid = _uuid_or_none(corr.get("instructor_id"))
    if not iid:
        return None
    ratings = corr.get("ratings") if isinstance(corr.get("ratings"), dict) else {}
    return {
        "action": corr.get("action") if corr.get("action") in REVIEW_ACTIONS else None,
        "instructor": {"id": iid, "name": _text(corr.get("instructor_name"), 200) or None},
        "subject": {"id": _uuid_or_none(corr.get("subject_id")), "code": _text(corr.get("subject_code"), 60) or None},
        "student_id": clean_student_id(corr.get("student_id")),
        "remarks": str(corr.get("remarks") or "")[:4000],
        "ratings": {k: normalize_rating(ratings.get(k)) for k in RATING_KEYS},
        "reviewed_by_name": _text(corr.get("reviewed_by_name"), 200) or None,
        "submitted_at": corr.get("submitted_at"),
        "stamp_no": stamp_no_or_none(corr.get("stamp_no")),
        "no_stamp": corr.get("no_stamp") is True,
    }


def serialize_import_error(row: dict, names: dict | None = None, now: datetime | None = None) -> dict:
    names = names or {}
    now = now or now_utc()
    raw = as_dict(row.get("raw_data"))
    state = row.get("review_state") if row.get("review_state") in REVIEW_STATES else None
    student = row.get("raw_student_id") or raw.get("Student ID") or raw.get("student_id")
    remarks = raw.get("Remarks & Suggestions") or raw.get("remarks") or ""
    resolved_by = row.get("resolved_by")
    return {
        "id": row.get("id"),
        "task_id": row.get("task_id"),
        "status": row.get("status"),
        "error_type": _text(row.get("error_type"), 60) or None,
        "source": _text(row.get("source"), 30) or None,
        "review_state": state,
        "review_error": _text(row.get("review_error"), 500) or None,
        "review_version": version_of(row),
        "updated_at": _stamp(row),
        "created_at": row.get("created_at"),
        "resolved_at": row.get("resolved_at"),
        "resolved_by_name": names.get(str(resolved_by)) if resolved_by else None,
        "raw_instructor_name": _text(row.get("raw_instructor_name"), 200) or None,
        "raw_subject_name": _text(row.get("raw_subject_name"), 200) or None,
        "raw_term_id": row.get("raw_term_id"),
        "student_id": clean_student_id(student),
        "remarks": str(remarks).strip()[:4000] if not isinstance(remarks, (dict, list)) else "",
        "ratings": extract_import_ratings(raw),
        "date": _text(raw.get("Date") or raw.get("date"), 50) or None,
        "review_reasons": reasons_list(row.get("review_reasons")),
        "review_note": _text(raw.get("review_note"), 3000) or None,
        "comparisons": clean_comparisons(raw.get("omr_comparisons")),
        "has_image": bool(row.get("scan_image_id")),
        "claimed_by": _claimed(row, names, now),
        "claimed_at": row.get("review_claimed_at") if claim_is_live(row, now) else None,
    }


def people_ids(queue: list[dict], imports: list[dict]) -> list[str]:
    ids = set()
    for r in queue:
        for v in (r.get("validated_by"), r.get("review_claimed_by"), as_dict(r.get("review_correction")).get("reviewed_by")):
            if is_valid_uuid(str(v or "")):
                ids.add(str(v))
    for r in imports:
        for v in (r.get("resolved_by"), r.get("review_claimed_by")):
            if is_valid_uuid(str(v or "")):
                ids.add(str(v))
    return sorted(ids)


# ---------------------------------------------------------------- n8n payloads
def correction_payload(row: dict, instructor: dict, subject: dict, ratings: dict, student_id: str,
                       remarks: str, term_id: str | None, reviewer_id: str, timestamp: str,
                       stamp_no: str | None = None, stamp_source: str | None = None) -> dict:
    """Body for the existing 'sast-manual-correction' webhook (the same keys
    the Flutter correction screen sends), plus the ids, reviewer and the stamp
    number (null = the form has no stamp; stamp_source 'staff' | 'override')."""
    payload = {
        "failed_scan_id": row.get("id"),
        "task_id": row.get("task_id"),
        "user_id": row.get("user_id"),
        "term_id": term_id,
        "instructor": full_name(instructor),
        "instructor_id": instructor.get("id"),
        "subject": (subject.get("subject_code") or subject.get("subject_name") or "").strip(),
        "subject_id": subject.get("id"),
        "remarks": remarks,
        "student_id": student_id,
    }
    for k in RATING_KEYS:
        payload[k] = ratings.get(k) or 0  # blank = 0, as the Flutter screen sends it
    payload.update({
        "manually_corrected": True,
        "validation_status": "corrected",
        "correction_source": "website_review",
        "reviewed_by": reviewer_id,
        "timestamp": timestamp,
        "stamp_no": stamp_no or None,
        "stamp_source": (stamp_source if stamp_source in ("staff", "override") else "staff") if stamp_no else None,
    })
    return payload


def corrected_raw_data(raw_data: Any, student_id: str | None, remarks: str | None, ratings: dict | None) -> dict:
    """import_errors.raw_data with the reviewer's edits. Both key spellings are
    written because n8n reads 'M1' before 'm1'."""
    raw = dict(as_dict(raw_data))
    if student_id is not None:
        raw["Student ID"] = student_id
        raw["student_id"] = student_id
    if remarks is not None:
        raw["Remarks & Suggestions"] = remarks
        raw["remarks"] = remarks
    if ratings is not None:
        for k in RATING_KEYS:
            v = ratings.get(k) or 0
            raw[k.upper()] = v
            raw[k] = v
    return raw


def import_error_payload(row: dict, instructor: dict, subject: dict, raw_data: dict, term_id: str | None,
                         reviewer_id: str, timestamp: str) -> dict:
    """Body for the existing 'import-error-correction' webhook (the Flutter
    import-error screen's keys) plus the instructor's DB name and task_id."""
    return {
        "import_error_id": row.get("id"),
        "corrected_instructor_id": instructor.get("id"),
        "corrected_subject_id": subject.get("id"),
        "term_id": term_id,
        "resolved_by": reviewer_id,
        "raw_data": raw_data,
        "source": row.get("source") or "scan",
        "timestamp": timestamp,
        "instructor": full_name(instructor),
        "task_id": row.get("task_id"),
    }
