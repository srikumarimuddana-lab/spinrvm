"""
Corporate accounts API routes for managing business clients and billing.

This module implements CRUD operations for corporate accounts that can be used
for business rides and expense management.
"""

import asyncio
import logging
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from db_supabase import (  # noqa: E402
    delete_corporate_account as db_delete_corporate_account,
)
from db_supabase import (  # noqa: E402
    ensure_corporate_wallet,
    get_corporate_account_by_id,
    get_corporate_wallet_by_company,
    get_rows,
    insert_corporate_account,
    update_corporate_stripe_customer_id,
    update_corporate_wallet_config,
)
from db_supabase import (  # noqa: E402
    update_corporate_account as db_update_corporate_account,
)
from dependencies import get_admin_user  # noqa: E402
from schemas.corporate import (  # noqa: E402
    CompanyStatus,
    CompanyStatusTransition,
    KYBReviewDecision,
    SizeTier,
)
from schemas.corporate import (  # noqa: E402
    CorporateAccountResponse as CorporateAccountDetailResponse,
)
from settings_loader import get_app_settings  # noqa: E402
from validators import (
    sanitize_string,
    validate_canadian_tax_region,
    validate_cra_business_number,
    validate_email,
    validate_id,
    validate_phone,
)  # noqa: E402

try:
    from ..utils.audit_logger import log_admin_action
except ImportError:
    from utils.audit_logger import log_admin_action  # type: ignore[no-redef]

try:
    from ..services.corporate_membership_service import bootstrap_owner
except ImportError:
    from services.corporate_membership_service import bootstrap_owner  # type: ignore[no-redef]

try:
    from ..services.corporate_suspension_service import cancel_pre_pickup_rides_for_company
except ImportError:
    from services.corporate_suspension_service import cancel_pre_pickup_rides_for_company  # type: ignore[no-redef]

try:
    from ..services.corporate_wallet_winddown_service import refund_wallet_balance_on_close
except ImportError:
    from services.corporate_wallet_winddown_service import refund_wallet_balance_on_close  # type: ignore[no-redef]

logger = logging.getLogger(__name__)

# Alias for backward compatibility
get_current_admin = get_admin_user

router = APIRouter(prefix="/admin/corporate-accounts", tags=["Corporate Accounts"])

# OWNERSHIP ASSUMPTION: All Spinr admins are currently global staff — there are
# no org-scoped admin roles. For this reason, endpoints authenticate via
# get_current_admin but do NOT check that the admin "owns" the company_id in
# the path. If per-org admin roles are ever added, every endpoint in this file
# must gain an ownership check (e.g. fetched_account["admin_email"] == current_admin["email"])
# before returning or mutating data.


# Pydantic models for request/response validation
class CorporateAccountBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=200, description="Company name")
    contact_name: Optional[str] = Field(None, max_length=100, description="Primary contact person")
    contact_email: Optional[str] = Field(None, description="Contact email address")
    contact_phone: Optional[str] = Field(None, description="Contact phone number")
    credit_limit: Decimal = Field(Decimal("0"), ge=0, description="Credit limit for corporate billing")
    is_active: bool = Field(True, description="Whether the account is active")


class CorporateAccountCreate(CorporateAccountBase):
    """Staff create payload — M1.4 upgraded it from the thin base to the rich
    B2B fields so business_number/legal_name land at CREATE time instead of a
    follow-up PUT, plus an optional owner_email that seeds the company's first
    (owner) member via bootstrap_owner."""

    legal_name: Optional[str] = Field(None, max_length=300)
    business_number: Optional[str] = Field(None, max_length=20)
    tax_region: Optional[str] = None
    billing_email: Optional[str] = None
    size_tier: Optional[str] = Field(None, pattern="^(smb|mid_market|enterprise)$")
    industry: Optional[str] = Field(None, max_length=100)
    # Not a corporate_accounts column — consumed by the endpoint to invite
    # the company's first owner.
    owner_email: Optional[str] = None

    @field_validator("business_number")
    @classmethod
    def _check_bn(cls, v: Optional[str]) -> Optional[str]:
        if not v:
            return None
        return validate_cra_business_number(v)

    @field_validator("tax_region")
    @classmethod
    def _check_region(cls, v: Optional[str]) -> Optional[str]:
        if not v:
            return None
        return validate_canadian_tax_region(v.strip().upper())


