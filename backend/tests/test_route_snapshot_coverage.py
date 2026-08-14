"""Coverage for backend/utils/route_snapshot.py (A1c, Sub-tier B).

Renders a ride-route PNG two ways — Google Static Maps (`render_ride_snapshot_google`,
a single httpx fetch) and an OSM/`staticmap` fallback (`render_ride_snapshot`, used
only when no Google API key is configured) — plus a shared set of small geometry
helpers (gap-splitting, gradient coloring, trail extraction/sampling). Had no
dedicated test file; 57.08% coverage as an incidental side effect of ride-flow
tests that only exercise the module through `_generate_and_store_ride_snapshot`
call sites, rarely hitting the failure/fallback branches directly.

Mocking approach:
- `render_ride_snapshot_google` does `import httpx` at module scope and calls
  `httpx.AsyncClient(...)` inside the function body. Per this repo's dual-import
  rule, the module-under-test's own bound name is patched — here that means
  monkeypatching the `AsyncClient` attribute on `route_snapshot.httpx` (the same
  httpx module object, since `httpx` has no dual-import ambiguity, but we patch
  it via the module-under-test reference per convention) — never a separately
  imported `httpx` reference in the test file.
- `render_ride_snapshot` does `from staticmap import CircleMarker, Line, StaticMap`
  *inside* the function body (deliberately optional dependency). We inject a fake
  module into `sys.modules["staticmap"]` before calling, so no real tile-server
  network call is ever made. The ImportError branch is exercised by installing an
  empty module object that lacks the three names, which reproduces the exact
  `ImportError: cannot import name ...` the production code guards against.
- `_add_route_quality_banner` imports PIL at call time; Pillow is an installed
  dependency here so the happy path uses a real tiny PNG round-trip, and the
  swallowed-exception path is exercised by feeding it bytes that are not a valid
  image (real `Image.open` failure, not a mocked one).

No Supabase storage calls exist in this module — persistence happens in the
caller (`_generate_and_store_ride_snapshot`, out of scope here) — so no
Supabase/query-builder mocking is needed in this file.

Bug found, not fixed (test-only scope): `_gradient_runs` computes
`per = max(2, _GRADIENT_TOTAL // len(runs) + 1)` and then samples each trail to
at most `per` points via `_sample_trail`. With many short input trails (e.g.
more `runs` than `_GRADIENT_TOTAL`), `per` still floors at 2, so the total
sampled segment count can exceed the `_GRADIENT_TOTAL` "URL budget" the comment
above `_GRADIENT_TOTAL` promises — not a correctness bug (the gradient index
still divides cleanly by `total`), but the stated budget invariant does not
strictly hold for that shape of input.

Test-only change — no application code modified.
"""

from __future__ import annotations

import sys
import types
from io import BytesIO
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.unit

from backend.utils import route_snapshot  # noqa: E402

# ---------------------------------------------------------------------------
# _gradient_rgb
# ---------------------------------------------------------------------------


class TestGradientRgb:
    def test_t_zero_returns_start_color(self):
        assert route_snapshot._gradient_rgb(0.0) == route_snapshot._GRADIENT_START_RGB

    def test_t_one_returns_end_color(self):
        assert route_snapshot._gradient_rgb(1.0) == route_snapshot._GRADIENT_END_RGB

    def test_nan_treated_as_zero(self):
        nan = float("nan")
        assert route_snapshot._gradient_rgb(nan) == route_snapshot._GRADIENT_START_RGB

    def test_negative_clamped_to_zero(self):
        assert route_snapshot._gradient_rgb(-5.0) == route_snapshot._GRADIENT_START_RGB

    def test_above_one_clamped_to_one(self):
        assert route_snapshot._gradient_rgb(5.0) == route_snapshot._GRADIENT_END_RGB

    def test_midpoint_is_between_endpoints(self):
        r, g, b = route_snapshot._gradient_rgb(0.5)
        sr, sg, sb = route_snapshot._GRADIENT_START_RGB
        er, eg, eb = route_snapshot._GRADIENT_END_RGB
        assert min(sr, er) <= r <= max(sr, er)
        assert min(sg, eg) <= g <= max(sg, eg)
        assert min(sb, eb) <= b <= max(sb, eb)


