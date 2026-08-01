# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-01 |
| Author | Claude Code |
| Surface(s) | backend (test-only) |
| Domain (Sentry tag) | drivers |
| PR / commit link | (this branch: `claude/fix-3029-3day-expiry-flag`) |
| Related issue or gap ID | #3029 |

## 1. Issue / gap identified

`tests/test_spinr_pass_subscription.py::TestExpiryWarning3Day::test_3d_warning_skipped_when_flag_already_set` failed on `main`, reproducing in complete isolation. It asserts that a subscription already flagged `expiry_warned_3d=True` gets no second 3-day-expiry push, but a push fired anyway.

## 2. Root cause

Test-only bug, not a production bug. `check_expiring_subscriptions()` (`backend/routes/drivers/subscriptions.py`) issues two distinct `get_rows("driver_subscriptions", ...)` calls with different filters: the main `active_subs` scan (`{"status": "active"}`, no `expiry_warned_3d` clause) and a dedicated 3-day-warning query that explicitly filters `{"expiry_warned_3d": False, ...}` — a real Postgres/PostgREST query would exclude an already-warned row from that second query.

The test mocked `backend.db_supabase.get_rows` as a single flat `AsyncMock(return_value=[sub])`, which answers *both* calls identically regardless of the filter argument. Since the fixture's `sub` has `expiry_warned_3d=True`, the dedicated query incorrectly received a row a real DB filter would have excluded, and the push fired — the exact behavior the test exists to prevent, but caused by an under-specified mock, not the application code.

Secondary, related bug: unlike its sibling test (`test_3d_warning_push_sent_and_flag_set`, which has a code comment explaining this), this test never patched `backend.utils.redis_client.redis_set_nx` to force the distributed lock to always be won. `check_expiring_subscriptions()` gates its whole loop body behind that lock, backed by a module-level in-process dict when Redis is unset (dev/test fallback). If any earlier test in the same pytest process already "acquired" that lock key, this test would silently no-op and pass for the wrong reason (lock contention) rather than exercising the filtering logic under test — meaning the assertion's pass/fail was non-deterministic depending on full-suite run order, independent of the primary bug above.

## 3. Fix / remediation

- Replaced the flat `get_rows` mock with a `side_effect` function that inspects the filter's `$and` clauses and returns `[]` when the dedicated 3d-warning query's `expiry_warned_3d: False` filter would have excluded the fixture's already-warned row — mirroring real DB filtering behavior.
- Added the same `redis_set_nx` force-lock patch already present on the sibling test, for the same documented reason (deterministic outcome independent of suite run order).

No application code changed.

## 4. Risk & impact on existing functionality

- Blast radius: **isolated** — single test file, test-only change.
- Grepped for other callers of `check_expiring_subscriptions` and `get_rows("driver_subscriptions", ...)`: none touched by this change; production `backend/routes/drivers/subscriptions.py` is untouched.
- No interaction with money, wallet, ride state, or background-loop *production* behavior — only this test's mocking of one background loop.

## 5. User-experience effect

None — test-only change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_spinr_pass_subscription.py` | `get_rows` mock changed from flat `AsyncMock(return_value=[sub])` to a filter-aware `side_effect`; added `redis_set_nx` force-lock patch | Match real DB filtering behavior and remove suite-order dependence, per #3029 |
| `docs/change-log/2026-08-01-fix-3029-3day-expiry-flag-test.md` | New change-log entry | Required by CLAUDE.md for anything closing a tracked gap |

## 7. Before / after

```python
# Before
with (
    patch("backend.db_supabase.get_rows", AsyncMock(return_value=[sub])),
    ...
):
```

```python
# After
async def _get_rows(table, filters=None, **kwargs):
    if filters and "$and" in filters:
        for clause in filters["$and"]:
            if clause.get("expiry_warned_3d") is False and sub["expiry_warned_3d"] is not False:
                return []
    return [sub]

with (
    patch("backend.db_supabase.get_rows", AsyncMock(side_effect=_get_rows)),
    ...
    patch("backend.utils.redis_client.redis_set_nx", AsyncMock(return_value=True)),
    ...
):
```

## 8. Rollback plan

`git revert` — pure test-only change, no live-data footprint, no migration.

## 9. Verification performed

- [x] Automated tests run: `backend/tests/test_spinr_pass_subscription.py` full file (40 tests, 0 failed) run in isolation
- [x] Full backend suite run (`pytest tests/ -q -p no:randomly --no-cov`) — see PR for final counts
- [ ] Manual repro in staging — n/a, test-only
- [x] Blast-radius grep performed: searched for other callers of `check_expiring_subscriptions`/`redis_set_nx` in tests — sibling test already uses the same lock-force pattern; no other test file depends on this test's mock shape
- [x] Reviewed against CLAUDE.md testing conventions (mock_supabase_client fixture pattern, filter-respecting mocks per the query-filter conventions section)
- [ ] Feature-flagged — n/a, test-only

## 10. What was NOT verified

- Not run against real Supabase — mocked throughout, matching repo convention for this test tier.
- Not run under `pytest-xdist` / parallel workers.
- Coverage (`--cov`) not separately verified — this diff is test-only, no production code path added or removed.
