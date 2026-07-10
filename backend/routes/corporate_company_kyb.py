"""Company-side KYB endpoints (Spinr for Business, M2.2).

The portal's verification flow for a company admin:

    GET  /company/{id}/kyb             — derived verification state
    POST /company/{id}/kyb/upload-url  — signed upload URL (private bucket)
    POST /company/{id}/kyb/submit      — confirm the upload → under review

Derived states (NOT a second state machine — computed from the existing
status lifecycle + migration 225 columns):

    not_submitted  pending_verification, no document yet
    under_review   pending_verification, document on file
    rejected       suspended AND kyb_last_decision='rejected' → may resubmit
                   (resubmit flips status back to pending_verification, so the
                   company re-enters the staff KYB queue)
    suspended      suspended by STAFF (not KYB) → may NOT self-unsuspend
    approved       active
    closed         closed

All endpoints are require_company_admin — members see only the status banner
(company.status via work-profile). The document is stored in the private
kyb-documents bucket; its storage key is never returned to the portal.

NOTE: no `from __future__ import annotations` — string annotations break
FastAPI body resolution through decorator wrappers (see corporate_signup.py).
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

try:
    from ..db_supabase import (  # type: ignore
        create_kyb_upload_url,
        get_corporate_account_by_id,
        kyb_object_exists,
        set_kyb_document,
        update_corporate_account_status,
    )
    from ..dependencies.company_guard import require_company_admin  # type: ignore
    from ..utils.audit_logger import log_admin_action  # type: ignore
except ImportError:
    from db_supabase import (  # type: ignore
        create_kyb_upload_url,
        get_corporate_account_by_id,
        kyb_object_exists,
        set_kyb_document,
        update_corporate_account_status,
    )
    from dependencies.company_guard import require_company_admin  # type: ignore
    from utils.audit_logger import log_admin_action  # type: ignore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/company/{company_id}", tags=["Corporate Company KYB"])

_ALLOWED_CONTENT_TYPES = {"application/pdf", "image/png", "image/jpeg"}

# States from which a (re)submission is allowed.
_SUBMITTABLE_STATES = {"not_submitted", "under_review", "rejected"}


def _derive_kyb_state(company: dict) -> str:
    status = (company.get("status") or "").lower()
    if status == "active":
        return "approved"
    if status == "closed":
        return "closed"
    if status == "suspended":
        return "rejected" if company.get("kyb_last_decision") == "rejected" else "suspended"
    return "under_review" if company.get("kyb_document_url") else "not_submitted"


async def _get_company_or_404(company_id: str) -> dict:
    company = await get_corporate_account_by_id(company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    return company


class KybUploadUrlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_type: str = Field(..., max_length=64)


class KybSubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., min_length=1, max_length=300)


@router.get("/kyb")
async def get_kyb_state(company_id: str, guard=Depends(require_company_admin)):
    """Verification state for the portal's verification page. The raw storage
    key is deliberately absent — the document is only reachable via the
    staff-side streaming endpoint."""
    company = await _get_company_or_404(company_id)
    state = _derive_kyb_state(company)
    return {
        "state": state,
        "submitted_at": company.get("kyb_submitted_at"),
        "reviewed_at": company.get("kyb_reviewed_at"),
        # The review note is the actionable "what to fix" — only meaningful
        # (and only shown) after a rejection.
        "review_note": company.get("kyb_review_note") if state == "rejected" else None,
        "can_resubmit": state in _SUBMITTABLE_STATES,
    }


@router.post("/kyb/upload-url")
async def kyb_upload_url(
    company_id: str,
    body: KybUploadUrlRequest,
    guard=Depends(require_company_admin),
):
    """Short-lived signed upload URL into the private kyb-documents bucket."""
    if body.content_type not in _ALLOWED_CONTENT_TYPES:
        raise HTTPException(status_code=415, detail="Document must be a PDF, PNG, or JPEG.")

    company = await _get_company_or_404(company_id)
    state = _derive_kyb_state(company)
    if state not in _SUBMITTABLE_STATES:
        raise HTTPException(
            status_code=409,
            detail=(
                "Your account is suspended — contact support."
                if state == "suspended"
                else "Verification is already complete for this company."
            ),
        )

    try:
        signed = await create_kyb_upload_url(company_id=company_id, content_type=body.content_type)
    except Exception as e:
        logger.error("kyb upload-url: signing failed for company %s", company_id, exc_info=True)
        raise HTTPException(status_code=503, detail="Could not prepare the upload. Please try again.") from e
    return signed


@router.post("/kyb/submit")
async def kyb_submit(
    company_id: str,
    body: KybSubmitRequest,
    guard=Depends(require_company_admin),
):
    """Confirm an upload: persist the document key and (re-)enter review.

    A rejected company resubmitting flips status suspended →
    pending_verification so it re-enters the staff KYB queue. A
    STAFF-suspended company gets 409 — it cannot self-unsuspend.
    """
    # Tenancy + traversal guard: the key must live under this company's own
    # prefix exactly as minted by upload-url.
    path = body.path.strip()
    if ".." in path or not path.startswith(f"kyb/{company_id}/"):
        raise HTTPException(status_code=400, detail="Invalid document path.")

    company = await _get_company_or_404(company_id)
    state = _derive_kyb_state(company)
    if state not in _SUBMITTABLE_STATES:
        raise HTTPException(
            status_code=409,
            detail=(
                "Your account is suspended — contact support."
                if state == "suspended"
                else "Verification is already complete for this company."
            ),
        )

    try:
        exists = await kyb_object_exists(path=path)
    except Exception as e:
        logger.error("kyb submit: storage existence check failed for company %s", company_id, exc_info=True)
        raise HTTPException(status_code=503, detail="Could not verify the upload. Please try again.") from e
    if not exists:
        raise HTTPException(status_code=400, detail="Upload not found — upload the document first.")

    updated = await set_kyb_document(company_id=company_id, path=path)
    if not updated:
        logger.error("kyb submit: set_kyb_document returned no row for company %s", company_id)
        raise HTTPException(status_code=503, detail="Could not record the submission. Please try again.")

    resubmitted = False
    if state == "rejected":
        # New tested transition: suspended(kyb_last_decision=rejected) →
        # pending_verification. Re-enters the staff queue.
        flipped = await update_corporate_account_status(company_id, "pending_verification")
        if not flipped:
            logger.error("kyb submit: status flip failed for company %s", company_id)
            raise HTTPException(status_code=503, detail="Could not reopen verification. Please try again.")
        resubmitted = True

    await log_admin_action(
        {"id": guard["user"]["id"], "role": "user"},
        action="corporate_kyb_submitted",
        resource="corporate_accounts",
        resource_id=company_id,
        details={"resubmitted_after_rejection": resubmitted},
    )

    return {
        "success": True,
        "state": "under_review",
        "submitted_at": updated.get("kyb_submitted_at"),
        "resubmitted": resubmitted,
    }


# Re-exported for tests / future automated-KYB wiring.
derive_kyb_state = _derive_kyb_state