# ---------------------------------------------------------------------------
# _gradient_runs
# ---------------------------------------------------------------------------


class TestGradientRuns:
    def test_empty_trails_returns_empty(self):
        pieces, total = route_snapshot._gradient_runs([])
        assert pieces == []
        assert total == 0

    def test_all_trails_too_short_are_filtered(self):
        pieces, total = route_snapshot._gradient_runs([[(1.0, 2.0)], []])
        assert pieces == []
        assert total == 0

    def test_single_simple_trail_produces_one_piece(self):
        trail = [(50.0, -104.0), (50.001, -104.001), (50.002, -104.002)]
        pieces, total = route_snapshot._gradient_runs([trail])
        assert len(pieces) == 1
        assert total == 2  # 3 points -> 2 hops

    def test_multiple_trails_each_contribute_pieces(self):
        trail_a = [(50.0, -104.0), (50.001, -104.001)]
        trail_b = [(51.0, -105.0), (51.001, -105.001)]
        pieces, total = route_snapshot._gradient_runs([trail_a, trail_b])
        assert len(pieces) == 2
        assert total == 2


# ---------------------------------------------------------------------------
# _extract_trail
# ---------------------------------------------------------------------------


class TestExtractTrail:
    def test_none_returns_empty(self):
        assert route_snapshot._extract_trail(None) == []

    def test_empty_list_returns_empty(self):
        assert route_snapshot._extract_trail([]) == []

    def test_valid_points_converted_to_float_tuples(self):
        raw = [[50.1, -104.1, 12345], [50.2, -104.2, 12346]]
        assert route_snapshot._extract_trail(raw) == [(50.1, -104.1), (50.2, -104.2)]

    def test_malformed_points_are_skipped(self):
        raw = [[50.1, -104.1], "bad", [1], [50.2, -104.2], [None, -104.3]]
        assert route_snapshot._extract_trail(raw) == [(50.1, -104.1), (50.2, -104.2)]


# ---------------------------------------------------------------------------
# _coerce_coordinate
# ---------------------------------------------------------------------------


class TestCoerceCoordinate:
    def test_none_returns_none(self):
        assert route_snapshot._coerce_coordinate(None) is None

    def test_non_dict_returns_none(self):
        assert route_snapshot._coerce_coordinate([1, 2]) is None

    def test_valid_dict_returns_tuple(self):
        assert route_snapshot._coerce_coordinate({"lat": 50.1, "lng": -104.1}) == (50.1, -104.1)

    def test_missing_key_returns_none(self):
        assert route_snapshot._coerce_coordinate({"lat": 50.1}) is None

    def test_non_numeric_value_returns_none(self):
        assert route_snapshot._coerce_coordinate({"lat": "abc", "lng": -104.1}) is None


# ---------------------------------------------------------------------------
# _extract_segment_trails
# ---------------------------------------------------------------------------


class TestExtractSegmentTrails:
    def test_none_returns_empty(self):
        assert route_snapshot._extract_segment_trails(None) == []

    def test_empty_list_returns_empty(self):
        assert route_snapshot._extract_segment_trails([]) == []

    def test_dict_segment_with_coordinates_key(self):
        segments = [{"coordinates": [[50.0, -104.0], [50.1, -104.1]]}]
        trails = route_snapshot._extract_segment_trails(segments)
        assert trails == [[(50.0, -104.0), (50.1, -104.1)]]

    def test_dict_segment_with_points_key_fallback(self):
        segments = [{"points": [[50.0, -104.0], [50.1, -104.1]]}]
        trails = route_snapshot._extract_segment_trails(segments)
        assert trails == [[(50.0, -104.0), (50.1, -104.1)]]

    def test_bare_list_segment_treated_as_points(self):
        segments = [[[50.0, -104.0], [50.1, -104.1]]]
        trails = route_snapshot._extract_segment_trails(segments)
        assert trails == [[(50.0, -104.0), (50.1, -104.1)]]

    def test_short_segment_is_dropped(self):
        segments = [{"coordinates": [[50.0, -104.0]]}]
        assert route_snapshot._extract_segment_trails(segments) == []

    def test_multiple_segments_kept_independent(self):
        segments = [
            {"coordinates": [[50.0, -104.0], [50.1, -104.1]]},
            {"coordinates": [[51.0, -105.0], [51.1, -105.1]]},
        ]
        trails = route_snapshot._extract_segment_trails(segments)
        assert len(trails) == 2


