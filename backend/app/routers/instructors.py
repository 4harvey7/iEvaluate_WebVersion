import logging
import secrets
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.audit import audit
from app.db import get_supabase, is_valid_uuid
from app.name_match import is_near_duplicate, same_name, search_words_match, surname_parts
from app.routers.departments import department_lookup
from app.security import INSTRUCTOR_ROLES, CurrentUser, require_staff, role_maps

router = APIRouter()
log = logging.getLogger("ievaluate.instructors")


def _full_name(r: dict) -> str:
    return " ".join(p for p in (r.get("first_name"), r.get("last_name")) if p)


def get_instructor_if_eligible(instructor_id: str) -> dict | None:
    """An instructor is a placeholder account, or a non-disabled user with a
    department_table role of FULL-TIME / PART-TIME / DEPARTMENT_HEAD."""
    if not is_valid_uuid(instructor_id):
        return None
    sb = get_supabase()
    res = (
        sb.table("user_info")
        .select("id, first_name, last_name, account_status, disabled, is_placeholder")
        .eq("id", instructor_id)
        .limit(1)
        .execute()
    )
    if not res.data:
        return None
    ui = res.data[0]
    if ui.get("is_placeholder"):
        return ui
    if ui.get("disabled") is True or str(ui.get("account_status") or "").lower() == "disabled":
        return None
    by_name, _ = role_maps()
    role_ids = [by_name[r] for r in INSTRUCTOR_ROLES if r in by_name]
    dt = sb.table("department_table").select("id").eq("user_id", instructor_id).in_("roles", role_ids).limit(1).execute()
    return ui if dt.data else None


def _search(q: str) -> list[dict]:
    q = " ".join((q or "").split())
    if len(q) < 2:
        raise HTTPException(status_code=400, detail={"code": "query_too_short", "message": "Type at least 2 characters."})
    q = q[:80]
    rows = get_supabase().rpc("search_instructors", {"query": q}).execute().data or []
    depts = department_lookup()
    out = []
    for r in rows:
        d = depts.get(int(r["department_id"])) if r.get("department_id") is not None else None
        out.append({
            "id": r["id"],
            "first_name": r.get("first_name"),
            "last_name": r.get("last_name"),
            "full_name": _full_name(r),
            "department_id": r.get("department_id"),
            "department_name": d.get("d_name") if d else None,
            "department_code": d.get("d_code") if d else None,
            "is_placeholder": bool(r.get("is_placeholder")),
            "disabled": bool(r.get("disabled")),
        })
    # Rows where every typed word starts a word of the name come first (stable sort keeps the database order).
    out.sort(key=lambda r: 0 if search_words_match(q, r["full_name"]) else 1)
    return out


@router.get("/search")
def search_instructors(q: str = Query(..., max_length=100), user: CurrentUser = Depends(require_staff)):
    return _search(q)


class CreatePlaceholder(BaseModel):
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)
    department_id: int  # department_name.id
    employment_type: Literal["FULL-TIME", "PART-TIME"]
    confirm_new_person: bool = False  # "This is a different person": skip the similar-name check


def _cleanup_placeholder(sb, user_id: str) -> None:
    """Best-effort rollback so a failed create never leaves orphans."""
    for table, col in (("instructor_departments", "instructor_id"), ("department_table", "user_id"), ("user_info", "id")):
        try:
            sb.table(table).delete().eq(col, user_id).execute()
        except Exception:
            log.exception("cleanup: failed deleting %s rows for placeholder", table)
    try:
        sb.auth.admin.delete_user(user_id)
    except Exception:
        log.exception("cleanup: failed deleting auth user for placeholder %s", user_id)


def _is_full_name_taken(exc: Exception) -> bool:
    """The database's unique index on first + last name rejected the row."""
    text = f"{getattr(exc, 'code', '')} {getattr(exc, 'message', '')} {exc}"
    return "full_name_unique" in text


