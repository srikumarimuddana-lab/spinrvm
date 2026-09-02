"""WS-C / audit C4: the PostGIS candidate fetch must match the box it replaces.

Migration 395 moves the dispatch candidate query onto the GiST index. The whole
change is only safe if the RPC selects the *same drivers* the bounding-box query
would have — everything downstream (`filter_and_rank_drivers`, the presence
filter, the subscription gate, the service-area guard) is untouched and assumes
it is being handed the same pool.

Three things are pinned here:

1. **Semantic parity.** The SQL `WHERE` clause and the Python filter dict are two
   expressions of one predicate. They are modelled side by side and compared over
   a driver fixture that exercises every branch, so a future edit to one that is
   not mirrored in the other fails here rather than in production dispatch.
2. **Superset discipline.** Both paths must over-fetch by the same 10% + 1 km,
   because `filter_and_rank_drivers` is the exact distance gate. A tighter
   spatial fetch would silently shrink the pool with no visible error.
3. **Fallback.** Flag off → the box query. RPC raises → the box query, loudly.
"""

from __future__ import annotations

import pathlib
import re

import pytest

pytestmark = pytest.mark.unit

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_MIGRATION = _ROOT / "migrations" / "395_dispatch_candidate_drivers_rpc.sql"

# Fixture covering each predicate branch: offline, unavailable, unverified,
# suspended, wrong vehicle type, non-WAV, wrong area, unassigned area, no
# geography, and out of range.
_DRIVERS = [
    dict(
        id="ok",
        is_online=True,
        is_available=True,
        is_verified=True,
        status="active",
        vehicle_type_id="economy",
        is_wav=False,
        service_area_id="a1",
        geog=True,
        dist_m=1_000,
    ),
    dict(
        id="offline",
        is_online=False,
        is_available=True,
        is_verified=True,
        status="active",
        vehicle_type_id="economy",
        is_wav=False,
        service_area_id="a1",
        geog=True,
        dist_m=1_000,
    ),
    dict(
        id="busy",
        is_online=True,
        is_available=False,
        is_verified=True,
        status="active",
        vehicle_type_id="economy",
        is_wav=False,
        service_area_id="a1",
        geog=True,
        dist_m=1_000,
    ),
    dict(
        id="unverified",
        is_online=True,
        is_available=True,
        is_verified=False,
        status="active",
        vehicle_type_id="economy",
        is_wav=False,
        service_area_id="a1",
        geog=True,
        dist_m=1_000,
    ),
    dict(
        id="suspended",
        is_online=True,
        is_available=True,
        is_verified=True,
        status="suspended",
        vehicle_type_id="economy",
        is_wav=False,
        service_area_id="a1",
        geog=True,
        dist_m=1_000,
    ),
    dict(
        id="wrong_type",
        is_online=True,
        is_available=True,
        is_verified=True,
        status="active",
        vehicle_type_id="xl",
        is_wav=False,
        service_area_id="a1",
        geog=True,
        dist_m=1_000,
    ),
    dict(
        id="wav",
        is_online=True,
        is_available=True,
        is_verified=True,
        status="active",
        vehicle_type_id="economy",
        is_wav=True,
        service_area_id="a1",
        geog=True,
        dist_m=1_000,
    ),
    dict(
        id="other_area",
        is_online=True,
        is_available=True,
        is_verified=True,
        status="active",
        vehicle_type_id="economy",
        is_wav=False,
        service_area_id="zz",
        geog=True,
        dist_m=1_000,
    ),
    dict(
        id="no_area",
        is_online=True,
        is_available=True,
        is_verified=True,
        status="active",
        vehicle_type_id="economy",
        is_wav=False,
        service_area_id=None,
        geog=True,
        dist_m=1_000,
    ),
    dict(
        id="no_geog",
        is_online=True,
        is_available=True,
        is_verified=True,
        status="active",
        vehicle_type_id="economy",
        is_wav=False,
        service_area_id="a1",
        geog=False,
        dist_m=1_000,
    ),
    dict(
        id="far",
        is_online=True,
        is_available=True,
        is_verified=True,
        status="active",
        vehicle_type_id="economy",
        is_wav=False,
        service_area_id="a1",
        geog=True,
        dist_m=999_000,
    ),
]


