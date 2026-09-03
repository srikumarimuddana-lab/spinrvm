# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-03 |
| Author | Claude (session) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments (C60), admin (C61) |
| PR / commit link | PR #4896 |
| Related issue or gap ID | ACTION_ITEMS.md C60, C61 |

## 1. Issue / gap identified

- **C60:** `backend-test`'s "Run backend tests" step failed
  `tests/test_loguru_call_conventions.py::test_no_exc_info_kwarg_in_loguru_calls`
  on `main`'s own tip — 9 call sites in the transactional-outbox worker
  pass `exc_info=True` to loguru's `.error()`, which loguru doesn't support.
- **C61:** `tests/test_referral_analytics.py::test_funnel_counts_and_amounts`
  failed with `assert 0 == 2` on `main`'s own tip.

Both pre-existing, unrelated to PR #4896's own diff (RLS conftest); found
while babysitting that PR's CI.

## 2. Root cause

- **C60:** `logger.error(msg, exc_info=True)` is the stdlib `logging`
  convention. These 3 files log via **loguru**, which has no `exc_info`
  parameter — it's silently swallowed as an ordinary `str.format` keyword
  and no traceback is captured. The loguru-correct form, already used
  elsewhere in this codebase (`documents.py`, `dependencies/__init__.py`,
  `corporate_member_offboarding_service.py`), is
  `logger.opt(exception=True).error(msg, ...)`. These 9 sites were
  introduced by the C53 finding-4 transactional-outbox work (PR #4887).
- **C61:** `admin_get_referral_analytics`'s `total_referred` count was
  refactored under migration 387 (`admin_referred_user_count_fn.sql`) from
  a Python-side fetch-all-`users`-rows-and-count to a single
  `db_supabase.rpc("admin_referred_user_count", ...)` call. The test never
  mocked `db_supabase.rpc`, so the real (unconfigured-in-tests) client's
  call returned `None`, which the endpoint's own fallback logic turns into
  `total_referred = 0` — the same class of stale-mock-after-RPC-refactor
  bug as C58 (`test_email_deliverability.py`).

## 3. Fix / remediation

- **C60:** converted all 9 sites to `logger.opt(exception=True).error(...)`.
  No control-flow change — only whether a traceback is actually captured
  when these `except` blocks fire.
- **C61:** mocked `admin_drivers.db_supabase.rpc` with
  `AsyncMock(return_value=2)` in the one failing test, plus an
  `assert_awaited_once_with(...)` pinning the RPC's call contract
  (`p_kind`, `p_start`, `p_end`). No production code changed — the
  endpoint itself was already correct.

## 4. Risk & impact on existing functionality

- **C60** is logging-only inside `except` blocks in the outbox worker
  (`services/outbox.py`, `services/outbox_receipts.py`,
  `utils/outbox_worker.py`). These paths are dark-launched
  (`settings.outbox_receipts_enabled` defaults `False` per migration 399)
  and only fire on an already-erroring branch, so there is no change to
  any success-path behavior, return value, or caller. Blast radius:
  isolated to these 3 files' error-logging calls; no other module imports
  or depends on their log output format.
- **C61** is test-only. No production code in
  `routes/admin/drivers.py` changed. Blast radius: isolated to this one
  test file.
- No interaction with background loops beyond the outbox worker's own
  (already-existing) error path, no ride-state-machine or money-delta
  changes.

## 5. User-experience effect

None. C60 changes only whether an internal error log carries a traceback;
no user-facing behavior, copy, or timing changes. C61 is test-only.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/outbox.py` | 1 site: `logger.error(..., exc_info=True)` → `logger.opt(exception=True).error(...)` | loguru call convention |
| `backend/services/outbox_receipts.py` | 1 site: same | loguru call convention |
| `backend/utils/outbox_worker.py` | 7 sites: same | loguru call convention |
| `backend/tests/test_referral_analytics.py` | Mocked `db_supabase.rpc` in `test_funnel_counts_and_amounts`; added call-contract assertion; comment on now-unused `users` fixture data | fix stale mock after migration-387 RPC refactor |

## 7. Before / after

```python
# Before (backend/utils/outbox_worker.py, one of 7 identical-shape sites)
logger.error("outbox_stats failed", exc_info=True)
```

```python
# After
logger.opt(exception=True).error("outbox_stats failed")
```

```python
# Before (backend/tests/test_referral_analytics.py)
with patch.object(admin_drivers.db_supabase, "get_rows", _rows_router(payouts, users)):
    res = _call(source="driver")
```

```python
# After
rpc_mock = AsyncMock(return_value=2)
with patch.object(admin_drivers.db_supabase, "get_rows", _rows_router(payouts, users)), patch.object(
    admin_drivers.db_supabase, "rpc", rpc_mock
):
    res = _call(source="driver")
rpc_mock.assert_awaited_once_with(
    "admin_referred_user_count", {"p_kind": "driver", "p_start": None, "p_end": None}
)
```

## 8. Rollback plan

`git revert` is sufficient for both — no data-level or live-behavior change
was applied. C60 is a pure logging-call rewrite (same log messages, same
control flow); C61 is test-only.

## 9. Verification performed

- [x] Automated tests run: `pytest tests/test_loguru_call_conventions.py -q`
  → 5/5 passed. `pytest tests/test_referral_analytics.py -q` → 5/5 passed.
  `pytest tests/ -k "outbox or referral" -q` → 189 passed, 21 skipped, no
  regressions across adjacent outbox/referral suites.
- [ ] Manual repro steps followed in staging — not applicable (logging
  convention + test-only fix, no runtime behavior to observe).
- [x] Blast-radius grep performed: `grep -n exc_info backend/services/
  outbox.py backend/services/outbox_receipts.py backend/utils/
  outbox_worker.py` confirms zero remaining `exc_info=` usages; no other
  file in the repo imports these modules' logging output.
- [x] Reviewed against relevant `CLAUDE.md` convention: Observability
  Conventions (loguru call correctness) and Testing Conventions (patch
  target discipline — `admin_drivers.db_supabase` is the correct binding
  per `db_supabase.py`'s re-export note).
- [x] `ruff check` clean on all 4 touched files.
- Not feature-flagged: not user-visible, not applicable.

## What was NOT verified

- Not verified against a real loguru sink capturing an actual traceback
  end-to-end (e.g. into Sentry) — verification was via the repo's existing
  static-analysis regression test (`test_loguru_call_conventions.py`),
  which checks the call convention itself, not runtime capture. This
  matches how the identical `documents.py` loguru fix was verified
  historically (same test, no live-Sentry check).
- No production build step applies here (backend-only, no
  admin-dashboard/rider-app/driver-app change).

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`, no data impact)
- [x] Blast radius is stated: C60 isolated to 3 files' error-log calls
  inside dark-launched, already-erroring paths; C61 isolated to 1 test file
- [x] No silent behavior change to an already-shipped flow (C60 changes
  only whether a traceback is captured on error; C61 has zero production
  code change)
