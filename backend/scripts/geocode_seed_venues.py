#!/usr/bin/env python3
"""Geocode pickup venues, discover missing brand locations, and gate activation.

The Saskatoon venue seed (migrations 307/308/309) is taken **dark** by migration
310 — which sets ``is_active = false`` on all 38 rows — because its coordinates
are not survey-grade. Venue centers were taken from public geo databases where
one existed and estimated from a street address otherwise, and every pickup
point was hand-authored as a small offset from the venue center.
``/maps/pickup-points`` sends the driver to the pickup point's coordinate, so an
invented entrance sends the driver to the wrong door. This script is what makes
those rows safe to turn on.

(307/308/309 themselves insert ``is_active = true``. They merged that way, and
the runner keys ``schema_migrations`` on the full filename, so editing an applied
migration never re-runs it — the deactivation had to be a new migration. If 310
has not been applied to the database this script is pointed at, those venues are
live on unverified coordinates; ``--report`` will show them as active.)

    # 1. Report drift between each stored center and its geocoded location.
    #    Reads only — no writes, no activation.
    python backend/scripts/geocode_seed_venues.py

    # 2. Correct the centers and replace fabricated entrance lists with a
    #    single geocoded main entrance. Venues stay inactive.
    python backend/scripts/geocode_seed_venues.py --apply

    # 3. Find every location of the brands in BRANDS that is not already a
    #    venue — this is how coverage gets complete, rather than by hand-
    #    listing whichever branches a web search happened to surface.
    python backend/scripts/geocode_seed_venues.py --discover
    python backend/scripts/geocode_seed_venues.py --discover --apply

    # 4. Turn a venue on, once a human has checked it on the map.
    python backend/scripts/geocode_seed_venues.py --activate "Market Mall (Saskatoon)"

Dry run is the default and activation is never implicit: a venue only goes live
through an explicit ``--activate``, one name at a time.

Environment — the same variables the backend reads, so a normal backend
``.env`` is enough:

    SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY

The Maps key is NOT read from the environment. It lives in ``app_settings``
(see CLAUDE.md, "Settings in DB"), so pointing this at a database points it at
the matching Google project — including that project's billing.

Cost: one Places Text Search per venue for ``--apply``, and one per brand for
``--discover``. A full pass over the current seed is well under 100 calls, but
it is real Places spend and is not routed through ``utils.maps_budget`` (that
circuit breaker exists to protect rider-facing traffic from a runaway loop, and
tripping it from an operator script would mask a genuine rider outage).

What this script deliberately does NOT do:
  * It never invents a second entrance. Places returns one coordinate per
    place, so ``--apply`` writes exactly one "Main entrance" point. Real named
    doors ("Arrivals curb", "Gate 4") are local knowledge and must be added by
    an admin in Dashboard → Pickup Venues.
  * It never activates a venue whose detection circle overlaps an already
    active one. ``/maps/pickup-points`` resolves by *nearest center* among all
    radius matches, so an overlap silently hands the rider the wrong venue's
    door list — the failure that kept a rider outside The Rook & Raven being
    offered the Delta Bessborough's doors.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import math
import os
import sys

# Import as the backend package does, so the dual-import modules resolve.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("geocode_seed_venues")

# Saskatoon envelope. Doubles as the Places Text Search locationRestriction
# (a hard filter, not a soft bias — see build_text_search_payload) and as the
# sanity gate on anything written back, so a "Market Mall" in another province
# can neither be returned nor stored.
SASKATOON_LAT = 52.13
SASKATOON_LNG = -106.67
SASKATOON_RADIUS_M = 20_000

# Drift beyond this between the stored center and the geocoded location is
# reported as a hard warning rather than a routine correction — it usually
# means the row is mislabelled rather than merely imprecise, the way
# Saskatchewan Polytechnic sat ~1.9 km from its own street address.
SUSPICIOUS_DRIFT_M = 1_000

# Brands whose every Saskatoon location should be a venue. Hand-listing
# branches is what left coverage arbitrary in the first place; --discover
# enumerates them instead.
BRANDS = [
    "Walmart",
    "Real Canadian Superstore",
    "Costco Wholesale",
    "Giant Tiger",
    "Sobeys",
    "FreshCo",
    "No Frills",
    "Save-On-Foods",
    "Saskatoon Co-op Food Store",
    "Canadian Tire",
    "Home Depot",
    "Rona",
    "Winners",
    "HomeSense",
    "Marshalls",
    "Shoppers Drug Mart",
]

# A discovered location closer than this to an existing venue center is treated
# as already covered rather than inserted as a duplicate.
DUPLICATE_RADIUS_M = 250

# Detection radius given to a newly discovered venue. Deliberately tight: a
# small circle that misses is a rider falling back to their own pin, while an
# oversized one swallows its neighbours and returns the wrong door list.
DISCOVERED_RADIUS_M = 150


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in metres. Mirrors maps_proxy._haversine_m so the
    overlap maths here matches what the endpoint actually does at request time."""
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def in_saskatoon(lat: float, lng: float) -> bool:
    return haversine_m(lat, lng, SASKATOON_LAT, SASKATOON_LNG) <= SASKATOON_RADIUS_M


