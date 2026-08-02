"""repositories/ride_repo.py — ride repository unit tests.

Ride repository: ride CRUD, route-detail projection, admin dashboard
enrichment, flags/complaints/lost-and-found, and live/location-trail reads.
Extracted from db_supabase.py (Phase 4 god-object decomposition). No
dedicated test file existed before this one — only indirect coverage via
route-level tests (routes/rides/*, routes/admin/rides.py, etc.).

Patch target: `repositories.ride_repo.supabase` (the domain-module binding),
per CLAUDE.md's "Patch target for DB" convention — this module defines its
own functions rather than re-exporting `_base`'s generic CRUD helpers.

These tests pin, per function: the "Supabase client not configured" branch
where the module documents one (some functions return `None`/`[]`/`0`
instead of raising — that's this module's own documented degrade-soft
convention for read paths, not a swallowed error; distinct from the
money-adjacent `wallet_repo.py`, which raises), the happy path, and — for
functions that DO call `run_sync` on a query that fails — that the failure
propagates as `DatabaseError` per `repositories/_base.py`'s `run_sync`
wrapper (CLAUDE.md's "never silently swallow a DB error" rule).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clear_request_deadline():
    """Defend against a pre-existing test-pollution bug (NOT in ride_repo.py):
    several tests in test_utils_extended.py's TestDeadline* class call
    `set_request_deadline(...)` directly and never reset the contextvar
    afterward, leaking a permanently-past deadline into every later test in
    the same pytest process that calls `repositories._base.run_sync` (every
    helper in this module does). Same workaround already used in
    `test_wallet_repo.py` — reset the contextvar to a known-good `None`
    state for every test here too.
    """
    from backend.utils.deadline import set_request_deadline

    set_request_deadline(None)
    yield


def _table_mock(row=None, rows=None, count=None):
    """Build a MagicMock supabase client whose `.table(...).<chain>.execute()`
    returns a response object shaped like a single row, a list of rows,
    and/or a `.count` (PostgREST count=exact response)."""
    client = MagicMock()
    response = MagicMock()
    if row is not None:
        response.data = [row]
    elif rows is not None:
        response.data = rows
    else:
        response.data = []
    if count is not None:
        response.count = count
    else:
        response.count = None
    client.table.return_value.select.return_value.eq.return_value.is_.return_value.execute.return_value = response
    client.table.return_value.select.return_value.eq.return_value.execute.return_value = response
    client.table.return_value.select.return_value.execute.return_value = response
    client.table.return_value.insert.return_value.execute.return_value = response
    client.table.return_value.update.return_value.eq.return_value.execute.return_value = response
    client.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value = response
    return client


def _table_raises(exc: Exception):
    client = MagicMock()
    client.table.side_effect = exc
    return client


# ─────────────────────────────────────────────────────────────────────────────
# _safe_route_segments (pure function)
# ─────────────────────────────────────────────────────────────────────────────


def test_safe_route_segments_rejects_non_list_input():
    from repositories.ride_repo import _safe_route_segments

    assert _safe_route_segments("not-a-list") == []
    assert _safe_route_segments(None) == []


def test_safe_route_segments_drops_segment_with_non_dict_entry():
    from repositories.ride_repo import _safe_route_segments

    assert _safe_route_segments(["not-a-dict"]) == []


def test_safe_route_segments_drops_segment_with_malformed_coordinate():
    from repositories.ride_repo import _safe_route_segments

    segments = [{"coordinates": [["bad", "coord"]]}]
    assert _safe_route_segments(segments) == []


def test_safe_route_segments_drops_segment_with_out_of_range_lat_lng():
    from repositories.ride_repo import _safe_route_segments

    segments = [{"coordinates": [[999.0, 0.0]]}]
    assert _safe_route_segments(segments) == []


def test_safe_route_segments_drops_segment_with_no_valid_coordinates():
    from repositories.ride_repo import _safe_route_segments

    segments = [{"coordinates": []}]
    assert _safe_route_segments(segments) == []


def test_safe_route_segments_allowlists_provider_and_geometry_kind():
    from repositories.ride_repo import _safe_route_segments

    segments = [
        {
            "coordinates": [[45.0, -73.0]],
            "provider": "osrm_match",
            "geometry_kind": "observed",
            "secret_field": "should not appear",
        }
    ]
    result = _safe_route_segments(segments)
    assert len(result) == 1
    assert result[0]["provider"] == "osrm_match"
    assert result[0]["geometry_kind"] == "observed"
    assert "secret_field" not in result[0]


def test_safe_route_segments_rejects_unknown_provider_and_geometry_kind():
    from repositories.ride_repo import _safe_route_segments

    segments = [{"coordinates": [[45.0, -73.0]], "provider": "evil_provider", "geometry_kind": "made_up"}]
    result = _safe_route_segments(segments)
    assert "provider" not in result[0]
    assert "geometry_kind" not in result[0]


def test_safe_route_segments_gap_reason_only_kept_for_inferred_kind():
    from repositories.ride_repo import _safe_route_segments

    segments = [
        {"coordinates": [[45.0, -73.0]], "geometry_kind": "inferred", "gap_reason": "missing_start"},
        {"coordinates": [[45.0, -73.0]], "geometry_kind": "observed", "gap_reason": "missing_start"},
    ]
    result = _safe_route_segments(segments)
    assert result[0].get("gap_reason") == "missing_start"
    assert "gap_reason" not in result[1]


# ─────────────────────────────────────────────────────────────────────────────
# create_route_snapshot_signed_url
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_route_snapshot_signed_url_rejects_empty_object_path():
    from repositories.ride_repo import create_route_snapshot_signed_url

    with pytest.raises(ValueError):
        await create_route_snapshot_signed_url("")
    with pytest.raises(ValueError):
        await create_route_snapshot_signed_url("   ")


@pytest.mark.asyncio
async def test_create_route_snapshot_signed_url_raises_when_supabase_unconfigured():
    with patch("repositories.ride_repo.supabase", None):
        from repositories.ride_repo import create_route_snapshot_signed_url

        with pytest.raises(RuntimeError):
            await create_route_snapshot_signed_url("path/to/snap.jpg")


@pytest.mark.asyncio
async def test_create_route_snapshot_signed_url_happy_path_dict_response():
    mock_sb = MagicMock()
    mock_sb.storage.from_.return_value.create_signed_url.return_value = {"signedURL": "https://signed.example/x"}
    with patch("repositories.ride_repo.supabase", mock_sb):
        from repositories.ride_repo import create_route_snapshot_signed_url

        result = await create_route_snapshot_signed_url("path/to/snap.jpg")

    assert result == "https://signed.example/x"


@pytest.mark.asyncio
async def test_create_route_snapshot_signed_url_happy_path_object_response():
    mock_sb = MagicMock()
    response_obj = MagicMock(spec=["signedURL"])
    response_obj.signedURL = "https://signed.example/y"
    mock_sb.storage.from_.return_value.create_signed_url.return_value = response_obj
    with patch("repositories.ride_repo.supabase", mock_sb):
        from repositories.ride_repo import create_route_snapshot_signed_url

        result = await create_route_snapshot_signed_url("path/to/snap.jpg")

    assert result == "https://signed.example/y"


@pytest.mark.asyncio
async def test_create_route_snapshot_signed_url_raises_when_no_url_returned():
    mock_sb = MagicMock()
    mock_sb.storage.from_.return_value.create_signed_url.return_value = {}
    with patch("repositories.ride_repo.supabase", mock_sb):
        from repositories.ride_repo import create_route_snapshot_signed_url

        with pytest.raises(RuntimeError):
            await create_route_snapshot_signed_url("path/to/snap.jpg")


# ─────────────────────────────────────────────────────────────────────────────
# _driver_profile_image
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_driver_profile_image_returns_empty_when_no_user_id():
    from repositories.ride_repo import _driver_profile_image

    assert await _driver_profile_image(None) == ""
    assert await _driver_profile_image("") == ""


@pytest.mark.asyncio
async def test_driver_profile_image_returns_empty_when_supabase_unconfigured():
    with patch("repositories.ride_repo.supabase", None):
        from repositories.ride_repo import _driver_profile_image

        assert await _driver_profile_image("u1") == ""


@pytest.mark.asyncio
async def test_driver_profile_image_happy_path():
    mock_sb = _table_mock(row={"profile_image": "base64data"})
    with patch("repositories.ride_repo.supabase", mock_sb):
        from repositories.ride_repo import _driver_profile_image

        assert await _driver_profile_image("u1") == "base64data"


# ─────────────────────────────────────────────────────────────────────────────
# get_ride
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_ride_returns_none_when_supabase_unconfigured():
    with patch("repositories.ride_repo.supabase", None):
        from repositories.ride_repo import get_ride

        assert await get_ride("r1") is None


@pytest.mark.asyncio
async def test_get_ride_returns_none_when_not_found():
    mock_sb = _table_mock(rows=[])
    with patch("repositories.ride_repo.supabase", mock_sb):
        from repositories.ride_repo import get_ride

        assert await get_ride("r1") is None


@pytest.mark.asyncio
async def test_get_ride_without_route_does_not_fetch_route():
    mock_sb = _table_mock(row={"id": "r1", "status": "completed"})
    with patch("repositories.ride_repo.supabase", mock_sb):
        from repositories.ride_repo import get_ride

        result = await get_ride("r1", include_route=False)

    assert result["id"] == "r1"


@pytest.mark.asyncio
async def test_get_ride_with_route_projects_when_route_row_exists():
    ride_row = {"id": "r1", "status": "completed"}
    route_row = {"route_schema_version": 1, "road_polyline": [[1, 2]]}
    mock_sb = MagicMock()

    def _table_side_effect(name):
        m = MagicMock()
        if name == "rides":
            resp = MagicMock()
            resp.data = [ride_row]
            m.select.return_value.eq.return_value.is_.return_value.execute.return_value = resp
        elif name == "ride_routes":
            resp = MagicMock()
            resp.data = [route_row]
            m.select.return_value.eq.return_value.execute.return_value = resp
        return m

    mock_sb.table.side_effect = _table_side_effect
    with patch("repositories.ride_repo.supabase", mock_sb):
        from repositories.ride_repo import get_ride

        result = await get_ride("r1", include_route=True)

    assert result["id"] == "r1"
    assert result["road_polyline"] == [[1, 2]]


@pytest.mark.asyncio
async def test_get_ride_with_route_skips_projection_when_no_route_row():
    ride_row = {"id": "r1", "status": "completed"}
    mock_sb = MagicMock()

    def _table_side_effect(name):
        m = MagicMock()
        resp = MagicMock()
        if name == "rides":
            resp.data = [ride_row]
            m.select.return_value.eq.return_value.is_.return_value.execute.return_value = resp
        elif name == "ride_routes":
            resp.data = []
            m.select.return_value.eq.return_value.execute.return_value = resp
        return m

    mock_sb.table.side_effect = _table_side_effect
    with patch("repositories.ride_repo.supabase", mock_sb):
        from repositories.ride_repo import get_ride

        result = await get_ride("r1", include_route=True)

    assert result["id"] == "r1"
    assert "road_polyline" not in result


# ─────────────────────────────────────────────────────────────────────────────
# _project_route_detail — v2 segmented geometry branch
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_project_route_detail_v2_strips_legacy_polyline_fields():
    from repositories.ride_repo import _project_route_detail

    ride = {"id": "r1", "road_polyline": [[1, 2]], "road_polyline_pickup": [[3, 4]], "phase_polylines": {}}
    route = {"route_schema_version": 2, "road_matched_segments": []}

    await _project_route_detail(ride, route)

    assert "road_polyline" not in ride
    assert "road_polyline_pickup" not in ride
    assert "phase_polylines" not in ride
    assert ride["route_schema_version"] == 2


@pytest.mark.asyncio
async def test_project_route_detail_v2_uses_observed_segments_fallback():
    from repositories.ride_repo import _project_route_detail

    ride = {"id": "r1"}
    route = {
        "route_schema_version": 2,
        "observed_segments": [{"coordinates": [[45.0, -73.0]]}],
    }

    await _project_route_detail(ride, route)

    assert len(ride["actual_route_segments"]) == 1


@pytest.mark.asyncio
async def test_project_route_detail_v2_sets_distance_basis_from_trip_metrics():
    from repositories.ride_repo import _project_route_detail

    ride = {"id": "r1", "ride_metrics": {"phases": {"trip_in_progress": {"distance_basis": "gps"}}}}
    route = {"route_schema_version": 2, "road_matched_segments": []}

    await _project_route_detail(ride, route)

    assert ride["route_quality"]["distance_basis"] == "gps"


@pytest.mark.asyncio
async def test_project_route_detail_v2_completion_point_valid_coordinates():
    from repositories.ride_repo import _project_route_detail

    ride = {"id": "r1"}
    route = {
        "route_schema_version": 2,
        "road_matched_segments": [],
        "completion_point": {"lat": 45.0, "lng": -73.0},
    }

    await _project_route_detail(ride, route)

    assert ride["actual_completion_point"] == {"latitude": 45.0, "longitude": -73.0}


@pytest.mark.asyncio
async def test_project_route_detail_v2_completion_point_invalid_coordinates_skipped():
    from repositories.ride_repo import _project_route_detail

    ride = {"id": "r1"}
    route = {
        "route_schema_version": 2,
        "road_matched_segments": [],
        "completion_point": {"lat": "not-a-number", "lng": -73.0},
    }

    await _project_route_detail(ride, route)

    assert "actual_completion_point" not in ride


@pytest.mark.asyncio
async def test_project_route_detail_v2_completion_point_out_of_range_skipped():
    from repositories.ride_repo import _project_route_detail

    ride = {"id": "r1"}
    route = {
        "route_schema_version": 2,
        "road_matched_segments": [],
        "completion_point": {"lat": 999.0, "lng": -73.0},
    }

    await _project_route_detail(ride, route)

    assert "actual_completion_point" not in ride


@pytest.mark.asyncio
async def test_project_route_detail_v2_snapshot_url_signing_failure_degrades_gracefully():
    from repositories.ride_repo import _project_route_detail

    ride = {"id": "r1"}
    route = {
        "route_schema_version": 2,
        "road_matched_segments": [],
        "snapshot_revision": 1,
        "route_revision": 1,
        "snapshot_object_path": "path/x.jpg",
    }

    with patch(
        "repositories.ride_repo.create_route_snapshot_signed_url",
        AsyncMock(side_effect=RuntimeError("storage down")),
    ):
        await _project_route_detail(ride, route)

    # A transient signing failure must not raise — the whole ride read must
    # not fail just because the thumbnail couldn't be signed.
    assert "route_snapshot_url" not in ride


@pytest.mark.asyncio
async def test_project_route_detail_v2_snapshot_url_skipped_when_revision_mismatch():
    from repositories.ride_repo import _project_route_detail

    ride = {"id": "r1", "route_snapshot_url": "stale-url"}
    route = {
        "route_schema_version": 2,
        "road_matched_segments": [],
        "snapshot_revision": 1,
        "route_revision": 2,
        "snapshot_object_path": "path/x.jpg",
    }

    await _project_route_detail(ride, route)

    assert "route_snapshot_url" not in ride


@pytest.mark.asyncio
async def test_project_route_detail_legacy_v1_populates_legacy_fields():
    from repositories.ride_repo import _project_route_detail

    ride = {"id": "r1"}
    route = {
        "route_schema_version": 1,
        "road_polyline": [[1, 2]],
        "road_polyline_pickup": [[3, 4]],
        "save_status": "saved",
    }

    await _project_route_detail(ride, route)

    assert ride["road_polyline"] == [[1, 2]]
    assert ride["road_polyline_pickup"] == [[3, 4]]
    assert ride["route_geometry_status"] == "saved"


# ─────────────────────────────────────────────────────────────────────────────
# insert_ride / update_ride
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_insert_ride_raises_when_supabase_unconfigured():
    with patch("repositories.ride_repo.supabase", None):
        from repositories.ride_repo import insert_ride

        with pytest.raises(RuntimeError):
            await insert_ride({"id": "r1"})


@pytest.mark.asyncio
async def test_insert_ride_happy_path():
    mock_sb = _table_mock(row={"id": "r1", "status": "searching"})
    with patch("repositories.ride_repo.supabase", mock_sb):
        from repositories.ride_repo import insert_ride

        result = await insert_ride({"id": "r1"})

    assert result["id"] == "r1"


@pytest.mark.asyncio
async def test_insert_ride_reraises_and_logs_on_db_failure():
    mock_sb = _table_raises(RuntimeError("insert failed"))
    with patch("repositories.ride_repo.supabase", mock_sb):
        from repositories.ride_repo import insert_ride

        with pytest.raises(Exception):
            await insert_ride({"id": "r1"})


@pytest.mark.asyncio
async def test_update_ride_returns_none_when_supabase_unconfigured():
    with patch("repositories.ride_repo.supabase", None):
        from repositories.ride_repo import update_ride

        assert await update_ride("r1", {"status": "completed"}) is None


@pytest.mark.asyncio
async def test_update_ride_strips_mongo_set_wrapper():
    mock_sb = _table_mock(row={"id": "r1", "status": "completed"})
    with patch("repositories.ride_repo.supabase", mock_sb):
        from repositories.ride_repo import update_ride

        result = await update_ride("r1", {"$set": {"status": "completed"}})

    assert result["id"] == "r1"


# ─────────────────────────────────────────────────────────────────────────────
# claim_ride_payment_processing — the payment-status atomic-claim race guard
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_claim_ride_payment_processing_raises_when_supabase_unconfigured():
    with patch("repositories.ride_repo.supabase", None):
        from repositories.ride_repo import claim_ride_payment_processing

        with pytest.raises(RuntimeError):
            await claim_ride_payment_processing("r1")


@pytest.mark.asyncio
async def test_claim_ride_payment_processing_returns_true_when_row_claimed():
    mock_sb = MagicMock()
    response = MagicMock()
    response.data = [{"id": "r1", "payment_status": "processing"}]
    mock_sb.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value = response
    with patch("repositories.ride_repo.supabase", mock_sb):
        from repositories.ride_repo import claim_ride_payment_processing

        assert await claim_ride_payment_processing("r1") is True


@pytest.mark.asyncio
async def test_claim_ride_payment_processing_returns_false_when_already_claimed():
    """Race-guard: a concurrent claim already flipped payment_status away from
    'pending', so the .eq('payment_status', 'pending') filter matches 0 rows —
    this caller must back off (return 409), never proceed to charge again."""
    mock_sb = MagicMock()
    response = MagicMock()
    response.data = []
    mock_sb.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value = response
    with patch("repositories.ride_repo.supabase", mock_sb):
        from repositories.ride_repo import claim_ride_payment_processing

        assert await claim_ride_payment_processing("r1") is False


# ─────────────────────────────────────────────────────────────────────────────
# get_rides_for_user / get_rides_for_driver
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_rides_for_user_returns_empty_when_supabase_unconfigured():
    with patch("repositories.ride_repo.supabase", None):
        from repositories.ride_repo import get_rides_for_user

        assert await get_rides_for_user("rider1") == []


@pytest.mark.asyncio
async def test_get_rides_for_user_happy_path():
    mock_sb = MagicMock()
    response = MagicMock()
    response.data = [{"id": "r1"}, {"id": "r2"}]
    mock_sb.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = response
    with patch("repositories.ride_repo.supabase", mock_sb):
        from repositories.ride_repo import get_rides_for_user

        result = await get_rides_for_user("rider1")

    assert len(result) == 2


@pytest.mark.asyncio
async def test_get_rides_for_driver_returns_empty_when_supabase_unconfigured():
    with patch("repositories.ride_repo.supabase", None):
        from repositories.ride_repo import get_rides_for_driver

        assert await get_rides_for_driver("driver1") == []


@pytest.mark.asyncio
async def test_get_rides_for_driver_applies_status_filter():
    mock_sb = MagicMock()
    response = MagicMock()
    response.data = [{"id": "r1", "status": "completed"}]
    query = mock_sb.table.return_value.select.return_value.eq.return_value
    query.or_.return_value.order.return_value.limit.return_value.execute.return_value = response
    query.order.return_value.limit.return_value.execute.return_value = response
    with patch("repositories.ride_repo.supabase", mock_sb):
        from repositories.ride_repo import get_rides_for_driver

        result = await get_rides_for_driver("driver1", statuses=["completed"])

    assert len(result) == 1
    query.or_.assert_called_once()


@pytest.mark.asyncio
async def test_get_rides_for_driver_applies_date_range_filters():
    mock_sb = MagicMock()
    response = MagicMock()
    response.data = []
    query = mock_sb.table.return_value.select.return_value.eq.return_value
    query.gte.return_value.lt.return_value.order.return_value.limit.return_value.execute.return_value = response
    with patch("repositories.ride_repo.supabase", mock_sb):
        from repositories.ride_repo import get_rides_for_driver

        await get_rides_for_driver("driver1", from_date="2026-01-01", to_date="2026-02-01")

    query.gte.assert_called_once_with("created_at", "2026-01-01")


# ─────────────────────────────────────────────────────────────────────────────
# get_ride_count_by_date_range
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_ride_count_by_date_range_returns_zero_when_supabase_unconfigured():
    with patch("repositories.ride_repo.supabase", None):
        from repositories.ride_repo import get_ride_count_by_date_range

        assert await get_ride_count_by_date_range("2026-01-01", "2026-02-01") == 0


@pytest.mark.asyncio
async def test_get_ride_count_by_date_range_happy_path():
    mock_sb = MagicMock()
    response = MagicMock()
    response.count = 42
    mock_sb.table.return_value.select.return_value.limit.return_value.gte.return_value.lt.return_value.execute.return_value = response
    with patch("repositories.ride_repo.supabase", mock_sb):
        from repositories.ride_repo import get_ride_count_by_date_range

        assert await get_ride_count_by_date_range("2026-01-01", "2026-02-01") == 42


@pytest.mark.asyncio
async def test_get_ride_count_by_date_range_returns_zero_when_no_count_attr():
    mock_sb = MagicMock()
    response = MagicMock(spec=[])
    mock_sb.table.return_value.select.return_value.limit.return_value.gte.return_value.lt.return_value.execute.return_value = response
    with patch("repositories.ride_repo.supabase", mock_sb):
        from repositories.ride_repo import get_ride_count_by_date_range

        assert await get_ride_count_by_date_range("2026-01-01", "2026-02-01") == 0


# ─────────────────────────────────────────────────────────────────────────────
# get_ride_details_enriched
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_ride_details_enriched_returns_none_when_supabase_unconfigured():
    with patch("repositories.ride_repo.supabase", None):
        from repositories.ride_repo import get_ride_details_enriched

        assert await get_ride_details_enriched("r1") is None


@pytest.mark.asyncio
async def test_get_ride_details_enriched_returns_none_when_ride_not_found():
    mock_sb = MagicMock()
    response = MagicMock()
    response.data = []
    mock_sb.table.return_value.select.return_value.eq.return_value.execute.return_value = response
    with patch("repositories.ride_repo.supabase", mock_sb):
        from repositories.ride_repo import get_ride_details_enriched

        assert await get_ride_details_enriched("r1") is None


@pytest.mark.asyncio
async def test_get_ride_details_enriched_minimal_ride_no_rider_no_driver():
    """A ride with no rider_id/driver_id (e.g. orphaned/deleted-account edge
    case) must still return successfully with only ride-level fields
    populated — none of the rider/driver-guarded blocks should raise."""
    mock_sb = MagicMock()

    def _table_side_effect(name):
        m = MagicMock()
        response = MagicMock()
        response.data = []
        response.count = 0
        if name == "rides":
            row_response = MagicMock()
            row_response.data = [{"id": "r1", "rider_id": None, "driver_id": None}]
            m.select.return_value.eq.return_value.execute.return_value = row_response
        else:
            m.select.return_value.eq.return_value.order.return_value.execute.return_value = response
            m.select.return_value.eq.return_value.limit.return_value.execute.return_value = response
            m.select.return_value.eq.return_value.execute.return_value = response
        return m

    mock_sb.table.side_effect = _table_side_effect
    with patch("repositories.ride_repo.supabase", mock_sb):
        from repositories.ride_repo import get_ride_details_enriched

        result = await get_ride_details_enriched("r1")

    assert result["id"] == "r1"
    assert result["flags"] == []
    assert result["complaints"] == []
    assert result["offers"] == []
    assert result["incentive_claims"] == []
    assert result["incentive_total"] == 0.0


@pytest.mark.asyncio
async def test_get_ride_details_enriched_incentive_claims_failure_degrades_gracefully():
    """A DB failure fetching incentive claims must not fail the whole
    enriched-detail read — this is explicitly caught and degraded per the
    module's own try/except around _get_incentive_claims."""
    mock_sb = MagicMock()

    def _table_side_effect(name):
        m = MagicMock()
        response = MagicMock()
        response.data = []
        response.count = 0
        if name == "rides":
            row_response = MagicMock()
            row_response.data = [{"id": "r1", "rider_id": None, "driver_id": None}]
            m.select.return_value.eq.return_value.execute.return_value = row_response
        elif name == "ride_incentive_claims":
            m.select.return_value.eq.return_value.order.return_value.execute.side_effect = RuntimeError("db down")
        else:
            m.select.return_value.eq.return_value.order.return_value.execute.return_value = response
            m.select.return_value.eq.return_value.limit.return_value.execute.return_value = response
            m.select.return_value.eq.return_value.execute.return_value = response
        return m

    mock_sb.table.side_effect = _table_side_effect
    with patch("repositories.ride_repo.supabase", mock_sb):
        from repositories.ride_repo import get_ride_details_enriched

        result = await get_ride_details_enriched("r1")

    assert result["incentive_claims"] == []
    assert result["incentive_total"] == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# create_flag — the auto-ban-at-3-active-flags branch
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_flag_raises_when_supabase_unconfigured():
    with patch("repositories.ride_repo.supabase", None):
        from repositories.ride_repo import create_flag

        with pytest.raises(RuntimeError):
            await create_flag({"target_type": "rider", "target_id": "u1"})


