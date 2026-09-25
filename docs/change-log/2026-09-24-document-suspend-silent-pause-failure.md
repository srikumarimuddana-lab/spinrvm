# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-24 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | drivers |
| PR / commit link | (opened alongside this file) |
| Related issue or gap ID | #5750 (finding 2) |

## 1. Issue / gap identified

`backend/routes/drivers/_shared.py::_suspend_driver_for_expired_documents` (v2 branch) does two
steps for a driver whose documents expire mid-session: (1) a CAS write setting
`drivers.status = 'suspended'`, then (2) a `user_id` lookup followed by
`pause_driver_for_policy(user_id, ...)` — the call that actually clears availability and handles
insurance-period-adjacent bookkeeping. If the `user_id` lookup returns no row, step 2 was skipped
entirely with zero logging.

## 2. Root cause

The function's docstring claimed "a failed suspend-write just means the sweep catches it later" —
true for step 1 (the CAS write itself failing leaves `status` unchanged, so the 12h `document_expiry`
sweep's own CAS on `status != 'suspended'` will re-match and retry on its next tick). It is **not**
true for step 2: once step 1's write lands, `status` is already `'suspended'`, so the sweep's CAS
never re-matches this driver again (confirmed directly in `backend/utils/document_expiry.py`, which
uses the identical `{"id": ..., "status": {"$ne": "suspended"}}` CAS and explicitly treats a non-match
as "already suspended by a prior tick... nothing to do"). A skipped or failed step 2 is therefore not
self-healing, and the empty-`user_id` case being silent meant there was no operational signal that a
driver was stuck `status='suspended'` without ever having been routed through the availability-pause
fence.

## 3. Fix / remediation

Added an `else: logger.error(...)` branch for the empty-`user_id` case, naming the driver ID so it's
greppable/alertable. Corrected the docstring to stop claiming the sweep is self-healing for the pause
step specifically, and to name the actual (narrower) guarantee. No compensating write (e.g. a direct
`is_online=False` fallback, or calling `close_period_for_forced_offline` directly) was added —
`pause_driver_for_policy` owns locking/epoch semantics for this transition, and a hand-rolled
workaround risks conflicting with whatever insurance-period bookkeeping it does internally. Per
CLAUDE.md's escalation gate, a real fix for the underlying gap (e.g. a background reconciler that
scans for `status='suspended'` drivers never routed through the pause fence) needs more context than
a scoped bugfix should assume, and is left as a follow-up rather than guessed at here.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated** — one function, one new log-only branch, no behavior change to the
  success path. `spinr-insurance-period-auditor` reviewed the diff directly before this commit: no
  insurance-period logic touched, blast radius confirmed isolated.
- **What else reads/writes the same fields**: `drivers.status`, `is_online`/`is_available` (this
  function doesn't touch the latter two on the v2 path either before or after this fix) and
  `driver_insurance_periods` (via `pause_driver_for_policy`, unaffected). The 12h
  `backend/utils/document_expiry.py` sweep shares the same CAS pattern; not touched by this fix.
- Does not change the happy path (`user_id` present): existing test
  `test_v2_document_suspend_uses_policy_pause_without_raw_offline` still passes unmodified.

## 5. User-experience effect

None directly visible — this only adds a log line on a path that was previously silent. The *end
state* for a driver caught by the empty-`user_id` case is unchanged by this fix (still
`status='suspended'`, still not routed through the pause fence) — this fix makes that state
operationally visible, it doesn't yet resolve it. No rider/driver/admin-facing copy or flow changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/drivers/_shared.py` | Added `else: logger.error(...)` for empty `user_id`; corrected the docstring's misleading "sweep catches it later" claim | Stop a silent swallow on a non-self-healing failure path (CLAUDE.md: never silently swallow a DB/auth error) |
| `backend/tests/test_accept_ride_document_expiry.py` | Added `test_v2_document_suspend_logs_loudly_when_user_id_lookup_is_empty` | Regression-guard the fix |

## 7. Before / after

```python
# Before
if user_id:
    try:
        result = await pause_driver_for_policy(...)
        if result.get("code") not in {"OK", "POLICY_STATE_CHANGED"}:
            logger.error(...)
    except Exception:
        logger.error(...)
# empty user_id: falls through silently, nothing logged
```

```python
# After
if user_id:
    try:
        result = await pause_driver_for_policy(...)
        if result.get("code") not in {"OK", "POLICY_STATE_CHANGED"}:
            logger.error(...)
    except Exception:
        logger.error(...)
else:
    logger.error(
        "[ACCEPT] scoped document policy pause skipped for driver=%s — "
        "user_id lookup returned no row; driver is status=suspended "
        "but was never taken through the availability-pause fence",
        driver_id,
    )
```

## 8. Rollback plan

`git revert` — no migration, no data backfill, no money/wallet/ride-state mutation. This only adds a
log line; reverting removes it with no other effect.

## 9. Verification performed

- [x] Automated tests: `pytest tests/test_accept_ride_document_expiry.py tests/test_insurance_period_forced_offline_sites.py tests/test_drivers_shared_status_profile_coverage.py tests/test_drivers_extended.py` — 235/235 pass
- [x] Blast-radius grep: confirmed via direct read of `backend/utils/document_expiry.py` that the sweep's CAS never retries this driver once `status='suspended'` lands
- [x] `spinr-insurance-period-auditor` reviewed the diff directly before this commit (gate 10): confirmed accurate, safe, no insurance-period logic touched, no compensating-write risk introduced by *not* adding one
- [ ] Manual repro in staging — not available from this session

## 10. Sign-off

- [x] Rollback plan is concrete (`git revert`, no data cleanup)
- [x] Blast radius stated: isolated to one function, one new branch
- [x] No silent behavior change to a shipped flow — the only change is a new log line on a previously-silent path; the driver's actual state on that path is unchanged by this fix

## What was NOT verified

The underlying gap (a driver can still end up `status='suspended'` without ever being routed through
the pause fence) is not fixed by this change — only made operationally visible. Whether a background
reconciler or a different compensating action is the right real fix is a follow-up decision, not
resolved here. Not tested against real Postgres/Supabase — only `mock_supabase_client`-backed unit
tests, this repo's standard tier for this kind of change.