class CorporateAccountCreatedResponse(CorporateAccountDetailResponse):
    """POST response = the account + owner-bootstrap outcome. Extra fields are
    optional so the admin dashboard's existing account typing stays valid."""

    owner_invite_url: Optional[str] = None
    owner_member_id: Optional[str] = None
    owner_bootstrap_error: bool = False


class KYBReviewResponse(CorporateAccountDetailResponse):
    """kyb-review response = the account + provisioning outcome. Approval is
    a DB-status -> wallet -> Stripe-customer sequence with no compensating
    rollback; same partial-success shape as CorporateAccountCreatedResponse's
    owner_bootstrap_error, so a step failing after the status has already
    flipped to 'active' is surfaced explicitly instead of raising a 503 that
    hides the fact the status change already committed. Corporate + admin
    portal review, gap #40."""

    wallet_provisioning_error: bool = False
    stripe_customer_creation_error: bool = False


class CorporateAccountUpdate(BaseModel):
    """Staff update payload. M1.6: gained the rich B2B fields — previously the
    thin schema silently DROPPED legal_name/business_number/tax_region/
    billing_email/size_tier sent by the admin detail page (pydantic default
    extra='ignore'), so those edits never persisted."""

    name: Optional[str] = Field(None, min_length=1, max_length=200)
    contact_name: Optional[str] = Field(None, max_length=100)
    contact_email: Optional[str] = Field(None)
    contact_phone: Optional[str] = Field(None)
    credit_limit: Optional[Decimal] = Field(None, ge=0)
    is_active: Optional[bool] = Field(None)
    legal_name: Optional[str] = Field(None, max_length=300)
    business_number: Optional[str] = Field(None, max_length=20)
    tax_region: Optional[str] = None
    billing_email: Optional[str] = None
    size_tier: Optional[str] = Field(None, pattern="^(smb|mid_market|enterprise)$")
    industry: Optional[str] = Field(None, max_length=100)

    @field_validator("business_number")
    @classmethod
    def _check_bn(cls, v: Optional[str]) -> Optional[str]:
        if not v:
            return None
        return validate_cra_business_number(v)

    @field_validator("tax_region")
    @classmethod
    def _check_region(cls, v: Optional[str]) -> Optional[str]:
        if not v:
            return None
        return validate_canadian_tax_region(v.strip().upper())


@router.get("", response_model=List[CorporateAccountDetailResponse])
async def get_corporate_accounts(
    request: Request,
    response: Response,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    offset: Optional[int] = Query(None, ge=0, description="Alias for skip"),
    search: Optional[str] = None,
    status: Optional[CompanyStatus] = None,
    size_tier: Optional[SizeTier] = None,
    is_active: Optional[bool] = None,
    current_admin: dict = Depends(get_current_admin),
):
    """List corporate accounts with optional filters and pagination.

    Returns a flat array (backwards compatible). Total row count and the
    applied limit are exposed via the ``X-Total-Count`` and ``X-Limit``
    response headers. ``offset`` is accepted as an alias for the existing
    ``skip`` query param so callers can use the standard offset/limit
    convention. Default limit is 100 — the admin dashboard already pages
    through this endpoint with PAGE_SIZE=50 so this is a no-op for the
    current frontend.
    """
    from db_supabase import count_documents, list_corporate_accounts_filtered

    effective_skip = offset if offset is not None else skip
    capped_limit = min(limit, 500)

    try:
        rows = await list_corporate_accounts_filtered(
            status=status.value if status else None,
            size_tier=size_tier.value if size_tier else None,
            search=search,
            skip=effective_skip,
            limit=capped_limit,
        )
        if is_active is not None:
            rows = [r for r in rows if bool(r.get("is_active")) == is_active]
        # X-Total-Count reflects unfiltered table size when no server-side
        # filters are active; with status/size_tier/search applied we'd need
        # a parallel filtered count query — kept simple for now and the
        # frontend uses hasNextPage (limit+1 trick) regardless.
        try:
            total = await count_documents("corporate_accounts")
            response.headers["X-Total-Count"] = str(total)
        except Exception:
            logger.warning("Failed to compute corporate_accounts total count", exc_info=True)
        response.headers["X-Limit"] = str(capped_limit)
        return rows
    except Exception as e:
        # B-P3-leak-cleanup: Postgres error strings carry constraint
        # names + table internals (e.g. unique-constraint violations
        # name the column). logger.exception preserves the full
        # traceback server-side; the framework sanitiser would scrub
        # this 5xx detail anyway, but cleaning it up at the source
        # avoids the next contributor copying the leak pattern.
        logger.exception("Failed to fetch corporate accounts")
        raise HTTPException(
            status_code=500,
            detail="Failed to fetch corporate accounts.",
        ) from e