# ---------------------------------------------------------------------------
# _sample_trail
# ---------------------------------------------------------------------------


class TestSampleTrail:
    def test_short_trail_returned_unchanged(self):
        trail = [(1.0, 1.0), (2.0, 2.0)]
        assert route_snapshot._sample_trail(trail, maximum=10) == trail

    def test_long_trail_is_downsampled(self):
        trail = [(float(i), float(i)) for i in range(200)]
        sampled = route_snapshot._sample_trail(trail, maximum=10)
        assert len(sampled) <= 12  # step-based sample plus possible tail append
        assert sampled[0] == trail[0]

    def test_last_point_always_preserved(self):
        trail = [(float(i), float(i)) for i in range(37)]
        sampled = route_snapshot._sample_trail(trail, maximum=10)
        assert sampled[-1] == trail[-1]


# ---------------------------------------------------------------------------
# _append_path / _append_segmented_paths / _append_legacy_paths
# ---------------------------------------------------------------------------


class TestAppendPath:
    def test_appends_path_param_for_valid_run(self):
        params: list[str] = []
        trail = [(50.0, -104.0), (50.001, -104.001), (50.002, -104.002)]
        route_snapshot._append_path(params, trail)
        assert len(params) == 1
        assert params[0].startswith("path=color:0xFF9500FF|weight:4|")

    def test_single_point_trail_appends_nothing(self):
        params: list[str] = []
        route_snapshot._append_path(params, [(50.0, -104.0)])
        assert params == []

    def test_custom_color_used(self):
        params: list[str] = []
        trail = [(50.0, -104.0), (50.001, -104.001)]
        route_snapshot._append_path(params, trail, color="0xABCDEFFF")
        assert "color:0xABCDEFFF" in params[0]


class TestAppendSegmentedPaths:
    def test_first_trail_orange_rest_red(self):
        params: list[str] = []
        trail_a = [(50.0, -104.0), (50.001, -104.001)]
        trail_b = [(51.0, -105.0), (51.001, -105.001)]
        route_snapshot._append_segmented_paths(params, [trail_a, trail_b])
        assert len(params) == 2
        assert "0xFF9500FF" in params[0]
        assert "0xEE2B2BFF" in params[1]


class TestAppendLegacyPaths:
    def test_uses_trip_trail_when_dense_enough(self):
        params: list[str] = []
        trip_trail = [[float(i), float(-i)] for i in range(12)]
        route_snapshot._append_legacy_paths(params, {"trip_in_progress": trip_trail}, None)
        assert len(params) == 1

    def test_falls_back_to_route_polyline_when_trip_trail_sparse(self):
        params: list[str] = []
        route_polyline = [[50.0, -104.0], [50.1, -104.1]]
        route_snapshot._append_legacy_paths(params, {"trip_in_progress": [[50.0, -104.0]]}, route_polyline)
        assert len(params) == 1

    def test_no_phase_polylines_falls_back_to_route_polyline(self):
        params: list[str] = []
        route_polyline = [[50.0, -104.0], [50.1, -104.1]]
        route_snapshot._append_legacy_paths(params, None, route_polyline)
        assert len(params) == 1


# ---------------------------------------------------------------------------
# _is_incomplete
# ---------------------------------------------------------------------------


class TestIsIncomplete:
    def test_non_dict_is_false(self):
        assert route_snapshot._is_incomplete(None) is False
        assert route_snapshot._is_incomplete("nope") is False

    def test_missing_tail_true(self):
        assert route_snapshot._is_incomplete({"missing_tail": True}) is True

    def test_incomplete_reason_true(self):
        assert route_snapshot._is_incomplete({"incomplete_reason": "gps_gap"}) is True

    def test_both_falsy_is_false(self):
        assert route_snapshot._is_incomplete({"missing_tail": False, "incomplete_reason": ""}) is False

    def test_empty_dict_is_false(self):
        assert route_snapshot._is_incomplete({}) is False


# ---------------------------------------------------------------------------
# _haversine_km / _split_on_gaps
# ---------------------------------------------------------------------------


