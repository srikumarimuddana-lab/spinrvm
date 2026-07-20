"""Renderer policy for ride route snapshots.

Google Static Maps is the only first-class renderer. With a key configured, a
failed Google render must DEFER via the finalizer's pending queue (bounded by
snapshot_attempts) instead of silently freezing the OSM look into an immutable
receipt image. OSM renders only after the attempt budget is exhausted (loud:
logger.error + spinr_rides_snapshot_fallback_total) or when no key is
configured at all.
"""

import asyncio
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from backend.routes.drivers._shared import (
    _SNAPSHOT_MAX_GOOGLE_ATTEMPTS,
    _defer_snapshot_retry,
    _generate_and_store_ride_snapshot,
)


def _fallback_metric_total() -> float:
    """Sum the fallback counter across both dual-import module identities.

    Imported lazily (and via importlib) so we observe the exact module
    instances the pipeline code binds at call time, whichever identity the
    test environment resolved.
    """
    import importlib

    modules = {}
    for name in ("backend.utils.metrics", "utils.metrics"):
        try:
            module = importlib.import_module(name)
        except ImportError:  # pragma: no cover — single-identity environments
            continue
        modules[id(module)] = module  # dedupe: both names may alias one module
    total = 0.0
    for module in modules.values():
        counters = module.snapshot().get("counters", {})
        total += sum(counters.get("spinr_rides_snapshot_fallback_total", {}).values())
    return total


@contextmanager
def _patch_both(suffix: str, replacement):
    """Patch a target under both module identities of the dual-import pattern.

    The pipeline imports its collaborators inside the function body via
    ``from ...utils.x import y`` with a top-level fallback, so depending on
    how the package was loaded either ``backend.utils.x`` or ``utils.x`` is
    the live module. Patch whichever of the two import paths exist so the
    test can never fall through to a real renderer/network call.
    """
    patched_any = False
    with ExitStack() as stack:
        for prefix in ("backend.", ""):
            try:
                stack.enter_context(patch(f"{prefix}{suffix}", replacement))
                patched_any = True
            except (ModuleNotFoundError, AttributeError):
                continue
        assert patched_any, f"could not patch {suffix} under any module identity"
        yield replacement

FINALIZED_AT = datetime(2026, 7, 19, 10, 5, 0, tzinfo=timezone.utc)


def _run(coro):
    return asyncio.run(coro)


def _route_row(**over):
    row = {"ride_id": "ride_1", "route_revision": 3, "snapshot_attempts": 0}
    row.update(over)
    return row


# ── _defer_snapshot_retry unit behaviour ─────────────────────────────


def test_defer_requeues_finalization_with_incremented_attempts():
    update = AsyncMock(return_value={"ride_id": "ride_1"})
    with (
        patch(
            "backend.routes.drivers._shared.db_supabase.get_rows",
            AsyncMock(return_value=[_route_row(snapshot_attempts=1)]),
        ),
        patch("backend.routes.drivers._shared.db_supabase.update_one", update),
    ):
        assert _run(_defer_snapshot_retry("ride_1", 3, FINALIZED_AT)) is True

    args, kwargs = update.await_args
    assert args[0] == "ride_routes"
    assert args[1] == {"ride_id": "ride_1", "route_revision": 3, "finalized_at": FINALIZED_AT}
    assert args[2]["processing_status"] == "pending"
    assert args[2]["processing_claimed_at"] is None
    assert args[2]["snapshot_attempts"] == 2
    assert args[2]["next_retry_at"] > datetime.now(timezone.utc)
    assert kwargs["upsert"] is False


def test_defer_refuses_when_attempt_budget_exhausted():
    update = AsyncMock()
    with (
        patch(
            "backend.routes.drivers._shared.db_supabase.get_rows",
            AsyncMock(return_value=[_route_row(snapshot_attempts=_SNAPSHOT_MAX_GOOGLE_ATTEMPTS)]),
        ),
        patch("backend.routes.drivers._shared.db_supabase.update_one", update),
    ):
        assert _run(_defer_snapshot_retry("ride_1", 3, FINALIZED_AT)) is False
    update.assert_not_awaited()


