import logging
import re
import secrets
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.audit import audit
from app.db import get_settings, get_supabase, is_valid_uuid, new_auth_client
from app.routers.departments import department_lookup
from app.security import INSTRUCTOR_ROLES, CurrentUser, require_admin, require_staff, role_maps

router = APIRouter()
log = logging.getLogger("ievaluate.instructors")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


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
    return out


@router.get("/search")
def search_instructors(q: str = Query(..., max_length=100), user: CurrentUser = Depends(require_staff)):
    return _search(q)


@router.get("/placeholders")
def search_placeholders(q: str = Query(..., max_length=100), user: CurrentUser = Depends(require_admin)):
    return [r for r in _search(q) if r["is_placeholder"]]


class CreatePlaceholder(BaseModel):
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)
    department_id: int  # department_name.id
    employment_type: Literal["FULL-TIME", "PART-TIME"]


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

    dup = sb.rpc("find_duplicate_instructor", {"p_first": first, "p_last": last}).execute()
    if dup.data:
        match = dup.data[0]
        raise HTTPException(
            status_code=409,
            detail={
                "code": "duplicate_instructor",
                "message": "An instructor with a very similar name already exists.",
                "existing_instructor": {
                    "id": match.get("id"),
                    "first_name": match.get("first_name"),
                    "last_name": match.get("last_name"),
                    "full_name": _full_name(match),
                    "disabled": bool(match.get("disabled")),
                    "eligible": get_instructor_if_eligible(match.get("id")) is not None,
                },
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
    except Exception:
        log.exception("placeholder instructor creation failed; rolling back")
        _cleanup_placeholder(sb, new_id)
        raise HTTPException(status_code=500, detail="Could not create the instructor. Nothing was saved; try again.")

    audit(user.id, "placeholder_instructor_created", {
        "instructor_id": new_id, "department_id": body.department_id, "employment_type": body.employment_type,
    })
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


class ActivateInstructor(BaseModel):
    real_email: str = Field(min_length=3, max_length=254)


@router.post("/{instructor_id}/activate")
def activate_instructor(instructor_id: str, body: ActivateInstructor, admin: CurrentUser = Depends(require_admin)):
    if not is_valid_uuid(instructor_id):
        raise HTTPException(status_code=404, detail="Instructor not found")
    email = body.real_email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail={"code": "invalid_email", "message": "Enter a valid email address."})

    sb = get_supabase()
    res = sb.table("user_info").select("id, first_name, last_name, email, is_placeholder").eq("id", instructor_id).limit(1).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Instructor not found")
    existing = res.data[0]
    if not existing.get("is_placeholder"):
        raise HTTPException(status_code=400, detail={"code": "not_placeholder", "message": "This is not a placeholder account."})

    like_email = email.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    taken = sb.table("user_info").select("id").ilike("email", like_email).neq("id", instructor_id).limit(1).execute()
    if taken.data:
        raise HTTPException(status_code=409, detail={"code": "email_in_use", "message": "That email already belongs to another account."})

    old_email = existing.get("email")
    try:
        # Update the EXISTING auth user so every batch/result tied to this id stays intact.
        sb.auth.admin.update_user_by_id(instructor_id, {"email": email, "email_confirm": True, "ban_duration": "none"})
    except Exception:
        log.exception("activation: auth update failed")
        raise HTTPException(status_code=502, detail="Could not update the login account (the email may already be registered).")

    try:
        sb.table("user_info").update({
            "email": email,
            "disabled": False,
            "is_placeholder": False,
            "account_status": "approved",
        }).eq("id", instructor_id).execute()
    except Exception:
        log.exception("activation: user_info update failed; reverting auth user")
        try:
            sb.auth.admin.update_user_by_id(instructor_id, {"email": old_email, "ban_duration": "876000h"})
        except Exception:
            log.exception("activation: auth revert failed")
        raise HTTPException(status_code=500, detail="Activation failed. Nothing was changed; try again.")

    email_sent = True
    try:
        opts = {}
        redirect = get_settings().password_reset_redirect_url
        if redirect:
            opts["redirect_to"] = redirect
        new_auth_client().auth.reset_password_for_email(email, opts)
    except Exception:
        email_sent = False
        log.exception("activation: password reset email failed")

    audit(admin.id, "instructor_account_activated", {
        "instructor_id": instructor_id, "new_email": email, "reset_email_sent": email_sent,
    })
    return {"ok": True, "email_sent": email_sent, "email": email}
