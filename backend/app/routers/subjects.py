import re

from fastapi import APIRouter, Depends, HTTPException, Query
from postgrest.exceptions import APIError
from pydantic import BaseModel, Field

from app.audit import audit
from app.db import get_supabase
from app.logic import clean_search_text, code_like_pattern, normalize_code, rank_subjects
from app.routers.departments import department_lookup
from app.security import CurrentUser, require_staff

router = APIRouter()

COLUMNS = "id, subject_code, subject_name, department_id"


def _with_dept(rows: list[dict]) -> list[dict]:
    depts = department_lookup()
    for r in rows:
        d = depts.get(int(r["department_id"])) if r.get("department_id") is not None else None
        r["department_name"] = d.get("d_name") if d else None
    return rows


def _find_by_normalized_code(norm: str) -> dict | None:
    if not norm:
        return None
    rows = get_supabase().table("subjects").select(COLUMNS).ilike("subject_code", code_like_pattern(norm)).limit(1000).execute().data or []
    for r in rows:
        if normalize_code(r.get("subject_code") or "") == norm:
            return r
    return None


@router.get("/search")
def search_subjects(q: str = Query(..., max_length=100), user: CurrentUser = Depends(require_staff)):
    text = clean_search_text(q)
    norm = normalize_code(text)
    if len(norm) < 2 and len(text) < 2:
        raise HTTPException(status_code=400, detail={"code": "query_too_short", "message": "Type at least 2 characters."})
    sb = get_supabase()
    by_code = sb.table("subjects").select(COLUMNS).ilike("subject_code", code_like_pattern(norm)).limit(50).execute().data or []
    by_name = sb.table("subjects").select(COLUMNS).ilike("subject_name", f"%{text}%").limit(20).execute().data if text else []
    merged = {r["id"]: r for r in (by_code + (by_name or []))}
    return _with_dept(rank_subjects(list(merged.values()), text)[:20])


class CreateSubject(BaseModel):
    subject_code: str = Field(min_length=1, max_length=30)
    subject_name: str = Field(min_length=1, max_length=200)
    department_id: int


@router.post("")
def create_subject(body: CreateSubject, user: CurrentUser = Depends(require_staff)):
    code = re.sub(r"\s+", " ", body.subject_code.strip().upper())
    name = " ".join(body.subject_name.split())
    norm = normalize_code(code)
    if len(norm) < 2 or not name:
        raise HTTPException(status_code=400, detail={"code": "invalid_subject", "message": "Subject code and name are required."})
    if body.department_id not in department_lookup():
        raise HTTPException(status_code=400, detail={"code": "invalid_department", "message": "Unknown department."})

    existing = _find_by_normalized_code(norm)
    if existing:
        raise HTTPException(status_code=409, detail={
            "code": "duplicate_subject", "message": "That subject already exists.", "existing_subject": _with_dept([existing])[0],
        })
    try:
        res = get_supabase().table("subjects").insert({
            "subject_code": code,
            "subject_name": name,
            "department_id": body.department_id,
        }).execute()
    except APIError as e:
        if getattr(e, "code", None) == "23505":
            existing = _find_by_normalized_code(norm)
            raise HTTPException(status_code=409, detail={
                "code": "duplicate_subject", "message": "That subject already exists.",
                "existing_subject": _with_dept([existing])[0] if existing else None,
            })
        raise
    created = {k: res.data[0].get(k) for k in ("id", "subject_code", "subject_name", "department_id")}
    audit(user.id, "subject_created", {"subject_id": created["id"], "subject_code": code})
    return _with_dept([created])[0]
