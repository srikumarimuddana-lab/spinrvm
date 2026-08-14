"""Service-area scoping for driver dispatch eligibility.

A driver is approved to operate in one service area (``drivers.service_area_id``).
Approval is a real-world process — municipal licensing, SGI ride-share
endorsement, background check filed with the right regulator — so a driver
approved for Saskatoon must not receive a Regina ride offer merely because
they happen to be parked within the dispatch search radius that day.

Geographic proximity is NOT a substitute for this check: two Saskatchewan
service areas can sit close enough that a single 10 km search box spans both,
and nothing about being nearby makes a driver authorised.

This module owns the one definition of "which drivers may serve this ride",
shared by the live dispatch path (``routes/rides/matching.py``) and the
service layer (``services/dispatch_service.py``) so the two cannot drift.

Two knobs, both read from ``app_settings`` (DB-backed, so they flip without a
redeploy — see ``schemas.AppSettings``):

``enforce_driver_service_area``
    Master switch. Off ⇒ this module is bypassed entirely and dispatch behaves
    exactly as it did before the guard existed. This is the rollback path.

``service_area_allow_unassigned_drivers``
    Whether a driver with a NULL/empty ``service_area_id`` is dispatchable.
    Defaults to **True**, and that default is load-bearing: the column is
    documented "assigned area (optional)", has no DB default, and no migration
    backfills it. Drivers created through the in-app signup path
    (``routes/drivers/profile.py`` auto-create) and the admin approve path
    (``routes/admin/drivers.py`` — sets it only when supplied) can legitimately
    have NULL today. Excluding NULL before a backfill would drop the whole
    unassigned cohort out of dispatch and strand rides fleet-wide, which is a
    far worse failure than the cross-area offer this guard exists to stop.
    Flip it to False only once every active driver row has an area.
"""

import logging
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

#: Row cap on the one ``service_areas`` read this module performs. A
#: Saskatchewan-scale deployment is nowhere near this; the cap only bounds the
#: read. Hitting it is logged and reported via the ``complete`` flag so a
#: truncated tree can never silently mark a real area incompatible.
MAX_TREE_AREAS = 200


async def resolve_compatible_area_ids(db, area_id: str) -> Tuple[Set[str], bool]:
    """Every service area whose drivers may serve a ride in ``area_id``.

    Resolves the *whole tree* the area belongs to: find the root by following
    ``parent_service_area_id`` upward, then take every descendant of that root.
    That makes the relation symmetric and transitive, which a naive
    "self + parent + direct children" version is not:

      - a ``regina`` driver serves ``regina_airport`` rides (parent → child)
      - a ``regina_airport`` driver serves ``regina`` rides (child → parent)
      - a ``regina_airport`` driver serves ``regina_downtown`` rides — both sit
        inside Regina, so a driver licensed for one is licensed for the other.
        A direct-children-only version wrongly excludes this.

    Costs exactly **one** query: the id/parent pairs for every area are fetched
    once and the tree is computed in memory. Dispatch is a hot path with a
    P95 < 2 s SLA that also re-runs on every retry, so a per-level BFS query
    (or a query per candidate) does not belong here. ``service_areas`` is a
    small, slow-changing table — ``routes/rides/booking.py`` and
    ``estimates.py`` already read all of it per request.

    Returns ``(area_ids, complete)``. ``complete`` is False when the read failed
    or the row cap truncated the table, meaning the set may be missing areas;
    callers must then fail *safe* — keep the ride's own area, neither widening
    to the whole province nor narrowing to nothing.

    Inactive areas are included deliberately: a driver assigned to an area an
    admin just deactivated is still an approved driver for that city, and
    dropping them mid-shift would knock them offline with no warning. Whether a
    *ride* may originate in an inactive area is a separate decision, already
    made upstream at booking.
    """
    if not area_id:
        return set(), True

    try:
        rows = await db.get_rows(
            "service_areas",
            {},
            columns="id,parent_service_area_id",
            limit=MAX_TREE_AREAS,
        )
    except Exception:
        logger.error(
            "Service-area tree read failed for area=%s — scope resolution incomplete",
            area_id,
            exc_info=True,
        )
        return {area_id}, False

    rows = rows or []
    complete = True
    if len(rows) >= MAX_TREE_AREAS:
        # Truncated: an area beyond the cap would be silently treated as
        # incompatible. Never silent — see CLAUDE.md "no silent caps".
        logger.error(
            "service_areas read hit the %d-row cap while scoping area=%s — scope resolution "
            "truncated (some areas omitted)",
            MAX_TREE_AREAS,
            area_id,
        )
        complete = False

    parent_of: Dict[str, Optional[str]] = {r["id"]: r.get("parent_service_area_id") for r in rows if r.get("id")}
    if area_id not in parent_of:
        # The ride's own area is missing from the table (deleted, or dropped by
        # the cap). Cannot resolve a tree; fail safe to just this area.
        logger.warning(
            "Service area %s not present in service_areas — scoping to itself only",
            area_id,
        )
        return {area_id}, False

    # ── Root: follow parents in memory, tolerating a cyclic pointer ────
    root = area_id
    seen_up: Set[str] = {area_id}
    while True:
        parent = parent_of.get(root)
        if not parent or parent not in parent_of:
            break
        if parent in seen_up:
            logger.error(
                "Service-area tree has a parent cycle at area=%s (parent=%s) — scope resolution truncated",
                root,
                parent,
            )
            complete = False
            break
        seen_up.add(parent)
        root = parent

    # ── Descendants of the root ───────────────────────────────────────
    children_of: Dict[str, List[str]] = {}
    for child, parent in parent_of.items():
        if parent:
            children_of.setdefault(parent, []).append(child)

    # One BFS down from the root reaches the entire tree — every ancestor in
    # `seen_up` and every sibling branch included, because `root` is by
    # construction the topmost ancestor of `area_id`.
    collected: Set[str] = set()
    frontier: List[str] = [root]
    while frontier:
        current = frontier.pop()
        if current in collected:
            continue
        collected.add(current)
        frontier.extend(children_of.get(current, ()))

    # A cycle break above can leave `root` mid-chain, so the ancestors walked
    # and the ride's own area are unioned in rather than assumed reachable.
    collected |= seen_up
    return collected, complete