_ALLOWED_KYB_CONTENT = {"application/pdf", "image/png", "image/jpeg"}


class KYBUploadURLRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_type: str = Field(..., description="MIME type of the KYB document to upload")


@router.post("/{company_id}/kyb-upload-url")
async def kyb_upload_url(
    company_id: str,
    body: KYBUploadURLRequest,
    current_admin: dict = Depends(get_current_admin),
):
    """Return a short-lived signed upload URL for a KYB document.

    The caller uploads the document directly to Supabase Storage using the
    returned URL; the backend never streams binary data.
    """
    _valid, normalized_id = validate_id(company_id, "Corporate Account ID", raise_exception=True)

    if body.content_type not in _ALLOWED_KYB_CONTENT:
        raise HTTPException(status_code=400, detail="Unsupported content type for KYB")

    from db_supabase import create_kyb_upload_url

    return await create_kyb_upload_url(company_id=normalized_id, content_type=body.content_type)


class KYBDocumentConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., min_length=1, max_length=300)


@router.post("/{company_id}/kyb-document")
async def kyb_document_confirm(
    company_id: str,
    body: KYBDocumentConfirm,
    current_admin: dict = Depends(get_current_admin),
):
    """Staff-side upload confirmation (M2.3): persist the uploaded document's
    storage key onto the account. Completes the previously half-wired flow
    where kyb-upload-url returned a path that nothing ever recorded, leaving
    /kyb/view reading an always-NULL kyb_document_url."""
    _valid, normalized_id = validate_id(company_id, "Corporate Account ID", raise_exception=True)

    path = body.path.strip()
    if ".." in path or not path.startswith(f"kyb/{normalized_id}/"):
        raise HTTPException(status_code=400, detail="Invalid document path.")

    from db_supabase import kyb_object_exists, set_kyb_document

    if not await kyb_object_exists(path=path):
        raise HTTPException(status_code=400, detail="Upload not found — upload the document first.")

    row = await set_kyb_document(company_id=normalized_id, path=path)
    if not row:
        raise HTTPException(status_code=404, detail="Corporate account not found")

    try:
        await log_admin_action(
            admin=current_admin,
            action="kyb_document_confirmed",
            resource="corporate_account",
            resource_id=str(normalized_id),
            details={},
        )
    except Exception:
        logger.error(f"Audit log failed for kyb_document_confirm {normalized_id}", exc_info=True)

    return {"success": True, "submitted_at": row.get("kyb_submitted_at")}


