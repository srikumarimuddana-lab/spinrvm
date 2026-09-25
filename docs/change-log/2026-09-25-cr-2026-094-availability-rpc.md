# Change Impact & Risk Log — CR-2026-094: availability RPC controller rebind + replay flag

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code (workstream G), on behalf of the repo owner |
| Surface(s) | backend (Postgres function + direct_pool tests only) |
| Domain (Sentry tag) | dispatch / drivers |
| PR / commit link | branch `claude/fix-cr-2026-094-availability-rpc` (draft PR, refs #5768) |
| Related issue or gap ID | CR-2026-094 / GitHub issue #5768 (Cluster A) |

## 1. Issue / gap identified

13 tests in `backend/tests/direct_pool/` failed on every PR run of the real-Postgres tier:

- 5 × controller rebind (`test_go_online_from_current_session_binds_controller` ×4,
  `test_relogin_takes_over_an_online_controller_only_without_obligation`): `go_online` from the
  driver's current session returned `CONTROLLER_SESSION_MISMATCH` whenever a different
  controller was recorded, and the OK result had no `controller_rebound`/`server_time`.
- 3 × replay flag (`test_idempotency_and_authenticated_controller`,
  `test_system_actor_pauses_without_current_session_or_controller` ×2): an idempotent replay
  returned the stored result without `replayed: true`.
- 4 × `test_system_actor_is_limited_to_known_sources_and_pause_actions[...]`: expected a
  SQLSTATE 22023 raise; migration 464 deliberately returns `{"code":"UNAUTHORIZED_SESSION"}`.
- 1 × `test_completed_refund_hold_does_not_itself_qualify_as_cash_paid`: asserted
  `held == 0` after seeding a 5.00 clawback row that `held()` always sums.

## 2. Root cause

- Rebind + replay: designed and implemented (architect addendum C7/C8 and item 8) by editing
  migration 457 in place (commits `6b97b28cb`, `95aca1d50`). Because 457 had already been
  applied to production, commit `392120698` restored 457 to its applied state on the premise
  that 458+ already covered those edits. They did not, and 464 (the current definition,
  "transition hardening") was written from the restored 457 body, so the rebind path, the
  `controller_rebound`/`server_time` result fields and the replay flag were lost while the tests
  that exercise them survived.
- System-actor tests: 464 intentionally changed system-actor misuse from a 22023 raise (the
  lost 457 edit) to a returned `UNAUTHORIZED_SESSION`; the tests were never updated.
- Refund test: test bug. `held()` sums every `payout_type='clawback'` row, including the seeded
  one, so `held == 0` is unreachable. Migration 451's SQL correctly excludes clawbacks from the
  cash-paid qualification (`payout_type IN ('auto','instant','standard')`).

## 3. Fix / remediation

- **Owner decision (2026-09-25):** YES — a newer login from the driver's *current* session takes
  over an idle controller (no active trip, no pending/accepted offer), exactly as the original
  design and the tests describe. A takeover while an obligation is active is still refused.
  464's return-a-code behaviour for system-actor misuse is kept.
- New migration `486_driver_availability_controller_rebind.sql`: `CREATE OR REPLACE` of
  `transition_driver_availability` from 464's exact body with only these changes:
  1. `go_online` added to the actions exempt from the `CONTROLLER_SESSION_MISMATCH` guard for
     non-system callers, and `go_online` now binds the caller as controller (464 bound only when
     the controller was NULL).
  2. OK result adds `controller_rebound` (true only when an existing, different controller was
     replaced by `go_online`/`displace_controller`) and `server_time`.
  3. Idempotent replay returns `stored_result || {"replayed": true}` (stored row unchanged).
  Signature, `SECURITY DEFINER`, `search_path`, system-actor allowlist, readiness window,
  insurance-period handling, REVOKE/GRANT and COMMENT are byte-identical to 464.
- System-actor tests now assert the returned `UNAUTHORIZED_SESSION` code, that nothing was
  mutated (epoch 0, controller NULL) and that no request row was saved.
- Refund test now asserts what the SQL guarantees: `held == 5` (only the seeded row), the only
  clawback row is the seeded one (no new hold written), and the refund still projected
  (`rides.refund_amount == 5`).
- `direct_pool/conftest.py`'s session-scoped `_MIGRATION_FILES` now lists 486 right after 464
  (same append-only precedent as 403 over 402 and 421 over 253), so every direct_pool file runs
  against the current definition. `test_offer_decision_atomicity.py`'s `offer_db` fixture, which
  re-applies 457-464 per test, now also applies 486 after 464 so it does not roll the function
  back. (An earlier revision applied 486 ad hoc in the epoch fixture; that leaked across files via
  the autocommit session connection and was replaced after reviewer feedback.)

## 4. Risk & impact on existing functionality

- **Who calls the RPC:** `backend/repositories/driver_availability_repo.transition_driver_availability`,
  used by `services/driver_availability_service.py` (driver Go/Stop/Offline and
  `pause_driver_for_policy`), `services/driver_session_end_service.py` (logout stop) and
  `utils/stale_intent_reconciler.py` (stale-intent pause). All branch only on `result["code"]`;
  none reads `controller_rebound`, `server_time` or `replayed` (grep for all three across
  `backend/`, `driver-app/`, `shared/`: no hits outside `tests/direct_pool`). Added fields are
  additive JSON; the service forwards the transition dict under `"transition"` on OK.
- **Behaviour change (only when `driver_availability_v2_enabled = true`):** a current-session
  `go_online` over a stale controller now succeeds with an epoch bump instead of
  `CONTROLLER_SESSION_MISMATCH`. The old session is fenced twice: it is no longer
  `users.current_session_id` (→ `UNAUTHORIZED_SESSION`) and its epoch is stale
  (→ `ONLINE_EPOCH_STALE`). Stop/Offline/pause from a non-controller session remain fenced.
- **Obligation safety:** the rebind can never happen during a trip or live offer — the
  `OBLIGATION_ACTIVE` return for `go_online` precedes the controller write and the UPDATE.
  Insurance period handling is unchanged (Go still records Period 1 only after the obligation
  check); Periods 2/3 are untouched.
- **Replay flag:** only the returned JSON gains `replayed: true`; the stored
  `driver_availability_requests.result` is unchanged, so repeated replays are stable.
- **Live production effect at apply time: none.** Per CR-2026-094 (reported 2026-09-25, not
  re-queried here) production has `driver_availability_v2_enabled = false`, 0 rows in
  `driver_availability_requests` and 0 drivers transitioned via this RPC, so every call returns
  `AVAILABILITY_V2_DISABLED` before any changed line (the replay branch precedes the flag
  check but has no saved request to replay).
- **Blast radius:** single-surface (backend DB function), dark behind an existing off flag. No
  ride state machine, money, or background-loop semantics change.

## 5. User-experience effect

- Nobody today (flag off). When v2 is enabled: a driver who logs in on a new phone while the old
  phone left them online can tap Go and take over instead of being stuck on a controller
  mismatch; during a trip or pending offer they still get the obligation refusal. Not visible
  mid-session to anyone until the flag is turned on. No copy/notification change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/486_driver_availability_controller_rebind.sql` | New `CREATE OR REPLACE` of `transition_driver_availability` from 464 + rebind, `controller_rebound`/`server_time`, `replayed` | Restore the lost 457 edits append-only (CR-2026-094, owner decision) |
| `backend/tests/direct_pool/conftest.py` | `_MIGRATION_FILES` adds 486 after 464 | Whole direct_pool tier runs against the current function definition |
| `backend/tests/direct_pool/test_offer_decision_atomicity.py` | `offer_db` applies 486 after its 457-464 re-apply | Its per-test re-apply would otherwise roll the function back to 464 |
| `backend/tests/direct_pool/test_driver_availability_epoch.py` | System-actor misuse tests assert returned `UNAUTHORIZED_SESSION` + no mutation + no saved request | Keep 464's return-a-code behaviour per owner |
| `backend/tests/direct_pool/test_atomic_driver_refund_holds.py` | Cash-paid test asserts `held == 5`, no new clawback row, refund still projected | Test asserted an unreachable value; SQL is correct |
| `docs/change-log/2026-09-25-cr-2026-094-availability-rpc.md` | This entry | Mandatory change log |

## 7. Before / after

```sql
-- Before (464)
    IF NOT v_is_system AND v_driver.controller_session_id IS NOT NULL
       AND v_driver.controller_session_id <> p_authenticated_session_id
       AND p_action NOT IN ('displace_controller','pause_policy') THEN
        RETURN jsonb_build_object('code','CONTROLLER_SESSION_MISMATCH', ...);
    ...
    IF p_action = 'go_online' AND v_driver.controller_session_id IS NULL THEN
        v_driver.controller_session_id := p_authenticated_session_id;
    ELSIF p_action = 'displace_controller' THEN
        v_driver.controller_session_id := p_authenticated_session_id;
    END IF;
    ...
        RETURN v_saved.result;                       -- idempotent replay
```

```sql
-- After (486)
    IF NOT v_is_system AND v_driver.controller_session_id IS NOT NULL
       AND v_driver.controller_session_id <> p_authenticated_session_id
       AND p_action NOT IN ('go_online','displace_controller','pause_policy') THEN
        RETURN jsonb_build_object('code','CONTROLLER_SESSION_MISMATCH', ...);
    ...
    v_rebound := p_action IN ('go_online','displace_controller')
                 AND v_driver.controller_session_id IS NOT NULL
                 AND v_driver.controller_session_id <> p_authenticated_session_id;
    IF p_action IN ('go_online','displace_controller') THEN
        v_driver.controller_session_id := p_authenticated_session_id;
    END IF;
    ...
        'controller_rebound',v_rebound,'server_time',clock_timestamp()   -- OK result
    ...
        RETURN v_saved.result || jsonb_build_object('replayed', true);  -- idempotent replay
```

Concrete scenario (flag on): driver online with `controller_session_id='sess-A'`, re-logs in so
`users.current_session_id='sess-B'`, no ride/offer. Before: `go_online` from `sess-B` →
`CONTROLLER_SESSION_MISMATCH`, driver stuck. After: → `OK`, controller `sess-B`, epoch +1,
`controller_rebound: true`; `sess-A` → `UNAUTHORIZED_SESSION`. Same scenario with a
`driver_assigned` ride: before and after → `OBLIGATION_ACTIVE`, controller and epoch unchanged.

## 8. Rollback plan

- Behavioural: keep/set `settings.driver_availability_v2_enabled = false` (no deploy) — every
  call returns `AVAILABILITY_V2_DISABLED` before the changed lines.
- Function: re-apply the `CREATE OR REPLACE FUNCTION ... transition_driver_availability` block
  plus its REVOKE/GRANT/COMMENT from
  `backend/migrations/464_driver_availability_transition_hardening.sql`. Callers ignore the
  added fields, so no code change is needed. No data rewrite is required: stored request rows
  are never modified by 486.

## 9. Verification performed

- [x] Real Postgres: local PostgreSQL 16 (throwaway cluster, trust auth), running the CI
  invocation `pytest tests/direct_pool -c /dev/null --confcutdir=tests/direct_pool`.
  Before (origin/main `9bd9b9877`): **13 failed, 168 passed, 1 skipped** — exactly the 13 in
  CR-2026-094. After: **181 passed, 1 skipped, 0 failed**, run twice (one earlier run had 2
  transient failures in unrelated `test_offer_decision_atomicity_resolve.py` race tests with
  `RuntimeError: can't start new thread` — host thread exhaustion from parallel workstreams;
  both passed on the next two full runs and in the baseline).
