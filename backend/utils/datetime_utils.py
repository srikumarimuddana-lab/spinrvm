"""Datetime helpers shared across backend modules.

The codebase had a recurring bug where ISO strings from Supabase
(trailing `Z` or `+00:00`) were parsed like::

    datetime.fromisoformat(s.replace("Z", "+00:00").replace("+00:00", ""))

The second `.replace("+00:00", "")` strips the offset, so every parse
returned a **naive** datetime. Comparing that with a
``datetime.now(timezone.utc)`` value blew up with
``TypeError: can't compare offset-naive and offset-aware datetimes`` —
observed in admin /drivers/stats, but the same pattern existed in ride,
payment, document-expiry and quest code paths.

Use ``parse_iso_utc`` going forward so every parsed value is aware and
in UTC.
"""

from datetime import datetime, timezone
from typing import Optional, Union


def parse_iso_utc(value: Union[str, datetime, None]) -> Optional[datetime]:
    """Parse an ISO-8601 string and return a UTC-aware ``datetime``.

    Accepts:
      * ``None`` → returns ``None``
      * an existing ``datetime`` (naive values are assumed UTC and tagged
        so the return is always aware)
      * a string with or without a trailing ``Z`` / ``+00:00`` / any other
        offset — the offset is preserved and converted to UTC.

    Returns ``None`` if the value can't be parsed — callers must handle
    that case rather than assume a valid datetime.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    s = str(value).strip()
    if not s:
        return None
    # Normalise `Z` to `+00:00` so fromisoformat accepts it on all
    # supported Python versions. Do NOT strip the offset afterwards.
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        # Value was naive — assume UTC (matches how Supabase returns
        # TIMESTAMPTZ columns when the tz is already folded into the
        # wall-clock time).
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
