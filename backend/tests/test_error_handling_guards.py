"""Regression tests for WS-13: fail-closed error handling.

Validates that critical DB/auth/payment error paths fail closed (raise or
log at ERROR) instead of swallowing exceptions with fallback defaults that
bypass safety checks. Contract-style: reads source and asserts the guard
patterns are present.
"""

import pathlib

import pytest

pytestmark = pytest.mark.unit

_ROOT = pathlib.Path(__file__).resolve().parents[1]

# ── Source files under test ──────────────────────────────────────────

_DRIVER_STATUS_SRC = (_ROOT / "routes" / "drivers" / "status.py").read_text()
_CANCEL_SVC_SRC = (_ROOT / "services" / "cancellation_service.py").read_text()
_CORP_POLICY_SRC = (_ROOT / "services" / "corporate_policy_service.py").read_text()
_EARNINGS_SRC = (_ROOT / "routes" / "drivers" / "earnings.py").read_text()
_PROFILE_SRC = (_ROOT / "routes" / "drivers" / "profile.py").read_text()
_SUBSCRIPTIONS_SRC = (_ROOT / "routes" / "drivers" / "subscriptions.py").read_text()
_USERS_SRC = (_ROOT / "routes" / "users.py").read_text()
_BOOKING_SRC = (_ROOT / "routes" / "rides" / "booking.py").read_text()
_ESTIMATES_SRC = (_ROOT / "routes" / "rides" / "estimates.py").read_text()
_LIFECYCLE_SRC = (_ROOT / "routes" / "rides" / "lifecycle.py").read_text()
_CANCELLATION_SRC = (_ROOT / "routes" / "rides" / "cancellation.py").read_text()
_QUERIES_SRC = (_ROOT / "routes" / "rides" / "queries.py").read_text()
_MATCHING_SRC = (_ROOT / "routes" / "rides" / "matching.py").read_text()


# ── CRITICAL: driver document verification must fail closed ──────────


class TestDriverDocumentVerificationFailsClosed:
    """WS-13: DB failure fetching driver_documents must NOT fall through to
    approved_docs=[] (which bypasses all document expiry checks)."""

    def test_no_approved_docs_empty_fallback(self):
        lines = _DRIVER_STATUS_SRC.split("\n")
        in_doc_fetch = False
        for line in lines:
            if "driver_documents" in line and "get_rows" in line:
                in_doc_fetch = True
            if in_doc_fetch and "approved_docs = []" in line:
                pytest.fail(
                    "approved_docs = [] fallback found — DB failure would bypass "
                    "document verification (driver goes online without valid docs)"
                )
            if in_doc_fetch and ("raise" in line or "HTTPException" in line):
                break

    def test_raises_on_document_fetch_failure(self):
        lines = _DRIVER_STATUS_SRC.split("\n")
        in_except = False
        for i, line in enumerate(lines):
            if "driver_documents" in line and "get_rows" in line:
                in_except = True
            if in_except and "except Exception" in line:
                context = "\n".join(lines[i : min(i + 10, len(lines))])
                assert "raise" in context and "503" in context, (
                    "driver_documents except block must raise HTTPException(503)"
                )
                return
        pytest.fail("except block for driver_documents lookup not found")


class TestServiceAreaRequirementsFailsClosed:
    """WS-13: DB failure fetching service_area requirements must NOT fall
    through to mandatory_reqs=[] (which skips all mandatory document checks)."""

    def test_no_mandatory_reqs_empty_fallback_in_except(self):
        lines = _DRIVER_STATUS_SRC.split("\n")
        in_except = False
        for line in lines:
            if "except Exception" in line:
                in_except = True
            elif in_except and "mandatory_reqs = []" in line:
                pytest.fail(
                    "mandatory_reqs = [] fallback in except block found — DB failure "
                    "would bypass mandatory document checks"
                )
            elif in_except and ("raise" in line or "def " in line):
                in_except = False

    def test_raises_on_area_requirements_failure(self):
        lines = _DRIVER_STATUS_SRC.split("\n")
        in_area_block = False
        for i, line in enumerate(lines):
            if "service_areas" in line and "get_rows" in line:
                in_area_block = True
            if in_area_block and "except Exception" in line:
                context = "\n".join(lines[i : min(i + 10, len(lines))])
                assert "raise" in context and "503" in context, (
                    "service_areas except block must raise HTTPException(503)"
                )
                return


# ── HIGH: audit trail must log at ERROR, never WARNING ───────────────