@pytest.mark.asyncio
async def test_create_flag_below_threshold_does_not_auto_ban():
    mock_sb = MagicMock()

    def _table_side_effect(name):
        m = MagicMock()
        if name == "flags":
            insert_resp = MagicMock()
            insert_resp.data = [{"id": "f1", "target_type": "rider", "target_id": "u1"}]
            m.insert.return_value.execute.return_value = insert_resp
            count_resp = MagicMock()
            count_resp.count = 1
            m.select.return_value.limit.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value = (
                count_resp
            )
        return m

    mock_sb.table.side_effect = _table_side_effect
    with patch("repositories.ride_repo.supabase", mock_sb):
        from repositories.ride_repo import create_flag

        result = await create_flag({"target_type": "rider", "target_id": "u1"})

    assert result["auto_banned"] is False
    assert result["active_flag_count"] == 1


@pytest.mark.asyncio
async def test_create_flag_at_threshold_auto_bans_rider():
    mock_sb = MagicMock()
    ban_calls = []

    def _table_side_effect(name):
        m = MagicMock()
        if name == "flags":
            insert_resp = MagicMock()
            insert_resp.data = [{"id": "f1", "target_type": "rider", "target_id": "u1"}]
            m.insert.return_value.execute.return_value = insert_resp
            count_resp = MagicMock()
            count_resp.count = 3
            m.select.return_value.limit.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value = (
                count_resp
            )
        elif name == "users":
            ban_calls.append(name)
            update_resp = MagicMock()
            m.update.return_value.eq.return_value.execute.return_value = update_resp
        return m

    mock_sb.table.side_effect = _table_side_effect
    with patch("repositories.ride_repo.supabase", mock_sb):
        from repositories.ride_repo import create_flag

        result = await create_flag({"target_type": "rider", "target_id": "u1"})

    assert result["auto_banned"] is True
    assert ban_calls == ["users"]