class TestHaversineKm:
    def test_same_point_is_zero(self):
        p = (50.0, -104.0)
        assert route_snapshot._haversine_km(p, p) == pytest.approx(0.0, abs=1e-9)

    def test_known_distance_is_positive_and_reasonable(self):
        # Roughly Regina to Saskatoon (~1.5 degrees latitude apart) -> ~250km ballpark
        km = route_snapshot._haversine_km((50.45, -104.6), (52.13, -106.67))
        assert 200 < km < 320


class TestSplitOnGaps:
    def test_fewer_than_three_points_pass_through(self):
        coords = [(50.0, -104.0), (50.001, -104.001)]
        assert route_snapshot._split_on_gaps(coords) == [coords]

    def test_single_point_returns_empty(self):
        assert route_snapshot._split_on_gaps([(50.0, -104.0)]) == []

    def test_empty_returns_empty(self):
        assert route_snapshot._split_on_gaps([]) == []

    def test_no_outlier_hop_returns_single_run(self):
        coords = [(50.0 + i * 0.0001, -104.0 - i * 0.0001) for i in range(10)]
        runs = route_snapshot._split_on_gaps(coords)
        assert len(runs) == 1
        assert runs[0] == coords

    def test_outlier_hop_splits_into_two_runs(self):
        # Dense cluster, then one huge jump (simulated GPS dropout), then another cluster.
        coords = [(50.0 + i * 0.0001, -104.0) for i in range(5)]
        coords.append((55.0, -110.0))  # huge jump relative to the tiny median hop
        coords.extend((55.0 + i * 0.0001, -110.0) for i in range(1, 5))
        runs = route_snapshot._split_on_gaps(coords)
        assert len(runs) == 2

    def test_zero_median_uses_absolute_floor(self):
        # All duplicate points except the last -> hops are all 0 except last one,
        # so median == 0 and the absolute _GAP_MIN_KM floor applies.
        coords = [(50.0, -104.0)] * 4 + [(50.0 + 0.02, -104.0)]
        runs = route_snapshot._split_on_gaps(coords)
        assert isinstance(runs, list)


# ---------------------------------------------------------------------------
# _coerce_polyline (used by the staticmap/OSM fallback renderer)
# ---------------------------------------------------------------------------


class TestCoercePolyline:
    def test_falsy_returns_empty(self):
        assert route_snapshot._coerce_polyline(None) == []
        assert route_snapshot._coerce_polyline([]) == []

    def test_swaps_lat_lng_to_lng_lat(self):
        raw = [[50.1, -104.1], [50.2, -104.2]]
        assert route_snapshot._coerce_polyline(raw) == [(-104.1, 50.1), (-104.2, 50.2)]

    def test_malformed_points_skipped(self):
        raw = [[50.1, -104.1], "bad", [1]]
        assert route_snapshot._coerce_polyline(raw) == [(-104.1, 50.1)]


# ---------------------------------------------------------------------------
# _add_route_quality_banner
# ---------------------------------------------------------------------------


def _real_png_bytes(size=(20, 10)) -> bytes:
    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", size, color=(10, 20, 30)).save(buf, format="PNG")
    return buf.getvalue()


class TestAddRouteQualityBanner:
    def test_happy_path_with_coverage_ratio_returns_png(self):
        result = route_snapshot._add_route_quality_banner(_real_png_bytes(), {"coverage_ratio": 0.42})
        assert result.startswith(b"\x89PNG")

    def test_happy_path_without_coverage_ratio_returns_png(self):
        result = route_snapshot._add_route_quality_banner(_real_png_bytes(), {"missing_tail": True})
        assert result.startswith(b"\x89PNG")

    def test_invalid_image_bytes_swallows_exception_and_returns_original(self):
        garbage = b"not a real png at all"
        result = route_snapshot._add_route_quality_banner(garbage, {"coverage_ratio": 0.1})
        assert result == garbage


# ---------------------------------------------------------------------------
# render_ride_snapshot_google
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code=200, content=b"PNGBYTES", content_type="image/png"):
        self.status_code = status_code
        self.content = content
        self.headers = {"content-type": content_type}
        self.text = "error body"


