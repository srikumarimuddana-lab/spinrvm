"""Coverage tests for backend/routes/admin/vehicle_fleet.py.

Complements test_admin_vehicle_illustration.py (illustration_url CRUD
round-trip). This file covers the remaining write/mutation and
error-handling branches: marker-variant validation, illustration/marker
upload validation gates (content-type, size, magic-byte, transparency),
storage-upload failure handling, vehicle-type delete-blocked-by-usage
guard, fare-config CRUD, and lost-and-found CRUD including the driver
push-notification best-effort path.

TEST-ONLY change; no application code modified.
"""

from __future__ import annotations

import io
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, UploadFile
from PIL import Image

from backend.routes.admin import vehicle_fleet as vf

ADMIN = {"id": "admin-1", "role": "super_admin"}


def _transparent_png_bytes() -> bytes:
    img = Image.new("RGBA", (10, 10), (255, 0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _opaque_png_bytes() -> bytes:
    img = Image.new("RGBA", (10, 10), (255, 0, 0, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _upload_file(content: bytes, content_type: str, filename: str = "art.png") -> UploadFile:
    uf = UploadFile(filename=filename, file=io.BytesIO(content))
    uf.headers = {"content-type": content_type}
    return uf


# ---------------------------------------------------------------------------
# marker variant validation
# ---------------------------------------------------------------------------


class TestMarkerVariantValidation:
    def test_valid_variant_noop(self):
        vf._validate_marker_variant("standard")  # no raise

    def test_invalid_variant_raises_422(self):
        with pytest.raises(HTTPException) as exc:
            vf._validate_marker_variant("sportscar")
        assert exc.value.status_code == 422

    @pytest.mark.asyncio
    async def test_create_vehicle_type_rejects_bad_marker_variant(self):
        req = vf.VehicleTypeCreateRequest(name="X", marker_variant="bogus")
        with pytest.raises(HTTPException) as exc:
            await vf.admin_create_vehicle_type(req)
        assert exc.value.status_code == 422

    @pytest.mark.asyncio
    async def test_update_vehicle_type_rejects_bad_marker_variant(self):
        req = vf.VehicleTypeUpdateRequest(marker_variant="bogus")
        with pytest.raises(HTTPException) as exc:
            await vf.admin_update_vehicle_type("vt_1", req)
        assert exc.value.status_code == 422


# ---------------------------------------------------------------------------
# transparency validation helper
# ---------------------------------------------------------------------------


class TestImageTransparencyValidation:
    def test_jpeg_always_rejected(self):
        with pytest.raises(HTTPException) as exc:
            vf._validate_image_transparency(b"not-real-jpeg-bytes", "image/jpeg", "car illustration")
        assert exc.value.status_code == 400
        assert "JPEG" in exc.value.detail

    def test_undecodable_bytes_rejected(self):
        with pytest.raises(HTTPException) as exc:
            vf._validate_image_transparency(b"garbage-not-an-image", "image/png", "car illustration")
        assert exc.value.status_code == 400
        assert "decode" in exc.value.detail

    def test_opaque_corners_rejected(self):
        with pytest.raises(HTTPException) as exc:
            vf._validate_image_transparency(_opaque_png_bytes(), "image/png", "map marker")
        assert exc.value.status_code == 400
        assert "opaque background" in exc.value.detail

    def test_transparent_corners_accepted(self):
        vf._validate_image_transparency(_transparent_png_bytes(), "image/png", "car illustration")  # no raise


# ---------------------------------------------------------------------------
# vehicle-type CRUD (list/create/update)
# ---------------------------------------------------------------------------


class TestVehicleTypeCrud:
    @pytest.mark.asyncio
    async def test_list_vehicle_types(self):
        with patch.object(vf.db_supabase, "get_rows", AsyncMock(return_value=[{"id": "vt_1"}])):
            result = await vf.admin_get_vehicle_types()
        assert result == [{"id": "vt_1"}]

    @pytest.mark.asyncio
    async def test_create_vehicle_type_invalidates_fare_cache(self):
        req = vf.VehicleTypeCreateRequest(name="Standard")
        with (
            patch.object(vf.db_supabase, "insert_one", AsyncMock(return_value={"id": "vt_1"})),
            patch.object(vf, "invalidate_fare_cache", AsyncMock()) as inv,
            patch.object(vf, "log_admin_action", AsyncMock(return_value="audit-1")) as audit_mock,
        ):
            result = await vf.admin_create_vehicle_type(req, admin=ADMIN)
        assert result == {"type_id": "vt_1"}
        inv.assert_awaited_once()
        audit_mock.assert_awaited_once()
        assert audit_mock.call_args[0][1] == "vehicle_type_created"

    @pytest.mark.asyncio
    async def test_create_vehicle_type_handles_non_dict_row(self):
        req = vf.VehicleTypeCreateRequest(name="Standard")
        with (
            patch.object(vf.db_supabase, "insert_one", AsyncMock(return_value=None)),
            patch.object(vf, "invalidate_fare_cache", AsyncMock()),
            patch.object(vf, "log_admin_action", AsyncMock(return_value="audit-2")),
        ):
            result = await vf.admin_create_vehicle_type(req, admin=ADMIN)
        assert result == {"type_id": ""}

    @pytest.mark.asyncio
    async def test_update_vehicle_type_no_fields_skips_write(self):
        update_mock = AsyncMock()
        with (
            patch.object(vf.db_supabase, "update_one", update_mock),
            patch.object(vf, "invalidate_fare_cache", AsyncMock()) as inv,
        ):
            result = await vf.admin_update_vehicle_type("vt_1", vf.VehicleTypeUpdateRequest())
        assert result == {"message": "Vehicle type updated"}
        update_mock.assert_not_awaited()
        inv.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_update_vehicle_type_clears_illustration_with_empty_string(self):
        seen = {}

        async def _capture(table, match, updates):
            seen.update(updates)

        with (
            patch.object(vf.db_supabase, "update_one", AsyncMock(side_effect=_capture)),
            patch.object(vf, "invalidate_fare_cache", AsyncMock()),
            patch.object(vf, "log_admin_action", AsyncMock(return_value="audit-3")),
        ):
            await vf.admin_update_vehicle_type("vt_1", vf.VehicleTypeUpdateRequest(illustration_url=""), admin=ADMIN)
        assert seen["illustration_url"] is None

    @pytest.mark.asyncio
    async def test_update_vehicle_type_clears_marker_image_with_empty_string(self):
        seen = {}

        async def _capture(table, match, updates):
            seen.update(updates)

        with (
            patch.object(vf.db_supabase, "update_one", AsyncMock(side_effect=_capture)),
            patch.object(vf, "invalidate_fare_cache", AsyncMock()),
            patch.object(vf, "log_admin_action", AsyncMock(return_value="audit-4")),
        ):
            await vf.admin_update_vehicle_type("vt_1", vf.VehicleTypeUpdateRequest(marker_image_url=""), admin=ADMIN)
        assert seen["marker_image_url"] is None


# ---------------------------------------------------------------------------
# delete vehicle type — usage guard
# ---------------------------------------------------------------------------


class TestDeleteVehicleType:
    @pytest.mark.asyncio
    async def test_delete_404_when_missing(self):
        with patch.object(vf.db_supabase, "get_rows", AsyncMock(return_value=[])):
            with pytest.raises(HTTPException) as exc:
                await vf.admin_delete_vehicle_type("missing")
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_blocked_by_fare_configs(self):
        async def _get_rows(table, *args, **kwargs):
            if table == "vehicle_types":
                return [{"id": "vt_1", "name": "Standard"}]
            if table == "fare_configs":
                return [{"id": "fc_1"}]
            if table == "service_areas":
                return []
            return []

        with patch.object(vf.db_supabase, "get_rows", AsyncMock(side_effect=_get_rows)):
            with pytest.raises(HTTPException) as exc:
                await vf.admin_delete_vehicle_type("vt_1")
        assert exc.value.status_code == 409
        assert "fare config" in exc.value.detail

    @pytest.mark.asyncio
    async def test_delete_blocked_by_service_area_pricing(self):
        async def _get_rows(table, *args, **kwargs):
            if table == "vehicle_types":
                return [{"id": "vt_1", "name": "Standard"}]
            if table == "fare_configs":
                return []
            if table == "service_areas":
                return [{"id": "a1", "name": "Regina", "vehicle_pricing": [{"vehicle_type": "Standard"}]}]
            return []

        with patch.object(vf.db_supabase, "get_rows", AsyncMock(side_effect=_get_rows)):
            with pytest.raises(HTTPException) as exc:
                await vf.admin_delete_vehicle_type("vt_1")
        assert exc.value.status_code == 409
        assert "Regina" in exc.value.detail

    @pytest.mark.asyncio
    async def test_delete_succeeds_when_unused(self):
        async def _get_rows(table, *args, **kwargs):
            if table == "vehicle_types":
                return [{"id": "vt_1", "name": "Standard"}]
            if table == "fare_configs":
                return []
            if table == "service_areas":
                return [{"id": "a1", "name": "Regina", "vehicle_pricing": [{"vehicle_type": "XL"}]}]
            return []

        with (
            patch.object(vf.db_supabase, "get_rows", AsyncMock(side_effect=_get_rows)),
            patch.object(vf.db_supabase, "delete_many", AsyncMock()) as del_mock,
            patch.object(vf, "invalidate_fare_cache", AsyncMock()),
            patch.object(vf, "log_admin_action", AsyncMock(return_value="audit-5")) as audit_mock,
        ):
            result = await vf.admin_delete_vehicle_type("vt_1", admin=ADMIN)
        assert result == {"message": "Vehicle type deleted"}
        del_mock.assert_awaited_once()
        audit_mock.assert_awaited_once()
        assert audit_mock.call_args[0][1] == "vehicle_type_deleted"

    @pytest.mark.asyncio
    async def test_delete_ignores_malformed_pricing_rows(self):
        """Non-list / non-dict vehicle_pricing entries must not raise."""

        async def _get_rows(table, *args, **kwargs):
            if table == "vehicle_types":
                return [{"id": "vt_1", "name": "Standard"}]
            if table == "fare_configs":
                return []
            if table == "service_areas":
                return [
                    {"id": "a1", "name": "Regina", "vehicle_pricing": "not-a-list"},
                    {"id": "a2", "name": "Saskatoon", "vehicle_pricing": ["not-a-dict"]},
                ]
            return []

        with (
            patch.object(vf.db_supabase, "get_rows", AsyncMock(side_effect=_get_rows)),
            patch.object(vf.db_supabase, "delete_many", AsyncMock()),
            patch.object(vf, "invalidate_fare_cache", AsyncMock()),
            patch.object(vf, "log_admin_action", AsyncMock(return_value="audit-6")),
        ):
            result = await vf.admin_delete_vehicle_type("vt_1", admin=ADMIN)
        assert result == {"message": "Vehicle type deleted"}


# ---------------------------------------------------------------------------
# illustration upload
# ---------------------------------------------------------------------------


class TestUploadIllustration:
    @pytest.mark.asyncio
    async def test_404_when_vehicle_type_missing(self):
        with patch.object(vf.db_supabase, "get_rows", AsyncMock(return_value=[])):
            with pytest.raises(HTTPException) as exc:
                await vf.admin_upload_vehicle_illustration("missing", file=_upload_file(b"x", "image/png"), admin=ADMIN)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_unsupported_content_type_rejected(self):
        with patch.object(vf.db_supabase, "get_rows", AsyncMock(return_value=[{"id": "vt_1"}])):
            with pytest.raises(HTTPException) as exc:
                await vf.admin_upload_vehicle_illustration(
                    "vt_1", file=_upload_file(b"x", "application/pdf"), admin=ADMIN
                )
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_image_jpg_normalised_to_jpeg(self):
        """content_type 'image/jpg' (iOS picker quirk) must be treated as JPEG => rejected for transparency."""
        with patch.object(vf.db_supabase, "get_rows", AsyncMock(return_value=[{"id": "vt_1"}])):
            with pytest.raises(HTTPException) as exc:
                await vf.admin_upload_vehicle_illustration(
                    "vt_1", file=_upload_file(_transparent_png_bytes(), "image/jpg"), admin=ADMIN
                )
        # JPEG rejection happens inside _validate_image_transparency after the
        # magic-byte check; PNG bytes with jpeg content-type fail the magic
        # byte check first, both are 400s.
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_file_exceeds_size_limit_rejected(self):
        big = b"\x00" * (vf._MAX_ILLUSTRATION_BYTES + 1)
        with patch.object(vf.db_supabase, "get_rows", AsyncMock(return_value=[{"id": "vt_1"}])):
            with pytest.raises(HTTPException) as exc:
                await vf.admin_upload_vehicle_illustration("vt_1", file=_upload_file(big, "image/png"), admin=ADMIN)
        assert exc.value.status_code == 400
        assert "500 KB" in exc.value.detail

    @pytest.mark.asyncio
    async def test_storage_upload_failure_returns_502(self):
        png = _transparent_png_bytes()
        fake_storage = MagicMock()
        fake_storage.from_.return_value.upload.side_effect = RuntimeError("storage down")
        with (
            patch.object(vf.db_supabase, "get_rows", AsyncMock(return_value=[{"id": "vt_1"}])),
            patch.object(vf, "supabase") as mock_supabase,
        ):
            mock_supabase.storage = fake_storage
            with pytest.raises(HTTPException) as exc:
                await vf.admin_upload_vehicle_illustration("vt_1", file=_upload_file(png, "image/png"), admin=ADMIN)
        assert exc.value.status_code == 502

    @pytest.mark.asyncio
    async def test_successful_upload_writes_url_and_invalidates_cache(self):
        png = _transparent_png_bytes()
        fake_storage = MagicMock()
        fake_storage.from_.return_value.upload.return_value = None
        fake_storage.from_.return_value.get_public_url.return_value = "https://cdn/vt_1/x.png"
        with (
            patch.object(vf.db_supabase, "get_rows", AsyncMock(return_value=[{"id": "vt_1"}])),
            patch.object(vf.db_supabase, "update_one", AsyncMock()) as update_mock,
            patch.object(vf, "invalidate_fare_cache", AsyncMock()) as inv,
            patch.object(vf, "supabase") as mock_supabase,
        ):
            mock_supabase.storage = fake_storage
            result = await vf.admin_upload_vehicle_illustration(
                "vt_1", file=_upload_file(png, "image/png"), admin=ADMIN
            )
        assert result == {"illustration_url": "https://cdn/vt_1/x.png"}
        update_mock.assert_awaited_once()
        inv.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unresolvable_public_url_returns_502(self):
        png = _transparent_png_bytes()
        fake_storage = MagicMock()
        fake_storage.from_.return_value.upload.return_value = None
        fake_storage.from_.return_value.get_public_url.return_value = None
        with (
            patch.object(vf.db_supabase, "get_rows", AsyncMock(return_value=[{"id": "vt_1"}])),
            patch.object(vf, "supabase") as mock_supabase,
        ):
            mock_supabase.storage = fake_storage
            with pytest.raises(HTTPException) as exc:
                await vf.admin_upload_vehicle_illustration("vt_1", file=_upload_file(png, "image/png"), admin=ADMIN)
        assert exc.value.status_code == 502


# ---------------------------------------------------------------------------
# marker upload
# ---------------------------------------------------------------------------


class TestUploadMarker:
    @pytest.mark.asyncio
    async def test_404_when_vehicle_type_missing(self):
        with patch.object(vf.db_supabase, "get_rows", AsyncMock(return_value=[])):
            with pytest.raises(HTTPException) as exc:
                await vf.admin_upload_vehicle_marker("missing", file=_upload_file(b"x", "image/png"), admin=ADMIN)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_jpeg_content_type_rejected(self):
        with patch.object(vf.db_supabase, "get_rows", AsyncMock(return_value=[{"id": "vt_1"}])):
            with pytest.raises(HTTPException) as exc:
                await vf.admin_upload_vehicle_marker("vt_1", file=_upload_file(b"x", "image/jpeg"), admin=ADMIN)
        assert exc.value.status_code == 400
        assert "alpha channel" in exc.value.detail

    @pytest.mark.asyncio
    async def test_size_limit_enforced(self):
        big = b"\x00" * (vf._MAX_ILLUSTRATION_BYTES + 1)
        with patch.object(vf.db_supabase, "get_rows", AsyncMock(return_value=[{"id": "vt_1"}])):
            with pytest.raises(HTTPException) as exc:
                await vf.admin_upload_vehicle_marker("vt_1", file=_upload_file(big, "image/png"), admin=ADMIN)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_storage_failure_returns_502(self):
        png = _transparent_png_bytes()
        fake_storage = MagicMock()
        fake_storage.from_.return_value.upload.side_effect = RuntimeError("boom")
        with (
            patch.object(vf.db_supabase, "get_rows", AsyncMock(return_value=[{"id": "vt_1"}])),
            patch.object(vf, "supabase") as mock_supabase,
        ):
            mock_supabase.storage = fake_storage
            with pytest.raises(HTTPException) as exc:
                await vf.admin_upload_vehicle_marker("vt_1", file=_upload_file(png, "image/png"), admin=ADMIN)
        assert exc.value.status_code == 502

    @pytest.mark.asyncio
    async def test_successful_marker_upload(self):
        webp_bytes = _transparent_png_bytes()  # PNG decodes fine; content-type drives ext choice
        fake_storage = MagicMock()
        fake_storage.from_.return_value.upload.return_value = None
        fake_storage.from_.return_value.get_public_url.return_value = "https://cdn/markers/vt_1/x.png"
        with (
            patch.object(vf.db_supabase, "get_rows", AsyncMock(return_value=[{"id": "vt_1"}])),
            patch.object(vf.db_supabase, "update_one", AsyncMock()) as update_mock,
            patch.object(vf, "invalidate_fare_cache", AsyncMock()),
            patch.object(vf, "supabase") as mock_supabase,
        ):
            mock_supabase.storage = fake_storage
            result = await vf.admin_upload_vehicle_marker(
                "vt_1", file=_upload_file(webp_bytes, "image/png"), admin=ADMIN
            )
        assert result == {"marker_image_url": "https://cdn/markers/vt_1/x.png"}
        update_mock.assert_awaited_once()


# ---------------------------------------------------------------------------
# fare configs
# ---------------------------------------------------------------------------


class TestFareConfigs:
    @pytest.mark.asyncio
    async def test_list_fare_configs(self):
        with patch.object(vf.db_supabase, "get_rows", AsyncMock(return_value=[{"id": "fc_1"}])):
            result = await vf.admin_get_fare_configs()
        assert result == [{"id": "fc_1"}]

    @pytest.mark.asyncio
    async def test_create_fare_config_uses_alias_fields(self):
        req = vf.FareConfigCreateRequest(name="Base", price_per_km=1.5, price_per_minute=0.3)
        inserted = {}

        async def _capture(table, doc):
            inserted.update(doc)
            return {"id": "fc_1"}

        with (
            patch.object(vf.db_supabase, "insert_one", AsyncMock(side_effect=_capture)),
            patch.object(vf, "invalidate_fare_cache", AsyncMock()),
            patch.object(vf, "log_admin_action", AsyncMock(return_value="audit-7")),
        ):
            result = await vf.admin_create_fare_config(req, admin=ADMIN)
        assert result == {"config_id": "fc_1"}
        assert inserted["per_km_rate"] == 1.5
        assert inserted["per_minute_rate"] == 0.3

    @pytest.mark.asyncio
    async def test_update_fare_config_no_fields_skips_write(self):
        update_mock = AsyncMock()
        with (
            patch.object(vf.db_supabase, "update_one", update_mock),
            patch.object(vf, "invalidate_fare_cache", AsyncMock()) as inv,
        ):
            result = await vf.admin_update_fare_config("fc_1", vf.FareConfigUpdateRequest())
        assert result == {"message": "Fare configuration updated"}
        update_mock.assert_not_awaited()
        inv.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_update_fare_config_prefers_explicit_rate_over_alias(self):
        req = vf.FareConfigUpdateRequest(per_km_rate=2.0, price_per_km=9.9)
        seen = {}

        async def _capture(table, match, updates):
            seen.update(updates)

        with (
            patch.object(vf.db_supabase, "update_one", AsyncMock(side_effect=_capture)),
            patch.object(vf, "invalidate_fare_cache", AsyncMock()),
            patch.object(vf, "log_admin_action", AsyncMock(return_value="audit-8")),
        ):
            await vf.admin_update_fare_config("fc_1", req, admin=ADMIN)
        assert seen["per_km_rate"] == 2.0

    @pytest.mark.asyncio
    async def test_delete_fare_config(self):
        audit_mock = AsyncMock(return_value="audit-9")
        with (
            patch.object(vf.db_supabase, "delete_many", AsyncMock()) as del_mock,
            patch.object(vf, "invalidate_fare_cache", AsyncMock()),
            patch.object(vf, "log_admin_action", audit_mock),
        ):
            result = await vf.admin_delete_fare_config("fc_1", admin=ADMIN)
        assert result == {"message": "Fare configuration deleted"}
        del_mock.assert_awaited_once_with("fare_configs", {"id": "fc_1"})
        audit_mock.assert_awaited_once_with(ADMIN, "fare_config_deleted", "fare_configs", "fc_1", {})


# ---------------------------------------------------------------------------
# lost and found
# ---------------------------------------------------------------------------


class TestLostAndFound:
    @pytest.mark.asyncio
    async def test_report_lost_item_ride_not_found(self):
        with patch.object(vf.db_supabase, "get_ride", AsyncMock(return_value=None)):
            with pytest.raises(HTTPException) as exc:
                await vf.admin_report_lost_item("ride_1", vf.LostAndFoundRequest(item_description="phone"))
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_report_lost_item_no_driver_assigned(self):
        with patch.object(vf.db_supabase, "get_ride", AsyncMock(return_value={"driver_id": None, "rider_id": "r1"})):
            with pytest.raises(HTTPException) as exc:
                await vf.admin_report_lost_item("ride_1", vf.LostAndFoundRequest(item_description="phone"))
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_report_lost_item_notifies_driver_when_fcm_token_present(self):
        ride = {"driver_id": "d1", "rider_id": "r1"}
        item = {"id": "laf_1"}
        push_mock = AsyncMock()
        with (
            patch.object(vf.db_supabase, "get_ride", AsyncMock(return_value=ride)),
            patch.object(vf.db_supabase, "create_lost_and_found", AsyncMock(return_value=item)),
            patch.object(vf.db_supabase, "get_driver_by_id", AsyncMock(return_value={"user_id": "u1"})),
            patch.object(vf.db_supabase, "get_user_by_id", AsyncMock(return_value={"fcm_token": "tok"})),
            patch.object(vf.db_supabase, "update_lost_and_found", AsyncMock()) as update_mock,
            patch.object(vf, "log_admin_action", AsyncMock(return_value="audit-10")) as audit_mock,
        ):
            with patch.dict("sys.modules", {}):
                import backend.features as features_mod  # noqa: F401

                with patch.object(features_mod, "send_push_notification", push_mock):
                    result = await vf.admin_report_lost_item(
                        "ride_1", vf.LostAndFoundRequest(item_description="phone"), admin=ADMIN
                    )
        assert result == item
        push_mock.assert_awaited_once()
        audit_mock.assert_awaited_once()
        assert audit_mock.call_args[0][1] == "lost_item_reported"
        # N3 (ACTION_ITEMS.md): the first positional arg must be the driver's
        # users.id ("u1"), not the raw fcm_token string ("tok") —
        # send_push_notification looks the recipient up by users.id itself;
        # passing the token there always missed that lookup and silently
        # dropped every lost-item push.
        assert push_mock.await_args[0][0] == "u1"
        update_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_report_lost_item_notification_failure_is_swallowed(self):
        """Push-notification failure must not fail the report (best-effort by design)."""
        ride = {"driver_id": "d1", "rider_id": "r1"}
        item = {"id": "laf_1"}
        with (
            patch.object(vf.db_supabase, "get_ride", AsyncMock(return_value=ride)),
            patch.object(vf.db_supabase, "create_lost_and_found", AsyncMock(return_value=item)),
            patch.object(vf.db_supabase, "get_driver_by_id", AsyncMock(side_effect=RuntimeError("db down"))),
            patch.object(vf, "log_admin_action", AsyncMock(return_value="audit-11")),
        ):
            result = await vf.admin_report_lost_item(
                "ride_1", vf.LostAndFoundRequest(item_description="phone"), admin=ADMIN
            )
        assert result == item

    @pytest.mark.asyncio
    async def test_resolve_lost_item_invalid_status(self):
        with pytest.raises(HTTPException) as exc:
            await vf.admin_resolve_lost_item("laf_1", vf.LostAndFoundResolveRequest(status="bogus"))
        assert exc.value.status_code == 422

    @pytest.mark.asyncio
    async def test_resolve_lost_item_sets_resolved_at(self):
        seen = {}

        async def _capture(item_id, update):
            seen.update(update)
            return {"id": item_id, **update}

        audit_mock = AsyncMock(return_value="audit-12")
        with (
            patch.object(vf.db_supabase, "update_lost_and_found", AsyncMock(side_effect=_capture)),
            patch.object(vf, "log_admin_action", audit_mock),
        ):
            result = await vf.admin_resolve_lost_item(
                "laf_1", vf.LostAndFoundResolveRequest(status="resolved"), admin=ADMIN
            )
        assert "resolved_at" in seen
        assert result["status"] == "resolved"
        audit_mock.assert_awaited_once_with(
            ADMIN, "lost_item_resolved", "lost_and_found", "laf_1", {"status": "resolved"}
        )

    @pytest.mark.asyncio
    async def test_resolve_lost_item_404_when_missing(self):
        with patch.object(vf.db_supabase, "update_lost_and_found", AsyncMock(return_value=None)):
            with pytest.raises(HTTPException) as exc:
                await vf.admin_resolve_lost_item("laf_1", vf.LostAndFoundResolveRequest(status="unresolved"))
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_list_lost_and_found_applies_filters(self):
        captured = {}

        async def _capture(table, filters, **kwargs):
            captured.update(filters)
            return []

        with patch.object(vf.db_supabase, "get_rows", AsyncMock(side_effect=_capture)):
            await vf.admin_list_lost_and_found(status="reported", service_area_id="area_1")
        assert captured == {"status": "reported", "service_area_id": "area_1"}

    @pytest.mark.asyncio
    async def test_list_lost_and_found_ignores_all_sentinel(self):
        captured = {}

        async def _capture(table, filters, **kwargs):
            captured.update(filters)
            return []

        with patch.object(vf.db_supabase, "get_rows", AsyncMock(side_effect=_capture)):
            await vf.admin_list_lost_and_found(status="all", service_area_id="all")
        assert captured == {}

    @pytest.mark.asyncio
    async def test_update_lost_item_invalid_status(self):
        with pytest.raises(HTTPException) as exc:
            await vf.admin_update_lost_item("laf_1", vf.LostAndFoundUpdateRequest(status="bogus"))
        assert exc.value.status_code == 422

    @pytest.mark.asyncio
    async def test_update_lost_item_404(self):
        with patch.object(vf.db_supabase, "update_lost_and_found", AsyncMock(return_value=None)):
            with pytest.raises(HTTPException) as exc:
                await vf.admin_update_lost_item("laf_1", vf.LostAndFoundUpdateRequest(item_description="x"))
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_update_lost_item_success(self):
        audit_mock = AsyncMock(return_value="audit-13")
        with (
            patch.object(
                vf.db_supabase, "update_lost_and_found", AsyncMock(return_value={"id": "laf_1", "status": "found"})
            ),
            patch.object(vf, "log_admin_action", audit_mock),
        ):
            result = await vf.admin_update_lost_item("laf_1", vf.LostAndFoundUpdateRequest(status="found"), admin=ADMIN)
        assert result["status"] == "found"
        audit_mock.assert_awaited_once()
        assert audit_mock.call_args[0][1] == "lost_item_updated"

    @pytest.mark.asyncio
    async def test_delete_lost_item_404(self):
        with patch.object(vf.db_supabase, "get_rows", AsyncMock(return_value=[])):
            with pytest.raises(HTTPException) as exc:
                await vf.admin_delete_lost_item("laf_1")
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_lost_item_success(self):
        audit_mock = AsyncMock(return_value="audit-14")
        with (
            patch.object(vf.db_supabase, "get_rows", AsyncMock(return_value=[{"id": "laf_1"}])),
            patch.object(vf.db_supabase, "delete_one", AsyncMock()) as del_mock,
            patch.object(vf, "log_admin_action", audit_mock),
        ):
            result = await vf.admin_delete_lost_item("laf_1", admin=ADMIN)
        assert result == {"message": "Item deleted"}
        del_mock.assert_awaited_once_with("lost_and_found", {"id": "laf_1"})
        audit_mock.assert_awaited_once_with(ADMIN, "lost_item_deleted", "lost_and_found", "laf_1", {})
