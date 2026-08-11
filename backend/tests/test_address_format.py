"""Unit tests for utils/address_format.py (N16).

This module was extracted from two byte-identical private copies in
utils/company_details.py and utils/marketing_email.py. These tests pin the
shared logic directly; each caller's own existing test suite
(test_company_details.py, test_marketing_email.py) proves output parity
through the public functions that consume this module.
"""

from backend.utils.address_format import coalesce_setting, postal_address


class TestCoalesceSetting:
    def test_returns_stripped_string_value(self):
        assert coalesce_setting({"k": "  hello  "}, "k") == "hello"

    def test_none_value_becomes_empty_string(self):
        """The settings loader can surface a DB NULL as Python None — must
        not render the literal string "None"."""
        assert coalesce_setting({"k": None}, "k") == ""

    def test_missing_key_becomes_empty_string(self):
        assert coalesce_setting({}, "k") == ""

    def test_non_string_value_is_stringified(self):
        """More defensive than a bare `.strip()` — a non-string truthy
        value (shouldn't happen for these settings columns, but the helper
        must not crash on one) is coerced to a string first."""
        assert coalesce_setting({"k": 123}, "k") == "123"


class TestPostalAddress:
    def test_all_parts_present(self):
        settings = {
            "company_address": "123 Main St",
            "company_city": "Saskatoon",
            "company_province": "SK",
            "company_postal_code": "S7K 0A1",
        }
        assert postal_address(settings) == "123 Main St, Saskatoon SK S7K 0A1"

    def test_only_street_present(self):
        assert postal_address({"company_address": "123 Main St"}) == "123 Main St"

    def test_only_locality_parts_present(self):
        settings = {"company_city": "Regina", "company_province": "SK"}
        assert postal_address(settings) == "Regina SK"

    def test_all_blank_returns_empty_string(self):
        assert postal_address({}) == ""

    def test_none_values_treated_as_blank(self):
        settings = {
            "company_address": None,
            "company_city": "Regina",
            "company_province": None,
            "company_postal_code": None,
        }
        assert postal_address(settings) == "Regina"
