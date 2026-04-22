"""Human-readable short codes for ride rows.

rides.id stays a UUID (foreign keys, WS channel keys, etc. depend on
it), but operators and riders need something they can quote over the
phone or paste into a support ticket. This module generates
``SPR-XXXXXX`` codes with a 32-character Crockford-ish alphabet that
omits 0/O/1/I/L so the code can't be misread aloud.

The alphabet has 32 symbols; 6 characters → 32⁶ ≈ 1.07 billion codes.
Collisions are handled by the caller retrying on the DB's
unique-index violation.
"""

import secrets

# No 0/O/1/I/L — each glyph is phonetically distinct.
_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
_CODE_LENGTH = 6
_PREFIX = "SPR-"


def generate_ride_code() -> str:
    """Return a fresh SPR-XXXXXX code using a crypto-strong RNG."""
    body = "".join(secrets.choice(_ALPHABET) for _ in range(_CODE_LENGTH))
    return f"{_PREFIX}{body}"
