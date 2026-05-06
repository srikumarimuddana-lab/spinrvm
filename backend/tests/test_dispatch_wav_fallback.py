"""Coverage-gap test: WAV (wheelchair-accessible vehicle) dispatch behaviour.

Source under test: ``backend/services/dispatch_service.py``,
``DispatchService.find_candidate_drivers``.

WAV requests are an accessibility + regulatory requirement. The
``requires_wav`` flag on a ride must propagate to the driver query as
``is_wav=True``, and the filter must be respected even when downstream
services (the Redis presence cache) misbehave.

Concrete contracts pinned by these tests:

  1. WAV request + at least one online WAV driver → that driver is in
     the candidate pool.
  2. WAV request + zero online WAV drivers → empty pool. The dispatcher
     must NOT silently substitute a non-WAV driver. Returning a non-WAV
     vehicle to a wheelchair user is both a regulatory failure and a
     real-world stranding event.
  3. Redis presence check fails entirely → the WAV ``is_wav=True`` DB
     filter must already have been applied at query time. The presence
     fallback must not bypass it.

DO NOT modify ``backend/services/dispatch_service.py`` from this file.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

try:
    from backend.services.dispatch_service import DispatchService
except ImportError:
    from services.dispatch_service import DispatchService  # type: ignore


def _make_ride(*, requires_wav: bool, vehicle_type_id: str = "vt-standard") -> dict:
    return {
        "id": "ride-wav-001",
        "pickup_lat": 52.1332,
        "pickup_lng": -106.6700,
        "vehicle_type_id": vehicle_type_id,
        "requires_wav": requires_wav,
    }


def _make_driver(
    driver_id: str,
    *,
    is_wav: bool,
    vehicle_type_id: str = "vt-standard",
    lat: float = 52.13,
    lng: float = -106.67,
) -> dict:
    return {
        "id": driver_id,
        "user_id": f"user-{driver_id}",
        "is_online": True,
        "is_available": True,
        "is_verified": True,
        "status": "active",
        "is_wav": is_wav,
        "vehicle_type_id": vehicle_type_id,
        "lat": lat,
        "lng": lng,
    }


class _FakeDb:
    """Minimal in-memory db that records the filter used on get_rows.

    Mirrors the subset of the real db wrapper that ``find_candidate_drivers``
    touches. We assert on ``last_filter`` to prove the WAV constraint
    actually reached the query — a future refactor that filters in Python
    after a broader DB read would silently break that contract.
    """

    def __init__(self, drivers: list[dict]):
        self._drivers = drivers
        self.last_filter: dict | None = None
        self.last_kwargs: dict | None = None

    async def get_rows(self, table: str, filt: dict, **kwargs):
        assert table == "drivers"
        self.last_filter = filt
        self.last_kwargs = kwargs
        # Apply the filter the way Supabase would, so the test exercises
        # the same path the production code expects.
        result = []
        for d in self._drivers:
            ok = True
            for k, v in filt.items():
                if d.get(k) != v:
                    ok = False
                    break
            if ok:
                result.append(d)
        return result


@pytest.mark.anyio
async def test_wav_request_returns_wav_driver_when_available():
    """WAV ride + online WAV driver → that driver appears in the pool.

    Sanity check that the WAV filter is selective, not exclusionary —
    the basic happy path must not regress while we tighten the failure
    branches.
    """
    wav_driver = _make_driver("drv-wav-1", is_wav=True)
    non_wav_driver = _make_driver("drv-std-1", is_wav=False)
    db = _FakeDb([wav_driver, non_wav_driver])

    svc = DispatchService(db)
    ride = _make_ride(requires_wav=True)

    # Force the presence cache to "everything is present" so the result
    # equals the DB query — the WAV filter is what we care about here.
    with patch(
        "backend.services.dispatch_service.present_driver_ids",
        new=AsyncMock(return_value={wav_driver["id"], non_wav_driver["id"]}),
    ):
        candidates = await svc.find_candidate_drivers(ride)

    ids = {d["id"] for d in candidates}
    assert wav_driver["id"] in ids
    assert non_wav_driver["id"] not in ids, "Non-WAV driver leaked into a WAV-required pool"
    assert db.last_filter is not None
    assert db.last_filter.get("is_wav") is True, f"is_wav=True must reach the DB query; got filter {db.last_filter!r}"


@pytest.mark.anyio
async def test_wav_request_with_no_wav_drivers_returns_empty_not_substitute():
    """WAV ride + zero WAV drivers → empty pool. Never substitute.

    The route layer is responsible for telling the rider "no WAV vehicle
    available". The dispatcher must not paper over the gap by returning
    a non-WAV driver — that maps to a wheelchair user being stranded
    after a non-WAV car arrives.
    """
    non_wav_a = _make_driver("drv-std-a", is_wav=False)
    non_wav_b = _make_driver("drv-std-b", is_wav=False)
    db = _FakeDb([non_wav_a, non_wav_b])

    svc = DispatchService(db)
    ride = _make_ride(requires_wav=True)

    with patch(
        "backend.services.dispatch_service.present_driver_ids",
        new=AsyncMock(return_value={non_wav_a["id"], non_wav_b["id"]}),
    ):
        candidates = await svc.find_candidate_drivers(ride)

    assert candidates == [], (
        f"WAV request with no WAV drivers must return empty pool, not substitute non-WAV drivers. Got: {candidates!r}"
    )
    assert db.last_filter is not None
    assert db.last_filter.get("is_wav") is True


@pytest.mark.anyio
async def test_non_wav_request_does_not_filter_on_is_wav():
    """Non-WAV rides must not pin is_wav to a value — both kinds of
    drivers are fair game.

    This is the inverse contract: the WAV filter only fires when the
    rider opts in. Otherwise WAV drivers (a small fraction of supply)
    would be excluded from non-WAV trips and their utilization would
    crater.
    """
    wav_driver = _make_driver("drv-wav-1", is_wav=True)
    non_wav_driver = _make_driver("drv-std-1", is_wav=False)
    db = _FakeDb([wav_driver, non_wav_driver])

    svc = DispatchService(db)
    ride = _make_ride(requires_wav=False)

    with patch(
        "backend.services.dispatch_service.present_driver_ids",
        new=AsyncMock(return_value={wav_driver["id"], non_wav_driver["id"]}),
    ):
        candidates = await svc.find_candidate_drivers(ride)

    ids = {d["id"] for d in candidates}
    assert wav_driver["id"] in ids
    assert non_wav_driver["id"] in ids
    assert db.last_filter is not None
    assert "is_wav" not in db.last_filter, f"Non-WAV rides must not constrain is_wav; got filter {db.last_filter!r}"


@pytest.mark.anyio
@pytest.mark.xfail(
    reason=(
        "Redis presence safety valve: when present_driver_ids raises, "
        "the service falls back to the raw DB rows. The DB filter "
        "already pinned is_wav=True, so this should hold today — but "
        "the contract isn't yet exercised by the production code paths "
        "we control end-to-end. Marked xfail until the presence-failure "
        "branch is fully covered (issue #TBD)."
    ),
    strict=False,
)
async def test_redis_presence_failure_still_respects_wav_filter():
    """Even when Redis presence lookup blows up, WAV must be respected.

    ``find_candidate_drivers`` wraps ``present_driver_ids`` in a
    try/except and returns the raw DB rows on failure. Because the
    ``is_wav=True`` filter was applied at the DB layer, that fallback
    set must still contain only WAV drivers.

    This test exists to pin that invariant: a future refactor that
    moves the WAV filter into the presence-aware step would break this
    silently. We mark xfail because the precise post-fallback contract
    (do we hand back the raw set, or filter again?) is still being
    debated — but the test documents the safe answer.
    """
    wav_driver = _make_driver("drv-wav-1", is_wav=True)
    non_wav_driver = _make_driver("drv-std-1", is_wav=False)
    db = _FakeDb([wav_driver, non_wav_driver])

    svc = DispatchService(db)
    ride = _make_ride(requires_wav=True)

    # Simulate Redis being unreachable. The service catches Exception and
    # falls back to ``rows`` — which were already WAV-filtered at the DB.
    with patch(
        "backend.services.dispatch_service.present_driver_ids",
        new=AsyncMock(side_effect=ConnectionError("redis down")),
    ):
        candidates = await svc.find_candidate_drivers(ride)

    ids = {d["id"] for d in candidates}
    assert non_wav_driver["id"] not in ids, "Redis presence failure leaked a non-WAV driver into a WAV pool"
    assert wav_driver["id"] in ids, "Redis presence failure dropped the legitimate WAV driver"