@pytest.mark.asyncio
async def test_create_flag_at_threshold_auto_bans_driver_via_drivers_table():
    mock_sb = MagicMock()
    ban_calls = []

    def _table_side_effect(name):
        m = MagicMock()
        if name == "flags":
            insert_resp = MagicMock()
            insert_resp.data = [{"id": "f1", "target_type": "driver", "target_id": "d1"}]
            m.insert.return_value.execute.return_value = insert_resp
            count_resp = MagicMock()
            count_resp.count = 5
            m.select.return_value.limit.return_value.eq.return_value.eq.return_value.eq.return_value.execute.return_value = (
                count_resp
            )
        elif name == "drivers":
            ban_calls.append(name)
            update_resp = MagicMock()
            m.update.return_value.eq.return_value.execute.return_value = update_resp
        return m

    mock_sb.table.side_effect = _table_side_effect
    with patch("repositories.ride_repo.supabase", mock_sb):
        from repositories.ride_repo import create_flag

        result = await create_flag({"target_type": "driver", "target_id": "d1"})

    assert result["auto_banned"] is True
    assert ban_calls == ["drivers"]


# ─────────────────────────────────────────────────────────────────────────────
# create_complaint / resolve_complaint
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_complaint_raises_when_supabase_unconfigured():
    with patch("repositories.ride_repo.supabase", None):
        from repositories.ride_repo import create_complaint

        with pytest.raises(RuntimeError):
            await create_complaint({"ride_id": "r1"})