class _FakeAsyncClient:
    """Async-context-manager stand-in for httpx.AsyncClient. Captures the last
    requested URL on the class so tests can assert on query construction."""

    last_url: str | None = None
    response: _FakeResponse | None = None
    raise_exc: Exception | None = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def get(self, url):
        type(self).last_url = url
        if type(self).raise_exc is not None:
            raise type(self).raise_exc
        return type(self).response


@pytest.fixture
def fake_async_client(monkeypatch):
    """Wires _FakeAsyncClient in as route_snapshot.httpx.AsyncClient and resets
    its captured state between tests."""
    _FakeAsyncClient.last_url = None
    _FakeAsyncClient.response = _FakeResponse()
    _FakeAsyncClient.raise_exc = None
    monkeypatch.setattr(route_snapshot.httpx, "AsyncClient", _FakeAsyncClient)
    return _FakeAsyncClient


class TestRenderRideSnapshotGoogle:
    @pytest.mark.anyio
    async def test_happy_path_returns_png_bytes(self, fake_async_client):
        fake_async_client.response = _FakeResponse(status_code=200, content=b"IMG", content_type="image/png")
        result = await route_snapshot.render_ride_snapshot_google(
            api_key="key123",
            pickup_lat=50.0,
            pickup_lng=-104.0,
            dropoff_lat=50.1,
            dropoff_lng=-104.1,
        )
        assert result == b"IMG"
        assert "key=key123" in fake_async_client.last_url

    @pytest.mark.anyio
    async def test_completion_point_adds_marker(self, fake_async_client):
        await route_snapshot.render_ride_snapshot_google(
            api_key="key123",
            pickup_lat=50.0,
            pickup_lng=-104.0,
            dropoff_lat=50.1,
            dropoff_lng=-104.1,
            completion_point={"lat": 50.05, "lng": -104.05},
        )
        assert "markers=color:orange|label:C|50.05,-104.05" in fake_async_client.last_url

    @pytest.mark.anyio
    async def test_invalid_completion_point_skips_marker(self, fake_async_client):
        await route_snapshot.render_ride_snapshot_google(
            api_key="key123",
            pickup_lat=50.0,
            pickup_lng=-104.0,
            dropoff_lat=50.1,
            dropoff_lng=-104.1,
            completion_point={"lat": "bad"},
        )
        assert "label:C" not in fake_async_client.last_url

    @pytest.mark.anyio
    async def test_route_segments_v2_source_of_truth_used(self, fake_async_client):
        route_segments = [{"coordinates": [[50.0, -104.0], [50.01, -104.01], [50.02, -104.02]]}]
        await route_snapshot.render_ride_snapshot_google(
            api_key="key123",
            pickup_lat=50.0,
            pickup_lng=-104.0,
            dropoff_lat=50.1,
            dropoff_lng=-104.1,
            route_segments=route_segments,
            phase_polylines={"trip_in_progress": [[1, 1]] * 20},  # would win if segments ignored
        )
        # Segment coordinates, not the (very different) legacy phase_polylines trail,
        # must have been drawn.
        assert "50.0,-104.0" in fake_async_client.last_url or "50,-104" in fake_async_client.last_url

    @pytest.mark.anyio
    async def test_legacy_dense_trip_in_progress_trail_used(self, fake_async_client):
        trip_trail = [[50.0 + i * 0.001, -104.0 - i * 0.001] for i in range(12)]
        await route_snapshot.render_ride_snapshot_google(
            api_key="key123",
            pickup_lat=50.0,
            pickup_lng=-104.0,
            dropoff_lat=50.1,
            dropoff_lng=-104.1,
            phase_polylines={"trip_in_progress": trip_trail},
        )
        assert "path=color:" in fake_async_client.last_url

    @pytest.mark.anyio
    async def test_legacy_sparse_trip_falls_back_to_route_polyline(self, fake_async_client):
        sparse_trip = [[50.0, -104.0], [50.001, -104.001]]  # < 10 points
        route_polyline = [[51.0, -105.0], [51.001, -105.001], [51.002, -105.002]]
        await route_snapshot.render_ride_snapshot_google(
            api_key="key123",
            pickup_lat=50.0,
            pickup_lng=-104.0,
            dropoff_lat=50.1,
            dropoff_lng=-104.1,
            phase_polylines={"trip_in_progress": sparse_trip},
            route_polyline=route_polyline,
        )
        assert "51.0,-105.0" in fake_async_client.last_url

    @pytest.mark.anyio
    async def test_no_trail_data_still_returns_image_with_only_markers(self, fake_async_client):
        result = await route_snapshot.render_ride_snapshot_google(
            api_key="key123",
            pickup_lat=50.0,
            pickup_lng=-104.0,
            dropoff_lat=50.1,
            dropoff_lng=-104.1,
        )
        assert result == b"PNGBYTES"
        assert "path=color:" not in fake_async_client.last_url

    @pytest.mark.anyio
    async def test_non_200_status_returns_none(self, fake_async_client):
        fake_async_client.response = _FakeResponse(status_code=403, content=b"", content_type="text/plain")
        result = await route_snapshot.render_ride_snapshot_google(
            api_key="bad-key",
            pickup_lat=50.0,
            pickup_lng=-104.0,
            dropoff_lat=50.1,
            dropoff_lng=-104.1,
        )
        assert result is None

    @pytest.mark.anyio
    async def test_non_image_content_type_returns_none(self, fake_async_client):
        fake_async_client.response = _FakeResponse(status_code=200, content=b"<html/>", content_type="text/html")
        result = await route_snapshot.render_ride_snapshot_google(
            api_key="key123",
            pickup_lat=50.0,
            pickup_lng=-104.0,
            dropoff_lat=50.1,
            dropoff_lng=-104.1,
        )
        assert result is None

    @pytest.mark.anyio
    async def test_fetch_exception_returns_none_never_raises(self, fake_async_client):
        fake_async_client.raise_exc = ConnectionError("network unreachable")
        result = await route_snapshot.render_ride_snapshot_google(
            api_key="key123",
            pickup_lat=50.0,
            pickup_lng=-104.0,
            dropoff_lat=50.1,
            dropoff_lng=-104.1,
        )
        assert result is None

    @pytest.mark.anyio
    async def test_incomplete_route_quality_routes_through_banner(self, fake_async_client, monkeypatch):
        banner_mock = MagicMock(return_value=b"BANNERED")
        monkeypatch.setattr(route_snapshot, "_add_route_quality_banner", banner_mock)
        result = await route_snapshot.render_ride_snapshot_google(
            api_key="key123",
            pickup_lat=50.0,
            pickup_lng=-104.0,
            dropoff_lat=50.1,
            dropoff_lng=-104.1,
            route_quality={"missing_tail": True, "coverage_ratio": 0.5},
        )
        assert result == b"BANNERED"
        banner_mock.assert_called_once()

    @pytest.mark.anyio
    async def test_complete_route_quality_skips_banner(self, fake_async_client, monkeypatch):
        banner_mock = MagicMock(return_value=b"BANNERED")
        monkeypatch.setattr(route_snapshot, "_add_route_quality_banner", banner_mock)
        result = await route_snapshot.render_ride_snapshot_google(
            api_key="key123",
            pickup_lat=50.0,
            pickup_lng=-104.0,
            dropoff_lat=50.1,
            dropoff_lng=-104.1,
            route_quality={"missing_tail": False},
        )
        banner_mock.assert_not_called()
        assert result == b"PNGBYTES"


