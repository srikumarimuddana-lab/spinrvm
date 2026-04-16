# backend/tests/test_corporate_validators.py
import pytest

from validators import (
    validate_cra_business_number,
    validate_canadian_tax_region,
    validate_email_domain,
)


class TestBusinessNumber:
    @pytest.mark.parametrize("bn", ["123456789RT0001", "987654321", "123456789-RT-0001"])
    def test_accepts_valid_formats(self, bn):
        assert validate_cra_business_number(bn) == bn.replace("-", "").upper()

    @pytest.mark.parametrize("bad", ["", "abc", "12345", "12345678", "123456789XY9999"])
    def test_rejects_invalid(self, bad):
        with pytest.raises(ValueError):
            validate_cra_business_number(bad)


class TestTaxRegion:
    @pytest.mark.parametrize("region", ["ON", "QC", "BC", "AB", "SK", "MB", "NS", "NB", "NL", "PE", "YT", "NT", "NU"])
    def test_accepts_all_provinces_and_territories(self, region):
        assert validate_canadian_tax_region(region) == region

    @pytest.mark.parametrize("bad", ["", "XX", "on", "ontario", "CA"])
    def test_rejects_unknown(self, bad):
        with pytest.raises(ValueError):
            validate_canadian_tax_region(bad)


class TestDomain:
    @pytest.mark.parametrize("raw,expected", [
        ("ACME.com", "acme.com"),
        (" acme.com ", "acme.com"),
        ("@acme.com", "acme.com"),
        ("acme.co.uk", "acme.co.uk"),
    ])
    def test_normalizes(self, raw, expected):
        assert validate_email_domain(raw) == expected

    @pytest.mark.parametrize("bad", ["", "acme", "acme.", ".com", "has spaces.com", "a" * 254 + ".com"])
    def test_rejects_invalid(self, bad):
        with pytest.raises(ValueError):
            validate_email_domain(bad)
