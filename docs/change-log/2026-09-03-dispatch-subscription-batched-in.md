# Change Impact & Risk Log — batch the dispatch driver_subscriptions `$in` lookup

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-03 |
| Author | Claude Code (production incident triage) |
| Surface(s) | backend |
| Domain (Sentry tag) | dispatch |
| PR / commit link | (none yet — pushed directly to `claude/supabase-pgbouncer-validation-x8p35d`, no PR opened) |
| Related issue or gap ID | none filed in `ACTION_ITEMS.md` — same bug class as the 2026-08-31 incident fixed via `repositories._base.get_rows_batched_in` (see that helper's `_IN_BATCH_SIZE` comment and `backend/tests/test_oversized_in_batching.py`) |

## 1. Issue / gap identified

Production log, 2026-09-03 22:28:16 UTC, tagged to a real dispatch attempt (`request_id`/`user_id` present, no admin/background marker):

```
ERROR | repositories._base:run_sync:569 | [DB] Supabase call failed (APIError):
{'message': 'JSON could not be generated', 'code': 400, 'hint': 'Refer to full message for details', 'details': "b'Bad Request'"}
```

## 2. Root cause

`backend/routes/rides/matching.py`'s dispatch attempt (`_match_driver_to_ride_attempt`) has two call sites that build `{"driver_id": {"$in": [...]}}` against `driver_subscriptions` over the **entire candidate driver pool** for a ride — the subscription-required-area gate (Spinr Pass eligibility) and the daily-quota (ride-allowance) filter. The candidate pool caps at 500 (the function's own "pool truncated" log fires when it does). A `$in` filter compiles to a PostgREST `col=in.(v1,v2,…)` URL query parameter, so the id list travels in the request line, not a body; at pool-cap scale that URL is long enough that the edge proxy in front of PostgREST rejects it with a plain-text `Bad Request` — not JSON — **before PostgREST ever sees it**. `postgrest-py` can't parse that body as JSON and synthesizes the opaque `APIError` above. Same mechanism as a 2026-08-31 incident (207 rejected requests/24h) already fixed for three other call sites; these two were missed because they sit inside `matching.py`'s per-attempt quota/subscription filters rather than the admin/background-sweep call sites that incident's own regression test (`test_oversized_in_batching.py`) enumerates.

## 3. Fix / remediation

Added a module-level helper in `matching.py`, `_get_active_subscriptions_batched(driver_ids, columns)`, that loops `_deps.db_supabase.get_rows` in chunks of 150 ids (mirrors `repositories._base._IN_BATCH_SIZE`) and concatenates the results. Both call sites now go through it instead of a single bare `$in` call.

**Deliberately does not reuse `repositories._base.get_rows_batched_in`** (the existing shared helper used by the three 2026-08-31 fixes) — see §4 for why.

## 4. Risk & impact on existing functionality