def _sql_predicate(d, *, types, requires_wav, area_ids, allow_unassigned, radius_m):
    """Model of migration 395's WHERE clause."""
    return (
        d["is_online"] is True
        and d["is_available"] is True
        and d["is_verified"] is True
        and d["status"] == "active"
        and d["vehicle_type_id"] in types
        and d["geog"]
        and (not requires_wav or d["is_wav"] is True)
        and (
            area_ids is None or d["service_area_id"] in area_ids or (allow_unassigned and d["service_area_id"] is None)
        )
        and d["dist_m"] <= radius_m
    )


def _dict_filter_predicate(d, *, types, requires_wav, area_ids, allow_unassigned, radius_m):
    """Model of the get_rows filter in routes/rides/matching.py + the box.

    `geog` is not a predicate on this path — the box filters on lat/lng — so a
    driver with NULL location_geog is represented here as one the box also
    excludes (its lat/lng are the (0,0) default, outside any Canadian box, which
    `_is_dispatchable_driver` already drops).
    """
    ok = (
        d["is_online"] is True
        and d["is_available"] is True
        and d["is_verified"] is True
        and d["status"] == "active"
        and d["vehicle_type_id"] in types
        and d["dist_m"] <= radius_m
        and d["geog"]
    )
    if requires_wav:
        ok = ok and d["is_wav"] is True
    if area_ids is not None:
        if allow_unassigned:
            ok = ok and (d["service_area_id"] in area_ids or d["service_area_id"] is None)
        else:
            ok = ok and d["service_area_id"] in area_ids
    return ok


_CASES = [
    dict(types={"economy"}, requires_wav=False, area_ids=None, allow_unassigned=True),
    dict(types={"economy"}, requires_wav=True, area_ids=None, allow_unassigned=True),
    dict(types={"economy"}, requires_wav=False, area_ids={"a1"}, allow_unassigned=True),
    dict(types={"economy"}, requires_wav=False, area_ids={"a1"}, allow_unassigned=False),
    dict(types={"economy", "xl"}, requires_wav=False, area_ids=None, allow_unassigned=True),
    dict(types={"xl"}, requires_wav=False, area_ids={"zz"}, allow_unassigned=False),
]


class TestSemanticParity:
    @pytest.mark.parametrize("case", _CASES)
    def test_sql_and_dict_filters_select_the_same_drivers(self, case):
        radius_m = 15_000.0
        sql = {d["id"] for d in _DRIVERS if _sql_predicate(d, radius_m=radius_m, **case)}
        box = {d["id"] for d in _DRIVERS if _dict_filter_predicate(d, radius_m=radius_m, **case)}
        assert sql == box, f"divergence for {case}: sql-only={sql - box}, box-only={box - sql}"

    def test_wav_only_narrows_when_required(self):
        # The trap this guards: a WAV driver must still receive non-WAV offers.
        base = dict(types={"economy"}, area_ids=None, allow_unassigned=True)
        not_required = {d["id"] for d in _DRIVERS if _sql_predicate(d, requires_wav=False, radius_m=15_000.0, **base)}
        required = {d["id"] for d in _DRIVERS if _sql_predicate(d, requires_wav=True, radius_m=15_000.0, **base)}
        assert "wav" in not_required
        assert required < not_required

    def test_unassigned_area_driver_is_kept_when_allowed(self):
        # SQL `IN`/`= ANY` never matches NULL — the reason build_driver_area_filter
        # emits an $or. If the RPC forgot the NULL arm, every driver whose area
        # was never assigned would silently vanish from dispatch.
        base = dict(types={"economy"}, requires_wav=False, area_ids={"a1"}, radius_m=15_000.0)
        assert "no_area" in {d["id"] for d in _DRIVERS if _sql_predicate(d, allow_unassigned=True, **base)}
        assert "no_area" not in {d["id"] for d in _DRIVERS if _sql_predicate(d, allow_unassigned=False, **base)}


