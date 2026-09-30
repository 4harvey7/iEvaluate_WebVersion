import logging

from app.db import get_supabase

log = logging.getLogger("ievaluate.audit")


def audit(user_id: str | None, action: str, metadata: dict | None = None) -> None:
    """Best-effort audit log entry; never breaks the calling request."""
    try:
        get_supabase().table("audit_logs").insert({
            "user_id": user_id,
            "action": action,
            "metadata": metadata or {},
        }).execute()
    except Exception:
        log.exception("audit log insert failed for action=%s", action)
