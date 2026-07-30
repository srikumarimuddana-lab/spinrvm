"""PIPEDA-safe redaction helpers for log/audit emission.

CLAUDE.md prohibits raw phone numbers, full emails, full names, raw GPS, and
government IDs in logs, Sentry events, audit-log payloads, or analytics.
Use these helpers at every emission site.
"""

from __future__ import annotations

_GEOHASH_BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"


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