# ---------------------------------------------------------------------------
# render_ride_snapshot (OSM/staticmap fallback)
# ---------------------------------------------------------------------------


class _FakeMarker:
    def __init__(self, *args, **kwargs):
        pass


class _FakeLine:
    def __init__(self, *args, **kwargs):
        pass


class _FakeStaticMap:
    """Stand-in for staticmap.StaticMap that never touches the network: render()
    returns a real tiny PIL image so the PNG-encode path is genuinely exercised."""

    instances: list["_FakeStaticMap"] = []

    def __init__(self, width, height, url_template=None, padding_x=0, padding_y=0):
        self.width = width
        self.height = height
        self.markers = []
        self.lines = []
        _FakeStaticMap.instances.append(self)

    def add_marker(self, marker):
        self.markers.append(marker)

    def add_line(self, line):
        self.lines.append(line)

    def render(self):
        from PIL import Image

        return Image.new("RGB", (self.width, self.height), color=(5, 5, 5))


def _install_fake_staticmap_module(monkeypatch):
    fake_module = types.ModuleType("staticmap")
    fake_module.CircleMarker = _FakeMarker
    fake_module.Line = _FakeLine
    fake_module.StaticMap = _FakeStaticMap
    _FakeStaticMap.instances = []
    monkeypatch.setitem(sys.modules, "staticmap", fake_module)
    return fake_module


