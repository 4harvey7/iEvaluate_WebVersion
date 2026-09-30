import threading
import time

from fastapi import APIRouter, Depends

from app.db import get_supabase
from app.security import CurrentUser, require_staff

router = APIRouter()

_cache: dict = {"at": 0.0, "rows": []}
_lock = threading.Lock()


def list_department_rows(max_age: float = 300.0) -> list[dict]:
    """Read-only view of the existing department_name table, cached briefly."""
    with _lock:
        if time.monotonic() - _cache["at"] > max_age or not _cache["rows"]:
            rows = get_supabase().table("department_name").select("id, d_name, d_code").order("d_name").execute().data or []
            _cache.update(at=time.monotonic(), rows=rows)
        return list(_cache["rows"])


def department_lookup() -> dict[int, dict]:
    return {int(d["id"]): d for d in list_department_rows()}


@router.get("")
def list_departments(user: CurrentUser = Depends(require_staff)):
    return list_department_rows()
