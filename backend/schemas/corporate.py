"""Pydantic v2 schemas for corporate accounts (B2B v1)."""

from __future__ import annotations

import re
from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

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


class CorporateSelfServeSignup(BaseModel):
    """Payload for the public self-serve company signup (migration 224).

    Submitted AUTHENTICATED: the caller has already completed the portal
    work-email OTP login, so their verified email/user_id come from the
    session — never from this payload. Unlike the staff-facing
    ``CorporateAccountCreate``, the KYB-relevant fields (legal_name,
    business_number) are REQUIRED here: a self-registering company must
    provide them up front so the staff KYB queue has something to review.
    ``province`` doubles as ``tax_region`` (two-letter code, validated).
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=200)
    legal_name: str = Field(..., min_length=1, max_length=300)
    business_number: str = Field(..., max_length=20)
    industry: Optional[str] = Field(None, max_length=100)
    size_tier: SizeTier = SizeTier.SMB
    contact_name: str = Field(..., min_length=1, max_length=100)
    contact_phone: Optional[str] = Field(None, max_length=32)
    billing_email: Optional[EmailStr] = None
    address_line1: str = Field(..., min_length=1, max_length=200)
    address_line2: Optional[str] = Field(None, max_length=200)
    city: str = Field(..., min_length=1, max_length=100)
    province: str = Field(..., min_length=2, max_length=2)
    postal_code: str = Field(..., min_length=6, max_length=7)
    locale: Locale = Locale.EN_CA
    # Business terms consent (PIPEDA-adjacent recording): the client sends the
    # version string it displayed; acceptance must be explicit.
    terms_accepted: bool
    terms_version: str = Field(..., min_length=1, max_length=32)

    @field_validator("business_number")
    @classmethod
    def _check_bn(cls, v: str) -> str:
        return validate_cra_business_number(v)

    @field_validator("province")
    @classmethod
    def _check_province(cls, v: str) -> str:
        return validate_canadian_tax_region(v.strip().upper())

    @field_validator("postal_code")
    @classmethod
    def _check_postal(cls, v: str) -> str:
        canon = v.strip().upper().replace(" ", "")
        if not re.match(r"^[A-Z]\d[A-Z]\d[A-Z]\d$", canon):
            raise ValueError("invalid Canadian postal code")
        return f"{canon[:3]} {canon[3:]}"

    @field_validator("terms_accepted")
    @classmethod
    def _check_terms(cls, v: bool) -> bool:
        if not v:
            raise ValueError("business terms must be accepted")
        return v


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


class MemberRole(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class MemberStatus(str, Enum):
    INVITED = "invited"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    REMOVED = "removed"


class AllowanceType(str, Enum):
    FIXED_RECURRING = "fixed_recurring"
    ONE_TIME = "one_time"
    UNLIMITED = "unlimited"


class AllowanceStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    EXPIRED = "expired"


class AllowanceRequestStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    AUTO_APPROVED = "auto_approved"


class MemberInvite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    role: MemberRole = MemberRole.MEMBER
    policy_override: bool = False

    @field_validator("email", mode="before")
    @classmethod
    def _normalize_email(cls, v: str) -> str:
        return (v or "").strip().lower()


class MemberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="ignore")

    id: str
    company_id: str
    user_id: Optional[str] = None
    role: MemberRole
    status: MemberStatus
    invited_email: Optional[str] = None
    invited_at: Optional[datetime] = None
    joined_at: Optional[datetime] = None
    policy_override: bool = False
    created_at: datetime
    updated_at: datetime


class MemberUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Optional[MemberRole] = None
    status: Optional[MemberStatus] = None
    policy_override: Optional[bool] = None
    # Section assignment (migration 206). Empty string clears the section
    # (None means "not provided" under exclude_none); the route validates
    # the section belongs to the same company.
    section_id: Optional[str] = None


class AllowanceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: AllowanceType
    amount: Optional[float] = Field(None, gt=0, le=100000)
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    rollover: bool = False
    auto_approve_topup_amount: Optional[float] = Field(None, gt=0, le=10000)
    auto_approve_monthly_count: Optional[int] = Field(None, ge=0, le=50)

    @model_validator(mode="after")
    def _check_shape(self):
        if self.type == AllowanceType.UNLIMITED:
            if self.amount is not None:
                raise ValueError("unlimited allowance cannot have amount")
            if self.period_start is not None or self.period_end is not None:
                raise ValueError("unlimited allowance cannot have period")
        elif self.type == AllowanceType.FIXED_RECURRING:
            if self.amount is None:
                raise ValueError("fixed_recurring requires amount")
            if self.period_start is None or self.period_end is None:
                raise ValueError("fixed_recurring requires period_start and period_end")
            if self.period_end <= self.period_start:
                raise ValueError("period_end must be after period_start")
        else:
            if self.amount is None:
                raise ValueError("one_time requires amount")
        return self


class AllowanceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: Optional[float] = Field(None, gt=0, le=100000)
    rollover: Optional[bool] = None
    auto_approve_topup_amount: Optional[float] = Field(None, gt=0, le=10000)
    auto_approve_monthly_count: Optional[int] = Field(None, ge=0, le=50)
    status: Optional[AllowanceStatus] = None


class AllowanceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="ignore")

    id: str
    member_id: str
    type: AllowanceType
    amount: Optional[float] = None
    used: float = 0
    period_start: Optional[date] = None
    period_end: Optional[date] = None
    rollover: bool = False
    auto_approve_topup_amount: Optional[float] = None
    auto_approve_monthly_count: Optional[int] = None
    auto_approved_this_period: int = 0
    status: AllowanceStatus


class AllowanceRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: float = Field(..., gt=0, le=10000)
    reason: str = Field(..., min_length=1, max_length=500)


class AllowanceRequestDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approve: bool
    note: Optional[str] = Field(None, max_length=500)


class AllowanceRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="ignore")

    id: str
    member_id: str
    amount: float
    reason: str
    status: AllowanceRequestStatus
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    decision_notes: Optional[str] = None
    created_at: datetime


class AllowedDomainCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: str = Field(..., min_length=3, max_length=253)

    @field_validator("domain", mode="before")
    @classmethod
    def _normalize(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if v.startswith("@"):
            raise ValueError("domain must not include '@'")
        if not v or "." not in v:
            raise ValueError("domain must contain a dot")
        return v


# ── Policy ────────────────────────────────────────────────────────────────────


class PaymentSourcePolicy(str, Enum):
    ALLOWANCE_ONLY = "allowance_only"
    MASTER_ONLY = "master_only"
    BOTH = "both"


class TimeWindow(BaseModel):
    """One allowed day+time slot for the time_window policy rule."""

    model_config = ConfigDict(extra="forbid")

    day: str = Field(
        ...,
        pattern=r"^(mon|tue|wed|thu|fri|sat|sun)$",
        description="Lowercase 3-letter day abbreviation",
    )
    start: str = Field(
        ...,
        pattern=r"^\d{2}:\d{2}$",
        description="Start time HH:MM (24h, company timezone)",
    )
    end: str = Field(
        ...,
        pattern=r"^\d{2}:\d{2}$",
        description="End time HH:MM (24h, company timezone)",
    )

    @model_validator(mode="after")
    def _check_order(self) -> "TimeWindow":
        if self.end <= self.start:
            raise ValueError("end must be after start")
        return self


class PolicyCreate(BaseModel):
    """Full policy replacement payload (PUT /company/{id}/policy)."""

    model_config = ConfigDict(extra="forbid")

    max_fare_per_ride: Optional[float] = Field(
        None,
        gt=0,
        le=10000,
        description="Max CAD fare per work ride.  NULL means no cap.",
    )
    allowed_geofence: Optional[dict] = Field(
        None,
        description="GeoJSON FeatureCollection.  Pickup AND dropoff must be "
        "inside at least one polygon.  NULL means no restriction.",
    )
    allowed_time_windows: Optional[list[TimeWindow]] = Field(
        None,
        description="Allowed booking windows.  NULL means 24/7.",
    )
    allowed_payment_source: PaymentSourcePolicy = PaymentSourcePolicy.BOTH
    active: bool = True


class PolicyUpdate(BaseModel):
    """Partial policy update (PATCH /company/{id}/policy)."""

    model_config = ConfigDict(extra="forbid")

    max_fare_per_ride: Optional[float] = Field(None, gt=0, le=10000)
    allowed_geofence: Optional[dict] = None
    allowed_time_windows: Optional[list[TimeWindow]] = None
    allowed_payment_source: Optional[PaymentSourcePolicy] = None
    active: Optional[bool] = None


class PolicyResponse(BaseModel):
    """Policy row as returned to the company portal and rider Work Profile."""

    model_config = ConfigDict(from_attributes=True, extra="ignore")

    id: str
    company_id: str
    active: bool
    max_fare_per_ride: Optional[float] = None
    allowed_geofence: Optional[dict] = None
    allowed_time_windows: Optional[list] = None
    allowed_payment_source: PaymentSourcePolicy = PaymentSourcePolicy.BOTH
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
