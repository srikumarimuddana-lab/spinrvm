"""Human-readable short codes for driver rows.

drivers.id stays a UUID (foreign keys, joins, WS keys depend on it), but
operators, riders, and support need a short identifier they can quote and
search by. This mirrors utils/ride_code.py: ``DRV-XXXXXX`` with a
Crockford-ish 32-character alphabet that omits 0/O/1/I/L so the code can't be
misread aloud.

32 symbols, 6 characters → 32⁶ ≈ 1.07 billion codes. Collisions are handled by
the caller retrying on the DB's unique-index violation.
"""

import secrets

# No 0/O/1/I/L — each glyph is phonetically distinct.
_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
_CODE_LENGTH = 6
_PREFIX = "DRV-"


def generate_driver_code() -> str:
    """Return a fresh DRV-XXXXXX code using a crypto-strong RNG."""
    body = "".join(secrets.choice(_ALPHABET) for _ in range(_CODE_LENGTH))
    return f"{_PREFIX}{body}"
