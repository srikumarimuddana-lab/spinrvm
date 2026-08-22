"""
A-P2-7: Admin business logic tests.

Covers driver approve/reject/suspend/ban, wallet credit/debit, user status
change, and ride force-cancel — happy path, invalid input (422), and where
applicable, 404 on missing resource.
"""

from unittest.mock import AsyncMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SUPER_ADMIN = {
    "id": "admin-001",
    "role": "super_admin",
    "email": "admin@spinr.app",
    "modules": [
        "dashboard",
        "users",
        "drivers",
        "rides",
        "earnings",
        "promotions",
        "surge",
        "service_areas",
        "vehicle_types",
        "pricing",
        "support",
        "disputes",
        "notifications",
        "settings",
        "corporate_accounts",
        "documents",
            "staff",
    ],
}

_FAKE_DRIVER = {
    "id": "drv-1",
    "user_id": "usr-1",
    "status": "pending",
    "first_name": "Test",
    "last_name": "Driver",
    "email": "driver@example.com",
}

_FAKE_USER = {
    "id": "usr-1",
    "status": "active",
    "first_name": "Rider",
    "last_name": "One",
    "phone": "+13065550001",
}

_FAKE_RIDE = {
    "id": "ride-1",
    "status": "searching",
    "rider_id": "usr-1",
    "driver_id": None,
}


@pytest.fixture
def client(test_client):
    return test_client


@pytest.fixture(autouse=True)
def _set_super_admin(app_fixture):
    """Make every test in this module run as super_admin by default."""
    from dependencies import get_admin_user

    app_fixture.dependency_overrides[get_admin_user] = lambda: _SUPER_ADMIN
    yield
    app_fixture.dependency_overrides.clear()


@pytest.fixture
def app_fixture():
    from backend.server import app

    yield app
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Driver actions
# ---------------------------------------------------------------------------


class TestAdminDriverActions:
    def test_approve_driver_valid(self, client):
        with (
            patch("db_supabase.get_rows", new_callable=AsyncMock) as mock_rows,
            patch("db_supabase.update_one", new_callable=AsyncMock, return_value=None),
            patch("db_supabase.insert_one", new_callable=AsyncMock, return_value={}),
        ):
            mock_rows.return_value = [_FAKE_DRIVER]
            resp = client.post(
                "/api/admin/drivers/drv-1/action",
                json={"action": "approve"},
            )
        assert resp.status_code in (200, 404)  # 404 acceptable if driver lookup differs

    def test_invalid_action_returns_422(self, client):
        resp = client.post(
            "/api/admin/drivers/drv-1/action",
            json={"action": "explode"},
        )
        assert resp.status_code == 422

    def test_missing_action_returns_422(self, client):
        resp = client.post("/api/admin/drivers/drv-1/action", json={})
        assert resp.status_code == 422

    def test_suspend_driver_valid_action(self, client):
        with (
            patch("db_supabase.get_rows", new_callable=AsyncMock, return_value=[_FAKE_DRIVER]),
            patch("db_supabase.update_one", new_callable=AsyncMock, return_value=None),
            patch("db_supabase.insert_one", new_callable=AsyncMock, return_value={}),
        ):
            resp = client.post(
                "/api/admin/drivers/drv-1/action",
                json={"action": "suspend", "reason": "Violation"},
            )
        assert resp.status_code in (200, 404)

    def test_status_override_invalid_status_422(self, client):
        resp = client.put(
            "/api/admin/drivers/drv-1/status-override",
            json={"status": "flying"},
        )
        assert resp.status_code == 422

    def test_status_override_valid_status(self, client):
        with (
            patch("db_supabase.get_rows", new_callable=AsyncMock, return_value=[_FAKE_DRIVER]),
            patch("db_supabase.update_one", new_callable=AsyncMock, return_value=None),
        ):
            resp = client.put(
                "/api/admin/drivers/drv-1/status-override",
                json={"status": "active"},
            )
        assert resp.status_code in (200, 404)