@router.post("/{company_id}/kyb-review", response_model=KYBReviewResponse)
async def kyb_review(
    company_id: str,
    decision: KYBReviewDecision,
    request: Request,
    current_admin: dict = Depends(get_current_admin),
):
    """Approve or reject a pending KYB submission.

    Approve → status='active'. Reject → status='suspended' so the company
    can re-upload and be re-reviewed from the queue.

    Approval is a DB-status -> wallet -> Stripe-customer sequence with no
    compensating rollback for the status flip. Corporate + admin portal
    review, gap #40: this used to raise a 503 from the wallet step, which
    hid the fact that record_kyb_decision had already committed
    status='active' — the admin saw a scary error for a company that was,
    in fact, already approved. Same partial-success shape as
    create_corporate_account's owner_bootstrap_error: each provisioning
    step is independently caught, logged loudly (ops follow-up), and
    surfaced as a boolean on the response instead of raised.
    """
    _valid, normalized_id = validate_id(company_id, "Corporate Account ID", raise_exception=True)

    from db_supabase import record_kyb_decision

    row = await record_kyb_decision(
        company_id=normalized_id,
        reviewer_id=current_admin["id"],
        approved=decision.approve,
        note=decision.note,
    )
    if not row:
        raise HTTPException(status_code=404, detail="Corporate account not found")

    wallet_provisioning_error = False
    stripe_customer_creation_error = False
    if decision.approve:
        try:
            await ensure_corporate_wallet(company_id=normalized_id)
        except Exception:
            logger.error(
                "[KYB] Wallet creation failed for company %s — status is already 'active' with no "
                "wallet; needs manual follow-up (re-run kyb-review or provision the wallet directly).",
                normalized_id,
                exc_info=True,
            )
            wallet_provisioning_error = True
        if not row.get("stripe_customer_id"):
            try:
                settings = await get_app_settings()
                stripe_secret = settings.get("stripe_secret_key", "")
                if stripe_secret:
                    import stripe

                    customer = await asyncio.to_thread(
                        lambda: stripe.Customer.create(
                            email=row.get("billing_email"),
                            name=row.get("legal_name") or row.get("name"),
                            metadata={"corporate_account_id": normalized_id},
                            api_key=stripe_secret,
                            idempotency_key=f"cus-create-corp-{normalized_id}",
                        )
                    )
                    await update_corporate_stripe_customer_id(company_id=normalized_id, stripe_customer_id=customer.id)
            except Exception:
                logger.error(
                    "[KYB] Stripe customer creation failed for company %s — status is already 'active' "
                    "with no Stripe customer; needs manual follow-up before the first billing event.",
                    normalized_id,
                    exc_info=True,
                )
                stripe_customer_creation_error = True

    # M2.3: notify the company of the decision — best-effort; the portal's
    # verification page (derived state + review note) is the durable signal.
    notify_to = row.get("contact_email") or row.get("billing_email")
    if notify_to:
        try:
            from utils.email_provider import send_transactional_email

            if decision.approve:
                subject = "Your Spinr for Business account is approved"
                text = (
                    f"Good news — {row.get('name')} has been verified and your "
                    "Spinr for Business account is now active.\n\n"
                    "Sign in to the business portal to invite your team and start booking rides."
                )
            else:
                subject = "Your Spinr for Business application needs attention"
                text = (
                    f"We reviewed the verification documents for {row.get('name')} "
                    "and couldn't approve them yet."
                    + (f"\n\nReviewer note: {decision.note}" if decision.note else "")
                    + "\n\nSign in to the business portal to view the details and resubmit."
                )
            await send_transactional_email(
                to=notify_to,
                subject=subject,
                text=text,
                log_id=str(normalized_id),
                email_type="corporate_kyb_decision",
            )
        except Exception:
            logger.error("kyb_review: decision email failed for company %s", normalized_id, exc_info=True)

    try:
        await log_admin_action(
            admin=current_admin,
            action="kyb_review",
            resource="corporate_account",
            resource_id=str(normalized_id),
            details={
                "decision": "approved" if decision.approve else "rejected",
                "reviewer_id": current_admin["id"],
                "note": decision.note,
                "wallet_provisioning_error": wallet_provisioning_error,
                "stripe_customer_creation_error": stripe_customer_creation_error,
            },
        )
    except Exception as _ae:
        logger.error(
            f"Audit log failed for kyb_review {normalized_id}: {_ae}",
            exc_info=True,
        )

    return {
        **row,
        "wallet_provisioning_error": wallet_provisioning_error,
        "stripe_customer_creation_error": stripe_customer_creation_error,
    }


@router.get("/{company_id}/kyb/view")
async def admin_view_kyb_document(
    company_id: str,
    current_admin: dict = Depends(get_current_admin),
):
    """Stream the KYB document for a corporate account through the backend.

    Uses the service-role key server-side — no public bucket policy needed,
    bucket stays private, browser never receives a Supabase Storage URL.
    """
    import mimetypes

    from supabase_client import supabase as _supabase

    _valid, normalized_id = validate_id(company_id, "Corporate Account ID", raise_exception=True)
    rows = await get_rows("corporate_accounts", {"id": normalized_id}, limit=1)
    if not rows:
        raise HTTPException(status_code=404, detail="Corporate account not found")

    stored_url = rows[0].get("kyb_document_url") or ""
    import re as _re

    m = _re.search(r"/storage/v1/object/(?:sign|public)/kyb-documents/([^?#]+)", stored_url)
    storage_key = m.group(1) if m else None
    # M2.3 fallback: set_kyb_document stores the RAW storage key
    # (kyb/{company_id}/{uuid}.ext), not a full URL — accept it directly.
    # Legacy full-URL rows keep resolving via the regex above.
    if not storage_key and stored_url.startswith("kyb/") and ".." not in stored_url:
        storage_key = stored_url

    if not storage_key:
        raise HTTPException(status_code=404, detail="No KYB document on file")
    if not _supabase:
        raise HTTPException(status_code=503, detail="Storage client not configured")

    try:
        data: bytes = _supabase.storage.from_("kyb-documents").download(storage_key)
    except Exception as exc:
        logger.error("KYB storage download failed company=%s key=%s: %s", normalized_id, storage_key, exc)
        raise HTTPException(status_code=502, detail="Could not fetch KYB document from storage") from exc

    content_type, _ = mimetypes.guess_type(storage_key)
    return Response(content=data, media_type=content_type or "application/octet-stream")


