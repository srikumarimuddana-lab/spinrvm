"""Factory helpers for corporate-account tests.

Kept out of conftest.py because pytest loads conftest modules by file path,
not by package-qualified name, so `from tests.conftest import X` fails.
This module is a regular importable module for shared test data builders.
"""

from typing import Any, Dict


def corporate_account_row(status_value: str = "active", **overrides: Any) -> Dict[str, Any]:
    """Build a CorporateAccountDetailResponse-shaped dict for route tests.

    The response schema in ``schemas.corporate`` requires every field here;
    missing any one raises a validation error when the handler serializes.
    """
    row: Dict[str, Any] = {
        "id": "c1",
        "name": "Acme",
        "status": status_value,
        "country_code": "CA",
        "currency": "CAD",
        "locale": "en-CA",
        "timezone": "America/Toronto",
        "size_tier": "smb",
        "is_active": True,
        "credit_limit": 0,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    row.update(overrides)
    return row
