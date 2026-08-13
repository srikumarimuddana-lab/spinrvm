"""Coverage-closure tests for routes/drivers/_shared.py, status.py, profile.py
(A1c Sub-tier A — last three files of the routes/drivers/ package batch).

Scope: test-only, no application code changed. Mirrors the local-mocking
convention established earlier the same day in test_subscriptions_coverage.py
and test_snapshot_renderer_policy.py.

Patch-target conventions (see routes/drivers/_deps.py + CLAUDE.md):
- `db_supabase` is a *module reference* shared by every importer, so
  `patch("backend.routes.drivers._shared.db_supabase.<fn>")` (or
  `backend.db_supabase.<fn>` for the route-level call sites in status.py/
  profile.py, which go through `_deps.db_supabase`) is the correct target —
  matches the existing convention in test_snapshot_renderer_policy.py and
  test_go_online_availability.py.
- Collaborators the pipeline imports *inside* function bodies via the
  dual-import pattern (`settings_loader.get_app_settings`,
  `utils.route_snapshot.render_ride_snapshot[_google]`, `supabase_client.supabase`,
  `core.config.settings`, `utils.route_distance.compute_road_route`,
  `utils.route_validation.validate_trip_route`) are patched under both the
  `backend.<x>` and bare `<x>` module identities via the `_patch_both` helper
  copied verbatim from test_snapshot_renderer_policy.py, for the same reason:
  whichever import form resolved at call time is the live module.
"""

from __future__ import annotations

import asyncio
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.anyio