@router.post("", response_model=CorporateAccountCreatedResponse, status_code=status.HTTP_201_CREATED)
async def create_corporate_account(
    request: Request,
    account: CorporateAccountCreate,
    current_admin: dict = Depends(get_current_admin),
):
    """
    Create a new corporate account (rich B2B fields + optional owner bootstrap).

    Args:
        account: Corporate account data (may include owner_email — the first
            owner member is invited immediately, fixing the zero-members gap)
        current_admin: Authenticated admin user
    """
    # Validate inputs
    if account.contact_email:
        valid, normalized_email = validate_email(account.contact_email, raise_exception=True)
        account.contact_email = normalized_email

    if account.owner_email:
        valid, normalized_owner = validate_email(account.owner_email, raise_exception=True)
        account.owner_email = normalized_owner

    if account.contact_phone:
        valid, normalized_phone = validate_phone(account.contact_phone, raise_exception=True)
        account.contact_phone = normalized_phone

    if account.name:
        _, account.name = sanitize_string(account.name, max_length=200, raise_exception=True)

    if account.contact_name:
        _, account.contact_name = sanitize_string(account.contact_name, max_length=100, raise_exception=True)

    # owner_email is not a corporate_accounts column — consumed below.
    payload = account.model_dump(exclude={"owner_email"})

    try:
        created_account = await insert_corporate_account(payload)
    except Exception as e:
        logger.exception("Failed to create corporate account")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create corporate account.",
        ) from e

    # Seed the first owner member. Partial-success is deliberate: the company
    # row is valid on its own, so a bootstrap failure is surfaced explicitly
    # in the response (owner_bootstrap_error) instead of rolling back — staff
    # can re-invite from the members page.
    owner_invite_url = None
    owner_member_id = None
    owner_bootstrap_error = False
    if account.owner_email:
        try:
            member, owner_invite_url = await bootstrap_owner(
                company_id=str(created_account["id"]),
                email=account.owner_email,
                invited_by=str(current_admin.get("id") or ""),
            )
            owner_member_id = member.get("id")
        except Exception:
            logger.error(
                "create_corporate_account: owner bootstrap failed for company %s",
                created_account.get("id"),
                exc_info=True,
            )
            owner_bootstrap_error = True

    try:
        await log_admin_action(
            admin=current_admin,
            action="create_corporate_account",
            resource="corporate_account",
            resource_id=str(created_account["id"]),
            details={
                "company_name": created_account.get("name"),
                "owner_email_provided": bool(account.owner_email),
                "owner_bootstrap_error": owner_bootstrap_error,
            },
        )
    except Exception as _ae:
        logger.error(
            f"Audit log failed for create_corporate_account {created_account.get('id')}: {_ae}",
            exc_info=True,
        )

    return {
        **created_account,
        "owner_invite_url": owner_invite_url,
        "owner_member_id": owner_member_id,
        "owner_bootstrap_error": owner_bootstrap_error,
    }


@router.get("/{account_id}", response_model=CorporateAccountDetailResponse)
async def get_corporate_account(account_id: str, current_admin: dict = Depends(get_current_admin)):
    """
    Get a specific corporate account by ID.

    Args:
        account_id: ID of the corporate account
        current_admin: Authenticated admin user
    """
    # Validate account ID
    valid, normalized_id = validate_id(account_id, "Corporate Account ID", raise_exception=True)

    try:
        account = await get_corporate_account_by_id(validated_id=normalized_id)
        if not account:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Corporate account not found",
            )
        return account
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to fetch corporate account")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to fetch corporate account.",
        ) from e


