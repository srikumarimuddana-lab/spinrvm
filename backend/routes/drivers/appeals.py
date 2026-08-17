"""Driver deactivation-appeal submission (self-service).

Backs docs/legal/driver-deactivation-appeals-policy.md's appeal process.
Admin-side review queue lives at routes/admin/driver_appeals.py. See
services/driver_appeals.py and migration 320 for the record itself.
"""

from pydantic import BaseModel

from ._deps import (  # noqa: F401
    APIRouter,
    Depends,
    Dict,
    HTTPException,
    db_supabase,
    get_current_user,
    logger,
)
from ._shared import get_own_driver_row

try:
    from ...services import driver_appeals as appeals_service
except ImportError:  # pragma: no cover - direct module imports in tests
    from services import driver_appeals as appeals_service  # type: ignore

router = APIRouter()

_STATUS_TO_APPEAL_TYPE = {
    "suspended": "suspension",
    "banned": "ban",
    "needs_review": "needs_review",
}
_STATUS_TO_REASON_FIELD = {
    "suspended": "suspension_reason",
    "banned": "ban_reason",
}


class SubmitAppealRequest(BaseModel):
    message: str


@router.get("/appeals")
async def get_my_appeals(current_user: Dict = Depends(get_current_user)):
    """The authenticated driver's own appeal history, newest first."""
    driver = await get_own_driver_row(current_user)
    return await appeals_service.get_appeal_history(driver["id"])


@router.post("/appeals")
async def submit_appeal(req: SubmitAppealRequest, current_user: Dict = Depends(get_current_user)):
    """Submit an appeal of the current account status. appeal_type and the
    reason snapshot are derived server-side from the driver's actual
    current status — never trust a client-supplied appeal_type, since a
    driver could otherwise appeal a status they aren't actually in."""
    driver = await get_own_driver_row(current_user)
    status = driver.get("status")
    appeal_type = _STATUS_TO_APPEAL_TYPE.get(status)
    if not appeal_type:
        raise HTTPException(
            status_code=400,
            detail="Your account isn't currently suspended, banned, or under review — there's nothing to appeal.",
        )
    original_reason = driver.get(_STATUS_TO_REASON_FIELD.get(status, ""))

    try:
        appeal = await appeals_service.create_appeal(
            driver["id"],
            appeal_type=appeal_type,
            driver_message=req.message,
            original_reason=original_reason,
        )
    except appeals_service.DuplicatePendingAppealError:
        raise HTTPException(
            status_code=409,
            detail="You already have a pending appeal. We'll respond to that one before you can submit another.",
        ) from None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    return appeal