def _near_duplicates(sb, first: str, last: str, limit: int = 5) -> list[dict]:
    """Existing people who look like the same person as first + last: same
    name, near-same name (see name_match) or a close spelling (old RPC)."""
    cols = "id, first_name, last_name, disabled, is_placeholder"
    found: dict[str, dict] = {}  # id -> row with "_strength": 0 exact, 1 near, 2 fuzzy
    parts = surname_parts(last)
    if parts:
        rows = sb.table("user_info").select(cols).ilike("last_name", f"%{parts[-1]}%").limit(500).execute().data or []
        for r in rows:
            if not r.get("id"):
                continue
            if same_name(first, last, r.get("first_name"), r.get("last_name")):
                found[r["id"]] = {**r, "_strength": 0}
            elif is_near_duplicate(first, last, r.get("first_name"), r.get("last_name")):
                found[r["id"]] = {**r, "_strength": 1}
    # Safety net for spelling mistakes: the database's similarity check.
    fuzzy = sb.rpc("find_duplicate_instructor", {"p_first": first, "p_last": last}).execute().data or []
    for r in fuzzy:
        if r.get("id") and r["id"] not in found:
            found[r["id"]] = {**r, "_strength": 2}
    if not found:
        return []

    ranked = sorted(found.values(), key=lambda r: (r["_strength"], _full_name(r).casefold()))
    eligible_ids = {r["id"] for r in ranked[:10] if get_instructor_if_eligible(r["id"]) is not None}
    ranked.sort(key=lambda r: (r["id"] not in eligible_ids, r["_strength"], _full_name(r).casefold()))
    ranked = ranked[:limit]

    ids = [r["id"] for r in ranked]
    links = sb.table("instructor_departments").select("instructor_id, department_id, is_primary").in_("instructor_id", ids).execute().data or []
    dept_of: dict[str, int] = {}
    for link in sorted(links, key=lambda x: not x.get("is_primary")):
        dept_of.setdefault(link["instructor_id"], link.get("department_id"))
    depts = department_lookup()

    out = []
    for r in ranked:
        dept_id = dept_of.get(r["id"])
        d = depts.get(int(dept_id)) if dept_id is not None else None
        out.append({
            "id": r["id"],
            "first_name": r.get("first_name"),
            "last_name": r.get("last_name"),
            "full_name": _full_name(r),
            "department_id": dept_id,
            "department_name": d.get("d_name") if d else None,
            "is_placeholder": bool(r.get("is_placeholder")),
            "disabled": bool(r.get("disabled")),
            "eligible": r["id"] in eligible_ids,
            "exact": r["_strength"] == 0,
        })
    return out


@router.post("")
def create_placeholder_instructor(body: CreatePlaceholder, user: CurrentUser = Depends(require_staff)):
    sb = get_supabase()
    first = " ".join(body.first_name.split())
    last = " ".join(body.last_name.split())
    if not first or not last:
        raise HTTPException(status_code=400, detail="First and last name are required.")

    dept = department_lookup().get(body.department_id)
    if not dept:
        raise HTTPException(status_code=400, detail={"code": "invalid_department", "message": "Unknown department."})
    by_name, _ = role_maps()
    role_id = by_name.get(body.employment_type)
    if not role_id:
        raise HTTPException(status_code=500, detail="Employment role missing from roles table.")

    matches = _near_duplicates(sb, first, last)
    if matches and not body.confirm_new_person:
        names = ", ".join(m["full_name"] for m in matches)
        msg = (f"An instructor with a very similar name already exists: {names}." if len(matches) == 1
               else f"Instructors with very similar names already exist: {names}.")
        raise HTTPException(
            status_code=409,
            detail={
                "code": "duplicate_instructor",
                "message": msg,
                "existing_instructor": matches[0],
                "existing_instructors": matches,
            },
        )

    placeholder_email = f"placeholder-{uuid.uuid4()}@ievaluate.internal"
    # Real (but banned, unusable) auth user so the instructor has a stable id.
    try:
        created = sb.auth.admin.create_user({
            "email": placeholder_email,
            "password": secrets.token_urlsafe(32),
            "email_confirm": True,
            "user_metadata": {"placeholder": True, "first_name": first, "last_name": last},
            "ban_duration": "876000h",
        })
    except Exception:
        log.exception("placeholder auth user creation failed")
        raise HTTPException(status_code=502, detail="Could not create the instructor account. Try again.")
    new_id = created.user.id if created and created.user else None
    if not new_id:
        raise HTTPException(status_code=502, detail="Could not create the instructor account. Try again.")

    try:
        # upsert: tolerate a signup trigger that may already have created the row.
        sb.table("user_info").upsert({
            "id": new_id,
            "first_name": first,
            "last_name": last,
            "email": placeholder_email,
            "account_status": "pending",
            "is_placeholder": True,
            "disabled": True,
        }, on_conflict="id").execute()

        sb.table("department_table").insert({
            "user_id": new_id,
            "Department_name_ID": body.department_id,
            "roles": role_id,
        }).execute()

        # The department_table_sync_instructor_department trigger should have
        # created the instructor_departments row; insert only if it did not.
        link = sb.table("instructor_departments").select("id").eq("instructor_id", new_id).limit(1).execute()
        if not link.data:
            sb.table("instructor_departments").insert({
                "instructor_id": new_id,
                "department_id": body.department_id,
                "is_primary": True,
            }).execute()
    except Exception as e:
        log.exception("placeholder instructor creation failed; rolling back")
        _cleanup_placeholder(sb, new_id)
        if _is_full_name_taken(e):
            raise HTTPException(status_code=409, detail={
                "code": "instructor_name_taken",
                "message": (f'Someone named "{first} {last}" already exists, and two people can\'t have exactly the '
                            "same name. Search for them and pick them instead. Nothing was saved."),
            })
        raise HTTPException(status_code=500, detail="Could not create the instructor. Nothing was saved; try again.")

    meta = {"instructor_id": new_id, "department_id": body.department_id, "employment_type": body.employment_type}
    if matches:  # only reachable with confirm_new_person: record who it looked like
        meta["similar_to"] = [m["id"] for m in matches]
    audit(user.id, "placeholder_instructor_created", meta)
    return {
        "id": new_id,
        "first_name": first,
        "last_name": last,
        "full_name": f"{first} {last}",
        "department_id": body.department_id,
        "department_name": dept.get("d_name"),
        "is_placeholder": True,
        "disabled": True,
    }

