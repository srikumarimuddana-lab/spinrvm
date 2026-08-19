"""Coverage tests for backend/routes/admin/service_areas.py.

Closes the coverage gap (65.80% -> target >=70%, per ACTION_ITEMS.md item 4 /
A1b Track 1 series). TEST-ONLY change — no application code touched.

Priority order (per CLAUDE.md / this series' established pattern):
  1. validation-error paths (400s: airport bbox guard, airport-subregion
     guard, surge_multiplier bounds/justification)
  2. not-found / empty-result shapes (tax config default, vehicle pricing)
  3. DB-failure propagation (503s: surge status)
  4. success-path shape assertions for the large, mostly-untested
     update/delete/fees/tax/vehicle-pricing handlers.

Uses ``patch("backend.routes.admin.service_areas.db_supabase.<fn>", ...)``
(the pattern already used by test_service_area_create_regulatory.py in this
file) rather than mocking the raw supabase client chain, since the handlers
call the ``db_supabase`` module functions directly.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pydantic
import pytest
from fastapi import HTTPException

from backend.routes.admin.service_areas import (
    AreaFeeCreateRequest,
    AreaFeeUpdateRequest,
    AreaTaxRequest,
    ServiceAreaCreateRequest,
    ServiceAreaUpdateRequest,
    SurgePricingRequest,
    admin_create_area_fee,
    admin_create_service_area,
    admin_delete_area_fee,
    admin_delete_service_area,
    admin_get_area_fees,
    admin_get_area_heatmap_config,
    admin_get_area_tax,
    admin_get_service_areas,
    admin_get_surge_status,
    admin_get_vehicle_pricing,
    admin_update_area_fee,
    admin_update_area_tax,
    admin_update_service_area,
    admin_update_surge_pricing,
)

_ADMIN = {"id": "admin-1", "email": "admin@spinr.ca", "role": "super_admin"}


def _patches(**overrides):
    """Return a dict of default AsyncMock patches for db_supabase + side helpers,
    merged with any per-test overrides."""
    defaults = {
        "backend.routes.admin.service_areas.invalidate_fare_cache": AsyncMock(),
        "backend.routes.admin.service_areas.log_admin_action": AsyncMock(),
    }
    defaults.update(overrides)
    return defaults


# ── admin_get_service_areas: parent/child nesting ───────────────────────


class TestGetServiceAreas:
    @pytest.mark.anyio
    async def test_nests_sub_regions_under_parent(self):
        rows = [
            {"id": "parent-1", "name": "Regina", "parent_service_area_id": None},
            {"id": "child-1", "name": "YQR", "parent_service_area_id": "parent-1"},
        ]
        with patch(
            "backend.routes.admin.service_areas.db_supabase.get_rows",
            AsyncMock(return_value=rows),
        ):
            result = await admin_get_service_areas()

        assert len(result) == 1
        assert result[0]["id"] == "parent-1"
        assert len(result[0]["sub_regions"]) == 1
        assert result[0]["sub_regions"][0]["id"] == "child-1"

    @pytest.mark.anyio
    async def test_no_areas_returns_empty_list(self):
        with patch(
            "backend.routes.admin.service_areas.db_supabase.get_rows",
            AsyncMock(return_value=[]),
        ):
            result = await admin_get_service_areas()
        assert result == []


# ── admin_create_service_area: validation guards ────────────────────────


class TestCreateServiceAreaGuards:
    @pytest.mark.anyio
    async def test_airport_polygon_too_large_rejected(self):
        # Bounding box ~ city-sized (~1 degree lat span => ~111km), way over
        # the 10km cap for an airport zone.
        req = ServiceAreaCreateRequest(
            name="Regina Airport",
            is_airport=True,
            parent_service_area_id="parent-1",
            polygon=[
                {"lat": 50.0, "lng": -104.0},
                {"lat": 51.0, "lng": -104.0},
                {"lat": 51.0, "lng": -103.0},
            ],
        )
        with pytest.raises(HTTPException) as exc_info:
            await admin_create_service_area(req, admin=_ADMIN)
        assert exc_info.value.status_code == 400
        assert "too large" in exc_info.value.detail

    @pytest.mark.anyio
    async def test_airport_on_top_level_row_rejected(self):
        req = ServiceAreaCreateRequest(
            name="Regina",
            is_airport=True,
            parent_service_area_id=None,
            polygon=[{"lat": 50.0, "lng": -104.0}],
        )
        with pytest.raises(HTTPException) as exc_info:
            await admin_create_service_area(req, admin=_ADMIN)
        assert exc_info.value.status_code == 400
        assert "sub-region" in exc_info.value.detail

    @pytest.mark.anyio
    async def test_happy_path_inserts_and_returns_area_id(self):
        insert_one = AsyncMock()
        with (
            patch.multiple(
                "backend.routes.admin.service_areas.db_supabase",
                insert_one=insert_one,
                get_rows=AsyncMock(return_value=[]),
            ),
            patch("backend.routes.admin.service_areas.invalidate_fare_cache", AsyncMock()),
            patch("backend.routes.admin.service_areas.log_admin_action", AsyncMock()),
        ):
            req = ServiceAreaCreateRequest(name="Saskatoon", city="Saskatoon")
            result = await admin_create_service_area(req, admin=_ADMIN)

        assert "area_id" in result
        insert_one.assert_awaited_once()
        table, doc = insert_one.await_args.args
        assert table == "service_areas"
        assert doc["name"] == "Saskatoon"

    @pytest.mark.anyio
    async def test_subscription_required_forces_spinr_pass_enabled(self):
        insert_one = AsyncMock()
        with (
            patch.multiple(
                "backend.routes.admin.service_areas.db_supabase",
                insert_one=insert_one,
                get_rows=AsyncMock(return_value=[]),
            ),
            patch("backend.routes.admin.service_areas.invalidate_fare_cache", AsyncMock()),
            patch("backend.routes.admin.service_areas.log_admin_action", AsyncMock()),
        ):
            req = ServiceAreaCreateRequest(name="Moose Jaw", subscription_required=True, spinr_pass_enabled=False)
            await admin_create_service_area(req, admin=_ADMIN)

        _, doc = insert_one.await_args.args
        assert doc["subscription_required"] is True
        assert doc["spinr_pass_enabled"] is True

    @pytest.mark.anyio
    async def test_seeds_vehicle_pricing_from_active_vehicle_types(self):
        insert_one = AsyncMock()
        vt_rows = [{"name": "sedan", "is_active": True}, {"name": "suv", "is_active": True}]
        with (
            patch.multiple(
                "backend.routes.admin.service_areas.db_supabase",
                insert_one=insert_one,
                get_rows=AsyncMock(return_value=vt_rows),
            ),
            patch("backend.routes.admin.service_areas.invalidate_fare_cache", AsyncMock()),
            patch("backend.routes.admin.service_areas.log_admin_action", AsyncMock()),
        ):
            req = ServiceAreaCreateRequest(name="Regina")
            await admin_create_service_area(req, admin=_ADMIN)

        _, doc = insert_one.await_args.args
        assert len(doc["vehicle_pricing"]) == 2
        assert {row["vehicle_type"] for row in doc["vehicle_pricing"]} == {"sedan", "suv"}


# ── admin_update_service_area: validation + coercion branches ───────────


class TestUpdateServiceAreaGuards:
    @pytest.mark.anyio
    async def test_polygon_flip_to_airport_blocked_when_top_level(self):
        existing = {"id": "area-1", "is_airport": False, "parent_service_area_id": None, "polygon": []}
        with patch(
            "backend.routes.admin.service_areas.db_supabase.find_one",
            AsyncMock(return_value=existing),
        ):
            req = ServiceAreaUpdateRequest(is_airport=True)
            with pytest.raises(HTTPException) as exc_info:
                await admin_update_service_area("area-1", req, admin=_ADMIN)
        assert exc_info.value.status_code == 400
        assert "sub-region" in exc_info.value.detail

    def test_surge_multiplier_out_of_range_rejected_by_pydantic_422(self):
        """Regression test for issue #3069: the handler's own manual
        `sm < 1.0 or sm > _SURGE_MAX` re-check was dead code — Pydantic's
        `Field(ge=1.0, le=10.0)` on `ServiceAreaUpdateRequest.surge_multiplier`
        already matches `_SURGE_MAX` exactly and rejects out-of-range values
        with a 422 before the handler ever runs. The dead branch was removed;
        this test pins that the boundary is still enforced (now solely at the
        Pydantic layer)."""
        with pytest.raises(pydantic.ValidationError):
            ServiceAreaUpdateRequest(surge_multiplier=10.01)
        with pytest.raises(pydantic.ValidationError):
            ServiceAreaUpdateRequest(surge_multiplier=0.99)

    @pytest.mark.anyio
    async def test_surge_above_cap_without_justification_rejected(self):
        req = ServiceAreaUpdateRequest(surge_multiplier=3.0)
        with pytest.raises(HTTPException) as exc_info:
            await admin_update_service_area("area-1", req, admin=_ADMIN)
        assert exc_info.value.status_code == 400
        assert "justification" in exc_info.value.detail

    @pytest.mark.anyio
    async def test_surge_above_cap_with_justification_logs_and_succeeds(self):
        log_admin_action = AsyncMock()
        with (
            patch.multiple(
                "backend.routes.admin.service_areas.db_supabase",
                update_one=AsyncMock(),
                find_one=AsyncMock(return_value=None),
            ),
            patch("backend.routes.admin.service_areas.invalidate_fare_cache", AsyncMock()),
            patch("backend.routes.admin.service_areas.log_admin_action", log_admin_action),
        ):
            req = ServiceAreaUpdateRequest(
                surge_multiplier=4.0, surge_justification="Approved by legal — special event"
            )
            result = await admin_update_service_area("area-1", req, admin=_ADMIN)

        assert result == {"message": "Service area updated"}
        # Two log_admin_action calls: one for the override, one for the update.
        assert log_admin_action.await_count == 2
        actions = [c.args[1] for c in log_admin_action.await_args_list]
        assert "surge_override_above_cap" in actions

    @pytest.mark.anyio
    async def test_manual_surge_change_appends_a_history_row(self):
        """A manual surge override must land in surge_pricing.

        Regression test: this is the only UI path that sets a manual
        multiplier, and it wrote no history at all. Because the surge engine
        skips areas with surge_source='manual', *no rows existed* for the
        whole duration of an override — so the admin Surge History chart
        flatlined at the last automatic value and its "contains manual
        overrides" marker could never fire, for exactly the periods that
        carry regulatory weight.
        """
        insert_one = AsyncMock()
        with (
            patch.multiple(
                "backend.routes.admin.service_areas.db_supabase",
                update_one=AsyncMock(),
                find_one=AsyncMock(return_value={"id": "area-1", "surge_source": "manual"}),
                insert_one=insert_one,
            ),
            patch("backend.routes.admin.service_areas.invalidate_fare_cache", AsyncMock()),
            patch("backend.routes.admin.service_areas.log_admin_action", AsyncMock()),
        ):
            req = ServiceAreaUpdateRequest(surge_multiplier=2.0, surge_active=True)
            await admin_update_service_area("area-1", req, admin=_ADMIN)

        assert insert_one.await_count == 1, "manual surge change wrote no history row"
        table, doc = insert_one.await_args.args
        assert table == "surge_pricing"
        assert doc["service_area_id"] == "area-1"
        assert doc["multiplier"] == 2.0
        assert doc["source"] == "manual"

    @pytest.mark.anyio
    async def test_non_surge_update_writes_no_history_row(self):
        """Renaming an area must not pollute the surge history."""
        insert_one = AsyncMock()
        with (
            patch.multiple(
                "backend.routes.admin.service_areas.db_supabase",
                update_one=AsyncMock(),
                find_one=AsyncMock(return_value={"id": "area-1"}),
                insert_one=insert_one,
            ),
            patch("backend.routes.admin.service_areas.invalidate_fare_cache", AsyncMock()),
            patch("backend.routes.admin.service_areas.log_admin_action", AsyncMock()),
        ):
            await admin_update_service_area("area-1", ServiceAreaUpdateRequest(name="Saskatoon North"), admin=_ADMIN)

        assert insert_one.await_count == 0

    @pytest.mark.anyio
    async def test_history_write_failure_does_not_fail_the_surge_change(self):
        """Losing an audit row must not block the operator's actual change."""
        with (
            patch.multiple(
                "backend.routes.admin.service_areas.db_supabase",
                update_one=AsyncMock(),
                find_one=AsyncMock(return_value={"id": "area-1"}),
                insert_one=AsyncMock(side_effect=RuntimeError("surge_pricing unavailable")),
            ),
            patch("backend.routes.admin.service_areas.invalidate_fare_cache", AsyncMock()),
            patch("backend.routes.admin.service_areas.log_admin_action", AsyncMock()),
        ):
            result = await admin_update_service_area(
                "area-1", ServiceAreaUpdateRequest(surge_multiplier=1.5), admin=_ADMIN
            )

        assert result == {"message": "Service area updated"}

    @pytest.mark.anyio
    async def test_surge_above_cap_allowed_when_disabling_surge(self):
        """An above-cap multiplier being turned OFF (surge_enabled=False)
        should not require justification."""
        with (
            patch.multiple(
                "backend.routes.admin.service_areas.db_supabase",
                update_one=AsyncMock(),
                find_one=AsyncMock(return_value=None),
            ),
            patch("backend.routes.admin.service_areas.invalidate_fare_cache", AsyncMock()),
            patch("backend.routes.admin.service_areas.log_admin_action", AsyncMock()),
        ):
            req = ServiceAreaUpdateRequest(surge_multiplier=5.0, surge_enabled=False)
            result = await admin_update_service_area("area-1", req, admin=_ADMIN)
        assert result == {"message": "Service area updated"}

    # ── A29 (ACTION_ITEMS.md): tax-rate justification requirement ──────

    @pytest.mark.anyio
    async def test_tax_change_without_justification_rejected(self):
        req = ServiceAreaUpdateRequest(pst_enabled=True, pst_rate=6.0)
        with pytest.raises(HTTPException) as exc_info:
            await admin_update_service_area("area-1", req, admin=_ADMIN)
        assert exc_info.value.status_code == 400
        assert "justification" in exc_info.value.detail

    @pytest.mark.anyio
    async def test_tax_change_with_justification_logs_and_succeeds(self):
        log_admin_action = AsyncMock()
        with (
            patch.multiple(
                "backend.routes.admin.service_areas.db_supabase",
                update_one=AsyncMock(),
                find_one=AsyncMock(return_value=None),
            ),
            patch("backend.routes.admin.service_areas.invalidate_fare_cache", AsyncMock()),
            patch("backend.routes.admin.service_areas.log_admin_action", log_admin_action),
        ):
            req = ServiceAreaUpdateRequest(
                pst_enabled=True, pst_rate=6.0, tax_justification="SK PST enablement, approved by finance"
            )
            result = await admin_update_service_area("area-1", req, admin=_ADMIN)

        assert result == {"message": "Service area updated"}
        actions = [c.args[1] for c in log_admin_action.await_args_list]
        assert "tax_config_updated" in actions
        tax_call = next(c for c in log_admin_action.await_args_list if c.args[1] == "tax_config_updated")
        assert set(tax_call.args[4]["updated_fields"]) == {"pst_enabled", "pst_rate"}

    @pytest.mark.anyio
    async def test_non_tax_field_update_does_not_require_justification(self):
        """A plain field edit (no gst/pst/hst touched) must not be blocked by
        the tax-justification gate — only actual tax-field changes trigger it."""
        with (
            patch.multiple(
                "backend.routes.admin.service_areas.db_supabase",
                update_one=AsyncMock(),
                find_one=AsyncMock(return_value=None),
            ),
            patch("backend.routes.admin.service_areas.invalidate_fare_cache", AsyncMock()),
            patch("backend.routes.admin.service_areas.log_admin_action", AsyncMock()),
        ):
            req = ServiceAreaUpdateRequest(name="Saskatoon Renamed")
            result = await admin_update_service_area("area-1", req, admin=_ADMIN)
        assert result == {"message": "Service area updated"}

    @pytest.mark.anyio
    async def test_subscription_required_true_forces_spinr_pass_enabled(self):
        update_one = AsyncMock()
        with (
            patch.multiple(
                "backend.routes.admin.service_areas.db_supabase",
                update_one=update_one,
                find_one=AsyncMock(return_value=None),
            ),
            patch("backend.routes.admin.service_areas.invalidate_fare_cache", AsyncMock()),
            patch("backend.routes.admin.service_areas.log_admin_action", AsyncMock()),
        ):
            req = ServiceAreaUpdateRequest(subscription_required=True)
            await admin_update_service_area("area-1", req, admin=_ADMIN)

        _, _, payload = update_one.await_args.args
        assert payload["subscription_required"] is True
        assert payload["spinr_pass_enabled"] is True

    @pytest.mark.anyio
    async def test_spinr_pass_disable_coerced_back_on_when_subscription_required_in_db(self):
        update_one = AsyncMock()
        existing = {"subscription_required": True}
        with (
            patch.multiple(
                "backend.routes.admin.service_areas.db_supabase",
                update_one=update_one,
                find_one=AsyncMock(return_value=existing),
            ),
            patch("backend.routes.admin.service_areas.invalidate_fare_cache", AsyncMock()),
            patch("backend.routes.admin.service_areas.log_admin_action", AsyncMock()),
        ):
            req = ServiceAreaUpdateRequest(spinr_pass_enabled=False)
            await admin_update_service_area("area-1", req, admin=_ADMIN)

        _, _, payload = update_one.await_args.args
        assert payload["spinr_pass_enabled"] is True

    @pytest.mark.anyio
    async def test_disabling_surge_clears_active_and_multiplier(self):
        update_one = AsyncMock()
        with (
            patch.multiple(
                "backend.routes.admin.service_areas.db_supabase",
                update_one=update_one,
                find_one=AsyncMock(return_value=None),
            ),
            patch("backend.routes.admin.service_areas.invalidate_fare_cache", AsyncMock()),
            patch("backend.routes.admin.service_areas.log_admin_action", AsyncMock()),
        ):
            req = ServiceAreaUpdateRequest(surge_enabled=False)
            await admin_update_service_area("area-1", req, admin=_ADMIN)

        _, _, payload = update_one.await_args.args
        assert payload["surge_enabled"] is False
        assert payload["surge_active"] is False
        assert payload["surge_multiplier"] == 1.0

    @pytest.mark.anyio
    async def test_airport_fee_zero_turns_off_is_airport(self):
        update_one = AsyncMock()
        with (
            patch.multiple(
                "backend.routes.admin.service_areas.db_supabase",
                update_one=update_one,
                find_one=AsyncMock(return_value=None),
            ),
            patch("backend.routes.admin.service_areas.invalidate_fare_cache", AsyncMock()),
            patch("backend.routes.admin.service_areas.log_admin_action", AsyncMock()),
        ):
            req = ServiceAreaUpdateRequest(airport_fee=0)
            await admin_update_service_area("area-1", req, admin=_ADMIN)

        _, _, payload = update_one.await_args.args
        assert payload["is_airport"] is False

    @pytest.mark.anyio
    async def test_empty_payload_skips_db_write(self):
        update_one = AsyncMock()
        with (
            patch.multiple(
                "backend.routes.admin.service_areas.db_supabase",
                update_one=update_one,
                find_one=AsyncMock(return_value=None),
            ),
            patch("backend.routes.admin.service_areas.invalidate_fare_cache", AsyncMock()) as inv,
            patch("backend.routes.admin.service_areas.log_admin_action", AsyncMock()),
        ):
            req = ServiceAreaUpdateRequest()
            result = await admin_update_service_area("area-1", req, admin=_ADMIN)

        update_one.assert_not_awaited()
        inv.assert_not_awaited()
        assert result == {"message": "Service area updated"}


