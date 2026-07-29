"""Approval queue for the shared admin-export dual-approval gate (AI-3,
docs/threat-model/admin-panel.md; ACTION_ITEMS.md B10). See
services/admin_export_approvals.py for the state machine this wraps.

Router-level gated on require_super_admin (routes/admin/__init__.py) --
approving access to bulk PII exports is not a module-flag-grantable
permission, same posture as the Data Transfer routers it exists to gate.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

try:
    from ...dependencies import get_admin_user
    from ...services import admin_export_approvals as approvals
    from ...utils.audit_logger import log_admin_action
    from ...utils.rate_limiter import export_approvals_decide_limit, export_approvals_list_limit
except ImportError:
    from dependencies import get_admin_user
    from services import admin_export_approvals as approvals  # type: ignore
    from utils.audit_logger import log_admin_action
    from utils.rate_limiter import export_approvals_decide_limit, export_approvals_list_limit

logger = logging.getLogger(__name__)

router = APIRouter()


class DecisionRequest(BaseModel):
    decision_note: str = Field("", max_length=500)


@router.get("/export-approvals/pending")
@export_approvals_list_limit
async def list_pending_export_approvals(request: Request, admin: dict = Depends(get_admin_user)):
    """The approval queue -- oldest pending request first."""
    return await approvals.list_pending()


@router.post("/export-approvals/{request_id}/approve")
@export_approvals_decide_limit
async def approve_export_request(
    request: Request,
    request_id: str,
    body: DecisionRequest,
    admin: dict = Depends(get_admin_user),
):
    try:
        result = await approvals.approve(request_id, decided_by=admin["id"], decision_note=body.decision_note or None)
    except approvals.RequestNotFound as e:
        raise HTTPException(status_code=404, detail="Export approval request not found") from e
    except approvals.RequestAlreadyDecided as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except approvals.SelfApprovalError as e:
        raise HTTPException(status_code=403, detail="You cannot approve your own export request") from e

    await log_admin_action(
        admin,
        "export_approval_approved",
        "admin_export_approval_requests",
        request_id,
        {"decision_note": body.decision_note},
    )
    return result


@router.post("/export-approvals/{request_id}/deny")
@export_approvals_decide_limit
async def deny_export_request(
    request: Request,
    request_id: str,
    body: DecisionRequest,
    admin: dict = Depends(get_admin_user),
):
    try:
        result = await approvals.deny(request_id, decided_by=admin["id"], decision_note=body.decision_note or None)
    except approvals.RequestNotFound as e:
        raise HTTPException(status_code=404, detail="Export approval request not found") from e
    except approvals.RequestAlreadyDecided as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except approvals.SelfApprovalError as e:
        raise HTTPException(status_code=403, detail="You cannot deny your own export request") from e

    await log_admin_action(
        admin,
        "export_approval_denied",
        "admin_export_approval_requests",
        request_id,
        {"decision_note": body.decision_note},
    )
    return result