async def _resolve_maps_key() -> str:
    from settings_loader import get_app_settings  # type: ignore

    settings = await get_app_settings()
    key = (settings or {}).get("google_maps_api_key") or ""
    key = key.strip()
    if not key:
        # Surfaced loudly rather than degraded: a silent skip here would leave
        # the fabricated coordinates in place and look like a clean run.
        raise RuntimeError(
            "app_settings.google_maps_api_key is empty. Set it in Dashboard → Settings "
            "before running; this script cannot verify coordinates without it."
        )
    return key


async def _text_search(client, api_key: str, query: str) -> list[dict]:
    """One Places API (New) Text Search, restricted to the Saskatoon envelope."""
    from utils.google_places_new import (  # type: ignore
        PLACES_NEW_TEXT_SEARCH_FIELD_MASK,
        PLACES_NEW_TEXT_SEARCH_URL,
        build_text_search_payload,
        legacy_place_results_from_text_search,
    )

    payload = build_text_search_payload(query, SASKATOON_LAT, SASKATOON_LNG, SASKATOON_RADIUS_M)
    headers = _places_headers(api_key, PLACES_NEW_TEXT_SEARCH_FIELD_MASK)
    resp = await client.post(PLACES_NEW_TEXT_SEARCH_URL, json=payload, headers=headers, timeout=20.0)
    if resp.status_code != 200:
        # Do not swallow — a 4xx here is usually a bad/unbilled key, and
        # continuing would report "no drift" for every venue.
        raise RuntimeError(f"Places text search failed ({resp.status_code}) for {query!r}: {resp.text[:300]}")
    return legacy_place_results_from_text_search(resp.json())


def _places_headers(api_key: str, field_mask: str) -> dict:
    from utils.google_places_new import places_new_headers  # type: ignore

    return places_new_headers(api_key, field_mask)


def _result_latlng(result: dict) -> tuple[float, float] | None:
    loc = ((result or {}).get("geometry") or {}).get("location") or {}
    lat, lng = loc.get("lat"), loc.get("lng")
    if lat is None or lng is None:
        return None
    return float(lat), float(lng)


async def _load_seeded_venues() -> list[dict]:
    import db_supabase  # type: ignore

    rows = await db_supabase.get_rows("venues", {}, limit=2000)
    return rows or []


def _overlaps(a: dict, b: dict) -> float | None:
    """Return centre separation when a and b's detection circles overlap, else None."""
    try:
        d = haversine_m(
            float(a["center_lat"]),
            float(a["center_lng"]),
            float(b["center_lat"]),
            float(b["center_lng"]),
        )
    except (TypeError, ValueError, KeyError):
        return None
    if d < float(a.get("radius_m") or 150) + float(b.get("radius_m") or 150):
        return d
    return None


