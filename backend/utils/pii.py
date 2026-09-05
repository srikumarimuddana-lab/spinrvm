"""PIPEDA-safe redaction helpers for log/audit emission.

CLAUDE.md prohibits raw phone numbers, full emails, full names, raw GPS, and
government IDs in logs, Sentry events, audit-log payloads, or analytics.
Use these helpers at every emission site.
"""

from __future__ import annotations

import re as _re
from typing import Any

_GEOHASH_BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"

REDACTED = "[REDACTED]"

# ── Key-name redaction ──────────────────────────────────────────────
# Value patterns alone cannot protect a structured payload, and the reason is
# specific: a coordinate split across two dict keys ({"lat": 52.1332,
# "lng": -106.67}) matches no coordinate pattern in ai/pii.py, because those all
# require lat and lng adjacent in one string. Names are not regex-matchable at all.
# So for mappings we redact on the KEY — the one place a name-denylist is the right
# tool, because we control none of the values and the key is the only reliable
# signal about what a value means.
#
# This lives here rather than in utils/sentry_scrub.py (where it was written for
# T1c) because there are now three consumers — the Sentry before_send hook, the
# loguru sink guard in utils/log_guard.py, and any future emission site — and
# "what counts as PII" must have exactly one definition. Sentry re-exports these
# under its old private names for continuity with its tests.
SENSITIVE_KEY_RE = _re.compile(
    r"(lat|lng|lon|longitude|latitude|coord"
    r"|phone|mobile|tel"
    r"|email|e_mail"
    r"|(?:^|_)name$|full_name|first_name|last_name|display_name"
    r"|address|street|postal|zip"
    r"|sin|licence|license|passport|dob|birth"
    r"|vin|plate"
    r"|password|passwd|secret|token|api_key|apikey|authorization|auth|cookie|session"
    r"|card|pan|cvv|cvc|iban|account_number)",
    _re.IGNORECASE,
)

# Keys that look sensitive by substring but are the IDs that make an event
# triageable at all. CLAUDE.md lists these as ID-only and explicitly permitted.
# Over-redaction is its own failure mode: a log line with every field redacted is
# as useless as no log line.
KEY_ALLOWLIST = frozenset(
    {
        "name",  # a bare 'name' on a frame/module dict is a symbol, not a person
        "filename",
        "module_name",
        "function_name",
        "event_id",
        "request_id",
        "ride_id",
        "driver_id",
        "rider_id",
        "user_id",
        "transaction_name",
        # Observability context — never PII, and needed to correlate anything.
        "domain",
        "surface",
        "trace_id",
        "span_id",
        "correlation_id",
        "idempotency_key",  # a derived key, not a credential
    }
)


def is_sensitive_key(key: Any) -> bool:
    """True when a mapping key names a value that must not be emitted."""
    if not isinstance(key, str):
        return False
    if key.lower() in KEY_ALLOWLIST:
        return False
    return bool(SENSITIVE_KEY_RE.search(key))


def geohash(lat: float | None, lng: float | None, precision: int = 5) -> str:
    """Encode a coordinate as a short geohash for logs (PIPEDA: never emit raw
    lat/lng; "log geohashed area at most" — CLAUDE.md).

    precision=5 ≈ a 4.9km × 4.9km cell — enough to correlate a rough area for
    debugging (which service area, roughly where a geofence tripped) without
    pinpointing a rider/driver. Returns ``"?"`` for missing/invalid input.
    """
    try:
        latf = float(lat)  # type: ignore[arg-type]
        lngf = float(lng)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "?"
    if not (-90.0 <= latf <= 90.0 and -180.0 <= lngf <= 180.0):
        return "?"
    lat_range = [-90.0, 90.0]
    lng_range = [-180.0, 180.0]
    out: list[str] = []
    bit = 0
    ch = 0
    even = True
    while len(out) < precision:
        if even:
            mid = (lng_range[0] + lng_range[1]) / 2
            if lngf >= mid:
                ch = (ch << 1) | 1
                lng_range[0] = mid
            else:
                ch = ch << 1
                lng_range[1] = mid
        else:
            mid = (lat_range[0] + lat_range[1]) / 2
            if latf >= mid:
                ch = (ch << 1) | 1
                lat_range[0] = mid
            else:
                ch = ch << 1
                lat_range[1] = mid
        even = not even
        bit += 1
        if bit == 5:
            out.append(_GEOHASH_BASE32[ch])
            bit = 0
            ch = 0
    return "".join(out)