@pytest.mark.asyncio
async def test_create_complaint_happy_path():
    mock_sb = _table_mock(row={"id": "c1", "ride_id": "r1"})
    with patch("repositories.ride_repo.supabase", mock_sb):
        from repositories.ride_repo import create_complaint

        result = await create_complaint({"ride_id": "r1"})

    assert result["id"] == "c1"


@pytest.mark.asyncio
async def test_resolve_complaint_returns_none_when_supabase_unconfigured():
    with patch("repositories.ride_repo.supabase", None):
        from repositories.ride_repo import resolve_complaint

        assert await resolve_complaint("c1", {"status": "resolved"}) is None


@pytest.mark.asyncio
async def test_resolve_complaint_happy_path():
    mock_sb = _table_mock(row={"id": "c1", "status": "resolved"})
    with patch("repositories.ride_repo.supabase", mock_sb):
        from repositories.ride_repo import resolve_complaint

        result = await resolve_complaint("c1", {"status": "resolved"})

    assert result["status"] == "resolved"


# ─────────────────────────────────────────────────────────────────────────────
# create_lost_and_found / update_lost_and_found
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_lost_and_found_raises_when_supabase_unconfigured():
    with patch("repositories.ride_repo.supabase", None):
        from repositories.ride_repo import create_lost_and_found

        with pytest.raises(RuntimeError):
            await create_lost_and_found({"ride_id": "r1"})