async def cmd_geocode(apply_changes: bool) -> int:
    """Correct stored centers against Places, and replace fabricated points."""
    import httpx

    import db_supabase  # type: ignore

    venues = await _load_seeded_venues()
    if not venues:
        logger.info("No venues found. Nothing to do.")
        return 0

    api_key = await _resolve_maps_key()
    corrected = suspicious = unresolved = 0

    async with httpx.AsyncClient() as client:
        for v in venues:
            name = v.get("name") or ""
            try:
                results = await _text_search(client, api_key, f"{name}, Saskatoon SK")
            except RuntimeError as e:
                logger.error("%s — lookup failed: %s", name, e)
                unresolved += 1
                continue

            hit = next((r for r in results if _result_latlng(r)), None)
            if not hit:
                logger.warning("%s — no Places match inside the Saskatoon envelope; left as-is", name)
                unresolved += 1
                continue

            new_lat, new_lng = _result_latlng(hit)  # type: ignore[misc]
            if not in_saskatoon(new_lat, new_lng):
                logger.warning("%s — Places returned a point outside Saskatoon; ignored", name)
                unresolved += 1
                continue

            old_lat, old_lng = float(v["center_lat"]), float(v["center_lng"])
            drift = haversine_m(old_lat, old_lng, new_lat, new_lng)
            level = logger.warning if drift >= SUSPICIOUS_DRIFT_M else logger.info
            if drift >= SUSPICIOUS_DRIFT_M:
                suspicious += 1
            level(
                "%s — stored %.5f,%.5f → geocoded %.5f,%.5f (drift %.0fm)%s",
                name,
                old_lat,
                old_lng,
                new_lat,
                new_lng,
                drift,
                "  ** check this row is not mislabelled **" if drift >= SUSPICIOUS_DRIFT_M else "",
            )

            if not apply_changes:
                continue

            # One point, at the geocoded location. Places gives a single
            # coordinate per place; inventing siblings around it is exactly
            # what made the original seed untrustworthy.
            points = [{"name": "Main entrance", "lat": round(new_lat, 6), "lng": round(new_lng, 6)}]
            await db_supabase.update_one(
                "venues",
                {"id": v["id"]},
                {
                    "center_lat": round(new_lat, 6),
                    "center_lng": round(new_lng, 6),
                    "pickup_points": points,
                    # Stays dark. Activation is a separate, explicit decision.
                    "is_active": False,
                },
            )
            corrected += 1

    logger.info(
        "%s: %d venue(s) checked, %d corrected, %d with suspicious drift, %d unresolved",
        "applied" if apply_changes else "dry run",
        len(venues),
        corrected,
        suspicious,
        unresolved,
    )
    if not apply_changes:
        logger.info("Re-run with --apply to write these corrections.")
    return 0


async def cmd_discover(apply_changes: bool) -> int:
    """Enumerate every Saskatoon location of each brand and add the missing ones."""
    import httpx

    import db_supabase  # type: ignore

    existing = await _load_seeded_venues()
    api_key = await _resolve_maps_key()
    found = added = 0

    async with httpx.AsyncClient() as client:
        for brand in BRANDS:
            try:
                results = await _text_search(client, api_key, f"{brand} in Saskatoon SK")
            except RuntimeError as e:
                logger.error("%s — discovery failed: %s", brand, e)
                continue

            for r in results:
                ll = _result_latlng(r)
                if not ll or not in_saskatoon(*ll):
                    continue
                lat, lng = ll
                found += 1

                near = next(
                    (
                        e
                        for e in existing
                        if e.get("center_lat") is not None
                        and haversine_m(lat, lng, float(e["center_lat"]), float(e["center_lng"])) <= DUPLICATE_RADIUS_M
                    ),
                    None,
                )
                if near:
                    logger.debug("%s @ %.5f,%.5f already covered by %r", brand, lat, lng, near.get("name"))
                    continue

                addr = r.get("formatted_address") or ""
                # Street-qualified so two branches of one brand stay tellable
                # apart in the admin list and in the rider's pickup label.
                street = addr.split(",")[0].strip() if addr else f"{lat:.4f},{lng:.4f}"
                name = f"{brand} ({street})"
                logger.info("NEW  %s — %s", name, addr or "no address")

                if not apply_changes:
                    continue

                row = {
                    "name": name,
                    "center_lat": round(lat, 6),
                    "center_lng": round(lng, 6),
                    "radius_m": DISCOVERED_RADIUS_M,
                    "pickup_points": [{"name": "Main entrance", "lat": round(lat, 6), "lng": round(lng, 6)}],
                    "is_active": False,
                }
                await db_supabase.insert_one("venues", row)
                existing.append(row)  # so later brands dedupe against it too
                added += 1

    logger.info(
        "%s: %d brand location(s) seen, %d new venue(s)%s",
        "applied" if apply_changes else "dry run",
        found,
        added if apply_changes else 0,
        " inserted (inactive)" if apply_changes else " would be inserted",
    )
    if not apply_changes:
        logger.info("Re-run with --discover --apply to insert them.")
    return 0


