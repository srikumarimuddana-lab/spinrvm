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
