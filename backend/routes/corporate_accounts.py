"""
Corporate accounts API routes for managing business clients and billing.

This module implements CRUD operations for corporate accounts that can be used
for business rides and expense management.
"""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from db_supabase import (  # noqa: E402
    delete_corporate_account as db_delete_corporate_account,
)
from db_supabase import (  # noqa: E402
    get_corporate_account_by_id,
    insert_corporate_account,
)
from db_supabase import (  # noqa: E402
    update_corporate_account as db_update_corporate_account,
)
from dependencies import get_admin_user
from schemas.corporate import (  # noqa: E402
    CompanyStatus,
    CompanyStatusTransition,
    KYBReviewDecision,
    SizeTier,
)
from schemas.corporate import (
    CorporateAccountResponse as CorporateAccountDetailResponse,
)
from validators import sanitize_string, validate_email, validate_id, validate_phone  # noqa: E402

# Alias for backward compatibility
get_current_admin = get_admin_user

router = APIRouter(prefix="/admin/corporate-accounts", tags=["Corporate Accounts"])


# Pydantic models for request/response validation
class CorporateAccountBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=200, description="Company name")
    contact_name: Optional[str] = Field(None, max_length=100, description="Primary contact person")
    contact_email: Optional[str] = Field(None, description="Contact email address")
    contact_phone: Optional[str] = Field(None, description="Contact phone number")
    credit_limit: float = Field(0, ge=0, description="Credit limit for corporate billing")
    is_active: bool = Field(True, description="Whether the account is active")


class CorporateAccountCreate(CorporateAccountBase):
    pass


class CorporateAccountUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    contact_name: Optional[str] = Field(None, max_length=100)
    contact_email: Optional[str] = Field(None)
    contact_phone: Optional[str] = Field(None)
    credit_limit: Optional[float] = Field(None, ge=0)
    is_active: Optional[bool] = Field(None)


class CorporateAccountResponse(CorporateAccountBase):
    id: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


@router.get("", response_model=List[CorporateAccountDetailResponse])
async def get_corporate_accounts(
    request: Request,
    skip: int = 0,
    limit: int = 100,
    search: Optional[str] = None,
    status: Optional[CompanyStatus] = None,
    size_tier: Optional[SizeTier] = None,
    is_active: Optional[bool] = None,
    current_admin: dict = Depends(get_current_admin),
):
    """List corporate accounts with optional filters and pagination."""
    from db_supabase import list_corporate_accounts_filtered

    try:
        rows = await list_corporate_accounts_filtered(
            status=status.value if status else None,
            size_tier=size_tier.value if size_tier else None,
            search=search,
            skip=skip,
            limit=min(limit, 500),
        )
        if is_active is not None:
            rows = [r for r in rows if bool(r.get("is_active")) == is_active]
        return rows
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch corporate accounts: {str(e)}",
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

    return await create_kyb_upload_url(
        company_id=normalized_id, content_type=body.content_type
    )


@router.post("/{company_id}/kyb-review", response_model=CorporateAccountDetailResponse)
async def kyb_review(
    company_id: str,
    decision: KYBReviewDecision,
    request: Request,
    current_admin: dict = Depends(get_current_admin),
):
    """Approve or reject a pending KYB submission.

    Approve → status='active'. Reject → status='suspended' so the company
    can re-upload and be re-reviewed from the queue.
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
    return row


@router.post("", response_model=CorporateAccountResponse, status_code=status.HTTP_201_CREATED)
async def create_corporate_account(
    request: Request, account: CorporateAccountCreate, current_admin: dict = Depends(get_current_admin)
):
    """
    Create a new corporate account.

    Args:
        account: Corporate account data
        current_admin: Authenticated admin user
    """
    # Validate inputs
    if account.contact_email:
        valid, normalized_email = validate_email(account.contact_email, raise_exception=True)
        account.contact_email = normalized_email

    if account.contact_phone:
        valid, normalized_phone = validate_phone(account.contact_phone, raise_exception=True)
        account.contact_phone = normalized_phone

    if account.name:
        account.name = sanitize_string(account.name, max_length=200, raise_exception=True)

    if account.contact_name:
        account.contact_name = sanitize_string(account.contact_name, max_length=100, raise_exception=True)

    try:
        created_account = await insert_corporate_account(account.model_dump())
        return created_account
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to create corporate account: {str(e)}"
        ) from e


@router.get("/{account_id}", response_model=CorporateAccountResponse)
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
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Corporate account not found")
        return account
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to fetch corporate account: {str(e)}"
        ) from e


@router.put("/{account_id}", response_model=CorporateAccountResponse)
async def update_corporate_account(
    account_id: str, account_update: CorporateAccountUpdate, current_admin: dict = Depends(get_current_admin)
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
                update_data[field] = sanitize_string(value, max_length=200, raise_exception=True)
            elif field == "contact_name" and value:
                update_data[field] = sanitize_string(value, max_length=100, raise_exception=True)
            else:
                update_data[field] = value

    try:
        updated_account = await db_update_corporate_account(normalized_id, update_data)
        return updated_account
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to update corporate account: {str(e)}"
        ) from e


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
        return  # 204 No Content
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to delete corporate account: {str(e)}"
        ) from e


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

    # transition.reason is accepted but not persisted — audit log table lands
    # with Plan 2, wallet freeze/unfreeze follows status in the same plan.
    row = await update_corporate_account_status(
        company_id=normalized_id,
        status=transition.status.value,
    )
    if not row:
        raise HTTPException(
            status_code=404,
            detail="Corporate account disappeared mid-transition",
        )
    return row
