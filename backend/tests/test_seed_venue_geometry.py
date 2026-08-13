"""Geometry invariants for the seeded pickup venues (migrations 135/307/308/309).

These migrations are data, not logic, so nothing else in the suite reads them —
which is how a venue center ended up ~1.9 km from its own street address and how
15 pairs of detection circles ended up overlapping. Both are silent at deploy
time and only surface as a driver sent to the wrong door.

The invariants below are the ones with a rider-visible failure mode:

* **A pickup point must lie inside the circle that offered it.** The rider is
  shown the chooser because their pin fell within ``radius_m`` of the center; a
  point outside that circle is a meeting spot they were never near.
* **No two *effectively active* venues may overlap.** ``/maps/pickup-points``
  returns the *nearest center* among every active venue containing the pin, so
  overlapping circles mean one venue silently answers for the other.
* **The Saskatoon seed stays dark.** 307/308/309 carry estimated centers and
  hand-authored entrances; they must not be live. Activation runs through
  ``scripts/geocode_seed_venues.py --activate``, which re-checks the overlap
  rule against live rows.

"Effectively active" spans two files. 307/308/309 merged with
``is_active = true``, and because the runner keys ``schema_migrations`` on the
full filename, editing an applied migration never re-runs it — so the fix had to
be a *new* migration. 310 flips those 38 rows off. This module therefore parses
the INSERT literals **and** 310's deactivation list, and asserts against the
combination rather than against any single file, which is what the database
actually ends up in.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
MIGRATIONS = BACKEND / "migrations"

# Seed files that must contain Saskatoon-only venues, and stay inactive.
SASKATOON_SEEDS = [
    "307_seed_saskatoon_pickup_venues.sql",
    "308_seed_saskatoon_stores.sql",
    "309_seed_saskatoon_retail_nightlife.sql",
]
# 135 seeds Regina venues too, so it is parsed for the geometry checks but
# excluded from the Saskatoon-envelope and stays-dark assertions.
ALL_SEEDS = ["135_seed_pickup_venues.sql", *SASKATOON_SEEDS]

SASKATOON_LAT, SASKATOON_LNG = 52.13, -106.67
SASKATOON_RADIUS_M = 20_000

# name, lat, lng, radius, points jsonb, then either "_sa_id, false" or a bare
# "true"/"false" depending on whether the file sets service_area_id.
_VENUE_RE = re.compile(
    r"SELECT '((?:[^']|'')+)',\s*(-?[\d.]+),\s*(-?[\d.]+),\s*(\d+),\s*"
    r"'(\[.*?\])'::jsonb,\s*(?:_sa_id,\s*)?(true|false)",
    re.DOTALL,
)


def _haversine_m():
    """Borrow the script's implementation so the test and the activation gate
    can never disagree about what counts as an overlap."""
    path = BACKEND / "scripts" / "geocode_seed_venues.py"
    spec = importlib.util.spec_from_file_location("geocode_seed_venues", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.haversine_m


haversine_m = _haversine_m()


def _parse(filename: str) -> list[dict]:
    sql = (MIGRATIONS / filename).read_text(encoding="utf-8")
    venues = []
    for m in _VENUE_RE.finditer(sql):
        venues.append(
            {
                "file": filename,
                "name": m.group(1).replace("''", "'"),
                "lat": float(m.group(2)),
                "lng": float(m.group(3)),
                "radius_m": int(m.group(4)),
                "points": json.loads(m.group(5)),
                "is_active": m.group(6) == "true",
            }
        )
    return venues


ALL_VENUES = [v for f in ALL_SEEDS for v in _parse(f)]
SASKATOON_VENUES = [v for f in SASKATOON_SEEDS for v in _parse(f)]

# Migration that takes the unverified Saskatoon seed back offline.
DEACTIVATION_MIGRATION = "310_deactivate_unverified_saskatoon_venues.sql"


def _parse_deactivated() -> set[str]:
    """Venue names 310 sets is_active = false, read from its IN (...) list.

    Anchored to the quoted entries on their own lines rather than split on ')',
    because venue names contain parentheses ("The Centre (8th Street)").
    """
    sql = (MIGRATIONS / DEACTIVATION_MIGRATION).read_text(encoding="utf-8")
    marker = "AND name IN ("
    start = sql.index(marker)
    block = sql[start : sql.index("   );", start)]
    return {m.replace("''", "'") for m in re.findall(r"^\s+'((?:[^']|'')+)',?\s*$", block, re.M)}


DEACTIVATED = _parse_deactivated()

# What the database is actually left in once every migration has run.
EFFECTIVELY_ACTIVE = [v for v in ALL_VENUES if v["is_active"] and v["name"] not in DEACTIVATED]


def test_every_seed_file_parses_at_least_one_venue():
    """Guards the regex itself: a format change that silently matched nothing
    would make every other test in this module vacuously pass."""
    for f in ALL_SEEDS:
        assert _parse(f), f"{f} parsed zero venues — the INSERT shape likely changed"


@pytest.mark.parametrize("venue", ALL_VENUES, ids=lambda v: f"{v['file'][:3]}:{v['name']}")
def test_venue_coordinates_are_valid(venue):
    assert -90 <= venue["lat"] <= 90, f"{venue['name']}: latitude out of range"
    assert -180 <= venue["lng"] <= 180, f"{venue['name']}: longitude out of range"
    # Matches the admin API's VenueRequest bounds, so a seeded row could always
    # be round-tripped through Dashboard → Pickup Venues without being rejected.
    assert 20 <= venue["radius_m"] <= 2000, f"{venue['name']}: radius outside the admin-allowed 20-2000m"
    assert len(venue["points"]) <= 30, f"{venue['name']}: more than the admin-allowed 30 pickup points"


@pytest.mark.parametrize("venue", ALL_VENUES, ids=lambda v: f"{v['file'][:3]}:{v['name']}")
def test_pickup_points_lie_inside_the_detection_radius(venue):
    """A point outside its own circle is a meeting spot the rider was never near."""
    for p in venue["points"]:
        assert p.get("name"), f"{venue['name']}: pickup point missing a name"
        d = haversine_m(venue["lat"], venue["lng"], p["lat"], p["lng"])
        assert d <= venue["radius_m"], (
            f"{venue['name']} :: {p['name']} is {d:.0f}m from the center "
            f"but the detection radius is only {venue['radius_m']}m"
        )


@pytest.mark.parametrize("venue", SASKATOON_VENUES, ids=lambda v: f"{v['file'][:3]}:{v['name']}")
def test_saskatoon_seeds_are_actually_in_saskatoon(venue):
    d = haversine_m(venue["lat"], venue["lng"], SASKATOON_LAT, SASKATOON_LNG)
    assert d <= SASKATOON_RADIUS_M, (
        f"{venue['name']} is {d / 1000:.1f}km from downtown Saskatoon — wrong city, or a transposed coordinate"
    )


def test_deactivation_migration_covers_every_saskatoon_seed():
    """Guards the regex and the list together: if 310's IN (...) list drifts out
    of sync with what 307/308/309 insert — a venue added to a seed, a name
    retyped — the uncovered venue would be left live on unverified coordinates."""
    assert DEACTIVATED, f"{DEACTIVATION_MIGRATION} parsed zero names — its IN (...) shape likely changed"
    seeded = {v["name"] for v in SASKATOON_VENUES}
    missing = seeded - DEACTIVATED
    assert not missing, "Seeded but never deactivated:\n  " + "\n  ".join(sorted(missing))
    stray = DEACTIVATED - seeded
    assert not stray, (
        "Deactivated but not part of the Saskatoon seed — this would take down a "
        "venue someone else owns:\n  " + "\n  ".join(sorted(stray))
    )


@pytest.mark.parametrize("venue", SASKATOON_VENUES, ids=lambda v: f"{v['file'][:3]}:{v['name']}")
def test_saskatoon_seeds_end_up_dark(venue):
    """Estimated centers and hand-authored entrances must not be live. They may
    be *inserted* active — 307/308/309 merged that way and cannot be edited
    retroactively — so what matters is the state after 310 runs. Activate via
    scripts/geocode_seed_venues.py --activate, which re-checks the overlap rule
    against whatever is already live."""
    assert not venue["is_active"] or venue["name"] in DEACTIVATED, (
        f"{venue['name']} ends up active. Its coordinates have not been "
        "geocode-verified; activation belongs to geocode_seed_venues.py --activate"
    )


def test_no_two_effectively_active_venues_overlap():
    """The wrong-chooser bug. /maps/pickup-points returns the nearest center
    among all active radius matches, so overlapping active circles mean one
    venue silently answers for the other — e.g. a rider outside The Rook &
    Raven being offered the Delta Bessborough's doors."""
    clashes = []
    for i in range(len(EFFECTIVELY_ACTIVE)):
        for j in range(i + 1, len(EFFECTIVELY_ACTIVE)):
            a, b = EFFECTIVELY_ACTIVE[i], EFFECTIVELY_ACTIVE[j]
            d = haversine_m(a["lat"], a["lng"], b["lat"], b["lng"])
            if d < a["radius_m"] + b["radius_m"]:
                clashes.append(f"{a['name']} (r{a['radius_m']}) <-> {b['name']} (r{b['radius_m']}): {d:.0f}m apart")
    assert not clashes, "Overlapping active venues:\n  " + "\n  ".join(clashes)


def test_venue_names_are_unique_across_seed_files():
    """The seeds dedupe with INSERT ... WHERE NOT EXISTS on name, and `venues`
    has no unique index on it, so two files seeding the same name would insert
    twice on a fresh database."""
    seen: dict[str, str] = {}
    dupes = []
    for v in ALL_VENUES:
        if v["name"] in seen:
            dupes.append(f"{v['name']} in both {seen[v['name']]} and {v['file']}")
        seen[v["name"]] = v["file"]
    assert not dupes, "Duplicate venue names:\n  " + "\n  ".join(dupes)