@router.put("/{account_id}", response_model=CorporateAccountDetailResponse)
async def update_corporate_account(
    account_id: str,
    account_update: CorporateAccountUpdate,
    current_admin: dict = Depends(get_current_admin),
):
    """
    Update an existing corporate account.

    Args:
        account_id: ID of the corporate account to update
        account_update: Updated account data
        current_admin: Authenticated admin user
    """
    # Validate account ID
    valid, normalized_id = validate_id(account_id, "Corporate Account ID", raise_exception=True)

    # Check if account exists
    existing_account = await get_corporate_account_by_id(validated_id=normalized_id)
    if not existing_account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Corporate account not found")

    # Prepare update data
    update_data = {}
    for field, value in account_update.model_dump(exclude_unset=True).items():
        if value is not None:
            if field == "contact_email" and value:
                valid, normalized_email = validate_email(value, raise_exception=True)
                update_data[field] = normalized_email
            elif field == "contact_phone" and value:
                valid, normalized_phone = validate_phone(value, raise_exception=True)
                update_data[field] = normalized_phone
            elif field == "name" and value:
                _, update_data[field] = sanitize_string(value, max_length=200, raise_exception=True)
            elif field == "contact_name" and value:
                _, update_data[field] = sanitize_string(value, max_length=100, raise_exception=True)
            else:
                update_data[field] = value

    try:
        updated_account = await db_update_corporate_account(normalized_id, update_data)
    except Exception as e:
        logger.exception("Failed to update corporate account")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update corporate account.",
        ) from e

    try:
        await log_admin_action(
            admin=current_admin,
            action="update_corporate_account",
            resource="corporate_account",
            resource_id=str(normalized_id),
            details={
                "changed_fields": list(update_data.keys()),
                **{k: v for k, v in update_data.items() if k not in ("contact_email", "contact_phone")},
            },
        )
    except Exception as _ae:
        logger.error(
            f"Audit log failed for update_corporate_account {normalized_id}: {_ae}",
            exc_info=True,
        )

    return updated_account


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_corporate_account(account_id: str, current_admin: dict = Depends(get_current_admin)):
    """
    Delete a corporate account.

    Args:
        account_id: ID of the corporate account to delete
        current_admin: Authenticated admin user
    """
    # Validate account ID
    valid, normalized_id = validate_id(account_id, "Corporate Account ID", raise_exception=True)

    # Check if account exists
    existing_account = await get_corporate_account_by_id(validated_id=normalized_id)
    if not existing_account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Corporate account not found")

    try:
        await db_delete_corporate_account(normalized_id)
    except Exception as e:
        logger.exception("Failed to delete corporate account")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete corporate account.",
        ) from e

    try:
        await log_admin_action(
            admin=current_admin,
            action="delete_corporate_account",
            resource="corporate_account",
            resource_id=str(normalized_id),
            details={"company_name": existing_account.get("name")},
        )
    except Exception as _ae:
        logger.error(
            f"Audit log failed for delete_corporate_account {normalized_id}: {_ae}",
            exc_info=True,
        )

    return  # 204 No Content