@contextmanager
def _patch_both(suffix: str, replacement):
    """Patch a target under both module identities of the dual-import pattern.

    Copied from test_snapshot_renderer_policy.py — see that file's docstring
    for why both `backend.<x>` and bare `<x>` must be tried.
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


def _run(coro):
    return asyncio.run(coro)


FINALIZED_AT = datetime(2026, 7, 19, 10, 5, 0, tzinfo=timezone.utc)


def _pipeline_kwargs(**overrides):
    base = dict(
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
        route_revision=0,
        finalized_at=None,
    )
    base.update(overrides)
    return base


# ============================================================
# _shared.py — small helpers
# ============================================================


class TestRouteSnapshotRetentionDueAt:
    def test_normal_date_adds_three_years(self):
        from backend.routes.drivers._shared import _route_snapshot_retention_due_at

        finalized_at = datetime(2026, 7, 19, 10, 5, 0, tzinfo=timezone.utc)
        due = _route_snapshot_retention_due_at(finalized_at)
        assert due == datetime(2029, 7, 19, 10, 5, 0, tzinfo=timezone.utc)

    def test_feb_29_leap_day_falls_back_to_feb_28(self):
        from backend.routes.drivers._shared import _route_snapshot_retention_due_at

        finalized_at = datetime(2028, 2, 29, 12, 0, 0, tzinfo=timezone.utc)
        due = _route_snapshot_retention_due_at(finalized_at)
        # 2031 is not a leap year — Feb 29 has no equivalent.
        assert due == datetime(2031, 2, 28, 12, 0, 0, tzinfo=timezone.utc)


class TestRideIncome:
    def test_falls_back_to_fare_components_for_legacy_rows_without_driver_earnings(self):
        """Rows written before the driver_earnings column existed must sum
        the fare components instead of reading the (absent) canonical column."""
        from backend.routes.drivers._shared import _ride_income

        ride = {
            "driver_earnings": None,
            "base_fare": "5.00",
            "distance_fare": "3.25",
            "time_fare": "1.50",
            "tip_amount": "2.00",
        }
        assert _ride_income(ride) == Decimal("11.75")

    def test_prefers_canonical_driver_earnings_column_when_present(self):
        from backend.routes.drivers._shared import _ride_income

        ride = {"driver_earnings": "20.00", "base_fare": "999.00"}
        assert _ride_income(ride) == Decimal("20.00")


class TestDHelper:
    def test_valid_value_quantized_to_two_places(self):
        from backend.routes.drivers._shared import _d

        assert _d("12.345") == Decimal("12.35")
        assert _d(10) == Decimal("10.00")

    def test_invalid_value_defaults_to_zero(self):
        from backend.routes.drivers._shared import _d

        assert _d("not-a-number") == Decimal("0")
        assert _d(None) == Decimal("0")
        assert _d(object()) == Decimal("0")


class TestDeferSnapshotRetryExceptionBranch:
    async def test_update_one_exception_falls_through_to_osm(self):
        """The snapshot_attempts column (migration 243) may not be deployed
        yet — a DB error on the defer-retry write must not raise, only log
        and fall through so the OSM last resort still renders."""
        from backend.routes.drivers._shared import _defer_snapshot_retry

        row = {"ride_id": "ride_1", "route_revision": 3, "snapshot_attempts": 0}
        with (
            patch("backend.routes.drivers._shared.db_supabase.get_rows", AsyncMock(return_value=[row])),
            patch(
                "backend.routes.drivers._shared.db_supabase.update_one",
                AsyncMock(side_effect=Exception("column snapshot_attempts does not exist")),
            ),
        ):
            result = await _defer_snapshot_retry("ride_1", 3, FINALIZED_AT)

        assert result is False


class TestVaultEncrypt:
    async def test_empty_value_passthrough(self):
        from backend.routes.drivers._shared import _vault_encrypt

        assert await _vault_encrypt("", "license_number") == ""

    async def test_import_error_raises_503(self):
        from fastapi import HTTPException

        from backend.routes.drivers._shared import _vault_encrypt

        with patch.dict("sys.modules", {"supabase_client": None}):
            with pytest.raises(HTTPException) as exc:
                await _vault_encrypt("ABC123", "license_number")
        assert exc.value.status_code == 503

    async def test_uninitialised_client_raises_503(self):
        from fastapi import HTTPException

        from backend.routes.drivers._shared import _vault_encrypt

        fake_module = MagicMock()
        fake_module.supabase = None
        with patch.dict("sys.modules", {"supabase_client": fake_module}):
            with pytest.raises(HTTPException) as exc:
                await _vault_encrypt("ABC123", "license_number")
        assert exc.value.status_code == 503

    async def test_rpc_returns_no_data_raises_503(self):
        from fastapi import HTTPException

        from backend.routes.drivers._shared import _vault_encrypt

        fake_sb = MagicMock()
        fake_module = MagicMock()
        fake_module.supabase = fake_sb
        rpc_result = MagicMock()
        rpc_result.data = None
        with (
            patch.dict("sys.modules", {"supabase_client": fake_module}),
            patch(
                "backend.routes.drivers._shared.db_supabase.run_sync",
                AsyncMock(return_value=rpc_result),
            ),
        ):
            with pytest.raises(HTTPException) as exc:
                await _vault_encrypt("ABC123", "license_number")
        assert exc.value.status_code == 503

    async def test_rpc_exception_raises_503(self):
        from fastapi import HTTPException

        from backend.routes.drivers._shared import _vault_encrypt

        fake_sb = MagicMock()
        fake_module = MagicMock()
        fake_module.supabase = fake_sb
        with (
            patch.dict("sys.modules", {"supabase_client": fake_module}),
            patch(
                "backend.routes.drivers._shared.db_supabase.run_sync",
                AsyncMock(side_effect=Exception("rpc down")),
            ),
        ):
            with pytest.raises(HTTPException) as exc:
                await _vault_encrypt("ABC123", "license_number")
        assert exc.value.status_code == 503

    async def test_success_returns_vault_token(self):
        from backend.routes.drivers._shared import _vault_encrypt

        fake_sb = MagicMock()
        fake_module = MagicMock()
        fake_module.supabase = fake_sb
        rpc_result = MagicMock()
        rpc_result.data = "vault-uuid-123"
        with (
            patch.dict("sys.modules", {"supabase_client": fake_module}),
            patch(
                "backend.routes.drivers._shared.db_supabase.run_sync",
                AsyncMock(return_value=rpc_result),
            ),
        ):
            result = await _vault_encrypt("ABC123", "license_number")
        assert result == "vault-uuid-123"


class TestVaultDecrypt:
    async def test_empty_value_passthrough(self):
        from backend.routes.drivers._shared import _vault_decrypt

        assert await _vault_decrypt("", "license_number") == ""

    async def test_import_error_returns_raw_token(self):
        from backend.routes.drivers._shared import _vault_decrypt

        with patch.dict("sys.modules", {"supabase_client": None}):
            result = await _vault_decrypt("vault-uuid-123", "license_number")
        assert result == "vault-uuid-123"

    async def test_uninitialised_client_returns_raw_token(self):
        from backend.routes.drivers._shared import _vault_decrypt

        fake_module = MagicMock()
        fake_module.supabase = None
        with patch.dict("sys.modules", {"supabase_client": fake_module}):
            result = await _vault_decrypt("vault-uuid-123", "license_number")
        assert result == "vault-uuid-123"

    async def test_success_returns_plaintext(self):
        from backend.routes.drivers._shared import _vault_decrypt

        fake_sb = MagicMock()
        fake_module = MagicMock()
        fake_module.supabase = fake_sb
        rpc_result = MagicMock()
        rpc_result.data = "S123456"
        with (
            patch.dict("sys.modules", {"supabase_client": fake_module}),
            patch(
                "backend.routes.drivers._shared.db_supabase.run_sync",
                AsyncMock(return_value=rpc_result),
            ),
        ):
            result = await _vault_decrypt("vault-uuid-123", "license_number")
        assert result == "S123456"

    async def test_rpc_exception_degrades_to_raw_token(self):
        """Decrypt failure must never raise — the vault token itself is not
        PII, so this degrades to an unreadable value rather than a 5xx."""
        from backend.routes.drivers._shared import _vault_decrypt

        fake_sb = MagicMock()
        fake_module = MagicMock()
        fake_module.supabase = fake_sb
        with (
            patch.dict("sys.modules", {"supabase_client": fake_module}),
            patch(
                "backend.routes.drivers._shared.db_supabase.run_sync",
                AsyncMock(side_effect=Exception("rpc down")),
            ),
        ):
            result = await _vault_decrypt("vault-uuid-123", "license_number")
        assert result == "vault-uuid-123"


class TestEncryptDecryptDriverPii:
    async def test_encrypt_calls_vault_encrypt_for_present_field(self):
        from backend.routes.drivers._shared import _encrypt_driver_pii

        with patch(
            "backend.routes.drivers._shared._vault_encrypt",
            AsyncMock(return_value="vault-token"),
        ) as mock_encrypt:
            result = await _encrypt_driver_pii({"license_number": "S1234567", "name": "Sam"})
        mock_encrypt.assert_awaited_once_with("S1234567", "license_number")
        assert result["license_number"] == "vault-token"
        assert result["name"] == "Sam"

    async def test_encrypt_skips_absent_or_falsy_field(self):
        from backend.routes.drivers._shared import _encrypt_driver_pii

        with patch("backend.routes.drivers._shared._vault_encrypt", AsyncMock()) as mock_encrypt:
            result = await _encrypt_driver_pii({"name": "Sam", "license_number": ""})
        mock_encrypt.assert_not_awaited()
        assert result == {"name": "Sam", "license_number": ""}

    async def test_decrypt_calls_vault_decrypt_for_present_field(self):
        from backend.routes.drivers._shared import _decrypt_driver_pii

        with patch(
            "backend.routes.drivers._shared._vault_decrypt",
            AsyncMock(return_value="S1234567"),
        ) as mock_decrypt:
            result = await _decrypt_driver_pii({"license_number": "vault-token", "name": "Sam"})
        mock_decrypt.assert_awaited_once_with("vault-token", "license_number")
        assert result["license_number"] == "S1234567"
        assert result["name"] == "Sam"

    async def test_decrypt_skips_absent_field_returns_shallow_copy(self):
        from backend.routes.drivers._shared import _decrypt_driver_pii

        driver = {"name": "Sam"}
        with patch("backend.routes.drivers._shared._vault_decrypt", AsyncMock()) as mock_decrypt:
            result = await _decrypt_driver_pii(driver)
        mock_decrypt.assert_not_awaited()
        assert result == driver
        assert result is not driver


# ============================================================
# _generate_and_store_ride_snapshot — the pipeline's storage/write tail
# (test_snapshot_renderer_policy.py already covers the Google/OSM renderer
# selection policy; this class covers what happens once a real png is
# produced, which none of those tests exercise since they all mock the OSM
# fallback to return None.)
# ============================================================


class TestGenerateAndStoreRideSnapshotStorageTail:
    async def test_missing_coordinates_returns_early(self):
        from backend.routes.drivers._shared import _generate_and_store_ride_snapshot

        osm = MagicMock()
        with _patch_both("utils.route_snapshot.render_ride_snapshot", osm):
            await _generate_and_store_ride_snapshot(**_pipeline_kwargs(pickup_lat=None))
        osm.assert_not_called()

    async def test_google_success_logs_and_uploads_legacy_path(self):
        """revision=0 (legacy/unrevisioned) + successful Google render:
        exercises the success-log line plus the legacy public-URL write."""
        from backend.routes.drivers._shared import _generate_and_store_ride_snapshot

        fake_settings = MagicMock()
        fake_settings.SUPABASE_URL = "https://proj.supabase.co"
        fake_supabase = MagicMock()
        update_one = AsyncMock()
        with (
            _patch_both("settings_loader.get_app_settings", AsyncMock(return_value={"google_maps_api_key": "k"})),
            _patch_both(
                "utils.route_snapshot.render_ride_snapshot_google",
                AsyncMock(return_value=b"\x89PNG-google-bytes"),
            ),
            _patch_both("core.config.settings", fake_settings),
            _patch_both("supabase_client.supabase", fake_supabase),
            patch("backend.routes.drivers._shared.db_supabase.update_one", update_one),
        ):
            await _generate_and_store_ride_snapshot(**_pipeline_kwargs(route_revision=0, finalized_at=None))

        fake_supabase.storage.from_.assert_called_with("ride-snapshots")
        update_one.assert_awaited_once()
        args = update_one.await_args.args
        assert args[0] == "rides"
        assert args[1] == {"id": "ride_1"}
        assert args[2]["route_snapshot_url"].startswith(
            "https://proj.supabase.co/storage/v1/object/public/ride-snapshots/"
        )

    async def test_legacy_path_write_failure_is_logged_not_raised(self):
        from backend.routes.drivers._shared import _generate_and_store_ride_snapshot

        fake_settings = MagicMock()
        fake_settings.SUPABASE_URL = "https://proj.supabase.co"
        fake_supabase = MagicMock()
        update_one = AsyncMock(side_effect=Exception("db down"))
        with (
            _patch_both("settings_loader.get_app_settings", AsyncMock(return_value={"google_maps_api_key": "k"})),
            _patch_both(
                "utils.route_snapshot.render_ride_snapshot_google",
                AsyncMock(return_value=b"\x89PNG-google-bytes"),
            ),
            _patch_both("core.config.settings", fake_settings),
            _patch_both("supabase_client.supabase", fake_supabase),
            patch("backend.routes.drivers._shared.db_supabase.update_one", update_one),
        ):
            # Must not raise — the reference write failure is logged only.
            await _generate_and_store_ride_snapshot(**_pipeline_kwargs(route_revision=0, finalized_at=None))
        update_one.assert_awaited_once()

    async def test_legacy_path_missing_supabase_url_logs_and_returns(self):
        from backend.routes.drivers._shared import _generate_and_store_ride_snapshot

        fake_settings = MagicMock()
        fake_settings.SUPABASE_URL = ""
        fake_supabase = MagicMock()
        update_one = AsyncMock()
        with (
            _patch_both("settings_loader.get_app_settings", AsyncMock(return_value={})),
            _patch_both("utils.route_snapshot.render_ride_snapshot", lambda **_: b"\x89PNG-osm-bytes"),
            _patch_both("core.config.settings", fake_settings),
            _patch_both("supabase_client.supabase", fake_supabase),
            patch("backend.routes.drivers._shared.db_supabase.update_one", update_one),
        ):
            await _generate_and_store_ride_snapshot(**_pipeline_kwargs(route_revision=0, finalized_at=None))

        update_one.assert_not_awaited()

    async def test_storage_upload_failure_is_logged_and_returns(self):
        from backend.routes.drivers._shared import _generate_and_store_ride_snapshot

        fake_supabase = MagicMock()
        fake_supabase.storage.from_.return_value.upload.side_effect = Exception("storage down")
        update_one = AsyncMock()
        with (
            _patch_both("settings_loader.get_app_settings", AsyncMock(return_value={})),
            _patch_both("utils.route_snapshot.render_ride_snapshot", lambda **_: b"\x89PNG-osm-bytes"),
            _patch_both("supabase_client.supabase", fake_supabase),
            patch("backend.routes.drivers._shared.db_supabase.update_one", update_one),
        ):
            await _generate_and_store_ride_snapshot(**_pipeline_kwargs(route_revision=0, finalized_at=None))

        update_one.assert_not_awaited()

    async def test_v2_revisioned_success_writes_private_object_path(self):
        from backend.routes.drivers._shared import _generate_and_store_ride_snapshot

        fake_supabase = MagicMock()
        insert_ledger = AsyncMock()
        update_one = AsyncMock(return_value={"ride_id": "ride_1"})
        with (
            _patch_both("settings_loader.get_app_settings", AsyncMock(return_value={})),
            _patch_both("utils.route_snapshot.render_ride_snapshot", lambda **_: b"\x89PNG-osm-bytes"),
            _patch_both("supabase_client.supabase", fake_supabase),
            patch("backend.routes.drivers._shared.db_supabase.insert_many_ignore_conflicts", insert_ledger),
            patch("backend.routes.drivers._shared.db_supabase.update_one", update_one),
        ):
            await _generate_and_store_ride_snapshot(**_pipeline_kwargs(route_revision=3, finalized_at=FINALIZED_AT))

        insert_ledger.assert_awaited_once()
        ledger_table, ledger_rows = insert_ledger.await_args.args[0], insert_ledger.await_args.args[1]
        assert ledger_table == "ride_route_snapshot_objects"
        assert ledger_rows[0]["route_revision"] == 3
        fake_supabase.storage.from_.assert_called_with("ride-route-snapshots")
        update_one.assert_awaited_once()
        assert update_one.await_args.args[0] == "ride_routes"
        assert update_one.await_args.args[2]["snapshot_revision"] == 3
        fake_supabase.storage.from_.return_value.remove.assert_not_called()

    async def test_v2_missing_finalized_at_logs_and_returns_before_upload(self):
        from backend.routes.drivers._shared import _generate_and_store_ride_snapshot

        fake_supabase = MagicMock()
        with (
            _patch_both("settings_loader.get_app_settings", AsyncMock(return_value={})),
            _patch_both("utils.route_snapshot.render_ride_snapshot", lambda **_: b"\x89PNG-osm-bytes"),
            _patch_both("supabase_client.supabase", fake_supabase),
        ):
            await _generate_and_store_ride_snapshot(**_pipeline_kwargs(route_revision=3, finalized_at=None))

        fake_supabase.storage.from_.return_value.upload.assert_not_called()

    async def test_v2_ledger_write_failure_still_uploads(self):
        """The ledger table (migration 240) may not be deployed yet — a
        failure there must not block the upload, only be logged."""
        from backend.routes.drivers._shared import _generate_and_store_ride_snapshot

        fake_supabase = MagicMock()
        insert_ledger = AsyncMock(side_effect=Exception("relation does not exist"))
        update_one = AsyncMock(return_value={"ride_id": "ride_1"})
        with (
            _patch_both("settings_loader.get_app_settings", AsyncMock(return_value={})),
            _patch_both("utils.route_snapshot.render_ride_snapshot", lambda **_: b"\x89PNG-osm-bytes"),
            _patch_both("supabase_client.supabase", fake_supabase),
            patch("backend.routes.drivers._shared.db_supabase.insert_many_ignore_conflicts", insert_ledger),
            patch("backend.routes.drivers._shared.db_supabase.update_one", update_one),
        ):
            await _generate_and_store_ride_snapshot(**_pipeline_kwargs(route_revision=3, finalized_at=FINALIZED_AT))

        fake_supabase.storage.from_.return_value.upload.assert_called_once()
        update_one.assert_awaited_once()

    async def test_v2_cas_miss_deletes_unreachable_object(self):
        """A newer evidence batch invalidated this revision before upload
        completed (update_one returns None) — the unreachable object must be
        deleted so it cannot outlive current route evidence."""
        from backend.routes.drivers._shared import _generate_and_store_ride_snapshot

        fake_supabase = MagicMock()
        update_one = AsyncMock(return_value=None)
        with (
            _patch_both("settings_loader.get_app_settings", AsyncMock(return_value={})),
            _patch_both("utils.route_snapshot.render_ride_snapshot", lambda **_: b"\x89PNG-osm-bytes"),
            _patch_both("supabase_client.supabase", fake_supabase),
            patch("backend.routes.drivers._shared.db_supabase.insert_many_ignore_conflicts", AsyncMock()),
            patch("backend.routes.drivers._shared.db_supabase.update_one", update_one),
        ):
            await _generate_and_store_ride_snapshot(**_pipeline_kwargs(route_revision=3, finalized_at=FINALIZED_AT))

        fake_supabase.storage.from_.return_value.remove.assert_called_once()

    async def test_v2_snapshot_attempts_column_missing_retries_without_it(self):
        from backend.routes.drivers._shared import _generate_and_store_ride_snapshot

        fake_supabase = MagicMock()
        update_one = AsyncMock(
            side_effect=[Exception("column snapshot_attempts does not exist"), {"ride_id": "ride_1"}]
        )
        with (
            _patch_both("settings_loader.get_app_settings", AsyncMock(return_value={})),
            _patch_both("utils.route_snapshot.render_ride_snapshot", lambda **_: b"\x89PNG-osm-bytes"),
            _patch_both("supabase_client.supabase", fake_supabase),
            patch("backend.routes.drivers._shared.db_supabase.insert_many_ignore_conflicts", AsyncMock()),
            patch("backend.routes.drivers._shared.db_supabase.update_one", update_one),
        ):
            await _generate_and_store_ride_snapshot(**_pipeline_kwargs(route_revision=3, finalized_at=FINALIZED_AT))

        assert update_one.await_count == 2
        second_call_payload = update_one.await_args_list[1].args[2]
        assert "snapshot_attempts" not in second_call_payload

    async def test_v2_reference_write_exception_is_logged_and_reraised(self):
        from backend.routes.drivers._shared import _generate_and_store_ride_snapshot

        fake_supabase = MagicMock()
        update_one = AsyncMock(side_effect=RuntimeError("db exploded"))
        with (
            _patch_both("settings_loader.get_app_settings", AsyncMock(return_value={})),
            _patch_both("utils.route_snapshot.render_ride_snapshot", lambda **_: b"\x89PNG-osm-bytes"),
            _patch_both("supabase_client.supabase", fake_supabase),
            patch("backend.routes.drivers._shared.db_supabase.insert_many_ignore_conflicts", AsyncMock()),
            patch("backend.routes.drivers._shared.db_supabase.update_one", update_one),
        ):
            # The outer try/except in _generate_and_store_ride_snapshot
            # catches everything and only logs — so this must NOT raise out
            # of the pipeline despite the inner explicit `raise`.
            await _generate_and_store_ride_snapshot(**_pipeline_kwargs(route_revision=3, finalized_at=FINALIZED_AT))

    async def test_top_level_import_failure_is_swallowed(self):
        """The outermost try/except wraps the whole pipeline body (including
        its own dual-import block) — any unexpected failure must never
        propagate out to the caller (a background task / finalizer)."""
        from backend.routes.drivers._shared import _generate_and_store_ride_snapshot

        with _patch_both("settings_loader.get_app_settings", AsyncMock(side_effect=RuntimeError("settings db down"))):
            # Must not raise.
            await _generate_and_store_ride_snapshot(**_pipeline_kwargs(route_revision=0, finalized_at=None))


# ============================================================
# _snap_pickup_leg_async
# ============================================================


class TestSnapPickupLegAsync:
    async def test_empty_breadcrumbs_is_noop(self):
        from backend.routes.drivers._shared import _snap_pickup_leg_async

        update_one = AsyncMock()
        with patch("backend.routes.drivers._shared.db_supabase.update_one", update_one):
            await _snap_pickup_leg_async("ride_1", [])
        update_one.assert_not_awaited()

    async def test_successful_snap_backfills_polyline(self):
        from backend.routes.drivers._shared import _snap_pickup_leg_async

        update_one = AsyncMock()
        with (
            _patch_both(
                "utils.route_distance.compute_road_route",
                AsyncMock(return_value={"polyline": [[50.1, -104.1], [50.2, -104.2]]}),
            ),
            patch("backend.routes.drivers._shared.db_supabase.update_one", update_one),
        ):
            await _snap_pickup_leg_async("ride_1", [{"lat": 50.1, "lng": -104.1}])

        update_one.assert_awaited_once_with(
            "ride_routes",
            {"ride_id": "ride_1"},
            {"road_polyline_pickup": [[50.1, -104.1], [50.2, -104.2]]},
            upsert=False,
        )

    async def test_empty_polyline_result_skips_update(self):
        from backend.routes.drivers._shared import _snap_pickup_leg_async

        update_one = AsyncMock()
        with (
            _patch_both("utils.route_distance.compute_road_route", AsyncMock(return_value={"polyline": []})),
            patch("backend.routes.drivers._shared.db_supabase.update_one", update_one),
        ):
            await _snap_pickup_leg_async("ride_1", [{"lat": 50.1, "lng": -104.1}])
        update_one.assert_not_awaited()

    async def test_exception_is_swallowed_as_warning(self):
        from backend.routes.drivers._shared import _snap_pickup_leg_async

        with _patch_both("utils.route_distance.compute_road_route", AsyncMock(side_effect=Exception("osrm down"))):
            # Must not raise — display-only backfill, best-effort.
            await _snap_pickup_leg_async("ride_1", [{"lat": 50.1, "lng": -104.1}])


# ============================================================
# _validate_ride_route
# ============================================================


class TestValidateRideRoute:
    async def test_too_few_breadcrumbs_is_noop(self):
        from backend.routes.drivers._shared import _validate_ride_route

        update_one = AsyncMock()
        with patch("backend.routes.drivers._shared.db_supabase.update_one", update_one):
            await _validate_ride_route("ride_1", [{"lat": 1, "lng": 1}] * 3, "driver_1")
        update_one.assert_not_awaited()

    async def test_ok_verdict_stores_result_without_warning(self):
        from backend.routes.drivers._shared import _validate_ride_route

        update_one = AsyncMock()
        breadcrumbs = [{"lat": 50.0 + i * 0.001, "lng": -104.0} for i in range(6)]
        result = {"verdict": "ok", "deviation_pct": 2.0}
        with (
            _patch_both("utils.route_validation.validate_trip_route", AsyncMock(return_value=result)),
            patch("backend.routes.drivers._shared.db_supabase.update_one", update_one),
        ):
            await _validate_ride_route("ride_1", breadcrumbs, "driver_1")

        update_one.assert_awaited_once_with("rides", {"id": "ride_1"}, {"route_validation": result})

    async def test_suspicious_verdict_logs_warning(self):
        from backend.routes.drivers._shared import _validate_ride_route

        breadcrumbs = [{"lat": 50.0 + i * 0.001, "lng": -104.0} for i in range(6)]
        result = {"verdict": "likely_spoofed", "deviation_pct": 87.5}
        with (
            _patch_both("utils.route_validation.validate_trip_route", AsyncMock(return_value=result)),
            patch("backend.routes.drivers._shared.db_supabase.update_one", AsyncMock()),
        ):
            # Must not raise; warning path is exercised (verified via no exception).
            await _validate_ride_route("ride_1", breadcrumbs, "driver_1")

    async def test_none_result_returns_early(self):
        from backend.routes.drivers._shared import _validate_ride_route

        breadcrumbs = [{"lat": 50.0 + i * 0.001, "lng": -104.0} for i in range(6)]
        update_one = AsyncMock()
        with (
            _patch_both("utils.route_validation.validate_trip_route", AsyncMock(return_value=None)),
            patch("backend.routes.drivers._shared.db_supabase.update_one", update_one),
        ):
            await _validate_ride_route("ride_1", breadcrumbs, "driver_1")
        update_one.assert_not_awaited()

    async def test_store_failure_is_logged_not_raised(self):
        from backend.routes.drivers._shared import _validate_ride_route

        breadcrumbs = [{"lat": 50.0 + i * 0.001, "lng": -104.0} for i in range(6)]
        result = {"verdict": "ok", "deviation_pct": 1.0}
        with (
            _patch_both("utils.route_validation.validate_trip_route", AsyncMock(return_value=result)),
            patch(
                "backend.routes.drivers._shared.db_supabase.update_one",
                AsyncMock(side_effect=Exception("db down")),
            ),
        ):
            await _validate_ride_route("ride_1", breadcrumbs, "driver_1")

    async def test_outer_exception_is_swallowed(self):
        from backend.routes.drivers._shared import _validate_ride_route

        breadcrumbs = [{"lat": 50.0 + i * 0.001, "lng": -104.0} for i in range(6)]
        with _patch_both("utils.route_validation.validate_trip_route", AsyncMock(side_effect=RuntimeError("boom"))):
            await _validate_ride_route("ride_1", breadcrumbs, "driver_1")


# ============================================================
# status.py — GET /{driver_id}   (the one uncovered endpoint in this file;
# update_driver_status and _fresh_pending_offers are already covered by
# test_go_online_availability.py and test_p1_driver_offline.py)
# ============================================================


class TestGetDriverDetail:
    DRIVER_ID = "driver-status-1"

    def _driver(self, **extra):
        return {
            "id": self.DRIVER_ID,
            "user_id": "user-owner",
            "name": "Pat Driver",
            "rating": 4.9,
            "vehicle_make": "Toyota",
            "vehicle_model": "Camry",
            "vehicle_color": "Black",
            "license_plate": "ABC123",
            **extra,
        }

    async def test_404_when_driver_not_found(self):
        from fastapi import HTTPException

        from backend.routes.drivers import status as status_mod

        with patch("backend.routes.drivers.status.db_supabase.get_driver_by_id", AsyncMock(return_value=None)):
            with pytest.raises(HTTPException) as exc:
                await status_mod.get_driver(self.DRIVER_ID, current_user={"id": "someone"})
        assert exc.value.status_code == 404

    async def test_admin_gets_full_decrypted_detail(self):
        from backend.routes.drivers import status as status_mod

        driver = self._driver(license_number="vault-token")
        with (
            patch("backend.routes.drivers.status.db_supabase.get_driver_by_id", AsyncMock(return_value=driver)),
            patch(
                "backend.routes.drivers.status._shared._decrypt_driver_pii",
                AsyncMock(return_value={**driver, "license_number": "S1234567"}),
            ),
        ):
            result = await status_mod.get_driver(
                self.DRIVER_ID, current_user={"id": "admin-1", "_admin_verified": True}
            )
        assert result["license_number"] == "S1234567"

    async def test_self_gets_full_decrypted_detail(self):
        from backend.routes.drivers import status as status_mod

        driver = self._driver()
        with (
            patch("backend.routes.drivers.status.db_supabase.get_driver_by_id", AsyncMock(return_value=driver)),
            patch(
                "backend.routes.drivers.status._shared._decrypt_driver_pii",
                AsyncMock(side_effect=lambda d: d),
            ),
        ):
            result = await status_mod.get_driver(self.DRIVER_ID, current_user={"id": "user-owner"})
        assert result["id"] == self.DRIVER_ID

    async def test_rider_with_active_ride_gets_safe_projection(self):
        from backend.routes.drivers import status as status_mod

        driver = self._driver()
        active_ride = {"id": "ride-1", "driver_id": self.DRIVER_ID, "rider_id": "rider-1", "status": "in_progress"}
        with (
            patch("backend.routes.drivers.status.db_supabase.get_driver_by_id", AsyncMock(return_value=driver)),
            patch("backend.routes.drivers.status.db_supabase.get_rows", AsyncMock(return_value=[active_ride])),
        ):
            result = await status_mod.get_driver(self.DRIVER_ID, current_user={"id": "rider-1"})

        assert result == {
            "id": driver["id"],
            "name": driver["name"],
            "rating": driver["rating"],
            "vehicle_make": driver["vehicle_make"],
            "vehicle_model": driver["vehicle_model"],
            "vehicle_color": driver["vehicle_color"],
            "license_plate": driver["license_plate"],
        }
        # PII fields must never leak into the rider-facing projection.
        assert "license_number" not in result
        assert "stripe_account_id" not in result

    async def test_rider_without_active_ride_gets_403(self):
        from fastapi import HTTPException

        from backend.routes.drivers import status as status_mod

        driver = self._driver()
        with (
            patch("backend.routes.drivers.status.db_supabase.get_driver_by_id", AsyncMock(return_value=driver)),
            patch("backend.routes.drivers.status.db_supabase.get_rows", AsyncMock(return_value=[])),
        ):
            with pytest.raises(HTTPException) as exc:
                await status_mod.get_driver(self.DRIVER_ID, current_user={"id": "rider-2"})
        assert exc.value.status_code == 403

    async def test_active_ride_lookup_failure_degrades_to_403_not_500(self):
        """A DB error checking for an active ride must not 500 — the rider
        just falls through to the standard 'not authorized' response."""
        from fastapi import HTTPException

        from backend.routes.drivers import status as status_mod

        driver = self._driver()
        with (
            patch("backend.routes.drivers.status.db_supabase.get_driver_by_id", AsyncMock(return_value=driver)),
            patch(
                "backend.routes.drivers.status.db_supabase.get_rows",
                AsyncMock(side_effect=Exception("db down")),
            ),
        ):
            with pytest.raises(HTTPException) as exc:
                await status_mod.get_driver(self.DRIVER_ID, current_user={"id": "rider-3"})
        assert exc.value.status_code == 403


# ============================================================
# profile.py
# ============================================================


class TestGetDriverConfigSettingsFailure:
    async def test_app_settings_lookup_exception_falls_back_to_defaults(self):
        from backend.routes.drivers import profile as profile_mod

        with patch("backend.settings_loader.get_app_settings", AsyncMock(side_effect=Exception("settings db down"))):
            result = await profile_mod.get_driver_config(current_user={"id": "u1"})

        assert result["ride_offer_timeout_seconds"] == 15
        assert result["pickup_radius_meters"] == 100
        assert result["ride_offer_sound_url"] is None


class TestUpdateMyDriverAutoCreateAndReview:
    async def test_auto_creates_driver_row_when_none_exists(self):
        from backend.routes.drivers import profile as profile_mod

        insert_one = AsyncMock()
        update_one_users = AsyncMock()
        with (
            patch("backend.routes.drivers.profile.db_supabase.get_rows", AsyncMock(return_value=[])),
            patch("backend.routes.drivers.profile.db_supabase.insert_one", insert_one),
            patch("backend.routes.drivers.profile.db_supabase.update_one", update_one_users),
            patch("backend.routes.drivers.profile.generate_driver_code", return_value="DRV123"),
        ):
            req = profile_mod.UpdateDriverProfileRequest(vehicle_make="Honda")
            result = await profile_mod.update_my_driver(
                body=req, current_user={"id": "new-user", "first_name": "Sam", "last_name": "Lee", "phone": "+1555"}
            )

        insert_one.assert_awaited_once()
        assert insert_one.await_args.args[0] == "drivers"
        assert insert_one.await_args.args[1]["vehicle_make"] == "Honda"
        update_one_users.assert_awaited_once_with("users", {"id": "new-user"}, {"role": "driver", "is_driver": True})
        assert result["vehicle_make"] == "Honda"

    async def test_active_driver_vehicle_change_triggers_needs_review(self):
        from backend.routes.drivers import profile as profile_mod

        driver = {
            "id": "d1",
            "user_id": "u1",
            "status": "active",
            "is_online": True,
            "vehicle_make": "Toyota",
        }
        updated = {**driver, "status": "needs_review", "is_online": False, "is_available": False}
        record_mock = AsyncMock()
        notify_mock = AsyncMock()
        period_mock = AsyncMock()
        with (
            patch("backend.routes.drivers.profile.db_supabase.get_rows", AsyncMock(return_value=[driver])),
            patch("backend.routes.drivers.profile.db_supabase.update_one", AsyncMock()),
            patch("backend.routes.drivers.profile.db_supabase.get_driver_by_id", AsyncMock(return_value=updated)),
            patch("backend.routes.drivers.profile._shared._encrypt_driver_pii", AsyncMock(side_effect=lambda d: d)),
            patch("backend.routes.drivers.profile._shared._decrypt_driver_pii", AsyncMock(side_effect=lambda d: d)),
            patch("backend.utils.vehicle_history.record_vehicle_changes", record_mock),
            patch("backend.utils.driver_status_notifications.notify_driver_status_change", notify_mock),
            patch("backend.utils.driver_status_notifications.status_message", return_value="needs review"),
            patch("backend.routes.drivers.profile._deps.record_period_transition", period_mock),
        ):
            req = profile_mod.UpdateDriverProfileRequest(vehicle_make="Honda")
            result = await profile_mod.update_my_driver(body=req, current_user={"id": "u1"})

        record_mock.assert_awaited_once()
        assert record_mock.await_args.kwargs["role"] == "driver"
        period_mock.assert_awaited_once_with("d1", 0)
        notify_mock.assert_awaited_once()
        assert result["status"] == "needs_review"

    async def test_pending_driver_vehicle_change_skips_review_flip(self):
        """A driver who isn't yet 'active' (still pending onboarding) doesn't
        get the needs_review/offline treatment on a vehicle edit."""
        from backend.routes.drivers import profile as profile_mod

        driver = {"id": "d1", "user_id": "u1", "status": "pending", "is_online": False}
        record_mock = AsyncMock()
        period_mock = AsyncMock()
        with (
            patch("backend.routes.drivers.profile.db_supabase.get_rows", AsyncMock(return_value=[driver])),
            patch("backend.routes.drivers.profile.db_supabase.update_one", AsyncMock()),
            patch("backend.routes.drivers.profile.db_supabase.get_driver_by_id", AsyncMock(return_value=driver)),
            patch("backend.routes.drivers.profile._shared._encrypt_driver_pii", AsyncMock(side_effect=lambda d: d)),
            patch("backend.routes.drivers.profile._shared._decrypt_driver_pii", AsyncMock(side_effect=lambda d: d)),
            patch("backend.utils.vehicle_history.record_vehicle_changes", record_mock),
            patch("backend.routes.drivers.profile._deps.record_period_transition", period_mock),
        ):
            req = profile_mod.UpdateDriverProfileRequest(vehicle_make="Honda")
            result = await profile_mod.update_my_driver(body=req, current_user={"id": "u1"})

        record_mock.assert_awaited_once()
        period_mock.assert_not_awaited()
        assert result.get("status") != "needs_review"


@contextmanager
def _heatmap_ctx(settings=None):
    """Combined context manager for heatmap tests — redis miss + configurable settings."""
    with ExitStack() as stack:
        stack.enter_context(_patch_both("utils.redis_client.redis_get", AsyncMock(return_value=None)))
        stack.enter_context(_patch_both("utils.redis_client.redis_set", AsyncMock()))
        stack.enter_context(_patch_both("settings_loader.get_app_settings", AsyncMock(return_value=settings or {})))
        stack.enter_context(_patch_both("utils.metrics.inc", lambda *a, **k: None))
        stack.enter_context(_patch_both("utils.metrics.observe", lambda *a, **k: None))
        yield


def _make_rides(coords, created_at=None):
    """Build ride dicts with coords + timestamps for heatmap tests."""
    from datetime import datetime, timezone

    if created_at is None:
        created_at = datetime.now(timezone.utc).isoformat()
    return [
        {"pickup_lat": lat, "pickup_lng": lng, "created_at": created_at, "status": "completed"} for lat, lng in coords
    ]


class TestGetDemandHeatmap:
    async def test_disabled_area_returns_empty(self):
        from backend.routes.drivers import profile as profile_mod

        driver = {"id": "d1", "user_id": "u1", "service_area_id": "area-1"}

        def fake_get_rows(table, filters, **kw):
            return {"drivers": [driver], "service_areas": [{"show_demand_heatmap": False}]}.get(table, [])

        with patch("backend.routes.drivers.profile.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)):
            result = await profile_mod.get_demand_heatmap(current_user={"id": "u1"})

        assert result == {"enabled": False, "points": [], "total_rides": 0}

    async def test_no_driver_profile_returns_disabled(self):
        from backend.routes.drivers import profile as profile_mod

        with patch("backend.routes.drivers.profile.db_supabase.get_rows", AsyncMock(return_value=[])):
            result = await profile_mod.get_demand_heatmap(current_user={"id": "u-nodriver"})

        assert result["enabled"] is False

    async def test_enabled_area_returns_aggregated_cells(self):
        """≥k rides in the same cell → one centroid point with decayed weight."""
        from backend.routes.drivers import profile as profile_mod

        driver = {"id": "d1", "user_id": "u1", "service_area_id": "area-1"}
        rides = _make_rides(
            [(52.132, -106.664), (52.133, -106.665), (52.1325, -106.6645)],
        )

        def fake_get_rows(table, filters, **kw):
            if table == "drivers":
                return [driver]
            if table == "service_areas":
                return [{"show_demand_heatmap": True}]
            if table == "rides":
                return rides
            return []

        with (
            patch("backend.routes.drivers.profile.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)),
            _heatmap_ctx(),
        ):
            result = await profile_mod.get_demand_heatmap(current_user={"id": "u1"})

        assert result["enabled"] is True
        assert result["total_rides"] == 3
        assert len(result["points"]) >= 1
        assert "refresh_seconds" in result
        assert "generated_at" in result
        for pt in result["points"]:
            assert len(pt) == 3
            assert pt[2] > 0

    async def test_k_floor_suppresses_sparse_cells(self):
        """Cells with < k rides are suppressed entirely (privacy floor)."""
        from backend.routes.drivers import profile as profile_mod

        driver = {"id": "d1", "user_id": "u1", "service_area_id": "area-1"}
        rides = _make_rides([(52.132, -106.664)])

        def fake_get_rows(table, filters, **kw):
            if table == "drivers":
                return [driver]
            if table == "service_areas":
                return [{"show_demand_heatmap": True}]
            if table == "rides":
                return rides
            return []

        with (
            patch("backend.routes.drivers.profile.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)),
            _heatmap_ctx(),
        ):
            result = await profile_mod.get_demand_heatmap(current_user={"id": "u1"})

        assert result["enabled"] is True
        assert result["total_rides"] == 1
        assert result["points"] == []

    async def test_missing_coords_skipped(self):
        from backend.routes.drivers import profile as profile_mod

        driver = {"id": "d1", "user_id": "u1", "service_area_id": "area-1"}
        rides = [
            {"pickup_lat": None, "pickup_lng": -106.0, "created_at": "2026-08-13T12:00:00Z", "status": "completed"},
            {"pickup_lat": 52.0, "pickup_lng": None, "created_at": "2026-08-13T12:00:00Z", "status": "completed"},
        ]

        def fake_get_rows(table, filters, **kw):
            if table == "drivers":
                return [driver]
            if table == "service_areas":
                return [{"show_demand_heatmap": True}]
            if table == "rides":
                return rides
            return []

        with (
            patch("backend.routes.drivers.profile.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)),
            _heatmap_ctx(),
        ):
            result = await profile_mod.get_demand_heatmap(current_user={"id": "u1"})

        assert result["total_rides"] == 0
        assert result["points"] == []

    async def test_recency_decay_weights_recent_higher(self):
        """Recent rides should have higher weight than older rides."""
        from datetime import datetime, timezone

        from backend.routes.drivers import profile as profile_mod

        driver = {"id": "d1", "user_id": "u1", "service_area_id": "area-1"}
        now = datetime.now(timezone.utc)
        coord = (52.132, -106.664)
        rides = [
            {"pickup_lat": coord[0], "pickup_lng": coord[1], "created_at": now.isoformat(), "status": "completed"},
            {
                "pickup_lat": coord[0] + 0.001,
                "pickup_lng": coord[1] + 0.001,
                "created_at": now.isoformat(),
                "status": "completed",
            },
            {
                "pickup_lat": coord[0] + 0.002,
                "pickup_lng": coord[1] + 0.002,
                "created_at": now.isoformat(),
                "status": "completed",
            },
        ]

        def fake_get_rows(table, filters, **kw):
            if table == "drivers":
                return [driver]
            if table == "service_areas":
                return [{"show_demand_heatmap": True}]
            if table == "rides":
                return rides
            return []

        with (
            patch("backend.routes.drivers.profile.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)),
            _heatmap_ctx(),
        ):
            result = await profile_mod.get_demand_heatmap(current_user={"id": "u1"})

        if result["points"]:
            for pt in result["points"]:
                assert pt[2] > 0.9

    async def test_cache_hit_returns_cached(self):
        """When Redis has a cached result, return it without querying rides."""
        import json

        from backend.routes.drivers import profile as profile_mod

        driver = {"id": "d1", "user_id": "u1", "service_area_id": "area-1"}
        cached_data = {
            "enabled": True,
            "points": [[52.0, -106.0, 2.5]],
            "total_rides": 5,
            "refresh_seconds": 90,
            "generated_at": "2026-08-13T12:00:00Z",
        }

        def fake_get_rows(table, filters, **kw):
            if table == "drivers":
                return [driver]
            if table == "service_areas":
                return [{"show_demand_heatmap": True}]
            return []

        with (
            patch("backend.routes.drivers.profile.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)),
            _patch_both("utils.redis_client.redis_get", AsyncMock(return_value=json.dumps(cached_data))),
            _patch_both("utils.metrics.inc", lambda *a, **k: None),
        ):
            result = await profile_mod.get_demand_heatmap(current_user={"id": "u1"})

        assert result == cached_data

    async def test_empty_area_returns_no_points(self):
        from backend.routes.drivers import profile as profile_mod

        driver = {"id": "d1", "user_id": "u1", "service_area_id": "area-1"}

        def fake_get_rows(table, filters, **kw):
            if table == "drivers":
                return [driver]
            if table == "service_areas":
                return [{"show_demand_heatmap": True}]
            if table == "rides":
                return []
            return []

        with (
            patch("backend.routes.drivers.profile.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)),
            _heatmap_ctx(),
        ):
            result = await profile_mod.get_demand_heatmap(current_user={"id": "u1"})

        assert result["enabled"] is True
        assert result["points"] == []
        assert result["total_rides"] == 0


class TestGetDemandHeatmapV2:
    """Tests for the v2 heatmap payload (HM-10): live + baseline + scheduled components."""

    def _v2_settings(self, **overrides):
        base = {"driver_heatmap_v2_enabled": True}
        base.update(overrides)
        return base

    def _service_area(self, **overrides):
        base = {"show_demand_heatmap": True, "surge_multiplier": 1.0, "surge_active": False}
        base.update(overrides)
        return base

    async def test_v2_enabled_returns_cells_and_surge(self):
        from backend.routes.drivers import profile as profile_mod

        driver = {"id": "d1", "user_id": "u1", "service_area_id": "area-1"}
        coord = (52.132, -106.664)
        now_iso = datetime.now(timezone.utc).isoformat()
        rides = _make_rides([coord, (coord[0] + 0.001, coord[1] + 0.001), (coord[0] + 0.002, coord[1] + 0.002)])
        for r in rides:
            r["status"] = "searching"

        call_count = 0

        def fake_get_rows(table, filters, **kw):
            nonlocal call_count
            if table == "drivers":
                return [driver]
            if table == "service_areas":
                return [self._service_area(surge_multiplier=1.5, surge_active=True)]
            if table == "rides":
                call_count += 1
                return rides
            return []

        with (
            patch("backend.routes.drivers.profile.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)),
            _heatmap_ctx(self._v2_settings()),
        ):
            result = await profile_mod.get_demand_heatmap(current_user={"id": "u1"})

        assert result["enabled"] is True
        assert "cells" in result
        assert "surge" in result
        assert result["surge"]["multiplier"] == 1.5
        assert result["surge"]["active"] is True
        for cell in result["cells"]:
            assert "live" in cell
            assert "baseline" in cell
            assert "scheduled" in cell
            assert "lat" in cell
            assert "lng" in cell

    async def test_v2_allowlist_grants_access(self):
        """Driver in heatmap_internal_driver_ids gets v2 even when global flag is off."""
        from backend.routes.drivers import profile as profile_mod

        driver = {"id": "d1", "user_id": "u1", "service_area_id": "area-1"}
        rides = _make_rides([(52.132, -106.664)] * 3)

        def fake_get_rows(table, filters, **kw):
            if table == "drivers":
                return [driver]
            if table == "service_areas":
                return [self._service_area()]
            if table == "rides":
                return rides
            return []

        settings = {
            "driver_heatmap_v2_enabled": False,
            "heatmap_internal_driver_ids": ["u1"],
        }
        with (
            patch("backend.routes.drivers.profile.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)),
            _heatmap_ctx(settings),
        ):
            result = await profile_mod.get_demand_heatmap(current_user={"id": "u1"})

        assert "cells" in result

    async def test_v2_disabled_no_cells(self):
        """Without v2 flag, response has no cells or surge fields."""
        from backend.routes.drivers import profile as profile_mod

        driver = {"id": "d1", "user_id": "u1", "service_area_id": "area-1"}
        rides = _make_rides([(52.132, -106.664)] * 3)

        def fake_get_rows(table, filters, **kw):
            if table == "drivers":
                return [driver]
            if table == "service_areas":
                return [self._service_area()]
            if table == "rides":
                return rides
            return []

        with (
            patch("backend.routes.drivers.profile.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)),
            _heatmap_ctx({"driver_heatmap_v2_enabled": False}),
        ):
            result = await profile_mod.get_demand_heatmap(current_user={"id": "u1"})

        assert "cells" not in result
        assert "surge" not in result

    async def test_v2_k_floor_per_component(self):
        """Components below k-floor are zeroed but cell survives if any component passes."""
        from backend.routes.drivers import profile as profile_mod

        driver = {"id": "d1", "user_id": "u1", "service_area_id": "area-1"}
        now = datetime.now(timezone.utc)
        coord = (52.132, -106.664)
        live_rides = [
            {"pickup_lat": coord[0], "pickup_lng": coord[1], "created_at": now.isoformat(), "status": "searching"},
            {
                "pickup_lat": coord[0] + 0.001,
                "pickup_lng": coord[1] + 0.001,
                "created_at": now.isoformat(),
                "status": "searching",
            },
            {
                "pickup_lat": coord[0] + 0.002,
                "pickup_lng": coord[1] + 0.002,
                "created_at": now.isoformat(),
                "status": "searching",
            },
        ]
        # one old ride at same coords but different hour (won't count as baseline)
        old_rides = [
            {
                "pickup_lat": coord[0],
                "pickup_lng": coord[1],
                "created_at": (now - timedelta(days=1)).isoformat(),
                "status": "completed",
            },
        ]

        call_idx = 0

        def fake_get_rows(table, filters, **kw):
            nonlocal call_idx
            if table == "drivers":
                return [driver]
            if table == "service_areas":
                return [self._service_area()]
            if table == "rides":
                call_idx += 1
                if call_idx == 1:
                    return live_rides + old_rides  # 7-day aggregate
                if call_idx == 2:
                    return live_rides  # 10-min live
                if call_idx == 3:
                    return live_rides + old_rides  # 28-day baseline
                return []  # scheduled
            return []

        with (
            patch("backend.routes.drivers.profile.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)),
            _heatmap_ctx(self._v2_settings()),
        ):
            result = await profile_mod.get_demand_heatmap(current_user={"id": "u1"})

        assert "cells" in result
        assert len(result["cells"]) >= 1
        for cell in result["cells"]:
            assert cell["live"] >= 0
            assert cell["baseline"] >= 0
            assert cell["scheduled"] >= 0

    async def test_v2_surge_mirror_from_service_area(self):
        """Surge data mirrors the service_area fields."""
        from backend.routes.drivers import profile as profile_mod

        driver = {"id": "d1", "user_id": "u1", "service_area_id": "area-1"}
        rides = _make_rides([(52.132, -106.664)] * 3)

        def fake_get_rows(table, filters, **kw):
            if table == "drivers":
                return [driver]
            if table == "service_areas":
                return [self._service_area(surge_multiplier=2.0, surge_active=True)]
            if table == "rides":
                return rides
            return []

        with (
            patch("backend.routes.drivers.profile.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)),
            _heatmap_ctx(self._v2_settings()),
        ):
            result = await profile_mod.get_demand_heatmap(current_user={"id": "u1"})

        assert result["surge"] == {"multiplier": 2.0, "active": True}

    async def test_v2_includes_forecast_when_available(self):
        """v2 response includes a 6-hour forecast array (HM-23)."""
        from backend.routes.drivers import profile as profile_mod

        driver = {"id": "d1", "user_id": "u1", "service_area_id": "area-1"}
        rides = _make_rides([(52.132, -106.664)] * 3)

        mock_forecast = [
            {
                "hour": 14,
                "day_name": "Thu",
                "predicted_rides": 5.0,
                "data_basis": "historical_average",
                "is_peak": False,
                "timestamp": "2026-08-13T14:00:00+00:00",
            },
            {
                "hour": 15,
                "day_name": "Thu",
                "predicted_rides": 8.0,
                "data_basis": "historical_average",
                "is_peak": True,
                "timestamp": "2026-08-13T15:00:00+00:00",
            },
            {
                "hour": 16,
                "day_name": "Thu",
                "predicted_rides": 10.0,
                "data_basis": "historical_average",
                "is_peak": True,
                "timestamp": "2026-08-13T16:00:00+00:00",
            },
        ]

        def fake_get_rows(table, filters, **kw):
            if table == "drivers":
                return [driver]
            if table == "service_areas":
                return [self._service_area()]
            if table == "rides":
                return rides
            return []

        with (
            patch("backend.routes.drivers.profile.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)),
            _heatmap_ctx(self._v2_settings()),
            patch("backend.utils.demand_forecast.forecast_demand", AsyncMock(return_value=mock_forecast)),
        ):
            result = await profile_mod.get_demand_heatmap(current_user={"id": "u1"})

        assert "forecast" in result
        assert len(result["forecast"]) == 3
        for entry in result["forecast"]:
            assert "hour" in entry
            assert "day_name" in entry
            assert "demand" in entry
            assert 0 <= entry["demand"] <= 1.0
            assert "is_peak" in entry

    async def test_v2_forecast_gracefully_degrades(self):
        """If forecast_demand raises, v2 response still works without forecast."""
        from backend.routes.drivers import profile as profile_mod

        driver = {"id": "d1", "user_id": "u1", "service_area_id": "area-1"}
        rides = _make_rides([(52.132, -106.664)] * 3)

        def fake_get_rows(table, filters, **kw):
            if table == "drivers":
                return [driver]
            if table == "service_areas":
                return [self._service_area()]
            if table == "rides":
                return rides
            return []

        with (
            patch("backend.routes.drivers.profile.db_supabase.get_rows", AsyncMock(side_effect=fake_get_rows)),
            _heatmap_ctx(self._v2_settings()),
            patch("backend.utils.demand_forecast.forecast_demand", AsyncMock(side_effect=Exception("DB error"))),
        ):
            result = await profile_mod.get_demand_heatmap(current_user={"id": "u1"})

        assert result["enabled"] is True
        assert "cells" in result
        assert "forecast" not in result


class TestDestinationMode404s:
    async def test_clear_destination_mode_404_when_no_driver(self):
        from fastapi import HTTPException

        from backend.routes.drivers import profile as profile_mod

        with patch("backend.routes.drivers.profile._deps.db.find_one", AsyncMock(return_value=None)):
            with pytest.raises(HTTPException) as exc:
                await profile_mod.clear_destination_mode(current_user={"id": "u-nodriver"})
        assert exc.value.status_code == 404

    async def test_get_destination_mode_404_when_no_driver(self):
        from fastapi import HTTPException

        from backend.routes.drivers import profile as profile_mod

        with patch("backend.routes.drivers.profile._deps.db.find_one", AsyncMock(return_value=None)):
            with pytest.raises(HTTPException) as exc:
                await profile_mod.get_destination_mode(current_user={"id": "u-nodriver"})
        assert exc.value.status_code == 404