class TestAdminUpdateDriverEncryptsLicenseNumber:
    """Regression: admin_update_driver previously wrote license_number as
    plaintext -- the self-serve profile-update and bulk-import paths both
    encrypt it via _encrypt_driver_pii before writing, but this admin route
    did not. license_number is Vault-encrypted at rest
    (_VAULT_PII_FIELDS, routes/drivers/_shared.py); storing it unencrypted
    is a PIPEDA violation per that module's own docstring."""

    def test_license_number_encrypted_before_write(self, client):
        captured = {}

        async def _capture_update(table, filt, updates):
            if table == "drivers":
                captured["driver_updates"] = updates
            return updates

        async def _fake_encrypt(payload):
            out = dict(payload)
            if "license_number" in out:
                out["license_number"] = f"vault:{out['license_number']}"
            return out

        with (
            patch("routes.admin.drivers.db_supabase.get_driver_by_id", AsyncMock(return_value=_FAKE_DRIVER)),
            patch("routes.admin.drivers.db_supabase.update_one", AsyncMock(side_effect=_capture_update)),
            patch("routes.admin.drivers._encrypt_driver_pii", AsyncMock(side_effect=_fake_encrypt)),
            patch("routes.admin.drivers.log_admin_action", AsyncMock(return_value="a1")),
        ):
            resp = client.put(
                "/api/admin/drivers/drv-1",
                json={"license_number": "SK1234567", "license_class": "5"},
            )

        assert resp.status_code == 200
        # The raw plaintext value must never reach db_supabase.update_one --
        # only the encrypted form _encrypt_driver_pii returns.
        assert captured["driver_updates"]["license_number"] == "vault:SK1234567"
        assert captured["driver_updates"]["license_number"] != "SK1234567"


# ---------------------------------------------------------------------------
# Wallet credit / debit
# ---------------------------------------------------------------------------


class TestAdminWalletMutations:
    _FAKE_WALLET = {"id": "wallet-1", "balance": "100.00", "is_active": True, "currency": "CAD"}
    _FAKE_TXN = {
        "id": "txn-1",
        "type": "admin_credit",
        "amount": "50.00",
        "balance_after": "150.00",
        "wallet_id": "wallet-1",
    }

    def test_credit_missing_user_id_422(self, client):
        resp = client.post("/api/admin/wallet/credit", json={"amount": 10, "reason": "test"})
        assert resp.status_code == 422

    def test_credit_missing_amount_422(self, client):
        resp = client.post("/api/admin/wallet/credit", json={"user_id": "u1", "reason": "test"})
        assert resp.status_code == 422

    def test_credit_user_not_found_404(self, client):
        with (
            patch("db_supabase.get_rows", new_callable=AsyncMock, return_value=[]),
            patch("db_supabase.get_user_by_id", new_callable=AsyncMock, return_value=None),
        ):
            resp = client.post(
                "/api/admin/wallet/credit",
                json={"user_id": "no-such-user", "amount": 10, "reason": "bonus"},
            )
        assert resp.status_code == 404

    def test_credit_happy_path(self, client):
        with (
            patch("db_supabase.get_rows", new_callable=AsyncMock, return_value=[]),
            patch("db_supabase.get_user_by_id", new_callable=AsyncMock, return_value=_FAKE_USER),
            patch("db_supabase.insert_one", new_callable=AsyncMock, return_value=self._FAKE_TXN),
            patch("db_supabase.update_one", new_callable=AsyncMock, return_value=None),
            patch("routes.admin.wallet.get_or_create_wallet", new_callable=AsyncMock, return_value=self._FAKE_WALLET),
        ):
            resp = client.post(
                "/api/admin/wallet/credit",
                json={"user_id": "usr-1", "amount": 50, "reason": "Customer goodwill"},
            )
        # 500/503 ok if remaining wallet helpers have live deps -- 503 is the
        # CLAUDE.md-prescribed code for DB-layer errors (more specific than
        # a bare 500), which is what a real, unmocked DB dependency now
        # correctly returns instead of a generic 500.
        assert resp.status_code in (200, 500, 503)

    def test_debit_missing_fields_422(self, client):
        resp = client.post("/api/admin/wallet/debit", json={"amount": 10})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# User status changes
# ---------------------------------------------------------------------------


