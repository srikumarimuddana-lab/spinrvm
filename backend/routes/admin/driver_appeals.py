"""Admin review queue for driver deactivation appeals.

Backs docs/legal/driver-deactivation-appeals-policy.md: "A different
reviewer than the one who made the original decision will review your
appeal." This module only enforces the queue/record side of that — it does
not (and cannot, from application code alone) verify the reviewing admin
differs from whoever made the original suspend/ban call; that's a process
control for the safety team, not something this endpoint can check.

Approving an appeal reuses routes.admin.drivers.admin_driver_action (the
same 'unban'/'reactivate' logic already used by the driver-detail admin
page) instead of duplicating driver-status-transition rules here — see
services/driver_appeals.py's module docstring for the full reasoning.
"""

import logging
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

try:
    from ...dependencies import get_admin_user
    from ...services import driver_appeals as appeals_service
    from ...utils.audit_logger import log_admin_action
    from .drivers import DriverActionRequest, admin_driver_action
except ImportError:  # pragma: no cover - direct module imports in tests
    from dependencies import get_admin_user  # type: ignore
    from services import driver_appeals as appeals_service  # type: ignore
    from utils.audit_logger import log_admin_action  # type: ignore

    from .drivers import DriverActionRequest, admin_driver_action  # type: ignore

logger = logging.getLogger(__name__)

router = APIRouter()

# What driver-status action reverses each appeal_type on approval — mirrors
# _STATUS_TO_APPEAL_TYPE in routes/drivers/appeals.py, inverted.
_APPEAL_TYPE_TO_REVERSAL_ACTION = {
    "suspension": "reactivate",
    "ban": "unban",
    # 'needs_review' and 'other' have no single-status reversal — approving
    # those requires a human admin to take the specific action manually
    # (the appeal is still marked approved either way, but drivers.status
    # is left untouched rather than guessing).
}


class ResolveAppealRequest(BaseModel):
    decision: str  # 'approved' | 'denied'
    admin_note: Optional[str] = None


@router.get("/driver-appeals")
async def admin_list_driver_appeals(
    status: Optional[str] = None,
    admin: Dict = Depends(get_admin_user),
):
    try:
        return await appeals_service.list_appeals(status=status)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/driver-appeals/stats")
async def admin_driver_appeal_stats(admin: Dict = Depends(get_admin_user)):
    return await appeals_service.get_appeal_stats()


@router.post("/driver-appeals/{appeal_id}/resolve")
async def admin_resolve_driver_appeal(
    appeal_id: str,
    req: ResolveAppealRequest,
    admin: Dict = Depends(get_admin_user),
):
    if req.decision not in ("approved", "denied"):
        raise HTTPException(status_code=400, detail="decision must be 'approved' or 'denied'")

    appeal = await appeals_service.get_appeal(appeal_id)
    if not appeal:
        raise HTTPException(status_code=404, detail="Appeal not found")
    if appeal.get("status") != "pending":
        raise HTTPException(status_code=409, detail=f"Appeal is already {appeal.get('status')}")

    reactivation_result = None
    if req.decision == "approved":
        reversal_action = _APPEAL_TYPE_TO_REVERSAL_ACTION.get(appeal.get("appeal_type"))
        if reversal_action:
            # Reuse the existing, already-tested driver-status-transition
            # endpoint rather than reimplementing it — see module docstring.
            reactivation_result = await admin_driver_action(
                appeal["driver_id"],
                DriverActionRequest(
                    action=reversal_action,
                    reason=req.admin_note or "Appeal approved",
                ),
                admin,
            )

    await appeals_service.mark_resolved(
        appeal_id,
        status=req.decision,
        admin_note=req.admin_note,
        resolved_by=admin.get("id") or admin.get("email") or "admin",
    )

    # Appeal-decision audit trail. The driver-status side (approve ->
    # reactivate/unban) is already audited by admin_driver_action above when
    # applicable; this row covers the appeal decision itself, including the
    # 'denied' path and 'needs_review'/'other' appeal_types where no driver
    # status transition happens at all.
    audit_id = await log_admin_action(
        admin,
        f"driver_appeal_{req.decision}",
        "driver_appeals",
        appeal_id,
        {
            "decision": req.decision,
            "admin_note": req.admin_note,
            "appeal_type": appeal.get("appeal_type"),
            "driver_id": appeal.get("driver_id"),
            "driver_reactivated": reactivation_result is not None,
        },
    )

    return {
        "appeal_id": appeal_id,
        "status": req.decision,
        "driver_reactivated": reactivation_result is not None,
        "audit_log_id": audit_id,
    }