def coarsen_coord(
    lat: float | None,
    lng: float | None,
    cell_m: int = 500,
) -> tuple[float, float] | None:
    """Snap a coordinate to a fixed grid, for coordinates that must be *displayed*
    rather than logged.

    ``geohash()`` is the right tool for a log line, but a rider map needs numbers
    back, so this snaps to the centre of a ``cell_m``-sized cell instead. Two
    properties matter and both are deliberate:

    * **Deterministic, not jittered.** The same input always yields the same
      output, so a driver's marker does not shimmer between polls. Random jitter
      would look like movement, and — worse — repeated sampling of a jittered
      position averages back to the true one, so it leaks what it appears to
      protect.
    * **Grid-snapped, not rounded per-axis.** Longitude degrees shrink with
      latitude, so a fixed decimal rounding gives a cell that is ~500m tall and
      ~300m wide at Saskatoon's latitude. Scaling the longitude step by
      ``cos(lat)`` keeps the cell roughly square in metres.

    Returns ``None`` for missing/invalid input, and for the literal ``(0, 0)``
    registration default, which means "no GPS yet" rather than a location in the
    Gulf of Guinea — callers already skip it and must keep doing so.
    """
    import math

    try:
        latf = float(lat)  # type: ignore[arg-type]
        lngf = float(lng)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not (-90.0 <= latf <= 90.0 and -180.0 <= lngf <= 180.0):
        return None
    if latf == 0.0 and lngf == 0.0:
        return None
    if cell_m <= 0:
        # Explicit opt-out (exact coordinates) — callers gate this on an
        # authorization check, never on a client-supplied value.
        return (latf, lngf)

    lat_step = cell_m / 111_320.0

    # Snap to cell centre rather than corner, so the displayed point is never
    # further from the truth than half a cell diagonal.
    snapped_lat = (math.floor(latf / lat_step) + 0.5) * lat_step

    # Scale the longitude step by cos(SNAPPED latitude), not the raw one. Deriving
    # it from the raw latitude makes lng_step vary continuously as the driver moves
    # north, so the snapped longitude drifts even when the true longitude is
    # unchanged — the output stops being a grid, and worse, the longitude then
    # encodes fine-grained latitude, leaking back the precision this removes.
    # Quantising the latitude first makes lng_step constant within a band.
    cos_lat = math.cos(math.radians(snapped_lat))
    # Guard the pole: cos(lat) → 0 would blow up the longitude step.
    lng_step = cell_m / (111_320.0 * cos_lat) if abs(cos_lat) > 1e-6 else 360.0

    if lng_step >= 360.0:
        # Near the poles one cell spans every meridian, so longitude carries no
        # information. Report the meridian itself rather than an arbitrary
        # multiple of a >360° step.
        snapped_lng = 0.0
    else:
        snapped_lng = (math.floor(lngf / lng_step) + 0.5) * lng_step
        # Snapping to a cell *centre* can push the value past the antimeridian
        # when the cell is very wide. Wrap into [-180, 180) so the client never
        # receives a coordinate its map cannot plot.
        snapped_lng = ((snapped_lng + 180.0) % 360.0) - 180.0

    # Latitude cannot wrap — clamp instead, so a driver at the pole does not come
    # back as 90.002.
    snapped_lat = max(-90.0, min(90.0, snapped_lat))

    # Round for a stable, compact wire representation. 5 dp ≈ 1.1m, well below
    # any cell size we would use, so this does not undo the coarsening.
    return (round(snapped_lat, 5), round(snapped_lng, 5))


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


# A trailing country token to drop so "Saskatoon, SK, Canada" -> city, not
# "Canada" (geocoder formatted_address strings usually end with the country).
_COUNTRY_TOKENS = frozenset({"canada", "united states", "usa"})

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
    words = token.lower().replace(".", "").split()
    # A leading "St."/"Ste." followed by more words is a Saint-prefixed CITY
    # ("St. Albert", "Ste. Anne"), not a street suffix — don't let the "st"
    # abbreviation erase real municipalities. "Main St" still matches below.
    if len(words) > 1 and words[0] in ("st", "ste"):
        words = words[1:]
    return any(w in _STREET_WORDS for w in words)


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
    # Drop a single trailing country token, then a single trailing province
    # token — geocoder output is ".., <city>, <province>, <country>".
    if len(cleaned) >= 2 and cleaned[-1].lower() in _COUNTRY_TOKENS:
        cleaned = cleaned[:-1]
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


