"""Deadline budget resolution: the clock-skew guard on the client time budget.

Background — the 2026-08-24 "Spinr DB calls rejected" alert storm. Clients sent
only `X-Deadline-Ms`, an absolute epoch stamped from the *device's* `Date.now()`.
The backend derived the request's budget by subtracting its *own* clock from it,
so device clock skew landed directly in the budget. A handset running ~15 s
behind produced a budget that was already negative on arrival, `run_sync()`
rejected every DB call in the request pre-flight with a 503, and the capacity
watchdog paged about a database that was perfectly healthy.

These tests pin both halves of the fix: the relative header that makes skew
structurally impossible, and the clamp that defangs the legacy absolute one for
app builds already in the field.

See backend/core/middleware.py::resolve_deadline_budget_ms.
"""

from __future__ import annotations

import pytest

from core.middleware import (
    DEADLINE_MAX_MS,
    DEADLINE_MIN_MS,
    resolve_deadline_budget_ms,
)

pytestmark = pytest.mark.unit

NOW_MS = 1_700_000_000_000  # fixed server clock; never read the real one here


def test_relative_timeout_header_is_used_verbatim():
    budget, source, clamped = resolve_deadline_budget_ms("15000", None, NOW_MS)
    assert (budget, source, clamped) == (15000, "timeout", None)


def test_relative_header_wins_over_a_skewed_absolute_one():
    """The whole point of X-Timeout-Ms: when both are present the skew-immune
    one decides, so a wrong device clock stops mattering the moment an updated
    app build ships."""
    skewed = str(NOW_MS - 20_000)  # device 20 s behind → legacy budget is -20 s
    budget, source, clamped = resolve_deadline_budget_ms("15000", skewed, NOW_MS)
    assert budget == 15000
    assert source == "timeout"
    assert clamped is None


def test_absolute_header_from_a_device_running_behind_is_clamped_up_not_obeyed():
    """The storm's root cause. An expired-on-arrival budget must not reject the
    request's first DB call — it is a broken clock, not a client that gave up."""
    skewed = str(NOW_MS - 20_000)
    budget, source, clamped = resolve_deadline_budget_ms(None, skewed, NOW_MS)
    assert budget == DEADLINE_MIN_MS, "expired legacy deadline was obeyed"
    assert source == "deadline"
    assert clamped == "up"


def test_absolute_header_from_a_device_running_ahead_is_clamped_down():
    """A clock hours ahead would pin the budget so far out that the fail-fast
    protection silently stops existing."""
    budget, source, clamped = resolve_deadline_budget_ms(None, str(NOW_MS + 3_600_000), NOW_MS)
    assert budget == DEADLINE_MAX_MS
    assert source == "deadline"
    assert clamped == "down"


def test_healthy_absolute_header_passes_through_unclamped():
    """A correct clock must be unaffected — the clamp is a guard, not a rewrite."""
    budget, source, clamped = resolve_deadline_budget_ms(None, str(NOW_MS + 15_000), NOW_MS)
    assert budget == 15_000
    assert source == "deadline"
    assert clamped is None


def test_no_headers_means_no_deadline_enforced():
    assert resolve_deadline_budget_ms(None, None, NOW_MS) == (None, None, None)


def test_malformed_headers_are_ignored_rather_than_rejected():
    """A bad header is not worth a 400 — it degrades to "no deadline"."""
    assert resolve_deadline_budget_ms("abc", None, NOW_MS) == (None, None, None)
    assert resolve_deadline_budget_ms(None, "not-a-number", NOW_MS) == (None, None, None)
    assert resolve_deadline_budget_ms("abc", "xyz", NOW_MS) == (None, None, None)


def test_malformed_relative_header_falls_back_to_the_absolute_one():
    """Dual-shipping means one header can be junk while the other is fine."""
    budget, source, clamped = resolve_deadline_budget_ms("junk", str(NOW_MS + 15_000), NOW_MS)
    assert budget == 15_000
    assert source == "deadline"


@pytest.mark.parametrize("value", ["0", "-1", "-30000"])
def test_non_positive_relative_timeouts_are_clamped_up_too(value):
    """A client that asks for zero budget is misconfigured, not a client that
    wants every DB call refused."""
    budget, _source, clamped = resolve_deadline_budget_ms(value, None, NOW_MS)
    assert budget == DEADLINE_MIN_MS
    assert clamped == "up"


def test_clamp_bounds_are_sane_relative_to_each_other():
    assert 0 < DEADLINE_MIN_MS < DEADLINE_MAX_MS