class TestCancellationAuditLogLevel:
    """WS-13: audit_log write failure for cancellation fees must log at ERROR,
    not WARNING — CLAUDE.md forbids logger.warning on DB errors."""

    def test_no_warning_on_audit_log_failure(self):
        lines = _CANCEL_SVC_SRC.split("\n")
        for i, line in enumerate(lines):
            if "audit_log" in line.lower() and "warning" in line.lower():
                context = "\n".join(lines[max(0, i - 2) : i + 1])
                if "logger.warning" in context and "audit_log" in context:
                    pytest.fail("audit_log write failure uses logger.warning — must be logger.error")

    def test_uses_error_level(self):
        lines = _CANCEL_SVC_SRC.split("\n")
        for i, line in enumerate(lines):
            if "audit_log write failed" in line:
                context = "\n".join(lines[max(0, i - 3) : i + 1])
                assert "logger.error" in context, "audit_log failure must use logger.error, not logger.warning"
                return
        pytest.fail("audit_log failure log line not found")


# ── HIGH: corporate policy DB errors must log at ERROR ───────────────


class TestCorporatePolicyErrorLevel:
    """WS-13: corporate policy/allowance DB failures must log at ERROR (not
    WARNING) so Sentry fires and ops knows policy enforcement is degraded."""

    def test_policy_fetch_uses_error_not_warning(self):
        lines = _CORP_POLICY_SRC.split("\n")
        for i, line in enumerate(lines):
            if "could not fetch policy" in line:
                context = "\n".join(lines[max(0, i - 3) : i + 1])
                assert "logger.error" in context, "corporate policy fetch failure must use logger.error"
                assert "logger.warning" not in context, "corporate policy fetch failure still uses logger.warning"
                return
        pytest.fail("policy fetch error log not found")

    def test_allowance_fetch_uses_error_not_warning(self):
        lines = _CORP_POLICY_SRC.split("\n")
        for i, line in enumerate(lines):
            if "could not fetch allowance" in line:
                context = "\n".join(lines[max(0, i - 3) : i + 1])
                assert "logger.error" in context, "corporate allowance fetch failure must use logger.error"
                assert "logger.warning" not in context, "corporate allowance fetch failure still uses logger.warning"
                return
        pytest.fail("allowance fetch error log not found")


# ── HIGH: earnings endpoints must not return zeroed/empty data on DB failure ──


def _except_block_raises(src: str, marker: str) -> bool:
    """Check that an except block near `marker` raises instead of returning."""
    lines = src.split("\n")
    for i, line in enumerate(lines):
        if marker in line:
            context = "\n".join(lines[max(0, i - 5) : min(i + 10, len(lines))])
            if "except" in context and "raise" in context:
                return True
    return False


class TestEarningsEndpointsFailClosed:
    """WS-13: driver earnings endpoints must raise 503 on DB failure, not
    return zeroed/empty financial data that misleads the driver."""

    def test_balance_bonus_fetch_raises(self):
        assert _except_block_raises(_EARNINGS_SRC, "Error fetching driver bonuses for balance")

    def test_bonuses_list_raises(self):
        assert _except_block_raises(_EARNINGS_SRC, "Error fetching driver bonuses:")

    def test_trip_earnings_raises(self):
        assert _except_block_raises(_EARNINGS_SRC, "Error fetching trip earnings")

    def test_weekly_earnings_fallback_raises(self):
        assert _except_block_raises(_EARNINGS_SRC, "Error fetching weekly earnings")

    def test_monthly_earnings_fallback_raises(self):
        assert _except_block_raises(_EARNINGS_SRC, "Error fetching monthly earnings")

    def test_comparison_raises(self):
        assert _except_block_raises(_EARNINGS_SRC, "Error fetching comparison")

    def test_forecast_raises(self):
        assert _except_block_raises(_EARNINGS_SRC, "FORECAST")

    def test_stats_fallback_logs_error(self):
        assert "falling back to rides table" in _EARNINGS_SRC
        lines = _EARNINGS_SRC.split("\n")
        for i, line in enumerate(lines):
            if "falling back to rides table" in line:
                context = "\n".join(lines[max(0, i - 3) : i + 1])
                assert "logger.error" in context, "stats-first fallback must log at ERROR"


class TestDriverRegistrationRoleFlip:
    """WS-13: users.role update failure during driver registration must
    raise 503 — not leave a half-registered driver (driver row exists but
    role still 'rider')."""

    def test_role_update_raises(self):
        assert _except_block_raises(_PROFILE_SRC, "failed to flip users.role")


