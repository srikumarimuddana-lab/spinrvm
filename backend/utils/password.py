"""
Password hashing utilities for admin staff authentication.

Uses bcrypt (from `requirements.txt`) as the primary hash. Supports
transparent reading of legacy SHA256 hashes so existing admin_staff
rows keep working until their owners log in once — at which point
the login route upgrades the stored hash in place.

Why this file exists
--------------------
Previously `routes/admin/auth.py` and `routes/admin/staff.py` stored
admin passwords as raw `hashlib.sha256(password.encode()).hexdigest()`.
SHA256 is a fast hash — on a modern GPU an attacker can try billions
of guesses per second against any leaked admin_staff row. bcrypt's
cost-factor-based design means each guess is deliberately slow
(tens of ms), which turns billion-guess attacks into impractical
timeframes.

Usage
-----
    from utils.password import hash_password, verify_password

    # On staff creation:
    staff["password_hash"] = hash_password(plain_password)

    # On login:
    ok, needs_upgrade = verify_password(plain_password, staff["password_hash"])
    if ok and needs_upgrade:
        await db.admin_staff.update_one(
            {"id": staff["id"]},
            {"$set": {"password_hash": hash_password(plain_password)}},
        )
"""

from __future__ import annotations

import hashlib
import re

import bcrypt

# bcrypt hashes always start with `$2a$`, `$2b$`, or `$2y$` followed
# by the cost factor. This is how we distinguish new-style rows from
# legacy SHA256 rows without a schema migration.
_BCRYPT_PREFIX = re.compile(r"^\$2[aby]\$")

# SHA256 hex digest is exactly 64 lowercase hex chars.
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")

# Cost factor of 12 is a reasonable default in 2026 — roughly
# 250 ms per hash on a modern CPU. High enough to deter brute force,
# low enough that legitimate logins don't feel slow.
_BCRYPT_ROUNDS = 12


def hash_password(password: str) -> str:
    """Hash a plaintext password with bcrypt.

    Returns a UTF-8 string so the hash can be stored as a plain TEXT
    column in Postgres without encoding hassles.
    """
    if not isinstance(password, str) or not password:
        raise ValueError("password must be a non-empty string")
    salt = bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(password: str, stored_hash: str) -> tuple[bool, bool]:
    """Verify `password` against `stored_hash`.

    Returns ``(is_valid, needs_upgrade)``.

    * ``is_valid`` — True iff the password matches the stored hash,
      regardless of which algorithm the stored hash uses.
    * ``needs_upgrade`` — True iff the caller should rewrite the
      stored hash using :func:`hash_password`. Fires for legacy
      SHA256 rows and for bcrypt rows whose cost factor is below
      the current target.
    """
    if not password or not stored_hash:
        return (False, False)

    # New-style bcrypt hash
    if _BCRYPT_PREFIX.match(stored_hash):
        try:
            ok = bcrypt.checkpw(password.encode("utf-8"), stored_hash.encode("utf-8"))
        except ValueError:
            return (False, False)
        if not ok:
            return (False, False)
        # Optional: detect hashes with an outdated cost factor so we
        # re-hash them to the current target when the user next logs
        # in. bcrypt's `gensalt` output embeds the cost as the chars
        # after the algorithm prefix, e.g. `$2b$10$...` → rounds=10.
        try:
            rounds = int(stored_hash.split("$")[2])
        except (IndexError, ValueError):
            rounds = _BCRYPT_ROUNDS
        return (True, rounds < _BCRYPT_ROUNDS)

    # Legacy SHA256 hash from before this utility existed.
    if _SHA256_HEX.match(stored_hash):
        legacy_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()
        # Constant-time compare to avoid timing leaks across the
        # thousands of hex-compare characters.
        ok = _constant_time_equal(legacy_hash, stored_hash)
        if ok:
            # Legacy rows ALWAYS need upgrading.
            return (True, True)
        return (False, False)

    # Unknown format — never match.
    return (False, False)


def _constant_time_equal(a: str, b: str) -> bool:
    """Length-safe, timing-safe string equality for hex digests."""
    if len(a) != len(b):
        return False
    result = 0
    # `strict=True` is redundant after the length check above, but ruff's
    # B905 rule requires it to be explicit so the intent is unambiguous.
    for x, y in zip(a, b, strict=True):
        result |= ord(x) ^ ord(y)
    return result == 0