# ── 4xx error-detail redaction (WS-E) ───────────────────────────────
# `utils/error_handling.py` sanitises 5xx details wholesale, because a 5xx
# message is never useful to a client. 4xx is different: those strings ARE the
# user-facing UX ("Invalid phone number", "Card declined"), so they cannot be
# replaced — but ~30 routes build them with `detail=str(e)` or an f-string
# interpolating an exception, and those exceptions carry things the client must
# never see. `routes/admin/legacy_sin_dob_backfill.py` is the worst case: its
# import service raises with the offending CSV row, so a SIN or date of birth
# lands in the 400 body, and from there in browser history, Vercel logs and
# Sentry breadcrumbs. PIPEDA does not care that only an admin saw it, and two
# driver-facing sites (appeals, tax_exports) leak upstream text to a contractor.
#
# So this redacts the dangerous *content* out of a 4xx detail while leaving the
# sentence readable. Order matters: email before the generic digit run, or the
# digits inside an address get eaten first.
#
# This is a backstop, not a licence — the right fix at any individual site is
# still to raise a vetted message instead of `str(e)`, and
# `tests/test_no_raw_exception_in_4xx_detail.py` fails the build on new ones.

_ERR_EMAIL_RE = _re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
# Stripe object ids and secrets: pi_/ch_/cus_/sub_/in_/pm_/seti_/txn_/evt_/dp_,
# plus sk_/rk_/pk_ keys. Matches the Stripe-documented shape (prefix + _ + at
# least 8 base62 chars) so ordinary words are not swallowed.
_ERR_STRIPE_RE = _re.compile(
    r"\b(?:pi|ch|cus|sub|in|pm|seti|txn|evt|dp|re|po|acct|price|prod|cs)_[A-Za-z0-9]{8,}\b"
    r"|\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{8,}\b"
)
# JWTs and long opaque bearer tokens.
_ERR_JWT_RE = _re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+\b")
# UUIDs — ride/driver/user ids are safe to log server-side but a raw id in a
# client-facing string is still an identifier we did not choose to disclose.
_ERR_UUID_RE = _re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
# 7+ consecutive digits, optionally separated by space/dash: SIN (9), card PAN
# (13-19), phone (10-11), driver's licence. Deliberately broad — a 4xx message
# has no legitimate reason to carry a long digit run.
_ERR_LONG_DIGITS_RE = _re.compile(r"\b(?:\d[ -]?){7,}\d\b")
# ISO-ish dates: a date of birth in an import-error message.
_ERR_DATE_RE = _re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
# Postgres / PostgREST internals: SQLSTATE codes, PGRST codes, and the
# constraint/relation names that come with them.
_ERR_DB_INTERNALS_RE = _re.compile(
    r"\bPGRST\d{3}\b"
    r"|\bSQLSTATE\s*\w+\b"
    r"|\b23\d{3}\b"
    r"|\b(?:duplicate key value violates unique constraint|violates foreign key constraint|"
    r"null value in column|relation)\s+\"[^\"]+\""
    r"|\bconstraint\s+\"[^\"]+\"",
    _re.IGNORECASE,
)

_ERR_MAX_DETAIL_LEN = 300


def redact_error_detail(detail: Any) -> Any:
    """Scrub client-visible identifiers out of a 4xx ``HTTPException`` detail.

    Returns non-string details untouched (FastAPI allows dicts/lists, and those
    are structured payloads a route built deliberately). Strings come back with
    emails, phone/SIN/PAN-shaped digit runs, dates, UUIDs, Stripe ids, JWTs and
    Postgres internals replaced by ``[redacted]``, and are truncated to
    ``_ERR_MAX_DETAIL_LEN`` — a 4xx message longer than that is a stack trace or
    a dumped row, not UX copy.

    Idempotent: running it on already-redacted text changes nothing.
    """
    if not isinstance(detail, str) or not detail:
        return detail

    out = detail
    # Email first: its local part can contain digit runs and dots that the
    # digit/date patterns would otherwise consume, leaving the domain exposed.
    out = _ERR_EMAIL_RE.sub("[redacted]", out)
    out = _ERR_JWT_RE.sub("[redacted]", out)
    out = _ERR_STRIPE_RE.sub("[redacted]", out)
    out = _ERR_UUID_RE.sub("[redacted]", out)
    out = _ERR_DB_INTERNALS_RE.sub("[redacted]", out)
    out = _ERR_DATE_RE.sub("[redacted]", out)
    out = _ERR_LONG_DIGITS_RE.sub("[redacted]", out)

    if len(out) > _ERR_MAX_DETAIL_LEN:
        out = out[:_ERR_MAX_DETAIL_LEN].rstrip() + "…"
    return out
