# backend/tests/test_corporate_schemas.py
import pytest
from pydantic import ValidationError

from schemas.corporate import (
    CompanyStatus,
    CorporateAccountCreate,
    CorporateAccountUpdate,
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