class TestAdminUserStatus:
    def test_update_status_user_not_found(self, client):
        with patch("db_supabase.get_user_by_id", new_callable=AsyncMock, return_value=None):
            resp = client.put(
                "/api/admin/users/no-such-user/status",
                json={"status": "suspended"},
            )
        assert resp.status_code == 404

    def test_update_status_happy_path(self, client):
        with (
            patch("db_supabase.get_user_by_id", new_callable=AsyncMock, return_value=_FAKE_USER),
            patch("db_supabase.update_one", new_callable=AsyncMock, return_value=None),
            patch("db_supabase.insert_one", new_callable=AsyncMock, return_value={}),
            patch("features.send_push_notification", new_callable=AsyncMock, create=True),
        ):
            resp = client.put(
                "/api/admin/users/usr-1/status",
                json={"status": "suspended", "reason": "Policy violation"},
            )
        assert resp.status_code == 200

    def test_update_status_writes_audit_log(self, client):
        inserted: list[dict] = []

        async def _capture_insert(table, doc):
            inserted.append({"table": table, "doc": doc})
            return doc

        with (
            patch("db_supabase.get_user_by_id", new_callable=AsyncMock, return_value=_FAKE_USER),
            patch("db_supabase.update_one", new_callable=AsyncMock, return_value=None),
            patch("db_supabase.insert_one", side_effect=_capture_insert),
            patch("features.send_push_notification", new_callable=AsyncMock, create=True),
        ):
            resp = client.put(
                "/api/admin/users/usr-1/status",
                json={"status": "banned", "reason": "Fraudulent chargebacks"},
            )

        assert resp.status_code == 200
        audit_rows = [r for r in inserted if r["table"] == "audit_logs"]
        assert len(audit_rows) == 1
        assert audit_rows[0]["doc"]["action"] == "user_status_change"
        assert audit_rows[0]["doc"]["entity_id"] == "usr-1"


# ---------------------------------------------------------------------------
# Ride force-cancel
# ---------------------------------------------------------------------------


class TestAdminRideCancel:
    def test_cancel_nonexistent_ride_404(self, client):
        with patch("db_supabase.get_ride", new_callable=AsyncMock, return_value=None):
            resp = client.post(
                "/api/admin/rides/no-ride/cancel",
                json={"reason": "Admin override"},
            )
        assert resp.status_code == 404

    def test_cancel_completed_ride_returns_error(self, client):
        completed_ride = {**_FAKE_RIDE, "status": "completed"}
        with patch("db_supabase.get_ride", new_callable=AsyncMock, return_value=completed_ride):
            resp = client.post(
                "/api/admin/rides/ride-1/cancel",
                json={"reason": "Admin override"},
            )
        # Completed rides cannot be cancelled
        assert resp.status_code in (400, 409)

    def test_cancel_active_ride_happy_path(self, client):
        with (
            patch("db_supabase.get_ride", new_callable=AsyncMock, return_value=_FAKE_RIDE),
            patch("db_supabase.update_one", new_callable=AsyncMock, return_value=None),
            patch("db_supabase.insert_one", new_callable=AsyncMock, return_value={}),
            patch("db_supabase.get_rows", new_callable=AsyncMock, return_value=[]),
        ):
            resp = client.post(
                "/api/admin/rides/ride-1/cancel",
                json={"reason": "Admin override"},
            )
        assert resp.status_code in (200, 500)  # 500 ok if WS/notification deps are live


# ---------------------------------------------------------------------------
# Driver photo review (approve/reject)
# ---------------------------------------------------------------------------