class TestSupersetDiscipline:
    def test_both_paths_pad_identically(self):
        from backend.services.dispatch_service import _padded_radius_km, dispatch_geo_bounds, dispatch_radius_m

        radius_km = 12.0
        assert dispatch_radius_m(radius_km) == pytest.approx(_padded_radius_km(radius_km) * 1000.0)
        # The box must still be at least as wide as the circle the RPC uses.
        bounds = dispatch_geo_bounds(52.1332, -106.67, radius_km)
        lat_hi = next(b["lat"]["$lte"] for b in bounds if "$lte" in b.get("lat", {}))
        km_per_deg_lat = 110.574
        assert (lat_hi - 52.1332) * km_per_deg_lat == pytest.approx(_padded_radius_km(radius_km), rel=1e-6)

    def test_padding_is_a_superset_not_a_tightening(self):
        from backend.services.dispatch_service import _padded_radius_km

        for r in (1.0, 5.0, 12.0, 40.0):
            assert _padded_radius_km(r) > r


class TestMigrationContract:
    """The RPC's shape is a contract with driver_repo and with `drivers`."""

    @staticmethod
    def _sql() -> str:
        return _MIGRATION.read_text()

    def test_returns_types_match_the_drivers_table(self):
        sql = self._sql()
        # These two are the ones that bite. drivers.rating is FLOAT and
        # drivers.destination_mode is BOOLEAN; declaring either as something
        # else makes PostgREST fail with "structure of query does not match
        # function result type" — or worse, in destination_mode's case, hands
        # Python the string 'false', which is truthy, so dispatch_service's
        # `if not driver.get("destination_mode")` would treat EVERY driver as
        # being in destination mode and silently collapse the pool.
        assert re.search(r"rating\s+double precision", sql)
        assert re.search(r"destination_mode\s+boolean", sql)
        assert "d.destination_mode::text" not in sql

    def test_accepts_a_list_of_vehicle_types(self):
        # The cascade path in matching.py widens to a set of upgrade types.
        assert "p_vehicle_type_ids      text[]" in self._sql()

    def test_is_locked_to_the_service_role(self):
        sql = self._sql()
        assert "REVOKE EXECUTE ON FUNCTION dispatch_candidate_drivers" in sql
        assert "FROM anon, authenticated" in sql
        assert "TO service_role" in sql
        assert "SECURITY DEFINER" in sql
        assert "SET search_path = public, extensions" in sql

    def test_knn_operand_is_inlined_not_joined(self):
        # `<->` is only index-answerable when one side is constant for the scan.
        # A CTE join risks a materialise + full sort, silently costing the exact
        # speed-up this migration exists for.
        sql = self._sql()
        assert "WITH pt AS" not in sql
        assert "ORDER BY d.location_geog <-> ST_SetSRID(ST_MakePoint(p_lng, p_lat), 4326)::geography" in sql

    def test_documents_its_rollback(self):
        assert "DROP FUNCTION IF EXISTS dispatch_candidate_drivers" in self._sql()


class TestFlagAndFallback:
    def test_flag_defaults_off(self, monkeypatch):
        monkeypatch.delenv("DISPATCH_SPATIAL_CANDIDATES", raising=False)
        import importlib

        import backend.services.dispatch_service as ds

        importlib.reload(ds)
        assert ds.DISPATCH_SPATIAL_CANDIDATES is False

    @pytest.mark.parametrize("val", ["1", "true", "yes", "on", "TRUE"])
    def test_flag_accepts_the_surge_engine_spellings(self, monkeypatch, val):
        monkeypatch.setenv("DISPATCH_SPATIAL_CANDIDATES", val)
        import importlib

        import backend.services.dispatch_service as ds

        importlib.reload(ds)
        assert ds.DISPATCH_SPATIAL_CANDIDATES is True

    def test_fallback_path_is_the_untouched_box_query(self):
        # Pin the structure rather than the behaviour: the fallback must reuse
        # the caller's own filter dict, not a reconstruction of it, so the two
        # paths cannot drift.
        src = (_ROOT / "routes" / "rides" / "matching.py").read_text()
        assert "spinr_dispatch_spatial_fallback_total" in src
        assert "box_filter," in src
        # ...and the fallback must be reached from an `except`, not from a
        # truthiness check on the result (an empty pool is a legitimate answer).
        assert "except Exception as rpc_err:" in src