class TestSubscriptionVerifyRaises:
    """WS-13: Stripe session verification failure must raise 502, not
    return fake 'pending' status."""

    def test_verify_session_raises(self):
        assert _except_block_raises(_SUBSCRIPTIONS_SRC, "verify-session Stripe error")


# ── MEDIUM: emergency contacts must not silently degrade ─────────────


class TestEmergencyContactsFailClosed:
    """WS-13: emergency contacts fetch/count failures must raise 503 —
    returning empty list hides contacts the SOS feature relies on, and
    bypassing the count check allows unlimited row creation."""

    def test_get_contacts_raises(self):
        assert _except_block_raises(_USERS_SRC, "Could not fetch emergency contacts")

    def test_add_contact_count_check_raises(self):
        assert _except_block_raises(_USERS_SRC, "Could not check emergency contact count")


# ── CRITICAL: geofence must not be bypassed on DB failure ──────────


class TestServiceAreaGeofenceFailsClosed:
    """WS-13: service_areas DB failure in booking must raise 503, not
    silently disable geofencing (which lets rides be booked from any
    location with zero fees/taxes)."""

    def test_service_areas_fetch_raises(self):
        assert _except_block_raises(_BOOKING_SRC, "Failed to fetch service areas")

    def test_no_all_areas_empty_fallback_in_except(self):
        lines = _BOOKING_SRC.split("\n")
        in_except = False
        for line in lines:
            if "Failed to fetch service areas" in line:
                in_except = True
            elif in_except and "all_areas = []" in line:
                pytest.fail("all_areas = [] fallback found — DB failure would disable geofencing")
            elif in_except and ("raise" in line or "def " in line):
                break


# ── HIGH: area fees/taxes must not be zeroed on DB failure ─────────


class TestAreaFeesFailClosed:
    """WS-13: calculate_all_fees failure must raise 503, not silently
    zero out GST/PST and area fees (tax compliance violation)."""

    def test_booking_fees_raises(self):
        assert _except_block_raises(_BOOKING_SRC, "Failed to calculate area fees")

    def test_estimate_fees_raises(self):
        assert _except_block_raises(_ESTIMATES_SRC, "calculate_all_fees failed")


# ── MEDIUM: incentive claim must not silently zero driver bonus ────


class TestIncentiveClaimFailsClosed:
    """WS-13: incentive claim failure on ride completion must raise 503,
    not silently zero the driver's earned bonus."""

    def test_incentive_claim_raises(self):
        assert _except_block_raises(_LIFECYCLE_SRC, "incentive claim failed")


# ── MEDIUM: correct log levels on DB errors in ride paths ──────────


class TestRidePathLogLevels:
    """WS-13: DB errors in ride paths must use logger.error (not warning
    or debug) per CLAUDE.md convention."""

    def test_fare_snapshot_uses_error(self):
        lines = _BOOKING_SRC.split("\n")
        for i, line in enumerate(lines):
            if "fare snapshot save failed" in line:
                context = "\n".join(lines[max(0, i - 3) : i + 1])
                assert "logger.error" in context, "fare snapshot save failure must use logger.error"
                return
        pytest.fail("fare snapshot failure log line not found")

    def test_cancel_attribution_uses_error(self):
        lines = _CANCELLATION_SRC.split("\n")
        for i, line in enumerate(lines):
            if "attribution write failed" in line:
                context = "\n".join(lines[max(0, i - 3) : i + 1])
                assert "logger.error" in context, "cancellation attribution failure must use logger.error"
                assert "logger.warning" not in context
                return
        pytest.fail("attribution write failure log line not found")

    def test_incentive_claims_lookup_uses_error(self):
        lines = _QUERIES_SRC.split("\n")
        for i, line in enumerate(lines):
            if "incentive_claims lookup failed" in line:
                context = "\n".join(lines[max(0, i - 3) : i + 1])
                assert "logger.error" in context, "incentive_claims lookup must use logger.error, not logger.debug"
                return
        pytest.fail("incentive_claims lookup failure log not found")

    def test_cascade_redis_filter_not_debug(self):
        lines = _MATCHING_SRC.split("\n")
        for i, line in enumerate(lines):
            if "cascade Redis filter skipped" in line:
                assert "logger.debug" not in line, "cascade Redis filter failure must not use logger.debug"
                return
        pytest.fail("cascade Redis filter log not found")
