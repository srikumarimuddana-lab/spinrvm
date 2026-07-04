"""PIPEDA-safe redaction helpers for log/audit emission.

CLAUDE.md prohibits raw phone numbers, full emails, full names, raw GPS, and
government IDs in logs, Sentry events, audit-log payloads, or analytics.
Use these helpers at every emission site.
"""

from __future__ import annotations


def redact_phone(phone: str | None) -> str:
    """Return ``****1234`` style mask. Empty/short input returns ``****``."""
    if not phone:
        return "****"
    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) < 4:
        return "****"
    return f"****{digits[-4:]}"


def redact_email(email: str | None) -> str:
    """Return ``j***@domain.com`` style mask. Empty input returns ``****``."""
    if not email or "@" not in email:
        return "****"
    local, domain = email.split("@", 1)
    if not local:
        return f"***@{domain}"
    return f"{local[0]}***@{domain}"


# A trailing province token to drop so "Regina, SK" -> "Regina".
_PROVINCE_TOKENS = frozenset(
    {
        "ab",
        "bc",
        "mb",
        "nb",
        "nl",
        "ns",
        "nt",
        "nu",
        "on",
        "pe",
        "qc",
        "sk",
        "yt",
        "alberta",
        "british columbia",
        "manitoba",
        "new brunswick",
        "newfoundland and labrador",
        "nova scotia",
        "northwest territories",
        "nunavut",
        "ontario",
        "prince edward island",
        "quebec",
        "saskatchewan",
        "yukon",
    }
)
# Unambiguous street-suffix words. A token containing one is a street, never an
# area. Deliberately excludes place-ambiguous words (bay, green, point, ridge,
# row, grove, cove, square, …) that also occur in city names (e.g. "Thunder
# Bay") — we'd rather drop a token to None than risk surfacing a street.
_STREET_WORDS = frozenset(
    {
        "street",
        "st",
        "avenue",
        "ave",
        "drive",
        "dr",
        "lane",
        "ln",
        "road",
        "rd",
        "boulevard",
        "blvd",
        "crescent",
        "cres",
        "court",
        "ct",
        "place",
        "pl",
        "way",
        "terrace",
        "terr",
        "parkway",
        "pkwy",
        "highway",
        "hwy",
        "close",
        "alley",
        "trail",
        "wynd",
        "circle",
        "cir",
    }
)


def _is_street(token: str) -> bool:
    return any(w in _STREET_WORDS for w in token.lower().replace(".", "").split())


def area_only(address: str | None) -> str | None:
    """Coarse area (city/locality) from a full address — PIPEDA data
    minimization for anything retained or emitted outside the ride row itself
    (financial ledgers, logs, lock-screen content states).

    "1742 Main Street, Regina, SK, S4P 3A1" -> "Regina"
    "Oak Lane, Regina"                       -> "Regina"
    "Regina, SK"                             -> "Regina"

    Heuristic, biased to return None over leaking a street: split on commas,
    drop digit-bearing tokens (house numbers, postal codes), drop a trailing
    province, drop any street-named token; the last token left is the city.
    Returns None when nothing usable survives.

    Moved from utils/live_activity.py (where it was ``_area_label``) so
    payment ledgers and the Live Activity share one redaction path.
    """
    if not address:
        return None
    parts = [p.strip() for p in address.split(",") if p.strip()]
    # Digit-bearing tokens are house-numbered streets / postal codes — drop them.
    cleaned = [p for p in parts if not any(ch.isdigit() for ch in p)]
    # Drop a single trailing province token.
    if len(cleaned) >= 2 and cleaned[-1].lower() in _PROVINCE_TOKENS:
        cleaned = cleaned[:-1]
    # Drop street-named tokens so a street can never surface as the area.
    cleaned = [p for p in cleaned if not _is_street(p)]
    if not cleaned:
        return None
    return cleaned[-1]


def first_name_only(user: dict | None, fallback: str = "") -> str:
    """Driver-/contact-safe display name: the user's FIRST name only, never the
    legal surname (PIPEDA, C5).

    Full legal names must not reach driver-visible WebSocket payloads, and no
    name at all may ride in an FCM/push payload (cleartext in the device tray,
    stored in Google/US infra with no Canadian-residency guarantee). Returns
    ``fallback`` when no first name is set.
    """
    first = ((user or {}).get("first_name") or "").strip()
    return first or fallback