- **Blast radius: single-surface, two call sites, one file of production code** (`backend/routes/rides/matching.py`), both inside `_match_driver_to_ride_attempt`. No schema, table, or endpoint contract changes. No other reader/writer of `driver_subscriptions` is touched — this is a read-only lookup; nothing here writes to `driver_subscriptions`, `ride_offers`, or `driver_insurance_periods`.
- **Why not the existing `get_rows_batched_in` helper:** grepped `backend/tests/test_dispatch_match_attempt_branches.py` — at least 5 existing tests (`test_stale_ride_status_skips_dispatch` and others at lines ~129, ~183, ~311, ~459) replace `_deps.db_supabase` **wholesale** with a `MagicMock`/`patch(...)` and only stub `.get_rows` (directly or via the file's own `_rows_by_table` helper). Renaming the call site to a differently-named attribute (`get_rows_batched_in`) would leave that attribute an unconfigured `MagicMock` on those mocks; awaiting it raises `TypeError`. Critically, the **subscription-required gate fails CLOSED** on any exception (`all_drivers = []`) but the **daily-quota filter fails OPEN** (logs and continues, per its own comment: "a transient read error must not drop everyone like the subscription filter") — so an unconfigured-mock `TypeError` on the quota path would be silently swallowed, turning a test's "quota-filtered" expectation into a silently-wrong "unfiltered" one, with no visible failure to signal the mismatch. Rather than hand-updating every affected existing test (and any others elsewhere not yet found) without being able to execute the suite to confirm correctness, the fix keeps calling `_deps.db_supabase.get_rows` under its existing name, just looped — every existing test's mock keeps receiving the identical call shape for pools under 150 (a single iteration), which is 100% of current test fixtures.
- **Behavioral equivalence:** both call sites only ever use the result for set-membership checks (`d["id"] in _subscribed_ids`, `d["id"] not in _q_exhausted`) — never row order — so concatenating batch results out of cross-batch order is safe. `limit=len(chunk)` per batch mirrors the original single call's `limit=len(_candidate_ids)`/`limit=len(_q_ids)` cap (both assume ≤1 active-subscription row per driver id, an assumption this fix does not change).
- **Interaction with background loops / state machine / money:** none. This is a read-only SELECT inside a request-scoped dispatch attempt; no `driver_insurance_periods` write, no ride-state transition, no wallet/Stripe path touches this code.

## 5. User-experience effect

None when the candidate pool is small (unchanged call shape). When the pool is large (busy service area, 150+ online matching drivers) and the ride's area is not Spinr-Pass-required (the common case): before this fix, the quota filter's request 400'd and was silently skipped (fail-open) — a driver who had exhausted their daily Spinr Pass ride allowance could occasionally still receive an offer, discovered only at accept-time (a wasted dispatch cycle + notification, not a stranded ride or a money bug). After this fix, the quota filter's request succeeds instead of 400ing, so that driver is correctly excluded up front. Not visible mid-session to a rider or driver in either case — this changes which driver receives an offer, not fare, receipt, or ride state.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/rides/matching.py` | Added `_get_active_subscriptions_batched()`; both `driver_subscriptions` `$in` lookups in `_match_driver_to_ride_attempt` now call it instead of a bare `_deps.db_supabase.get_rows(...)` | Caps each real HTTP request's `$in` list at 150 ids so it can't 400 at the edge proxy |
| `backend/tests/test_oversized_in_batching.py` | Added `TestGetActiveSubscriptionsBatched` (3 new tests: large-pool split, small-pool single-call parity, empty-pool no-query); updated the file's own docstring to list this as a third covered instance of the bug class | Regression coverage for the new helper; keeps this bug class's tests in one canonical file per the file's existing organization |

## 7. Before / after

Subscription-required-area gate:

```python
# Before
_active_subs = await _deps.db_supabase.get_rows(
    "driver_subscriptions",
    {"driver_id": {"$in": _candidate_ids}, "status": "active"},
    columns="driver_id,started_at,expires_at,plan_id,rides_per_day",
    limit=len(_candidate_ids),
)

# After
_active_subs = await _get_active_subscriptions_batched(
    _candidate_ids, "driver_id,started_at,expires_at,plan_id,rides_per_day"
)
```

Free-area daily-quota filter — identical shape, same helper:

```python
# Before
_q_subs = await _deps.db_supabase.get_rows(
    "driver_subscriptions",
    {"driver_id": {"$in": _q_ids}, "status": "active"},
    columns="driver_id,started_at,expires_at,rides_per_day",
    limit=len(_q_ids),
)

# After
_q_subs = await _get_active_subscriptions_batched(
    _q_ids, "driver_id,started_at,expires_at,rides_per_day"
)
```

## 8. Rollback plan

`git revert` is sufficient and complete — this is a pure code change with no schema, `app_settings`/feature-flag, or migration involved, and no live data (Stripe charges, wallet deltas, ride state, insurance-period rows) is written by this path. No flag was added because the change is a strict behavioral improvement over a pre-existing silent-failure path (fail-open), not a new user-visible behavior needing staged rollout.

## 9. Verification performed

- [x] `python3 -m py_compile` on both changed files — clean.
- [x] `ruff check` and `ruff format --check` on both changed files — clean.
- [x] Blast-radius grep performed: searched `backend/tests/` for `driver_subscriptions` and for `_rows_by_table(` usage in `test_dispatch_match_attempt_branches.py` to enumerate every existing test exercising these two call sites (5 found) and manually traced the highest-risk one (`test_dispatch_match_attempt_branches.py`'s quota fail-open test, ~line 183) line-by-line to confirm the new helper produces an identical single call for its 1-driver pool.
- [x] Reviewed against `CLAUDE.md` conventions: query-filter rules (§"Query filters — the layer owns escaping"), "do not silently swallow errors" (this fix doesn't change the existing fail-open/fail-closed exception policy, only removes a cause of hitting it), dispatch state-machine guidance (no ride-state transition touched).
- [x] Dispatched `spinr-dispatch-reviewer` via the Agent tool for an adversarial pass on the diff (correctness of batching/chunking, existing-test-compatibility claim, column-list parity, new-test validity) — running in the background at the time this doc was drafted; incorporate its findings before treating this as final if any surfaced.
- [ ] `pytest` — **not run**. This sandbox has no Python virtualenv / third-party dependencies installed (`ModuleNotFoundError: No module named 'fastapi'`, no `pytest` module) — confirmed directly, not assumed. Only `python3` itself (stdlib) and a standalone `ruff` binary are available.
- [ ] No manual staging repro — no staging access from this session.
- [ ] No production build applies (backend-only change).

## 10. What was NOT verified

- **The actual test suite was never executed.** All correctness claims about existing-test compatibility (identical call shape for small pools, the 5 enumerated `test_dispatch_match_attempt_branches.py` tests staying green) are from reading the mock code and reasoning through the call chain by hand, not from a passing CI/pytest run. This is the single biggest gap in this verification — flagged explicitly per `CLAUDE.md`'s "what was NOT verified" requirement rather than implied as covered.
- **Not confirmed against the real Supabase project** that this specific fix resolves the exact production incident — the incident log has a `request_id`/`user_id` but no request path, so the identification of `matching.py`'s daily-quota filter as the culprit (vs. some other unbatched `$in` call site) is a strong-evidence inference (candidate-pool size, "pool truncated" precedent, single-user-request log signature), not a confirmed stack-trace match.
- **The sibling issue class is not fully swept.** A broader grep found ~105 files using raw (non-batched) `$in` filters codebase-wide; only the two call sites implicated in this specific incident were fixed. Others may exist with the same latent risk and are out of scope for this change.
- Whether the separate Fly "app not listening on port 0.0.0.0:8000" 502 symptom (reported alongside this log line) shares a root cause was investigated and **ruled out** — that symptom points to a `Settings()` validation crash at process boot (a different code path entirely; see `backend/core/config.py`'s `model_validator`s), not this dispatch-read issue, which fails open and does not crash or block the process. Not something this change fixes.

## Sign-off

- [ ] Rollback plan is concrete and testable — yes (plain `git revert`, no data-level dependency).
- [x] Blast radius is stated, not assumed — see §4.
- [x] No silent behavior change to an already-shipped flow without the UX field filled in — see §5 (the one behavior change — quota filter starts actually filtering instead of silently no-op'ing on 400 — is stated explicitly, not left implicit).
