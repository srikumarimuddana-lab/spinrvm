"""Pydantic v2 schemas for corporate accounts (B2B v1)."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

try:
    from ..validators import (
        validate_canadian_tax_region,
        validate_cra_business_number,
    )
except ImportError:
    from validators import (  # type: ignore
        validate_canadian_tax_region,
        validate_cra_business_number,
    )


class CompanyStatus(str, Enum):
    PENDING_VERIFICATION = "pending_verification"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    CLOSED = "closed"


class SizeTier(str, Enum):
    SMB = "smb"
    MID_MARKET = "mid_market"
    ENTERPRISE = "enterprise"


class Locale(str, Enum):
    EN_CA = "en-CA"
    FR_CA = "fr-CA"


class CorporateAccountBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=200)
    legal_name: Optional[str] = Field(None, max_length=300)
    business_number: Optional[str] = Field(None, max_length=20)
    country_code: str = Field("CA", min_length=2, max_length=2)
    currency: str = Field("CAD", min_length=3, max_length=3)
    tax_region: Optional[str] = None
    timezone: str = Field("America/Toronto", max_length=64)
    locale: Locale = Locale.EN_CA
    billing_email: Optional[EmailStr] = None
    contact_name: Optional[str] = Field(None, max_length=100)
    contact_email: Optional[EmailStr] = None
    contact_phone: Optional[str] = Field(None, max_length=32)
    size_tier: SizeTier = SizeTier.SMB
    is_active: bool = True

    @field_validator("business_number")
    @classmethod
    def _check_bn(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return None
        return validate_cra_business_number(v)

    @field_validator("tax_region")
    @classmethod
    def _check_region(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        return validate_canadian_tax_region(v)


class CorporateAccountCreate(CorporateAccountBase):
    """Payload for creating a new corporate account (internal use by super-admin).

    The public self-serve signup flow (Plan not in this document) will wrap
    this schema with an outer form that also uploads the KYB document.
    """


class CorporateAccountUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(None, min_length=1, max_length=200)
    legal_name: Optional[str] = Field(None, max_length=300)
    business_number: Optional[str] = Field(None, max_length=20)
    tax_region: Optional[str] = None
    timezone: Optional[str] = Field(None, max_length=64)
    locale: Optional[Locale] = None
    billing_email: Optional[EmailStr] = None
    contact_name: Optional[str] = Field(None, max_length=100)
    contact_email: Optional[EmailStr] = None
    contact_phone: Optional[str] = Field(None, max_length=32)
    size_tier: Optional[SizeTier] = None
    is_active: Optional[bool] = None
    credit_limit: Optional[float] = Field(None, ge=0)

    @field_validator("business_number")
    @classmethod
    def _check_bn(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return None
        return validate_cra_business_number(v)

    @field_validator("tax_region")
    @classmethod
    def _check_region(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        return validate_canadian_tax_region(v)


class CorporateAccountResponse(CorporateAccountBase):
    model_config = ConfigDict(from_attributes=True, extra="ignore")

    id: str
    status: CompanyStatus
    stripe_customer_id: Optional[str] = None
    kyb_document_url: Optional[str] = None
    kyb_reviewed_at: Optional[datetime] = None
    kyb_reviewed_by: Optional[str] = None
    credit_limit: float = Field(0, ge=0)
    created_at: datetime
    updated_at: datetime


class KYBReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approve: bool
    note: Optional[str] = Field(None, max_length=500)


class CompanyStatusTransition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: CompanyStatus
    reason: Optional[str] = Field(None, max_length=500)