@pytest.mark.asyncio
async def test_create_lost_and_found_happy_path():
    mock_sb = _table_mock(row={"id": "l1", "ride_id": "r1"})
    with patch("repositories.ride_repo.supabase", mock_sb):
        from repositories.ride_repo import create_lost_and_found

        result = await create_lost_and_found({"ride_id": "r1"})

    assert result["id"] == "l1"


@pytest.mark.asyncio
async def test_update_lost_and_found_returns_none_when_supabase_unconfigured():
    with patch("repositories.ride_repo.supabase", None):
        from repositories.ride_repo import update_lost_and_found

        assert await update_lost_and_found("l1", {"status": "returned"}) is None


@pytest.mark.asyncio
async def test_update_lost_and_found_happy_path():
    mock_sb = _table_mock(row={"id": "l1", "status": "returned"})
    with patch("repositories.ride_repo.supabase", mock_sb):
        from repositories.ride_repo import update_lost_and_found

        result = await update_lost_and_found("l1", {"status": "returned"})

    assert result["status"] == "returned"


# ─────────────────────────────────────────────────────────────────────────────
# get_ride_location_trail
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_ride_location_trail_returns_empty_when_supabase_unconfigured():
    with patch("repositories.ride_repo.supabase", None):
        from repositories.ride_repo import get_ride_location_trail

        assert await get_ride_location_trail("r1") == []