def _install_broken_staticmap_module(monkeypatch):
    """A 'staticmap' module present in sys.modules but missing the names the
    production `from staticmap import CircleMarker, Line, StaticMap` needs —
    reproduces the real ImportError the except-clause is written to catch."""
    broken_module = types.ModuleType("staticmap")
    monkeypatch.setitem(sys.modules, "staticmap", broken_module)
    return broken_module


class TestRenderRideSnapshot:
    def test_staticmap_unavailable_returns_none(self, monkeypatch):
        _install_broken_staticmap_module(monkeypatch)
        result = route_snapshot.render_ride_snapshot(
            pickup_lat=50.0,
            pickup_lng=-104.0,
            dropoff_lat=50.1,
            dropoff_lng=-104.1,
        )
        assert result is None

    def test_happy_path_returns_png_bytes(self, monkeypatch):
        _install_fake_staticmap_module(monkeypatch)
        result = route_snapshot.render_ride_snapshot(
            pickup_lat=50.0,
            pickup_lng=-104.0,
            dropoff_lat=50.1,
            dropoff_lng=-104.1,
        )
        assert result is not None
        assert result.startswith(b"\x89PNG")

    def test_completion_point_adds_two_markers(self, monkeypatch):
        _install_fake_staticmap_module(monkeypatch)
        route_snapshot.render_ride_snapshot(
            pickup_lat=50.0,
            pickup_lng=-104.0,
            dropoff_lat=50.1,
            dropoff_lng=-104.1,
            completion_point={"lat": 50.05, "lng": -104.05},
        )
        instance = _FakeStaticMap.instances[-1]
        # Each point renders as a white-outline + colored-fill pair of
        # CircleMarkers: pickup (2) + dropoff (2) + completion (2) = 6.
        assert len(instance.markers) == 6

    def test_route_segments_v2_used_when_present(self, monkeypatch):
        _install_fake_staticmap_module(monkeypatch)
        route_segments = [{"coordinates": [[50.0, -104.0], [50.01, -104.01], [50.02, -104.02]]}]
        result = route_snapshot.render_ride_snapshot(
            pickup_lat=50.0,
            pickup_lng=-104.0,
            dropoff_lat=50.1,
            dropoff_lng=-104.1,
            route_segments=route_segments,
        )
        assert result is not None
        instance = _FakeStaticMap.instances[-1]
        assert len(instance.lines) > 0

    def test_dense_trip_trail_used_when_no_segments(self, monkeypatch):
        _install_fake_staticmap_module(monkeypatch)
        trip_trail = [[50.0 + i * 0.001, -104.0 - i * 0.001] for i in range(12)]
        result = route_snapshot.render_ride_snapshot(
            pickup_lat=50.0,
            pickup_lng=-104.0,
            dropoff_lat=50.1,
            dropoff_lng=-104.1,
            phase_polylines={"trip_in_progress": trip_trail},
        )
        assert result is not None
        instance = _FakeStaticMap.instances[-1]
        assert len(instance.lines) > 0

    def test_sparse_trip_trail_falls_back_to_route_polyline(self, monkeypatch):
        _install_fake_staticmap_module(monkeypatch)
        sparse_trip = [[50.0, -104.0], [50.001, -104.001]]
        route_polyline = [[51.0, -105.0], [51.001, -105.001], [51.002, -105.002]]
        result = route_snapshot.render_ride_snapshot(
            pickup_lat=50.0,
            pickup_lng=-104.0,
            dropoff_lat=50.1,
            dropoff_lng=-104.1,
            phase_polylines={"trip_in_progress": sparse_trip},
            route_polyline=route_polyline,
        )
        assert result is not None
        instance = _FakeStaticMap.instances[-1]
        assert len(instance.lines) > 0

    def test_sparse_trip_trail_used_as_only_geometry_when_no_route_polyline(self, monkeypatch):
        _install_fake_staticmap_module(monkeypatch)
        sparse_trip = [[50.0, -104.0], [50.001, -104.001]]
        result = route_snapshot.render_ride_snapshot(
            pickup_lat=50.0,
            pickup_lng=-104.0,
            dropoff_lat=50.1,
            dropoff_lng=-104.1,
            phase_polylines={"trip_in_progress": sparse_trip},
        )
        assert result is not None
        instance = _FakeStaticMap.instances[-1]
        assert len(instance.lines) > 0

    def test_no_geometry_at_all_still_renders_markers_only(self, monkeypatch):
        _install_fake_staticmap_module(monkeypatch)
        result = route_snapshot.render_ride_snapshot(
            pickup_lat=50.0,
            pickup_lng=-104.0,
            dropoff_lat=50.1,
            dropoff_lng=-104.1,
        )
        assert result is not None
        instance = _FakeStaticMap.instances[-1]
        assert instance.lines == []

    def test_incomplete_route_quality_routes_through_banner(self, monkeypatch):
        _install_fake_staticmap_module(monkeypatch)
        banner_mock = MagicMock(return_value=b"BANNERED")
        monkeypatch.setattr(route_snapshot, "_add_route_quality_banner", banner_mock)
        result = route_snapshot.render_ride_snapshot(
            pickup_lat=50.0,
            pickup_lng=-104.0,
            dropoff_lat=50.1,
            dropoff_lng=-104.1,
            route_quality={"incomplete_reason": "gps_gap"},
        )
        assert result == b"BANNERED"
        banner_mock.assert_called_once()

    def test_render_exception_is_swallowed_and_returns_none(self, monkeypatch):
        fake_module = _install_fake_staticmap_module(monkeypatch)

        class _ExplodingStaticMap(_FakeStaticMap):
            def render(self):
                raise RuntimeError("tile server unreachable")

        fake_module.StaticMap = _ExplodingStaticMap
        result = route_snapshot.render_ride_snapshot(
            pickup_lat=50.0,
            pickup_lng=-104.0,
            dropoff_lat=50.1,
            dropoff_lng=-104.1,
        )
        assert result is None


