"""Score summary of one scanned page, stored on scan_batch_items.result_summary
(migration 010) so the live batch list can show it without another query
per row.

Where the numbers come from (n8n's answer to the backend carries no ratings,
see n8n/WEBSITE_BRANCH.md "Response"; nothing in n8n had to change):
  * success   -> the row n8n's "Website Raw Upsert" wrote to
                 sast_all_raw_data_survey BEFORE it answered, found by task_id
                 (m1..m10, p1..p10, the credited instructor and subject).
  * flagged   -> the failed_scan_queue row (same task_id): partial_data.ratings
                 as read by the OMR, the suggested instructor/subject, the
                 Python/Gemini agreement. Not credited yet.
  * duplicate -> the same task_id was stored by an earlier try: whichever of
                 the two tables has it (that IS the original page's result).
  * approved on the review page -> the reviewer's corrected values.
Rejected / error pages have no ratings (the list shows their reason).

`ratings` is kept in the stored summary for the page viewer but stripped
from the list payload (list_summary)."""
from __future__ import annotations

import logging
from typing import Any

from app import review_logic as rl

log = logging.getLogger("ievaluate.page_scores")

RAW = "sast_all_raw_data_survey"
FSQ = "failed_scan_queue"
SUMMARY_VERSION = 1
RAW_COLUMNS = "task_id, instructor, subject_id, student_id, " + ", ".join(rl.RATING_KEYS)
# Statuses whose result can be looked up in marking's own tables.
SCORED_STATUSES = ("success", "flagged", "duplicate")


def _avg(values: list[int]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def summarize_ratings(ratings: Any) -> dict:
    """{m1..p10} (any spelling rl.normalize_rating accepts) -> counts + averages.
    Blank answers are left out of the averages."""
    r = ratings if isinstance(ratings, dict) else {}
    clean = {k: rl.normalize_rating(r.get(k, r.get(k.upper()))) for k in rl.RATING_KEYS}
    m = [v for k, v in clean.items() if k.startswith("m") and v is not None]
    p = [v for k, v in clean.items() if k.startswith("p") and v is not None]
    return {
        "ratings": clean,
        "answered": len(m) + len(p),
        "questions": len(rl.RATING_KEYS),
        "average": _avg(m + p),
        "management_avg": _avg(m),
        "performance_avg": _avg(p),
    }


def _text(v: Any, limit: int = 200) -> str | None:
    return rl._text(v, limit) or None


def from_raw_row(row: dict, subject: dict | None = None) -> dict:
    """A stored (credited) result from sast_all_raw_data_survey."""
    subject = subject or {}
    return {
        "v": SUMMARY_VERSION,
        "source": "marking",
        "credited": True,
        **summarize_ratings({k: row.get(k) for k in rl.RATING_KEYS}),
        "instructor": _text(row.get("instructor")),
        "subject_code": _text(subject.get("subject_code"), 60),
        "subject_name": _text(subject.get("subject_name")),
        "student_id": rl.clean_student_id(row.get("student_id")) or None,
        "agreement_percent": None,
    }


def from_queue_row(row: dict) -> dict:
    """A flagged page (failed_scan_queue): what the reviewer credited once it
    was approved on the website, else the partial result waiting for review."""
    corr = rl.as_dict(row.get("review_correction"))
    if (row.get("status") == "validated" and corr.get("action") in ("approved", "rerouted")
            and isinstance(corr.get("ratings"), dict)):
        return {
            "v": SUMMARY_VERSION,
            "source": "review",
            "credited": True,
            **summarize_ratings(corr["ratings"]),
            "instructor": _text(corr.get("instructor_name")),
            "subject_code": _text(corr.get("subject_code"), 60),
            "subject_name": None,
            "student_id": rl.clean_student_id(corr.get("student_id")) or None,
            "agreement_percent": None,
        }
    p = rl.as_dict(row.get("partial_data"))
    s = rl.suggestions(p, {})
    agreement = p.get("agreement_percent")
    if not isinstance(agreement, (int, float)) or isinstance(agreement, bool):
        agreement = rl.as_dict(row.get("omr_comparison")).get("agreement_percent")
    if not isinstance(agreement, (int, float)) or isinstance(agreement, bool):
        agreement = None
    ratings = rl.extract_ratings(p, row.get("omr_comparison"), row)
    return {
        "v": SUMMARY_VERSION,
        "source": "review_queue",
        "credited": False,
        **summarize_ratings(ratings),
        "instructor": (s["suggested_instructor"] or {}).get("name") or _text(p.get("instructor")),
        "subject_code": (s["suggested_subject"] or {}).get("code") or _text(p.get("subject"), 60),
        "subject_name": (s["suggested_subject"] or {}).get("name"),
        "student_id": rl.clean_student_id(p.get("student_id")) or None,
        "agreement_percent": agreement,
    }


def from_correction(ratings: dict, instructor: dict, subject: dict, student_id: str | None) -> dict:
    """What a reviewer credited on the review page."""
    return {
        "v": SUMMARY_VERSION,
        "source": "review",
        "credited": True,
        **summarize_ratings(ratings),
        "instructor": rl.full_name(instructor) or None,
        "subject_code": _text(subject.get("subject_code"), 60),
        "subject_name": _text(subject.get("subject_name")),
        "student_id": rl.clean_student_id(student_id) or None,
        "agreement_percent": None,
    }


def list_summary(summary: Any) -> dict | None:
    """The compact part the live list needs (no per-question ratings)."""
    if not isinstance(summary, dict) or not summary:
        return None
    return {k: summary.get(k) for k in ("source", "credited", "answered", "questions", "average",
                                        "management_avg", "performance_avg", "instructor", "subject_code",
                                        "agreement_percent")}


def _first(res) -> dict | None:
    data = getattr(res, "data", None)
    return data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else None


def lookup(sb, task_id: str | None, status: str | None) -> dict | None:
    """The page's result from marking's tables, by task_id. None when there is
    nothing (rejected, error, not stored). Raises on database errors."""
    if not task_id or status not in SCORED_STATUSES:
        return None
    order = (FSQ, RAW) if status == "flagged" else (RAW, FSQ)
    for table in order:
        if table == RAW:
            row = _first(sb.table(RAW).select(RAW_COLUMNS).eq("task_id", task_id).limit(1).execute())
            if row:
                subject = None
                if row.get("subject_id"):
                    subject = _first(sb.table("subjects").select("subject_code, subject_name")
                                     .eq("id", str(row["subject_id"])).limit(1).execute())
                return from_raw_row(row, subject)
        else:
            # "*": the review columns (migration 005) may not exist yet
            row = _first(sb.table(FSQ).select("*").eq("task_id", task_id).limit(1).execute())
            if row:
                return from_queue_row(row)
    return None