@router.post(
    "/{company_id}/status",
    response_model=CorporateAccountDetailResponse,
)
async def change_company_status(
    company_id: str,
    transition: CompanyStatusTransition,
    current_admin: dict = Depends(get_current_admin),
):
    _valid, normalized_id = validate_id(company_id, "Corporate Account ID", raise_exception=True)

    current = await get_corporate_account_by_id(validated_id=normalized_id)
    if not current:
        raise HTTPException(status_code=404, detail="Corporate account not found")

    if current.get("status") == CompanyStatus.CLOSED.value:
        raise HTTPException(
            status_code=409,
            detail="Corporate account is closed and cannot be reopened",
        )

    from db_supabase import update_corporate_account_status

    # transition.reason is accepted but not persisted — the audit log table
    # lands in a later plan. Wallet freeze/unfreeze is handled below.
    row = await update_corporate_account_status(
        company_id=normalized_id,
        status=transition.status.value,
    )
    if not row:
        raise HTTPException(
            status_code=404,
            detail="Corporate account disappeared mid-transition",
        )

    # ── Wallet freeze ────────────────────────────────────────────────
    # Suspending or closing a company disables auto top-up so the next
    # scheduler tick won't silently charge the customer while rides are
    # blocked. Manual top-up / adjust endpoints already enforce the
    # status check themselves, so we only touch the auto-topup switch.
    if transition.status in (CompanyStatus.SUSPENDED, CompanyStatus.CLOSED):
        wallet = await get_corporate_wallet_by_company(normalized_id)
        if wallet and wallet.get("auto_topup_enabled"):
            await update_corporate_wallet_config(wallet_id=wallet["id"], patch={"auto_topup_enabled": False})

    # Pre-pickup rides (no passenger aboard yet) for this company are
    # auto-cancelled on suspend/close — a suspended company shouldn't keep
    # dispatching new drivers. Rides already `in_progress` are grandfathered:
    # the state machine forbids cancelling after trip start, so those bill
    # normally at settlement (flagged there for audit, not blocked here).
    cancelled_rides = 0
    if transition.status in (CompanyStatus.SUSPENDED, CompanyStatus.CLOSED):
        settings = await get_app_settings()
        if settings.get("corporate_suspend_cancels_pre_pickup_rides", True):
            try:
                cancelled_rides = await cancel_pre_pickup_rides_for_company(normalized_id)
            except Exception as _cancel_exc:
                logger.error(
                    f"Pre-pickup ride cancellation failed for suspended company {normalized_id}: {_cancel_exc}",
                    exc_info=True,
                )

    # Wallet wind-down: 'closed' is terminal (see the 409 check above — a
    # closed account can never reopen), so any balance left in the master
    # wallet has no other path back to the company. 'suspended' is
    # reversible and deliberately untouched here — only auto-topup is
    # disabled for it, above.
    winddown_result = None
    if transition.status == CompanyStatus.CLOSED:
        settings = await get_app_settings()
        if settings.get("corporate_close_refunds_wallet_balance", False):
            try:
                winddown_result = await refund_wallet_balance_on_close(
                    company_id=normalized_id,
                    stripe_customer_id=row.get("stripe_customer_id"),
                    actor_user_id=str(current_admin.get("id") or ""),
                )
                if (
                    winddown_result.get("stripe_error")
                    or winddown_result.get("unrefundable_amount", "0.00") != "0.00"
                    or winddown_result.get("ledger_write_failed")
                ):
                    logger.error(
                        "Corporate wallet close wind-down incomplete for company %s: %s",
                        normalized_id,
                        winddown_result,
                    )
            except Exception:
                logger.error(
                    "Corporate wallet close wind-down failed for company %s",
                    normalized_id,
                    exc_info=True,
                )
                winddown_result = {"skipped_reason": "unhandled_exception"}

    # Reactivation visibility: change_company_status only ever DISABLES
    # auto-topup on suspend/close (above) — there's no corresponding "turn it
    # back on" for the reverse transition, and doing that automatically would
    # be a real behavior change (silently re-enabling auto-charges) with no
    # way to know whether the company wanted auto-topup on before it was
    # suspended. Rather than guess, surface it so an admin notices and
    # re-enables via the existing wallet-config endpoint if appropriate.
    # Corporate module lifecycle audit Finding 4.
    auto_topup_needs_review = False
    if current.get("status") == CompanyStatus.SUSPENDED.value and transition.status == CompanyStatus.ACTIVE:
        try:
            reactivated_wallet = await get_corporate_wallet_by_company(normalized_id)
            auto_topup_needs_review = bool(reactivated_wallet) and not reactivated_wallet.get(
                "auto_topup_enabled", True
            )
        except Exception:
            logger.error(
                "Could not check wallet auto-topup state on reactivation for company %s",
                normalized_id,
                exc_info=True,
            )

    try:
        await log_admin_action(
            admin=current_admin,
            action="change_company_status",
            resource="corporate_account",
            resource_id=str(normalized_id),
            details={
                "old_status": current.get("status"),
                "new_status": transition.status.value,
                "reason": transition.reason if hasattr(transition, "reason") else None,
                "pre_pickup_rides_cancelled": cancelled_rides,
                "wallet_winddown": winddown_result,
                "auto_topup_needs_review": auto_topup_needs_review,
            },
        )
    except Exception as _ae:
        logger.error(
            f"Audit log failed for change_company_status {normalized_id}: {_ae}",
            exc_info=True,
        )

    return row