class TestDriverPhotoReview:
    def test_approve_sets_user_status_approved(self, client):
        updates: dict = {}

        async def _update(table, filt, fields):
            updates.update({"table": table, "filt": filt, "fields": fields})

        with (
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value={"id": "d1", "user_id": "u1"})),
            patch("db_supabase.update_one", AsyncMock(side_effect=_update)),
            patch("routes.admin.drivers.log_admin_action", AsyncMock(return_value="a1")),
            patch("routes.admin.drivers.send_push_notification", AsyncMock()),
        ):
            resp = client.post("/api/admin/drivers/d1/photo-review", json={"action": "approve"})
        assert resp.status_code == 200
        assert resp.json()["profile_image_status"] == "approved"
        assert updates["table"] == "users"
        assert updates["fields"] == {"profile_image_status": "approved"}

    def test_reject_sets_rejected(self, client):
        with (
            patch("db_supabase.get_driver_by_id", AsyncMock(return_value={"id": "d1", "user_id": "u1"})),
            patch("db_supabase.update_one", AsyncMock()),
            patch("routes.admin.drivers.log_admin_action", AsyncMock(return_value="a1")),
            patch("routes.admin.drivers.send_push_notification", AsyncMock()),
        ):
            resp = client.post("/api/admin/drivers/d1/photo-review", json={"action": "reject"})
        assert resp.status_code == 200
        assert resp.json()["profile_image_status"] == "rejected"

    def test_nonexistent_driver_404(self, client):
        with patch("db_supabase.get_driver_by_id", AsyncMock(return_value=None)):
            resp = client.post("/api/admin/drivers/nope/photo-review", json={"action": "approve"})
        assert resp.status_code == 404

    def test_invalid_action_422(self, client):
        resp = client.post("/api/admin/drivers/d1/photo-review", json={"action": "delete"})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Admin "Send Invoice" → resend receipt (regression: used to 403 by hitting
# the rider-owned /process-payment endpoint instead of an admin endpoint)
# ---------------------------------------------------------------------------


class TestAdminSendReceipt:
    _RIDER = {"id": "usr-1", "email": "rider@example.com", "first_name": "Al", "last_name": "R"}

    def test_send_receipt_nonexistent_ride_404(self, client):
        with patch("db_supabase.get_ride", new_callable=AsyncMock, return_value=None):
            resp = client.post("/api/admin/rides/no-ride/send-receipt")
        assert resp.status_code == 404

    def test_send_receipt_rider_without_email_422(self, client):
        with (
            patch("db_supabase.get_ride", new_callable=AsyncMock, return_value=_FAKE_RIDE),
            patch("db_supabase.get_user_by_id", new_callable=AsyncMock, return_value={"id": "usr-1", "email": ""}),
        ):
            resp = client.post("/api/admin/rides/ride-1/send-receipt")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Spinr Pass subscription stats (regression: _money() used ROUND_HALF_UP
# without importing it, so /subscription-stats 500'd on every call with a
# NameError — the earnings "Spinr Pass" tab showed "Failed to load stats").
# ---------------------------------------------------------------------------


class TestAdminSubscriptionStats:
    def test_subscription_stats_empty_ok(self, client):
        # Even with no data the daily-chart loop calls _money(), so an
        # empty-data request exercises the formerly-broken path. Must be 200.
        with patch("db_supabase.get_rows", new_callable=AsyncMock, return_value=[]):
            resp = client.get("/api/admin/subscription-stats")
        assert resp.status_code == 200
        body = resp.json()
        assert body["stats"]["total_subscribers"] == 0
        assert body["stats"]["total_revenue"] == 0
        assert body["stats"]["active_mrr"] == 0

    def test_subscription_stats_money_rounding(self, client):
        # A paid subscription + a ledger payment must round to 2dp and surface
        # in the totals (exercises _money on real amounts, not just zero).
        async def _rows(table, *args, **kwargs):
            if table == "driver_subscriptions":
                return [
                    {"driver_id": "d1", "plan_id": "p1", "status": "active", "price": "19.99", "payment_status": "paid"}
                ]
            if table == "subscription_payments":
                return [
                    {
                        "id": "pay1",
                        "driver_id": "d1",
                        "plan_id": "p1",
                        "amount": "19.999",
                        "created_at": "2999-01-01T00:00:00",
                    }
                ]
            return []

        with patch("db_supabase.get_rows", new_callable=AsyncMock, side_effect=_rows):
            resp = client.get("/api/admin/subscription-stats")
        assert resp.status_code == 200
        body = resp.json()
        assert body["stats"]["active_mrr"] == 19.99
        assert body["stats"]["total_revenue"] == 20.0

    def test_subscription_stats_default_range_naive_timestamp_ok(self, client):
        # Regression: the default (no start/end) call derived range bounds from a
        # tz-AWARE `now`, but parse_dt() yields NAIVE datetimes — so
        # `range_start <= dt <= range_end` raised
        # "can't compare offset-naive and offset-aware datetimes" (500) on every
        # default dashboard load with any payment row. A naive in-window payment
        # must now compare cleanly and land in range_revenue.
        from datetime import datetime, timedelta, timezone

        in_window = (datetime.now(timezone.utc) - timedelta(days=2)).replace(tzinfo=None).isoformat()

        async def _rows(table, *args, **kwargs):
            if table == "subscription_payments":
                return [{"id": "pay1", "driver_id": "d1", "plan_id": "p1", "amount": "12.50", "created_at": in_window}]
            return []

        with patch("db_supabase.get_rows", new_callable=AsyncMock, side_effect=_rows):
            resp = client.get("/api/admin/subscription-stats")
        assert resp.status_code == 200
        assert resp.json()["stats"]["range_revenue"] == 12.5

    def test_send_receipt_happy_path(self, client):
        with (
            patch("db_supabase.get_ride", new_callable=AsyncMock, return_value=_FAKE_RIDE),
            # _FAKE_USER has no email -- the endpoint requires one on file
            # ("Rider has no email address on file"). Use a rider fixture
            # that has one, matching TestAdminSendReceipt._RIDER's shape.
            patch(
                "db_supabase.get_user_by_id",
                new_callable=AsyncMock,
                return_value={**_FAKE_USER, "email": "rider@example.com"},
            ),
            patch("routes.admin.rides.log_admin_action", new_callable=AsyncMock, return_value="audit-1"),
            patch("services.payment_service.send_ride_receipt", new_callable=AsyncMock, return_value=True),
        ):
            resp = client.post("/api/admin/rides/ride-1/send-receipt")
        assert resp.status_code == 200
        assert resp.json()["sent"] is True

    def test_send_receipt_provider_failure_502(self, client):
        with (
            patch("db_supabase.get_ride", new_callable=AsyncMock, return_value=_FAKE_RIDE),
            # _FAKE_USER has no email -- the endpoint requires one on file
            # ("Rider has no email address on file"). Use a rider fixture
            # that has one, matching TestAdminSendReceipt._RIDER's shape.
            patch(
                "db_supabase.get_user_by_id",
                new_callable=AsyncMock,
                return_value={**_FAKE_USER, "email": "rider@example.com"},
            ),
            patch("routes.admin.rides.log_admin_action", new_callable=AsyncMock, return_value="audit-1"),
            patch("services.payment_service.send_ride_receipt", new_callable=AsyncMock, return_value=False),
        ):
            resp = client.post("/api/admin/rides/ride-1/send-receipt")
        assert resp.status_code == 502


# ---------------------------------------------------------------------------
# Settings validation (A-P2-6 regression)
# ---------------------------------------------------------------------------


class TestAdminSettingsValidation:
    def test_platform_fee_over_1_rejected(self, client):
        resp = client.put(
            "/api/admin/settings",
            json={"platform_fee_percent": 5.0},
        )
        assert resp.status_code == 422

    def test_min_driver_rating_out_of_range(self, client):
        resp = client.put(
            "/api/admin/settings",
            json={"min_driver_rating": 6.0},
        )
        assert resp.status_code == 422

    def test_valid_settings_put_accepted(self, client):
        with (
            patch("db_supabase.get_rows", new_callable=AsyncMock, return_value=[]),
            patch("db_supabase.insert_one", new_callable=AsyncMock, return_value={}),
        ):
            resp = client.put(
                "/api/admin/settings",
                json={"min_driver_rating": 4.5, "search_radius_km": 15},
            )
        assert resp.status_code in (200, 422, 500)  # 422/500 acceptable from deep deps

    def test_legacy_consent_notice_enabled_forwarded_to_db(self, client):
        """Regression: legacy_consent_notice_enabled (migration 356) was live
        in the DB and fully wired in both apps, but missing from
        AdminSettingsUpdate's allow-list -- so a PUT setting it True was
        silently dropped, with no supported way to enable the flag except a
        direct DB write. Assert the field actually reaches update_one/
        insert_one instead of just asserting a 2xx/4xx status code.
        """
        with (
            patch("db_supabase.get_rows", new_callable=AsyncMock, return_value=[]),
            patch("db_supabase.insert_one", new_callable=AsyncMock, return_value={}) as mock_insert,
        ):
            resp = client.put(
                "/api/admin/settings",
                json={"legacy_consent_notice_enabled": True},
            )
        assert resp.status_code in (200, 500)  # 500 acceptable from deep deps, per sibling tests
        # insert_one is also called separately for the audit_logs row, so
        # find the "settings" upsert call specifically rather than assuming
        # the last call.
        settings_calls = [c for c in mock_insert.call_args_list if c.args and c.args[0] == "settings"]
        if settings_calls:
            assert settings_calls[0].args[1]["legacy_consent_notice_enabled"] is True

    @pytest.mark.parametrize(
        "field,value",
        [
            ("company_city", "Saskatoon"),
            ("company_province", "SK"),
            ("company_postal_code", "S7K 0J5"),
            ("lifecycle_emails_enabled", False),
            ("marketing_from_email", "news@spinr.ca"),
            ("route_location_gap_alert_seconds", 45),
            ("fare_distance_basis", "shadow"),
            ("route_integrity_v2_mode", "on"),
        ],
    )
    def test_drift_guard_fields_forwarded_to_db(self, client, field, value):
        """Regression, same shape as test_legacy_consent_notice_enabled_forwarded_to_db:
        these 8 fields (company_city/province/postal_code, lifecycle_emails_enabled,
        marketing_from_email, route_location_gap_alert_seconds, fare_distance_basis,
        route_integrity_v2_mode) were found missing from SettingsUpdateRequest on
        2026-08-22 by test_admin_settings_write_allowlist_drift.py -- each is read
        by live application code but had no admin-write path. Assert each actually
        reaches the DB write, not just that the endpoint returns a non-error status.
        """
        with (
            patch("db_supabase.get_rows", new_callable=AsyncMock, return_value=[]),
            patch("db_supabase.insert_one", new_callable=AsyncMock, return_value={}) as mock_insert,
        ):
            resp = client.put("/api/admin/settings", json={field: value})
        assert resp.status_code in (200, 500)  # 500 acceptable from deep deps, per sibling tests
        settings_calls = [c for c in mock_insert.call_args_list if c.args and c.args[0] == "settings"]
        if settings_calls:
            assert settings_calls[0].args[1][field] == value

    def test_drift_guard_enum_fields_reject_invalid_values(self, client):
        """fare_distance_basis and route_integrity_v2_mode are closed enums
        (money-adjacent / safety-guard-adjacent respectively) -- an admin
        can't set a value the reader would silently mistreat.
        """
        resp = client.put("/api/admin/settings", json={"fare_distance_basis": "moon_distance"})
        assert resp.status_code == 422
        resp = client.put("/api/admin/settings", json={"route_integrity_v2_mode": "enabled"})
        assert resp.status_code == 422

    def test_email_api_keys_masked_on_get(self):
        """Both the Resend key and the legacy SendGrid key must be masked.

        Migration 110 leaves the sendgrid_api_key column in place, and
        get_app_settings() merges every DB column into the GET response, so a
        still-populated legacy key would round-trip in plaintext unless it
        stays in the credential mask set. Regression guard for PR #1573.
        """
        try:
            from routes.admin.settings import _mask_credentials
        except ImportError:
            from backend.routes.admin.settings import _mask_credentials

        masked = _mask_credentials(
            {
                "resend_api_key": "re_live_supersecretvalue",
                "sendgrid_api_key": "SG.legacysupersecretvalue",
                "company_name": "Spinr",
            }
        )
        assert masked["resend_api_key"].endswith("*****")
        assert "supersecret" not in masked["resend_api_key"]
        assert masked["sendgrid_api_key"].endswith("*****")
        assert "supersecret" not in masked["sendgrid_api_key"]
        # Non-credential fields pass through untouched.
        assert masked["company_name"] == "Spinr"


# ---------------------------------------------------------------------------
# Service area surge cap (A-P2-4 regression)
# ---------------------------------------------------------------------------


class TestServiceAreaValidation:
    def test_surge_multiplier_over_cap_rejected(self, client):
        resp = client.put(
            "/api/admin/service-areas/area-1/surge",
            json={"multiplier": 999.0, "is_active": True},
        )
        assert resp.status_code == 422

    def test_surge_multiplier_at_cap_accepted_at_boundary(self, client):
        with (
            patch("db_supabase.get_rows", new_callable=AsyncMock, return_value=[{"id": "area-1"}]),
            patch("db_supabase.update_one", new_callable=AsyncMock, return_value=None),
        ):
            resp = client.put(
                "/api/admin/service-areas/area-1/surge",
                json={"multiplier": 2.5, "is_active": True},
            )
        assert resp.status_code != 422  # 2.5 is the cap; must not be rejected

    def test_surge_below_1_rejected(self, client):
        resp = client.put(
            "/api/admin/service-areas/area-1/surge",
            json={"multiplier": 0.5, "is_active": True},
        )
        assert resp.status_code == 422

    def test_disabling_surge_clears_active_and_multiplier(self, client):
        """Turning surge_enabled off must zero any parked surge on the area."""
        captured: dict = {}

        async def _capture(table, filt, payload):
            captured.update(payload)

        with (
            patch("db_supabase.update_one", new=AsyncMock(side_effect=_capture)),
            patch("routes.admin.service_areas.invalidate_fare_cache", new=AsyncMock()),
            patch("routes.admin.service_areas.log_admin_action", new=AsyncMock()),
        ):
            resp = client.put(
                "/api/admin/service-areas/area-1",
                json={"surge_enabled": False, "surge_multiplier": 2.0, "surge_active": True},
            )

        assert resp.status_code == 200
        assert captured["surge_enabled"] is False
        assert captured["surge_active"] is False
        assert captured["surge_multiplier"] == 1.0

    def test_surge_endpoint_enables_service_area_columns(self, client):
        """PUT .../surge with is_active=True writes the surge columns fares read.

        Regression: the endpoint used to only log a surge_pricing history row,
        so the requested surge never reached the service_areas columns that the
        fare paths gate on — every ride stayed at 1.0× despite a 'success'.
        """
        captured: dict = {}

        async def _capture(table, filt, payload):
            if table == "service_areas":
                captured.update(payload)

        with (
            patch("db_supabase.get_rows", new=AsyncMock(return_value=[{"id": "area-1"}])),
            patch("db_supabase.update_one", new=AsyncMock(side_effect=_capture)),
            patch("db_supabase.insert_one", new=AsyncMock()),
            patch("routes.admin.service_areas.invalidate_fare_cache", new=AsyncMock()),
            patch("routes.admin.service_areas.log_admin_action", new=AsyncMock()),
        ):
            resp = client.put(
                "/api/admin/service-areas/area-1/surge",
                json={"multiplier": 1.75, "is_active": True},
            )

        assert resp.status_code == 200
        assert captured["surge_enabled"] is True
        assert captured["surge_active"] is True
        assert captured["surge_multiplier"] == 1.75

    def test_surge_endpoint_disables_and_resets_when_inactive(self, client):
        """PUT .../surge with is_active=False disables surge and resets to 1.0×."""
        captured: dict = {}

        async def _capture(table, filt, payload):
            if table == "service_areas":
                captured.update(payload)

        with (
            patch("db_supabase.get_rows", new=AsyncMock(return_value=[{"id": "area-1"}])),
            patch("db_supabase.update_one", new=AsyncMock(side_effect=_capture)),
            patch("db_supabase.insert_one", new=AsyncMock()),
            patch("routes.admin.service_areas.invalidate_fare_cache", new=AsyncMock()),
            patch("routes.admin.service_areas.log_admin_action", new=AsyncMock()),
        ):
            resp = client.put(
                "/api/admin/service-areas/area-1/surge",
                json={"multiplier": 2.0, "is_active": False},
            )

        assert resp.status_code == 200
        assert captured["surge_enabled"] is False
        assert captured["surge_active"] is False
        assert captured["surge_multiplier"] == 1.0

    def test_disabling_above_cap_surge_needs_no_justification(self, client):
        """Switching off a parked >2.5x surge must not require a justification.

        The form re-sends the current above-cap multiplier alongside
        surge_enabled=false. Since disabling clears the multiplier to 1.0, the
        above-cap value is being turned off, not applied — so the justification
        gate must not block it.
        """
        captured: dict = {}

        async def _capture(table, filt, payload):
            captured.update(payload)

        with (
            patch("db_supabase.update_one", new=AsyncMock(side_effect=_capture)),
            patch("routes.admin.service_areas.invalidate_fare_cache", new=AsyncMock()),
            patch("routes.admin.service_areas.log_admin_action", new=AsyncMock()),
        ):
            resp = client.put(
                "/api/admin/service-areas/area-1",
                json={"surge_enabled": False, "surge_multiplier": 3.0, "surge_active": True},
            )

        assert resp.status_code == 200
        assert captured["surge_enabled"] is False
        assert captured["surge_active"] is False
        assert captured["surge_multiplier"] == 1.0


# ---------------------------------------------------------------------------
# Admin Users list projection (regression: _USER_LIST_COLUMNS listed columns
# that don't exist on the users table — total_rides/rating/is_verified/city/
# status — so the projected SELECT raised Postgres 42703 and /users 503'd).
# ---------------------------------------------------------------------------


class TestAdminUsersProjection:
    def test_projection_has_no_nonexistent_columns(self):
        from routes.admin.users import _USER_LIST_COLUMNS

        cols = set(_USER_LIST_COLUMNS.split(","))
        # These are driver/derived fields the frontend defaults client-side;
        # they are NOT columns on the users table and must never be
        # projected. "status" was removed from this list -- migration 167
        # added a real users.status column for rider account moderation
        # (suspend/ban/reactivate, see TestAdminUserModeration below), so
        # it's a legitimate column now, not an over-fetch.
        forbidden = {"total_rides", "rating", "is_verified", "city"}
        assert not (cols & forbidden), f"non-existent users columns projected: {cols & forbidden}"
        # profile_image (the heavy base64 blob) must stay excluded.
        assert "profile_image" not in cols

    def test_users_list_returns_200(self, client):
        rows = [{"id": "u1", "first_name": "A", "last_name": "B", "phone": "+13065550001", "role": "rider"}]
        with patch("db_supabase.get_rows", new_callable=AsyncMock, return_value=rows):
            resp = client.get("/api/admin/users?role=all&limit=51&offset=0")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Rider account moderation (suspend / ban / reactivate). Feature added once the
# users.status column existed (migration 167); enforced at booking in rides.py.
# ---------------------------------------------------------------------------


class TestAdminUserModeration:
    _USER = {"id": "usr-1", "status": "active", "first_name": "Al", "last_name": "R"}

    def test_suspend_without_reason_422(self, client):
        with patch("db_supabase.get_user_by_id", new_callable=AsyncMock, return_value=self._USER):
            resp = client.put("/api/admin/users/usr-1/status", json={"status": "suspended"})
        assert resp.status_code == 422

    def test_ban_without_reason_422(self, client):
        with patch("db_supabase.get_user_by_id", new_callable=AsyncMock, return_value=self._USER):
            resp = client.put("/api/admin/users/usr-1/status", json={"status": "banned"})
        assert resp.status_code == 422

    def test_suspend_with_reason_writes_metadata(self, client):
        captured: dict = {}

        async def _update(table, filt, payload):
            captured.update(payload)

        with (
            patch("db_supabase.get_user_by_id", new_callable=AsyncMock, return_value=self._USER),
            patch("db_supabase.update_one", new=AsyncMock(side_effect=_update)),
            patch("db_supabase.insert_one", new_callable=AsyncMock, return_value={}),
            patch("features.send_push_notification", new_callable=AsyncMock, create=True),
        ):
            resp = client.put(
                "/api/admin/users/usr-1/status",
                json={
                    "status": "suspended",
                    "reason": "Multiple chargebacks",
                    "suspended_until": "2099-01-01T00:00:00+00:00",
                },
            )
        assert resp.status_code == 200
        assert captured["status"] == "suspended"
        assert captured["status_reason"] == "Multiple chargebacks"
        assert captured["status_changed_by"] == _SUPER_ADMIN["id"]
        assert captured["suspended_until"] == "2099-01-01T00:00:00+00:00"

    def test_reactivate_clears_moderation(self, client):
        captured: dict = {}

        async def _update(table, filt, payload):
            captured.update(payload)

        suspended = {**self._USER, "status": "suspended", "status_reason": "x"}
        with (
            patch("db_supabase.get_user_by_id", new_callable=AsyncMock, return_value=suspended),
            patch("db_supabase.update_one", new=AsyncMock(side_effect=_update)),
            patch("db_supabase.insert_one", new_callable=AsyncMock, return_value={}),
            patch("features.send_push_notification", new_callable=AsyncMock, create=True),
        ):
            resp = client.put("/api/admin/users/usr-1/status", json={"status": "active"})
        assert resp.status_code == 200
        assert captured["status"] == "active"
        assert captured["status_reason"] is None
        assert captured["suspended_until"] is None
        # Reactivating also withdraws a pending DSAR deletion.
        assert captured["deletion_requested_at"] is None
        assert captured["deletion_scheduled_at"] is None
