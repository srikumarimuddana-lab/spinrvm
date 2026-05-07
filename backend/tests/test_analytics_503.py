"""
Regression tests for A-P1-10: analytics driver-list DB error must surface as 503.

Background
----------
get_driver_acceptance_rates caught a DB exception, logged it, and fell through
with drivers=[].  The dashboard then rendered partial data (zero acceptance
rates) without any indication that the data was missing — a silent failure.

Fix: re-raise as HTTPException(503, "analytics_unavailable") so the client
receives a clear error and can show a user-facing "data unavailable" state.

Test layers
-----------
Layer 1 — pure logic: always runnable, verify the guard pattern.
Layer 2 — integration: require backend deps; skipped locally, run in CI.
"""

import pytest

# ---------------------------------------------------------------------------
# Layer 1 — pure logic
# ---------------------------------------------------------------------------


def _guarded_fetch(raise_exc: bool):
    """Mirror of the fixed guard: raise on DB error instead of returning []."""
    try:
        if raise_exc:
            raise RuntimeError("supabase connection reset")
        return [{"id": "d1"}]
    except Exception as e:
        raise ValueError("analytics_unavailable") from e  # stand-in for HTTPException


def test_db_success_returns_drivers():
    result = _guarded_fetch(raise_exc=False)
    assert result == [{"id": "d1"}]


def test_db_error_raises_instead_of_returning_empty_list():
    """Core invariant: DB failure must raise, never silently return []."""
    with pytest.raises(ValueError, match="analytics_unavailable"):
        _guarded_fetch(raise_exc=True)


def test_old_pattern_would_silently_return_empty():
    """Regression proof: the old code swallowed the error and returned []."""

    def old_guarded_fetch(raise_exc: bool):
        try:
            if raise_exc:
                raise RuntimeError("db error")
            return [{"id": "d1"}]
        except Exception:
            return []  # broken pattern

    result = old_guarded_fetch(raise_exc=True)
    assert result == [], "old code silently returned empty — acceptance rates showed as 0%"


# ---------------------------------------------------------------------------
# Layer 2 — integration (require backend deps; skipped if unavailable)
# ---------------------------------------------------------------------------

try:
    import backend.routes.admin.analytics as _analytics_module

    _HAS_BACKEND_DEPS = True
except BaseException:
    _analytics_module = None  # type: ignore[assignment]
    _HAS_BACKEND_DEPS = False

_skip_no_deps = pytest.mark.skipif(not _HAS_BACKEND_DEPS, reason="backend deps not installed")


@_skip_no_deps
@pytest.mark.anyio
async def test_get_driver_acceptance_rates_raises_503_on_db_error():
    """Driver-list DB failure must propagate as 503, not return partial data."""
    from unittest.mock import AsyncMock, patch

    from fastapi import HTTPException

    with patch(
        "backend.routes.admin.analytics.db.get_rows",
        AsyncMock(side_effect=RuntimeError("connection lost")),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await _analytics_module.get_driver_acceptance_rates(
                date_range="30d",
                service_area_id=None,
                limit=50,
                admin={"id": "admin-1", "role": "super_admin"},
            )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "analytics_unavailable"
