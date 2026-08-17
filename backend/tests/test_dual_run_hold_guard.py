"""CR-4104 / A34 dual-run cutover guard tests.

Covers the two enforcement points described in the CR's own risk
mitigation: block go-online and block payout for a driver an operator has
flagged `dual_run_hold=True` (drivers.dual_run_hold, migration 327) — but
ONLY when that driver also carries non-empty `legacy_import_metadata`
(migration 221), since the flag is meaningless for natively-onboarded
drivers.

Key safety property under test: the flag defaults to False everywhere and
nothing in this codebase sets it automatically, so a driver with
`dual_run_hold=False` (or the key missing entirely) must be completely
unaffected by this guard — see the "default is a no-op" tests below.

The dispatch-claim path (services/dispatch_service.py::claim_driver) is
NOT guarded here by deliberate scope reduction — see the PR description
for CR-4104. Blocking go-online is sufficient to keep a held driver out of
the dispatch pool entirely, since is_available can only become True
through the go-online endpoint.

Run:
    pytest backend/tests/test_dual_run_hold_guard.py -v
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from backend.utils.error_handling import SpinrException
from backend.utils.error_keys import ErrorKeys

DRIVER_USER_ID = "driver_user_dual_run"
DRIVER_ID = "driver_row_dual_run"


def _driver_row(**extra) -> dict:
    return {
        "id": DRIVER_ID,
        "user_id": DRIVER_USER_ID,
        "name": "Legacy Driver",
        "rating": 4.9,
        "total_rides": 50,
        "is_online": False,
        "is_available": False,
        "status": "active",
        "is_verified": True,
        "legacy_import_metadata": {"source": "legacy_saskatoon_driver_import", "old_driver_id": "OLD-1"},
        "dual_run_hold": False,
        **extra,
    }


# ── go-online guard (routes/drivers/status.py::update_driver_status) ──────


@pytest.mark.anyio
async def test_go_online_blocked_when_dual_run_hold_true():
    """A legacy-imported driver with dual_run_hold=True cannot go online —
    a clear, structured error, not a crash, and no DB write must occur."""
    from backend.routes import drivers as drv_mod

    driver = _driver_row(dual_run_hold=True)
    update_one = AsyncMock()

    with (
        patch("backend.routes.drivers._deps.db_supabase.get_driver_by_id", AsyncMock(return_value=driver)),
        patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(return_value=[])),
        patch("backend.routes.drivers._deps.db_supabase.update_one", update_one),
    ):
        with pytest.raises(SpinrException) as exc_info:
            await drv_mod.update_driver_status(
                driver_id=DRIVER_ID,
                is_online=True,
                current_user={"id": DRIVER_USER_ID},
            )

    assert exc_info.value.status_code == 403
    assert exc_info.value.message_key == ErrorKeys.DRIVER_DUAL_RUN_HOLD
    update_one.assert_not_awaited()


@pytest.mark.anyio
async def test_go_online_hold_guard_does_not_block_going_offline():
    """The guard only gates is_online=True (the go-online transition) — a
    held driver already online (edge case: flag flipped mid-session) must
    still be able to go offline without tripping this guard."""
    from backend.routes import drivers as drv_mod

    driver = _driver_row(dual_run_hold=True, is_online=True, is_available=False)
    updated = {**driver, "is_online": False, "is_available": False}

    async def _get_rows(table, filters=None, limit=None, **kwargs):
        return []

    async def _update_one(table, filters, update):
        return updated

    with (
        patch(
            "backend.routes.drivers._deps.db_supabase.get_driver_by_id",
            AsyncMock(side_effect=[driver, updated]),
        ),
        patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
        patch("backend.routes.drivers._deps.db_supabase.update_one", AsyncMock(side_effect=_update_one)),
    ):
        result = await drv_mod.update_driver_status(
            driver_id=DRIVER_ID,
            is_online=False,
            current_user={"id": DRIVER_USER_ID},
        )

    assert result["success"] is True


@pytest.mark.anyio
async def test_go_online_unaffected_when_dual_run_hold_false_default():
    """The core safety property: dual_run_hold defaults to False and
    nothing sets it automatically, so the default (unset/False) driver must
    go online exactly as before this guard existed."""
    from backend.routes import drivers as drv_mod

    driver = _driver_row(dual_run_hold=False)
    updated = {**driver, "is_online": True, "is_available": True}
    writes = []

    async def _get_rows(table, filters=None, limit=None, **kwargs):
        return []

    async def _update_one(table, filters, update):
        writes.append(update.get("$set", update))
        return updated

    with (
        patch(
            "backend.routes.drivers._deps.db_supabase.get_driver_by_id",
            AsyncMock(side_effect=[driver, updated]),
        ),
        patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
        patch("backend.routes.drivers._deps.db_supabase.update_one", AsyncMock(side_effect=_update_one)),
    ):
        result = await drv_mod.update_driver_status(
            driver_id=DRIVER_ID,
            is_online=True,
            current_user={"id": DRIVER_USER_ID},
        )

    assert result["success"] is True
    assert writes and writes[0]["is_online"] is True


@pytest.mark.anyio
async def test_go_online_hold_true_but_not_legacy_imported_is_unaffected():
    """dual_run_hold is only meaningful for legacy-imported drivers (the
    CR's own risk mitigation) — an organically-onboarded driver with an
    (invalid/mistaken) True flag but empty legacy_import_metadata must not
    be blocked, so a data-entry mistake can never false-positive block a
    driver who was never on the old app."""
    from backend.routes import drivers as drv_mod

    driver = _driver_row(dual_run_hold=True, legacy_import_metadata={})
    updated = {**driver, "is_online": True, "is_available": True}
    writes = []

    async def _get_rows(table, filters=None, limit=None, **kwargs):
        return []

    async def _update_one(table, filters, update):
        writes.append(update.get("$set", update))
        return updated

    with (
        patch(
            "backend.routes.drivers._deps.db_supabase.get_driver_by_id",
            AsyncMock(side_effect=[driver, updated]),
        ),
        patch("backend.routes.drivers._deps.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
        patch("backend.routes.drivers._deps.db_supabase.update_one", AsyncMock(side_effect=_update_one)),
    ):
        result = await drv_mod.update_driver_status(
            driver_id=DRIVER_ID,
            is_online=True,
            current_user={"id": DRIVER_USER_ID},
        )

    assert result["success"] is True


# ── payout guard (routes/drivers/payouts.py::request_payout) ──────────────


@pytest.mark.anyio
async def test_request_payout_blocked_when_dual_run_hold_true():
    """A legacy-imported driver with dual_run_hold=True is rejected before
    the endpoint's own unconditional 410 (standard cashout is disabled) —
    a clear, specific error rather than the generic disabled message."""
    from backend.routes import drivers as drv_mod

    driver = _driver_row(dual_run_hold=True)

    with patch(
        "backend.routes.drivers._deps.db_supabase.get_rows",
        AsyncMock(return_value=[driver]),
    ):
        with pytest.raises(SpinrException) as exc_info:
            await drv_mod.request_payout(current_user={"id": DRIVER_USER_ID})

    assert exc_info.value.status_code == 403
    assert exc_info.value.message_key == ErrorKeys.DRIVER_DUAL_RUN_HOLD


@pytest.mark.anyio
async def test_request_payout_unaffected_when_dual_run_hold_false_default():
    """Default (False) driver still hits the endpoint's existing
    unconditional 410 behavior — unchanged by this guard."""
    from fastapi import HTTPException

    from backend.routes import drivers as drv_mod

    driver = _driver_row(dual_run_hold=False)

    with patch(
        "backend.routes.drivers._deps.db_supabase.get_rows",
        AsyncMock(return_value=[driver]),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await drv_mod.request_payout(current_user={"id": DRIVER_USER_ID})

    assert exc_info.value.status_code == 410
