"""
Corporate accounts API routes for managing business clients and billing.

This module implements CRUD operations for corporate accounts that can be used
for business rides and expense management.
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Request
from pydantic import BaseModel, Field
from datetime import datetime

from dependencies import get_admin_user
# Alias for backward compatibility
get_current_admin = get_admin_user
try:
    from utils.rate_limiter import admin_rate_limit
except ImportError:
    from utils.rate_limiter import admin_rate_limit
from validators import validate_id, validate_email, validate_phone, sanitize_string

# Validate that we're importing the right function
from db_supabase import get_all_corporate_accounts, get_corporate_account_by_id, insert_corporate_account, update_corporate_account, delete_corporate_account

router = APIRouter(prefix="/api/admin/corporate-accounts", tags=["Corporate Accounts"])


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


@router.get("/", response_model=List[CorporateAccountResponse])
async def get_corporate_accounts(
    request: Request,
    skip: int = 0,
    limit: int = 100,
    search: Optional[str] = None,
    is_active: Optional[bool] = None,
    current_admin: dict = Depends(get_current_admin)
):
    """
    Get all corporate accounts with optional filtering and pagination.
    
    Args:
        skip: Number of records to skip (for pagination)
        limit: Maximum number of records to return
        search: Search term to match against company name, contact name, or email
        is_active: Filter by active status
        current_admin: Authenticated admin user
    """
    from ..db_supabase import get_all_corporate_accounts
    
    try:
        accounts = await get_all_corporate_accounts(
            skip=skip,
            limit=limit,
            search=search,
            is_active=is_active
        )
        return accounts
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch corporate accounts: {str(e)}"
        )


@router.post("/", response_model=CorporateAccountResponse, status_code=status.HTTP_201_CREATED)
async def create_corporate_account(
    request: Request,
    account: CorporateAccountCreate,
    current_admin: dict = Depends(get_current_admin)
):
    """
    Create a new corporate account.
    
    Args:
        account: Corporate account data
        current_admin: Authenticated admin user
    """
    from ..db_supabase import insert_corporate_account
    
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
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create corporate account: {str(e)}"
        )


@router.get("/{account_id}", response_model=CorporateAccountResponse)
async def get_corporate_account(
    account_id: str,
    current_admin: dict = Depends(get_current_admin)
):
    """
    Get a specific corporate account by ID.
    
    Args:
        account_id: ID of the corporate account
        current_admin: Authenticated admin user
    """
    from ..db_supabase import get_corporate_account_by_id
    
    # Validate account ID
    valid, normalized_id = validate_id(account_id, "Corporate Account ID", raise_exception=True)
    
    try:
        account = await get_corporate_account_by_id(validated_id=normalized_id)
        if not account:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Corporate account not found"
            )
        return account
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch corporate account: {str(e)}"
        )


@router.put("/{account_id}", response_model=CorporateAccountResponse)
async def update_corporate_account(
    account_id: str,
    account_update: CorporateAccountUpdate,
    current_admin: dict = Depends(get_current_admin)
):
    """
    Update an existing corporate account.
    
    Args:
        account_id: ID of the corporate account to update
        account_update: Updated account data
        current_admin: Authenticated admin user
    """
    from ..db_supabase import get_corporate_account_by_id, update_corporate_account
    
    # Validate account ID
    valid, normalized_id = validate_id(account_id, "Corporate Account ID", raise_exception=True)
    
    # Check if account exists
    existing_account = await get_corporate_account_by_id(validated_id=normalized_id)
    if not existing_account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Corporate account not found"
        )
    
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
        updated_account = await update_corporate_account(normalized_id, update_data)
        return updated_account
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update corporate account: {str(e)}"
        )


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_corporate_account(
    account_id: str,
    current_admin: dict = Depends(get_current_admin)
):
    """
    Delete a corporate account.
    
    Args:
        account_id: ID of the corporate account to delete
        current_admin: Authenticated admin user
    """
    from ..db_supabase import get_corporate_account_by_id, delete_corporate_account
    
    # Validate account ID
    valid, normalized_id = validate_id(account_id, "Corporate Account ID", raise_exception=True)
    
    # Check if account exists
    existing_account = await get_corporate_account_by_id(validated_id=normalized_id)
    if not existing_account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Corporate account not found"
        )
    
    try:
        await delete_corporate_account(normalized_id)
        return  # 204 No Content
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete corporate account: {str(e)}"
        )