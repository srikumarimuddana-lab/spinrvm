"""Canadian Social Insurance Number validation.

Spinr collects a driver's SIN for one reason: a T4A slip cannot be filed
without it, and Stripe will not return one (``individual.id_number`` is
write-only on Connect). Everything here exists to make sure the number we
store is the number CRA will accept, because a typo is not discovered until
filing — months later, by which time the driver may be unreachable.

**Nothing in this module logs, returns, or raises a message containing a SIN.**
Validation failures are described by what is wrong with the input, never by
echoing it. A ``ValueError`` from here can be surfaced to a client verbatim.
CLAUDE.md forbids government IDs in logs, Sentry events and analytics
payloads — that includes exception messages, which reach all three.
"""

from __future__ import annotations

import re

__all__ = ["normalize_sin", "validate_sin", "sin_last4", "SIN_LENGTH"]

SIN_LENGTH = 9

_NON_DIGIT = re.compile(r"\D+")


def normalize_sin(raw: str | None) -> str:
    """Strip formatting to bare digits. ``"123 456-789"`` → ``"123456789"``.

    Callers hand us whatever the driver typed; SINs are conventionally written
    in three groups, and phone keyboards make spaces and dashes likely. The
    example above is a formatting illustration and is not a valid SIN — this
    file deliberately contains no number that would pass its own checks.
    """
    return _NON_DIGIT.sub("", raw or "")


def _luhn_ok(digits: str) -> bool:
    """Luhn (mod-10) checksum, which every real SIN satisfies.

    This is what makes validation worth doing: it catches every single-digit
    typo and almost every transposition of adjacent digits — by far the two
    most likely ways a driver mistypes their own number.
    """
    total = 0
    # Right to left: double every second digit, and cast a 2-digit result back
    # down by subtracting 9 (equivalent to summing its digits).
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def validate_sin(raw: str | None) -> str:
    """Return the normalized 9-digit SIN, or raise ``ValueError``.

    Raises rather than returning a bool so a caller cannot accidentally store
    an unvalidated value by ignoring a falsy return.

    Deliberately NOT rejected:

    - **A leading 9.** That marks a temporary resident (work/study permit).
      Those SINs are valid, those drivers are real, and rejecting them would
      lock a lawful worker out of getting paid. Only the CRA cares that such
      a number carries an expiry.
    - **Anything about the other leading digits.** Beyond ``0``, which is
      unassigned, the first digit encodes a region and the assignment table
      changes. Enforcing it would eventually reject a real driver to catch a
      typo that Luhn already catches.
    """
    digits = normalize_sin(raw)
    if not digits:
        raise ValueError("SIN is required")
    if len(digits) != SIN_LENGTH:
        # Says the length we got, never the value.
        raise ValueError(f"SIN must be {SIN_LENGTH} digits; got {len(digits)}")
    if digits[0] == "0":
        raise ValueError("SIN cannot start with 0")
    if len(set(digits)) == 1:
        # 111111111 passes neither reasonableness nor, usually, Luhn — but
        # 000000000 does pass Luhn, so this guard is not redundant.
        raise ValueError("SIN cannot be a single repeated digit")
    if not _luhn_ok(digits):
        raise ValueError("SIN failed its checksum — check for a mistyped digit")
    return digits


def sin_last4(validated: str) -> str:
    """Last 4 digits, for display. Input must already be validated."""
    return validated[-4:]