@pytest.mark.parametrize(
    ("revision", "finalized_at", "row"),
    [
        (0, FINALIZED_AT, _route_row()),  # legacy unrevisioned pipeline
        (None, FINALIZED_AT, _route_row()),
        (3, None, _route_row()),  # no finalization token
        (3, FINALIZED_AT, _route_row(route_revision=4)),  # superseded revision
        (3, FINALIZED_AT, None),  # row gone
    ],
)
def test_defer_refuses_outside_the_revisioned_retry_machinery(revision, finalized_at, row):
    update = AsyncMock()
    with (
        patch(
            "backend.routes.drivers._shared.db_supabase.get_rows",
            AsyncMock(return_value=[row] if row else []),
        ),
        patch("backend.routes.drivers._shared.db_supabase.update_one", update),
    ):
        assert _run(_defer_snapshot_retry("ride_1", revision, finalized_at)) is False
    update.assert_not_awaited()


def test_defer_reports_superseded_when_cas_misses():
    with (
        patch(
            "backend.routes.drivers._shared.db_supabase.get_rows",
            AsyncMock(return_value=[_route_row()]),
        ),
        patch("backend.routes.drivers._shared.db_supabase.update_one", AsyncMock(return_value=None)),
    ):
        assert _run(_defer_snapshot_retry("ride_1", 3, FINALIZED_AT)) is False


# ── pipeline-level policy ────────────────────────────────────────────


def _pipeline_kwargs():
    return dict(
        ride_id="ride_1",
        pickup_lat=50.40,
        pickup_lng=-104.66,
        dropoff_lat=50.45,
        dropoff_lng=-104.55,
        phase_polylines=None,
        route_polyline=None,
        route_segments=[{"points": [[50.40, -104.66], [50.45, -104.55]]}],
        completion_point=None,
        route_quality={},
        route_revision=3,
        finalized_at=FINALIZED_AT,
    )


def test_google_failure_defers_and_never_renders_osm():
    osm = AsyncMock()
    with (
        _patch_both("settings_loader.get_app_settings", AsyncMock(return_value={"google_maps_api_key": "k"})),
        _patch_both(
            "utils.route_snapshot.render_ride_snapshot_google",
            AsyncMock(side_effect=RuntimeError("static maps 500")),
        ),
        _patch_both("utils.route_snapshot.render_ride_snapshot", osm),
        _patch_both("routes.drivers._shared._defer_snapshot_retry", AsyncMock(return_value=True)) as defer,
    ):
        _run(_generate_and_store_ride_snapshot(**_pipeline_kwargs()))

    defer.assert_awaited_once_with("ride_1", 3, FINALIZED_AT)
    osm.assert_not_called()


def test_exhausted_budget_renders_osm_and_counts_fallback():
    before_total = _fallback_metric_total()
    with (
        _patch_both("settings_loader.get_app_settings", AsyncMock(return_value={"google_maps_api_key": "k"})),
        _patch_both(
            "utils.route_snapshot.render_ride_snapshot_google",
            AsyncMock(side_effect=RuntimeError("static maps 500")),
        ),
        _patch_both("utils.route_snapshot.render_ride_snapshot", lambda **_: None),
        _patch_both("routes.drivers._shared._defer_snapshot_retry", AsyncMock(return_value=False)),
    ):
        _run(_generate_and_store_ride_snapshot(**_pipeline_kwargs()))

    assert _fallback_metric_total() == before_total + 1


def test_no_key_renders_osm_directly_without_defer_or_fallback_metric():
    before_total = _fallback_metric_total()
    with (
        _patch_both("settings_loader.get_app_settings", AsyncMock(return_value={})),
        _patch_both("utils.route_snapshot.render_ride_snapshot", lambda **_: None),
        _patch_both("routes.drivers._shared._defer_snapshot_retry", AsyncMock()) as defer,
    ):
        _run(_generate_and_store_ride_snapshot(**_pipeline_kwargs()))

    defer.assert_not_awaited()
    assert _fallback_metric_total() == before_total
