"""Who may act on a batch or a station (security review, item c).

Staff share office laptops, so binding to a station ("I'm the one at this
laptop now") must stay easy. But binding must never hand over somebody
else's batches, and it must not let anyone re-point or cut off a laptop that
another person is using:

  * Batch access (review page, page viewer, unsaved pages, normal close):
    SAO admin, or the staff member who started the batch (created_by). The
    station's current binding grants nothing by itself.
  * Bind: the station is unbound, already bound to the caller, the caller is
    an admin, or the person bound there has no open batch on that station.
  * Change the folder / rotate the key / download the installer: the person
    bound to that station, or an admin.
"""
from __future__ import annotations

from app.security import CurrentUser

STATION_IN_USE_CODE = "station_in_use"
NOT_YOUR_STATION_CODE = "not_your_station"
NOT_YOUR_STATION_MSG = (
    "Only the person logged in at this laptop, or an SAO admin, can change its scanner setup. "
    "Log in on this laptop first (it takes over the laptop if nobody has a batch open on it)."
)


def is_batch_owner(batch: dict, user: CurrentUser) -> bool:
    """SAO admin, or the staff member who started the batch."""
    if user.is_admin:
        return True
    creator = str(batch.get("created_by") or "")
    return bool(creator) and creator == user.id


def can_manage_station(station: dict, user: CurrentUser) -> bool:
    """Folder / key / installer: the staff bound to the station, or an admin."""
    if user.is_admin:
        return True
    bound = str(station.get("current_staff_id") or "")
    return bool(bound) and bound == user.id


def station_in_use_message(name: str | None) -> str:
    who = name or "Another staff member"
    return (
        f"{who} is still logged in on this laptop with a batch open. Ask them to close the batch "
        "or log out here, or ask an SAO admin. If the batch was forgotten, it can be closed with "
        "\"Close a forgotten batch\" once it has been quiet for a few hours."
    )
