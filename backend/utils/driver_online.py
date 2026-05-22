"""
Effective driver online state — Uber/Lyft-style composition.

Source of truth split:
  * **Intent** — durable, in the ``drivers`` row. ``went_online_at`` and
    ``went_offline_at`` (migration 97). The driver tapping Go Online /
    Go Offline (or an admin force-off) is the only writer. Survives
    restarts, Redis loss, and analytics queries.
  * **Reachability** — transient, in Redis. ``spinr:presence:driver:<id>``
    refreshed by every WS heartbeat, every location batch, and every
    explicit status flip.

``effective_online`` composes the two at read time:

    intent_online(driver) AND driver.id in present_ids

Nothing on the hot path writes a derived "online" flag back to Postgres.
That dual-source-of-truth pattern is what the retired presence_sweeper
got wrong — when Redis briefly fell back to per-replica in-process state,
the sweeper concluded every driver was a ghost and mass-flipped them
offline. Composing at read time means a Redis hiccup degrades visibility,
not data integrity.

Use this module everywhere a reader currently asks "is this driver
online?" Do NOT add a setter — there is no such concept.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional, Set


def _parse_ts(value: Any) -> Optional[datetime]:
    """Parse a Supabase/PostgREST timestamp into a tz-aware datetime.

    Returns ``None`` for missing/empty values and silently for malformed
    values so callers can default cleanly. Callers must not rely on
    parser errors — the intent_online() fall-through handles that.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def intent_online(driver: Mapping[str, Any]) -> bool:
    """True iff the driver's last toggle was Go Online.

    Composition rules:
      * ``went_online_at`` set, ``went_offline_at`` NULL → online
      * ``went_offline_at`` set, ``went_online_at`` NULL → offline
      * both set → whichever is more recent wins
      * neither set → never toggled → fall back to legacy ``is_online``
        column so freshly-migrated rows behave the same as before the
        backfill lands.
    """
    on_ts = _parse_ts(driver.get("went_online_at"))
    off_ts = _parse_ts(driver.get("went_offline_at"))
    if on_ts is None and off_ts is None:
        return bool(driver.get("is_online"))
    if on_ts is None:
        return False
    if off_ts is None:
        return True
    return on_ts > off_ts


def effective_online(driver: Mapping[str, Any], present_ids: Set[str]) -> bool:
    """True iff driver intends online AND is currently reachable.

    ``present_ids`` is the result of a single ``present_driver_ids()``
    call covering all candidates — never call presence per-driver in a
    loop; the batched MGET is the whole point of the helper.
    """
    if not intent_online(driver):
        return False
    driver_id = driver.get("id")
    return bool(driver_id) and driver_id in present_ids


def effective_available(
    driver: Mapping[str, Any],
    present_ids: Set[str],
    *,
    on_active_ride: bool,
) -> bool:
    """True iff driver can take a new ride right now.

    ``on_active_ride`` is computed once per request from the rides
    table — pass the boolean rather than re-querying inside this helper.
    Matches the legacy ``is_available`` semantics: online and reachable
    and not currently transporting / committed to a passenger.
    """
    return effective_online(driver, present_ids) and not on_active_ride


def filter_effective_online(
    drivers: Iterable[Mapping[str, Any]],
    present_ids: Set[str],
) -> list:
    """Convenience filter for readers that want only effective-online rows."""
    return [d for d in drivers if effective_online(d, present_ids)]


__all__ = [
    "effective_available",
    "effective_online",
    "filter_effective_online",
    "intent_online",
]