@pytest.mark.asyncio
async def test_get_ride_location_trail_happy_path():
    mock_sb = MagicMock()
    response = MagicMock()
    response.data = [{"lat": 45.0, "lng": -73.0}]
    mock_sb.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = response
    with patch("repositories.ride_repo.supabase", mock_sb):
        from repositories.ride_repo import get_ride_location_trail

        result = await get_ride_location_trail("r1")

    assert len(result) == 1


# ─────────────────────────────────────────────────────────────────────────────
# get_live_ride_data
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_live_ride_data_returns_none_when_supabase_unconfigured():
    with patch("repositories.ride_repo.supabase", None):
        from repositories.ride_repo import get_live_ride_data

        assert await get_live_ride_data("r1") is None


@pytest.mark.asyncio
async def test_get_live_ride_data_returns_none_when_ride_not_found():
    mock_sb = _table_mock(rows=[])
    with patch("repositories.ride_repo.supabase", mock_sb):
        from repositories.ride_repo import get_live_ride_data

        assert await get_live_ride_data("r1") is None


@pytest.mark.asyncio
async def test_get_live_ride_data_populates_driver_and_rider_fields():
    mock_sb = MagicMock()

    def _table_side_effect(name):
        m = MagicMock()
        if name == "rides":
            resp = MagicMock()
            resp.data = [{"id": "r1", "driver_id": "d1", "rider_id": "u1"}]
            m.select.return_value.eq.return_value.execute.return_value = resp
        elif name == "drivers":
            resp = MagicMock()
            resp.data = [{"user_id": "du1", "name": "Driver Name", "phone": "555", "lat": 45.0, "lng": -73.0}]
            m.select.return_value.eq.return_value.execute.return_value = resp
        elif name == "users":
            resp = MagicMock()
            resp.data = [{"profile_image": "", "first_name": "Rider", "last_name": "One", "phone": "555"}]
            m.select.return_value.eq.return_value.execute.return_value = resp
        return m

    mock_sb.table.side_effect = _table_side_effect
    with patch("repositories.ride_repo.supabase", mock_sb):
        from repositories.ride_repo import get_live_ride_data

        result = await get_live_ride_data("r1")

    assert result["driver_current_lat"] == 45.0
    assert result["driver_name"] == "Driver Name"
    assert result["rider_name"] == "Rider One"