def build_driver_area_filter(area_ids: Set[str], *, allow_unassigned: bool) -> Dict[str, Any]:
    """Filter fragment restricting a ``drivers`` query to ``area_ids``.

    ``allow_unassigned=True`` emits an ``$or`` that also matches NULL, because
    SQL ``IN`` never matches NULL: a bare ``{"$in": [...]}`` would silently drop
    every driver whose area was never assigned. See the module docstring for
    why that is the default.

    Merge the result into the query filter with ``dict.update`` — it returns a
    single-key dict so it composes with the caller's other predicates. Note it
    claims the ``$or`` key when ``allow_unassigned`` is set; a caller that
    already uses ``$or`` must combine them itself rather than overwriting.
    """
    ordered = sorted(area_ids)  # deterministic — keeps queries/logs comparable
    if allow_unassigned:
        return {"$or": [{"service_area_id": {"$in": ordered}}, {"service_area_id": None}]}
    return {"service_area_id": {"$in": ordered}}


def driver_area_allowed(
    driver_area_id: Optional[str],
    area_ids: Optional[Set[str]],
    *,
    allow_unassigned: bool,
) -> bool:
    """Whether one driver row passes the area guard.

    The in-Python twin of :func:`build_driver_area_filter`, so the guard is
    enforced twice — once in SQL to keep the candidate fetch small, once here
    so a pool assembled by any other route (a future RPC, a cached list, a
    hand-built test fixture) is still gated. ``is_wav`` is double-checked the
    same way, for the same reason.

    ``area_ids=None`` means "guard disabled" and returns True for everything.
    """
    if area_ids is None:
        return True
    if not driver_area_id:  # None or ""
        return allow_unassigned
    return driver_area_id in area_ids


async def resolve_dispatch_area_scope(
    db,
    area_id: Optional[str],
    app_settings: Dict[str, Any],
) -> Tuple[Optional[Set[str]], bool]:
    """Resolve the area guard for one dispatch attempt.

    Returns ``(allowed_area_ids, allow_unassigned)`` where ``allowed_area_ids``
    is None when the guard does not apply — the master flag is off, or the ride
    has no ``service_area_id`` (nothing to scope against).

    On an incomplete tree walk this narrows to the ride's own area rather than
    disabling the guard: a partial tree must not silently authorise the whole
    province. The accept-time gate in ``routes/drivers/ride_flow.py`` applies
    the identical rule, so a driver wrongly offered a ride during a DB fault
    still cannot complete the accept.
    """
    if not area_id:
        return None, True
    if not bool(app_settings.get("enforce_driver_service_area", True)):
        return None, True

    allow_unassigned = bool(app_settings.get("service_area_allow_unassigned_drivers", True))
    area_ids, complete = await resolve_compatible_area_ids(db, area_id)
    if not complete:
        logger.warning(
            "Service-area scope for area=%s is incomplete — narrowing to the ride's own area "
            "(cross-area drivers stay blocked; same-area dispatch continues)",
            area_id,
        )
        area_ids = {area_id} | (area_ids or set())
    return area_ids, allow_unassigned
