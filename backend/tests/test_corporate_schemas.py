# backend/tests/test_corporate_schemas.py
import pytest
from pydantic import ValidationError

from schemas.corporate import (
    CompanyStatus,
    CorporateAccountCreate,
    CorporateAccountUpdate,
    CorporateSelfServeSignup,
    Locale,
    SizeTier,
)


def _valid_create() -> dict:
    return {
        "name": "Acme Corp",
        "legal_name": "Acme Corporation Inc.",
        "business_number": "123456789RT0001",
        "tax_region": "ON",
        "billing_email": "billing@acme.com",
        "size_tier": "smb",
        "contact_email": "contact@acme.com",
    }


class TestCorporateAccountCreate:
    def test_accepts_valid_payload(self):
        m = CorporateAccountCreate(**_valid_create())
        assert m.country_code == "CA"
        assert m.currency == "CAD"
        assert m.locale == "en-CA"
        assert m.timezone == "America/Toronto"
        assert m.business_number == "123456789RT0001"

    def test_normalizes_business_number(self):
        p = _valid_create()
        p["business_number"] = "123-456-789-RT-0001"
        m = CorporateAccountCreate(**p)
        assert m.business_number == "123456789RT0001"

    def test_rejects_bad_tax_region(self):
        p = _valid_create()
        p["tax_region"] = "XX"
        with pytest.raises(ValidationError):
            CorporateAccountCreate(**p)

    def test_rejects_bad_locale(self):
        p = _valid_create()
        p["locale"] = "en-US"
        with pytest.raises(ValidationError):
            CorporateAccountCreate(**p)

    def test_rejects_bad_size_tier(self):
        p = _valid_create()
        p["size_tier"] = "tiny"
        with pytest.raises(ValidationError):
            CorporateAccountCreate(**p)


class TestEnums:
    def test_company_status_values(self):
        assert {s.value for s in CompanyStatus} == {"pending_verification", "active", "suspended", "closed"}

    def test_size_tier_values(self):
        assert {s.value for s in SizeTier} == {"smb", "mid_market", "enterprise"}

    def test_locale_values(self):
        assert {loc.value for loc in Locale} == {"en-CA", "fr-CA"}


class TestCorporateAccountUpdate:
    def test_all_fields_optional(self):
        CorporateAccountUpdate()  # should not raise

    def test_normalizes_bn_on_update(self):
        m = CorporateAccountUpdate(business_number="987654321")
        assert m.business_number == "987654321"


# ── CorporateSelfServeSignup (migration 224) ─────────────────────────────────


def _valid_signup() -> dict:
    return {
        "name": "Acme Corp",
        "legal_name": "Acme Corporation Inc.",
        "business_number": "123456789RT0001",
        "industry": "Automotive",
        "contact_name": "Jane Doe",
        "contact_phone": "+13065550100",
        "address_line1": "123 Main St",
        "city": "Saskatoon",
        "province": "SK",
        "postal_code": "S7K 1M1",
        "terms_accepted": True,
        "terms_version": "biz-tos-2026-01",
    }


class TestCorporateSelfServeSignup:
    def test_accepts_valid_payload(self):
        m = CorporateSelfServeSignup(**_valid_signup())
        assert m.province == "SK"
        assert m.size_tier == SizeTier.SMB

    def test_kyb_fields_required(self):
        # Unlike the staff create schema, legal_name and business_number are
        # mandatory — a self-registering company must be KYB-reviewable.
        for missing in ("legal_name", "business_number", "contact_name"):
            payload = _valid_signup()
            del payload[missing]
            with pytest.raises(ValidationError):
                CorporateSelfServeSignup(**payload)

    def test_normalizes_business_number(self):
        m = CorporateSelfServeSignup(**{**_valid_signup(), "business_number": "12-345 6789 rt0001"})
        assert m.business_number == "123456789RT0001"

    def test_rejects_bad_business_number(self):
        with pytest.raises(ValidationError):
            CorporateSelfServeSignup(**{**_valid_signup(), "business_number": "12345"})

    def test_province_normalized_and_validated(self):
        m = CorporateSelfServeSignup(**{**_valid_signup(), "province": "sk"})
        assert m.province == "SK"
        with pytest.raises(ValidationError):
            CorporateSelfServeSignup(**{**_valid_signup(), "province": "XX"})

    def test_postal_code_canonicalized(self):
        m = CorporateSelfServeSignup(**{**_valid_signup(), "postal_code": "s7k1m1"})
        assert m.postal_code == "S7K 1M1"
        with pytest.raises(ValidationError):
            CorporateSelfServeSignup(**{**_valid_signup(), "postal_code": "12345"})

    def test_terms_must_be_accepted(self):
        with pytest.raises(ValidationError):
            CorporateSelfServeSignup(**{**_valid_signup(), "terms_accepted": False})

    def test_no_email_field_accepted(self):
        # Email/user identity comes from the authenticated session, never the
        # payload (extra="forbid" enforces this).
        with pytest.raises(ValidationError):
            CorporateSelfServeSignup(**{**_valid_signup(), "contact_email": "spoof@evil.com"})