async def cmd_activate(name: str) -> int:
    """Flip one verified venue live, refusing if it would shadow an active one."""
    import db_supabase  # type: ignore

    venues = await _load_seeded_venues()
    target = next((v for v in venues if (v.get("name") or "") == name), None)
    if not target:
        logger.error("No venue named %r. Names are matched exactly.", name)
        return 1

    if not target.get("pickup_points"):
        logger.error("%s has no pickup points — activating it would return an empty chooser.", name)
        return 1

    # The guard that matters. /maps/pickup-points picks the NEAREST center
    # among every active venue containing the rider's pin, so two overlapping
    # circles mean one of them silently answers for the other.
    clashes = [
        (v, d)
        for v in venues
        if v.get("id") != target.get("id") and v.get("is_active") and (d := _overlaps(target, v)) is not None
    ]
    if clashes:
        for v, d in clashes:
            logger.error(
                "%s overlaps active venue %r (centers %.0fm apart, radii %sm and %sm)",
                name,
                v.get("name"),
                d,
                target.get("radius_m"),
                v.get("radius_m"),
            )
        logger.error(
            "Refusing to activate. Re-center or shrink one of them first — an overlap "
            "hands the rider the other venue's door list."
        )
        return 1

    await db_supabase.update_one("venues", {"id": target["id"]}, {"is_active": True})
    logger.info("Activated %s.", name)
    return 0


async def cmd_report() -> int:
    """Print current venue state and every overlapping pair, without calling Places."""
    venues = await _load_seeded_venues()
    active = [v for v in venues if v.get("is_active")]
    logger.info("%d venue(s): %d active, %d dark", len(venues), len(active), len(venues) - len(active))

    pairs = 0
    for i in range(len(venues)):
        for j in range(i + 1, len(venues)):
            d = _overlaps(venues[i], venues[j])
            if d is None:
                continue
            pairs += 1
            both_live = venues[i].get("is_active") and venues[j].get("is_active")
            (logger.error if both_live else logger.info)(
                "%soverlap: %r (r%s) <-> %r (r%s), %.0fm apart",
                "LIVE " if both_live else "",
                venues[i].get("name"),
                venues[i].get("radius_m"),
                venues[j].get("name"),
                venues[j].get("radius_m"),
                d,
            )
    logger.info("%d overlapping pair(s).", pairs)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apply", action="store_true", help="Write changes (default is a dry run)")
    p.add_argument("--discover", action="store_true", help="Find brand locations that are not yet venues")
    p.add_argument("--activate", metavar="NAME", help="Activate one venue by exact name, if it overlaps nothing live")
    p.add_argument("--report", action="store_true", help="Show venue state and overlaps; makes no Places calls")
    args = p.parse_args()

    if args.activate:
        return asyncio.run(cmd_activate(args.activate))
    if args.report:
        return asyncio.run(cmd_report())
    if args.discover:
        return asyncio.run(cmd_discover(args.apply))
    return asyncio.run(cmd_geocode(args.apply))


if __name__ == "__main__":
    sys.exit(main())