# ── admin_delete_service_area ────────────────────────────────────────────


class TestDeleteServiceArea:
    @pytest.mark.anyio
    async def test_deletes_invalidates_cache_and_logs(self):
        delete_many = AsyncMock()
        log_admin_action = AsyncMock()
        with (
            patch("backend.routes.admin.service_areas.db_supabase.delete_many", delete_many),
            patch("backend.routes.admin.service_areas.invalidate_fare_cache", AsyncMock()) as inv,
            patch("backend.routes.admin.service_areas.log_admin_action", log_admin_action),
        ):
            result = await admin_delete_service_area("area-1", admin=_ADMIN)

        delete_many.assert_awaited_once_with("service_areas", {"id": "area-1"})
        inv.assert_awaited_once()
        log_admin_action.assert_awaited_once()
        assert result == {"message": "Service area deleted"}


# ── admin_update_surge_pricing ───────────────────────────────────────────


class TestUpdateSurgePricing:
    @pytest.mark.anyio
    async def test_activate_surge_updates_area_and_inserts_history_row(self):
        update_one = AsyncMock()
        insert_one = AsyncMock()
        with (
            patch.multiple(
                "backend.routes.admin.service_areas.db_supabase",
                update_one=update_one,
                insert_one=insert_one,
                get_rows=AsyncMock(return_value=[]),  # no existing surge_pricing row
            ),
            patch("backend.routes.admin.service_areas.invalidate_fare_cache", AsyncMock()),
            patch("backend.routes.admin.service_areas.log_admin_action", AsyncMock()),
        ):
            req = SurgePricingRequest(multiplier=1.5, is_active=True)
            result = await admin_update_surge_pricing("area-1", req, admin=_ADMIN)

        assert result == {"message": "Surge pricing updated"}
        area_call = update_one.await_args_list[0]
        assert area_call.args[0] == "service_areas"
        assert area_call.args[2]["surge_enabled"] is True
        assert area_call.args[2]["surge_active"] is True
        # Ranked blocker #21 (docs/audit/2026-08-18-full-fleet-whole-app-audit.md):
        # without this stamp surge_engine.py treats the row as surge_source
        # unset -> defaults to "auto" -> the auto engine silently overwrites
        # this manual override on its next 2-minute pass.
        assert area_call.args[2]["surge_source"] == "manual"
        insert_one.assert_awaited_once()

    @pytest.mark.anyio
    async def test_deactivate_surge_resets_multiplier_and_appends_history(self):
        """Deactivating surge writes the service_areas row and APPENDS history.

        This previously read surge_pricing for an existing row and, when it
        found one, called update_one with a filter matching every row for the
        area while the payload carried a freshly-generated `id`. That both
        destroyed the audit trail and would raise a unique violation (-> 500)
        as soon as the surge engine had appended more than one row — i.e.
        within ~4 minutes of surge being enabled. surge_pricing is append-only.
        """
        update_one = AsyncMock()
        insert_one = AsyncMock()
        with (
            patch.multiple(
                "backend.routes.admin.service_areas.db_supabase",
                update_one=update_one,
                insert_one=insert_one,
                find_one=AsyncMock(return_value={"id": "area-1"}),
                get_rows=AsyncMock(return_value=[{"id": "surge-1"}]),
            ),
            patch("backend.routes.admin.service_areas.invalidate_fare_cache", AsyncMock()),
            patch("backend.routes.admin.service_areas.log_admin_action", AsyncMock()),
        ):
            req = SurgePricingRequest(multiplier=1.0, is_active=False)
            await admin_update_surge_pricing("area-1", req, admin=_ADMIN)

        # The only update_one is the authoritative service_areas write.
        assert [c.args[0] for c in update_one.await_args_list] == ["service_areas"]
        area_call = update_one.await_args_list[0]
        assert area_call.args[2]["surge_source"] == "manual"
        # History is appended, never rewritten.
        insert_one.assert_awaited_once()
        table, doc = insert_one.await_args.args
        assert table == "surge_pricing"
        assert doc["source"] == "manual"
        assert doc["is_active"] is False
        assert "id" not in doc, "let the DB generate the PK; a client-set id is what collided"

    @pytest.mark.anyio
    async def test_surge_source_stamped_matches_sibling_endpoint(self):
        """Regression test for ranked blocker #21: this endpoint's
        service_areas write must stamp surge_source="manual", the exact
        value admin_update_service_area's GeneralTabForm-driven path already
        writes (see TestUpdateServiceArea's surge tests). surge_engine.py
        defaults a missing/unset surge_source to "auto" and only skips rows
        stamped "manual" -- an unstamped row from this route would be
        silently overwritten by the auto engine's next 2-minute pass.
        """
        update_one = AsyncMock()
        with (
            patch.multiple(
                "backend.routes.admin.service_areas.db_supabase",
                update_one=update_one,
                insert_one=AsyncMock(),
                get_rows=AsyncMock(return_value=[]),
            ),
            patch("backend.routes.admin.service_areas.invalidate_fare_cache", AsyncMock()),
            patch("backend.routes.admin.service_areas.log_admin_action", AsyncMock()),
        ):
            req = SurgePricingRequest(multiplier=2.0, is_active=True)
            await admin_update_surge_pricing("area-1", req, admin=_ADMIN)

        area_call = update_one.await_args_list[0]
        assert area_call.args[0] == "service_areas"
        assert area_call.args[2]["surge_source"] == "manual"

    def test_surge_multiplier_hard_capped_at_2_5_by_pydantic(self):
        """SurgePricingRequest.multiplier is Field(ge=1.0, le=2.5) -- unlike
        ServiceAreaUpdateRequest.surge_multiplier (le=10.0, with a >2.5
        justification gate in admin_update_service_area), this dedicated
        /surge endpoint structurally cannot accept an above-cap value at all,
        so there is no reachable justification check to add here: pydantic's
        422 already blocks it before the handler runs.
        """
        with pytest.raises(pydantic.ValidationError):
            SurgePricingRequest(multiplier=2.51, is_active=True)