- [x] After moving 486 into `conftest.py`'s `_MIGRATION_FILES` and `offer_db`: one sequential
  full direct_pool run (no xdist, `timeout 900`): **181 passed, 1 skipped, 0 failed** across all
  files.
- [x] 486 applied repeatedly (once per `offer_db` test, after 457-464) with no error —
  `CREATE OR REPLACE` is re-runnable.
- [x] Mocked suite subset: `pytest -o addopts="" -k "availability or go_online or driver_presence or refund_hold"`
  (excluding `tests/rls`, `tests/direct_pool`): 137 passed, 1 skipped.
- [x] `ruff check` / `ruff format --check` on both changed test files: clean.
- [x] Blast-radius grep: `transition_driver_availability`, `controller_rebound`, `replayed`
  across `backend/`, `driver-app/`, `shared/`.
- [x] Feature flag: already dark behind `driver_availability_v2_enabled` (off in production).
- No app build applies (no rider-app/driver-app/admin-dashboard change).

## 10. What was NOT verified

- Not run against Supabase/production or the CI `postgres:15` image — local PG16 only; the
  `driver-availability-db.yml` job (postgres:15) is the CI confirmation.
- Production state (flag off, 0 rows/drivers) is taken from the CR-2026-094 report, not
  re-queried by this change. Migration 486 has not been applied to any shared database.
- Pre-existing, not changed here: `test_driver_availability_presence_epoch.py`'s `presence_db`
  fixture re-applies **457 alone** per test on the autocommit session connection, so every
  direct_pool file that runs after it in collection order (the readiness files, until
  `offer_db` re-applies 457-464+486) exercises 457's transition body, not 486's. Those files
  pass either way today, but the fixture should stop re-applying 457 (the fix #5770 already made
  for the epoch file).

## Pre-rollout follow-ups (not in this PR — before `driver_availability_v2_enabled` is turned on)

From the dispatch review of this change:

1. No proactive WebSocket notice to a displaced device: after a rebind, the old phone only learns
   it lost control on its next call (`UNAUTHORIZED_SESSION` / `ONLINE_EPOCH_STALE`).
2. `displace_controller` has no `OBLIGATION_ACTIVE` guard. It is unreachable today (no caller
   sends it), but needs an owner acknowledgement or a ticket before v2 is enabled.
3. Get one green `driver-availability-db.yml` run on postgres:15 (local verification was PG16).
- No end-to-end driver-app flow was exercised (v2 is dark; the app has no visual tooling).

## Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow without the UX field filled in
