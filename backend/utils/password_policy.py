"""
Admin password policy enforcer (A-P4-3).

Rules applied to every admin credential mutation
(create-staff, change-password, reset-password):
  1. Minimum 20 characters
  2. At least one uppercase letter, one digit, one punctuation symbol
  3. Not in the embedded common-password blacklist (case-insensitive)

The blacklist covers the most-targeted patterns in credential-stuffing
dictionaries. It is intentionally compact (not the full 10 k file) so
the module loads fast and the list can be reviewed in code review.
Update it as new spray campaigns surface new top-targets.
"""

from __future__ import annotations

import string

from fastapi import HTTPException

# ---------------------------------------------------------------------------
# Common-password blacklist (case-insensitive; matched against pw.lower())
# ---------------------------------------------------------------------------
# Source: top patterns from SecLists/Passwords/Common-Credentials, trimmed to
# patterns an admin is most likely to try when choosing a "strong" password
# that still fails the spirit of the policy.
_COMMON: frozenset[str] = frozenset(
    {
        # Simple numeric runs
        "12345678901234567890",
        "123456789012345678",
        # keyboard walks
        "1q2w3e4r5t6y7u8i",
        "1q2w3e4r5t6y7u8i9o",
        "qwertyuiopasdfgh",
        "qwertyuiopasdfghjkl",
        "qazwsxedcrfvtgbyhn",
        "zxcvbnmasdfghjklqwer",
        # password variants
        "password",
        "password1",
        "password12",
        "password123",
        "password1234",
        "password12345",
        "password123456",
        "password1!",
        "password12!",
        "password123!",
        "password1234!",
        "password@123",
        "password@1234",
        "p@ssword",
        "p@ssw0rd",
        "p@55word",
        "p@ssword1",
        "p@ssword!",
        "p@ssw0rd1",
        "p@ssw0rd!",
        "p@55w0rd!",
        "passw0rd",
        "passw0rd1",
        "passw0rd!",
        "passw0rd12",
        # admin variants
        "admin",
        "admin1",
        "admin12",
        "admin123",
        "admin1234",
        "admin12345",
        "admin@123",
        "admin@1234",
        "admin@12345",
        "admin!123",
        "admin!1234",
        "administrator",
        "administrator1",
        "administrator!",
        "administrator@1",
        # welcome / changeme
        "welcome",
        "welcome1",
        "welcome123",
        "welcome!",
        "welcome@123",
        "welcome1234!",
        "changeme",
        "changeme1",
        "changeme!",
        "change.me.123",
        "change_me_123",
        # letmein / trustno1
        "letmein",
        "letmein1",
        "letmein!",
        "letmein@1",
        "trustno1",
        "trustno1!",
        # product-specific patterns
        "spinr",
        "spinr123",
        "spinr@123",
        "spinrpass",
        "spinrpass1",
        "spinr@1234",
        "spinr!1234",
        # seasonal patterns (years 2024-2030)
        "summer2024",
        "summer2025",
        "summer2026",
        "summer2027",
        "winter2024",
        "winter2025",
        "winter2026",
        "winter2027",
        "spring2024",
        "spring2025",
        "spring2026",
        "fall2024",
        "fall2025",
        "fall2026",
        # role/function keywords
        "superadmin",
        "superadmin1",
        "superadmin!",
        "superadmin@1",
        "root",
        "root123",
        "root@123",
        "root!123",
        "test",
        "test123",
        "test@123",
        "test@1234",
        "test!1234",
        # common phrases
        "iloveyou",
        "iloveyou1",
        "iloveyou!",
        "monkey",
        "dragon",
        "master",
        "sunshine",
        "football",
        "baseball",
        "basketball",
        "superman",
        "batman",
        "shadow",
        "mustang",
        "hockey",
        "dallas",
    }
)


def validate_admin_password(password: str) -> None:
    """Raise ``HTTPException(422)`` if *password* violates admin policy.

    Callers must invoke this before hashing and storing any new admin
    password (create-staff, change-password, reset-password).
    """
    if len(password) < 20:
        raise HTTPException(
            status_code=422,
            detail="password_too_short: admin passwords must be at least 20 characters",
        )

    has_upper = any(c.isupper() for c in password)
    has_digit = any(c.isdigit() for c in password)
    has_symbol = any(c in string.punctuation for c in password)

    if not (has_upper and has_digit and has_symbol):
        raise HTTPException(
            status_code=422,
            detail="password_complexity_required: must contain at least one uppercase letter, one digit, and one symbol",
        )

    if password.lower() in _COMMON:
        raise HTTPException(
            status_code=422,
            detail="password_too_common: choose a less predictable password",
        )