# ── admin_get_surge_status ────────────────────────────────────────────────


class TestGetSurgeStatus:
    @pytest.mark.anyio
    async def test_happy_path_returns_status(self):
        with patch(
            "backend.utils.surge_engine.get_surge_status",
            AsyncMock(return_value={"areas": []}),
        ):
            result = await admin_get_surge_status()
        assert result == {"areas": []}

    @pytest.mark.anyio
    async def test_db_error_returns_503(self):
        with patch(
            "backend.utils.surge_engine.get_surge_status",
            AsyncMock(side_effect=RuntimeError("boom")),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await admin_get_surge_status()
        assert exc_info.value.status_code == 503


# ── surge-status cache invalidation ───────────────────────────────────────


class TestSurgeStatusCacheInvalidation:
    """`GET /surge/status` is cached for 30s. Any write that changes surge state
    must drop it, or the operator who just flipped surge sees the OLD multiplier
    on the screen whose whole job is confirming a regulated price took effect —
    and reasonably concludes the toggle failed."""

    @pytest.mark.anyio
    async def test_surge_toggle_invalidates_the_status_cache(self):
        invalidate = AsyncMock()
        with (
            patch("backend.routes.admin.service_areas.db_supabase.update_one", AsyncMock()),
            patch("backend.routes.admin.service_areas._record_manual_surge_history", AsyncMock()),
            patch("backend.utils.surge_engine.invalidate_surge_status_cache", invalidate),
            patch("backend.routes.admin.service_areas.invalidate_fare_cache", AsyncMock()),
            patch("backend.routes.admin.service_areas.log_admin_action", AsyncMock()),
        ):
            await admin_update_surge_pricing("area-1", SurgePricingRequest(multiplier=1.75, is_active=True), _ADMIN)
        invalidate.assert_awaited_once()

    @pytest.mark.anyio
    async def test_area_update_touching_surge_invalidates(self):
        invalidate = AsyncMock()
        with (
            patch("backend.routes.admin.service_areas.db_supabase.update_one", AsyncMock()),
            patch("backend.routes.admin.service_areas._record_manual_surge_history", AsyncMock()),
            patch("backend.utils.surge_engine.invalidate_surge_status_cache", invalidate),
            patch("backend.routes.admin.service_areas.invalidate_fare_cache", AsyncMock()),
            patch("backend.routes.admin.service_areas.log_admin_action", AsyncMock()),
        ):
            await admin_update_service_area("area-1", ServiceAreaUpdateRequest(surge_enabled=False), _ADMIN)
        invalidate.assert_awaited_once()

    @pytest.mark.anyio
    async def test_unrelated_area_update_does_not_invalidate(self):
        """A name edit must not blow away a cache it cannot have affected."""
        invalidate = AsyncMock()
        with (
            patch("backend.routes.admin.service_areas.db_supabase.update_one", AsyncMock()),
            patch("backend.routes.admin.service_areas._record_manual_surge_history", AsyncMock()),
            patch("backend.utils.surge_engine.invalidate_surge_status_cache", invalidate),
            patch("backend.routes.admin.service_areas.invalidate_fare_cache", AsyncMock()),
            patch("backend.routes.admin.service_areas.log_admin_action", AsyncMock()),
        ):
            await admin_update_service_area("area-1", ServiceAreaUpdateRequest(name="Regina"), _ADMIN)
        invalidate.assert_not_awaited()


# ── area fees CRUD ────────────────────────────────────────────────────────


class TestAreaFees:
    @pytest.mark.anyio
    async def test_get_area_fees_returns_rows(self):
        rows = [{"id": "fee-1", "fee_name": "Booking"}]
        with patch(
            "backend.routes.admin.service_areas.db_supabase.get_rows",
            AsyncMock(return_value=rows),
        ):
            result = await admin_get_area_fees("area-1")
        assert result == rows

    @pytest.mark.anyio
    async def test_create_area_fee_inserts_and_returns_doc(self):
        insert_one = AsyncMock()
        audit_mock = AsyncMock(return_value="audit-1")
        with (
            patch("backend.routes.admin.service_areas.db_supabase.insert_one", insert_one),
            patch("backend.routes.admin.service_areas.log_admin_action", audit_mock),
        ):
            req = AreaFeeCreateRequest(fee_name="Peak", fee_type="custom", amount=2.5)
            result = await admin_create_area_fee("area-1", req, admin=_ADMIN)

        assert result["fee_name"] == "Peak"
        assert result["service_area_id"] == "area-1"
        insert_one.assert_awaited_once()
        audit_mock.assert_awaited_once()
        assert audit_mock.call_args[0][1] == "area_fee_created"

    @pytest.mark.anyio
    async def test_update_area_fee_only_sends_provided_fields(self):
        update_one = AsyncMock()
        audit_mock = AsyncMock(return_value="audit-2")
        with (
            patch("backend.routes.admin.service_areas.db_supabase.update_one", update_one),
            patch("backend.routes.admin.service_areas.log_admin_action", audit_mock),
        ):
            req = AreaFeeUpdateRequest(amount=9.99)
            result = await admin_update_area_fee("area-1", "fee-1", req, admin=_ADMIN)

        assert result == {"message": "Area fee updated"}
        table, filters, updates = update_one.await_args.args
        assert table == "area_fees"
        assert filters == {"id": "fee-1"}
        assert updates["amount"] == 9.99
        assert "fee_name" not in updates
        audit_mock.assert_awaited_once()
        assert audit_mock.call_args[0][1] == "area_fee_updated"

    @pytest.mark.anyio
    async def test_update_area_fee_empty_payload_skips_write(self):
        update_one = AsyncMock()
        audit_mock = AsyncMock()
        with (
            patch("backend.routes.admin.service_areas.db_supabase.update_one", update_one),
            patch("backend.routes.admin.service_areas.log_admin_action", audit_mock),
        ):
            req = AreaFeeUpdateRequest()
            result = await admin_update_area_fee("area-1", "fee-1", req, admin=_ADMIN)

        update_one.assert_not_awaited()
        audit_mock.assert_not_awaited()
        assert result == {"message": "Area fee updated"}

    @pytest.mark.anyio
    async def test_delete_area_fee(self):
        delete_many = AsyncMock()
        audit_mock = AsyncMock(return_value="audit-3")
        with (
            patch("backend.routes.admin.service_areas.db_supabase.delete_many", delete_many),
            patch("backend.routes.admin.service_areas.log_admin_action", audit_mock),
        ):
            result = await admin_delete_area_fee("area-1", "fee-1", admin=_ADMIN)

        delete_many.assert_awaited_once_with("area_fees", {"id": "fee-1"})
        assert result == {"message": "Area fee deleted"}
        audit_mock.assert_awaited_once_with(
            _ADMIN, "area_fee_deleted", "area_fees", "fee-1", {"service_area_id": "area-1"}
        )


# ── area tax config ────────────────────────────────────────────────────────


class TestAreaTax:
    @pytest.mark.anyio
    async def test_get_area_tax_returns_defaults_when_area_missing(self):
        with patch(
            "backend.routes.admin.service_areas.db_supabase.get_rows",
            AsyncMock(return_value=[]),
        ):
            result = await admin_get_area_tax("missing-area")

        assert result["service_area_id"] == "missing-area"
        assert result["gst_enabled"] is True
        assert result["gst_rate"] == 5.0
        assert result["pst_enabled"] is False

    @pytest.mark.anyio
    async def test_get_area_tax_returns_row_values(self):
        row = {
            "gst_enabled": False,
            "gst_rate": 0,
            "pst_enabled": True,
            "pst_rate": 6.0,
            "hst_enabled": False,
            "hst_rate": 0,
        }
        with patch(
            "backend.routes.admin.service_areas.db_supabase.get_rows",
            AsyncMock(return_value=[row]),
        ):
            result = await admin_get_area_tax("area-1")

        assert result["pst_enabled"] is True
        assert result["pst_rate"] == 6.0

    @pytest.mark.anyio
    async def test_update_area_tax_writes_and_returns_new_values(self):
        update_one = AsyncMock()
        updated_row = {
            "gst_enabled": True,
            "gst_rate": 5.0,
            "pst_enabled": True,
            "pst_rate": 6.0,
            "hst_enabled": False,
            "hst_rate": 0,
        }
        with (
            patch.multiple(
                "backend.routes.admin.service_areas.db_supabase",
                update_one=update_one,
                get_rows=AsyncMock(return_value=[updated_row]),
            ),
            patch("backend.routes.admin.service_areas.log_admin_action", AsyncMock()) as log_action,
        ):
            req = AreaTaxRequest(pst_enabled=True, pst_rate=6.0, tax_justification="SK PST enablement")
            result = await admin_update_area_tax("area-1", req, admin=_ADMIN)

        update_one.assert_awaited_once()
        log_action.assert_awaited_once()
        assert log_action.call_args.args[1] == "tax_config_updated"
        assert result["pst_enabled"] is True
        assert result["pst_rate"] == 6.0

    @pytest.mark.anyio
    async def test_update_area_tax_requires_justification(self):
        update_one = AsyncMock()
        with patch.multiple(
            "backend.routes.admin.service_areas.db_supabase",
            update_one=update_one,
            get_rows=AsyncMock(return_value=[{"id": "area-1"}]),
        ):
            req = AreaTaxRequest(pst_enabled=True, pst_rate=6.0)
            with pytest.raises(HTTPException) as exc_info:
                await admin_update_area_tax("area-1", req, admin=_ADMIN)

        assert exc_info.value.status_code == 400
        update_one.assert_not_awaited()

    @pytest.mark.anyio
    async def test_update_area_tax_404s_on_missing_area_no_write_no_audit(self):
        """A29 follow-up: previously fell through to an unhandled
        AttributeError (500) at the final `area.get(...)` — worse, would
        have already called update_one/log_admin_action for a nonexistent
        area before crashing. Now 404s up front with neither side effect."""
        update_one = AsyncMock()
        with (
            patch.multiple(
                "backend.routes.admin.service_areas.db_supabase",
                update_one=update_one,
                get_rows=AsyncMock(return_value=[]),
            ),
            patch("backend.routes.admin.service_areas.log_admin_action", AsyncMock()) as log_action,
        ):
            req = AreaTaxRequest(pst_enabled=True, pst_rate=6.0, tax_justification="test")
            with pytest.raises(HTTPException) as exc_info:
                await admin_update_area_tax("missing-area", req, admin=_ADMIN)

        assert exc_info.value.status_code == 404
        update_one.assert_not_awaited()
        log_action.assert_not_awaited()

    @pytest.mark.anyio
    async def test_update_area_tax_empty_payload_skips_write_still_returns_row(self):
        update_one = AsyncMock()
        row = {
            "gst_enabled": True,
            "gst_rate": 5.0,
            "pst_enabled": False,
            "pst_rate": 0,
            "hst_enabled": False,
            "hst_rate": 0,
        }
        with patch.multiple(
            "backend.routes.admin.service_areas.db_supabase",
            update_one=update_one,
            get_rows=AsyncMock(return_value=[row]),
        ):
            req = AreaTaxRequest()
            result = await admin_update_area_tax("area-1", req, admin=_ADMIN)

        update_one.assert_not_awaited()
        assert result["gst_rate"] == 5.0


# ── vehicle pricing ─────────────────────────────────────────────────────


class TestVehiclePricing:
    @pytest.mark.anyio
    async def test_returns_vehicle_types_and_fare_configs(self):
        vt = [{"name": "sedan"}]
        fc = [{"vehicle_type": "sedan", "base_fare": 5.0}]

        async def _get_rows(table, *args, **kwargs):
            if table == "vehicle_types":
                return vt
            return fc

        with patch(
            "backend.routes.admin.service_areas.db_supabase.get_rows",
            AsyncMock(side_effect=_get_rows),
        ):
            result = await admin_get_vehicle_pricing("area-1")

        assert result["vehicle_types"] == vt
        assert result["fare_configs"] == fc

    @pytest.mark.anyio
    async def test_missing_rows_default_to_empty_lists(self):
        with patch(
            "backend.routes.admin.service_areas.db_supabase.get_rows",
            AsyncMock(return_value=None),
        ):
            result = await admin_get_vehicle_pricing("area-1")

        assert result["vehicle_types"] == []
        assert result["fare_configs"] == []


# ── Per-area heatmap config (migration 312) ───────────────────────────────


class TestPerAreaHeatmapConfig:
    """Per-area overrides for the driver-heatmap tuning knobs.

    Validation here is deliberately strict rather than store-and-clamp-later:
    this blob reaches a driver-facing endpoint and one of its keys is a PIPEDA
    k-anonymity control, so an operator who types 0 must get a 422 saying so —
    not a silent snap to 1 that looks like it saved what they asked for. The
    read site clamps independently for the paths that never come through here
    (direct SQL, migrations, bulk scripts).
    """

    def test_valid_overrides_are_accepted(self):
        req = ServiceAreaUpdateRequest(heatmap_config={"k_floor": 5, "baseline_window_days": 56})
        assert req.heatmap_config == {"k_floor": 5, "baseline_window_days": 56}

    def test_empty_object_clears_all_overrides(self):
        """An area must be able to go back to inheriting everything."""
        assert ServiceAreaUpdateRequest(heatmap_config={}).heatmap_config == {}

    def test_null_value_drops_that_single_override(self):
        req = ServiceAreaUpdateRequest(heatmap_config={"k_floor": 5, "refresh_seconds": None})
        assert req.heatmap_config == {"k_floor": 5}

    def test_unknown_key_is_rejected_with_a_useful_message(self):
        with pytest.raises(pydantic.ValidationError) as exc:
            ServiceAreaUpdateRequest(heatmap_config={"kfloor": 5})
        assert "unknown heatmap config key" in str(exc.value)

    @pytest.mark.parametrize(
        "key,value",
        [
            ("k_floor", 0),  # would disable the k-anonymity floor
            ("k_floor", 51),
            ("refresh_seconds", 1),  # would make the fleet poll every second
            ("refresh_seconds", 601),
            ("cell_lat_deg", 0),  # ZeroDivisionError in the cell-key math
            ("live_window_days", 0),
            ("baseline_window_days", 1),
            ("forecast_hours_ahead", 100),
        ],
    )
    def test_out_of_range_value_is_rejected(self, key, value):
        with pytest.raises(pydantic.ValidationError):
            ServiceAreaUpdateRequest(heatmap_config={key: value})

    def test_non_numeric_value_is_rejected(self):
        with pytest.raises(pydantic.ValidationError):
            ServiceAreaUpdateRequest(heatmap_config={"k_floor": "abc"})

    def test_untouched_config_is_omitted_from_the_update(self):
        """exclude_none keeps a partial save from clearing an area's overrides."""
        assert "heatmap_config" not in ServiceAreaUpdateRequest().model_dump(exclude_none=True)

    @pytest.mark.anyio
    async def test_config_is_persisted_on_update(self):
        update_one = AsyncMock()
        with (
            patch.multiple(
                "backend.routes.admin.service_areas.db_supabase",
                update_one=update_one,
                find_one=AsyncMock(return_value={"id": "area-1"}),
                insert_one=AsyncMock(),
            ),
            patch("backend.routes.admin.service_areas.invalidate_fare_cache", AsyncMock()),
            patch("backend.routes.admin.service_areas.log_admin_action", AsyncMock()),
        ):
            req = ServiceAreaUpdateRequest(heatmap_config={"k_floor": 9})
            await admin_update_service_area("area-1", req, admin=_ADMIN)

        payload = update_one.await_args.args[2]
        assert payload["heatmap_config"] == {"k_floor": 9}, "override must reach the DB write"

    @pytest.mark.anyio
    async def test_endpoint_reports_effective_overrides_and_inherited(self):
        area = {"id": "area-1", "name": "Saskatoon", "heatmap_config": {"k_floor": 9}}
        with (
            patch(
                "backend.routes.admin.service_areas.db_supabase.find_one",
                AsyncMock(return_value=area),
            ),
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(return_value={"heatmap_k_floor": 4, "heatmap_refresh_seconds": 200}),
            ),
        ):
            result = await admin_get_area_heatmap_config("area-1")

        assert result["effective"]["k_floor"] == 9, "area override wins"
        assert result["effective"]["refresh_seconds"] == 200, "unset key still inherits the global"
        # The overrides/inherited split is what lets the form show "inherits 4"
        # separately from "explicitly set to 4" — they diverge when the global moves.
        assert result["overrides"] == {"k_floor": 9}
        assert result["inherited"]["k_floor"] == 4
        # Bounds are served, not duplicated in the frontend, so they can't drift.
        assert result["spec"]["k_floor"]["min"] == 1
        assert result["spec"]["k_floor"]["max"] == 50

    @pytest.mark.anyio
    async def test_endpoint_404s_for_a_missing_area(self):
        with patch(
            "backend.routes.admin.service_areas.db_supabase.find_one",
            AsyncMock(return_value=None),
        ):
            with pytest.raises(HTTPException) as exc:
                await admin_get_area_heatmap_config("nope")
        assert exc.value.status_code == 404

    @pytest.mark.anyio
    async def test_endpoint_503s_rather_than_reporting_defaults_as_globals(self):
        """A settings read failure must not be dressed up as real config."""
        with (
            patch(
                "backend.routes.admin.service_areas.db_supabase.find_one",
                AsyncMock(return_value={"id": "area-1", "name": "X"}),
            ),
            patch(
                "backend.settings_loader.get_app_settings",
                AsyncMock(side_effect=RuntimeError("settings table down")),
            ),
        ):
            with pytest.raises(HTTPException) as exc:
                await admin_get_area_heatmap_config("area-1")
        assert exc.value.status_code == 503