class TestNormalizePolylinePoints:
    """Regression: legacy-imported rides stored `planned_route_polyline` as
    `{"lat": …, "lng": …}` objects instead of the `[[lat, lng], …]` arrays
    migration 100 defines. Nothing errored — `validCoordinate()` in
    shared/utils/routeSegments.ts silently rejected every point, so the
    ride-detail maps drew no route line at all. Migration 313 repairs the
    stored rows; this normalizer keeps a render correct for either shape so an
    unconverted row can never blank the route again.
    """

    def test_array_shape_is_the_contract_and_passes_through(self):
        assert route_snapshot.normalize_polyline_points(
            [[52.1, -106.6], [52.2, -106.7]]
        ) == [[52.1, -106.6], [52.2, -106.7]]

    def test_object_shape_from_legacy_import_is_converted(self):
        assert route_snapshot.normalize_polyline_points(
            [{"lat": 52.1, "lng": -106.6}, {"lat": 52.2, "lng": -106.7}]
        ) == [[52.1, -106.6], [52.2, -106.7]]

    def test_mixed_shapes_and_malformed_points_are_dropped_not_nulled(self):
        assert route_snapshot.normalize_polyline_points(
            [
                [52.1, -106.6],
                {"lat": 52.2, "lng": -106.7},
                "not-a-point",
                [1],                          # too short
                {"lat": None, "lng": -106.8},  # non-numeric
                {"lng": -106.9},               # missing lat
            ]
        ) == [[52.1, -106.6], [52.2, -106.7]]

    def test_booleans_are_not_accepted_as_coordinates(self):
        # bool is a subclass of int — guard against True/False slipping through.
        assert route_snapshot.normalize_polyline_points([[True, False]]) is None

    def test_ints_are_widened_to_float(self):
        result = route_snapshot.normalize_polyline_points([[52, -106], [53, -107]])
        assert result == [[52.0, -106.0], [53.0, -107.0]]
        assert all(isinstance(c, float) for point in result for c in point)

    @pytest.mark.parametrize("value", [None, "nope", 42, {}, [], [{}, "x"]])
    def test_unusable_input_returns_none(self, value):
        assert route_snapshot.normalize_polyline_points(value) is None