@pytest.mark.asyncio
async def test_get_live_ride_data_no_driver_no_rider_ids():
    mock_sb = _table_mock(row={"id": "r1", "driver_id": None, "rider_id": None})
    with patch("repositories.ride_repo.supabase", mock_sb):
        from repositories.ride_repo import get_live_ride_data

        result = await get_live_ride_data("r1")

    assert result["id"] == "r1"
    assert "driver_current_lat" not in result
    assert "rider_name" not in result


# ─────────────────────────────────────────────────────────────────────────────
# get_user_status
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_user_status_returns_none_when_supabase_unconfigured():
    with patch("repositories.ride_repo.supabase", None):
        from repositories.ride_repo import get_user_status

        assert await get_user_status("u1") is None


@pytest.mark.asyncio
async def test_get_user_status_returns_none_when_user_not_found():
    mock_sb = _table_mock(rows=[])
    with patch("repositories.ride_repo.supabase", mock_sb):
        from repositories.ride_repo import get_user_status

        assert await get_user_status("u1") is None


@pytest.mark.asyncio
async def test_get_user_status_happy_path():
    mock_sb = _table_mock(row={"status": "active"})
    with patch("repositories.ride_repo.supabase", mock_sb):
        from repositories.ride_repo import get_user_status

        assert await get_user_status("u1") == "active"


@pytest.mark.asyncio
async def test_get_user_status_defaults_to_active_when_status_key_missing():
    mock_sb = _table_mock(row={"id": "u1"})
    with patch("repositories.ride_repo.supabase", mock_sb):
        from repositories.ride_repo import get_user_status

        assert await get_user_status("u1") == "active"


# ─────────────────────────────────────────────────────────────────────────────
# get_flags_for_target
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_flags_for_target_returns_empty_when_supabase_unconfigured():
    with patch("repositories.ride_repo.supabase", None):
        from repositories.ride_repo import get_flags_for_target

        assert await get_flags_for_target("rider", "u1") == []


@pytest.mark.asyncio
async def test_get_flags_for_target_happy_path():
    mock_sb = MagicMock()
    response = MagicMock()
    response.data = [{"id": "f1", "target_type": "rider", "target_id": "u1"}]
    mock_sb.table.return_value.select.return_value.eq.return_value.eq.return_value.eq.return_value.order.return_value.execute.return_value = response
    with patch("repositories.ride_repo.supabase", mock_sb):
        from repositories.ride_repo import get_flags_for_target

        result = await get_flags_for_target("rider", "u1")

    assert len(result) == 1
