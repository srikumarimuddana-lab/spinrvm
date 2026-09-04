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

`backend/routes/rides/matching.py`'s dispatch attempt (`_match_driver_to_ride_attempt`) had **three** call sites that build `{"driver_id": {"$in": [...]}}` against `driver_subscriptions` over an entire candidate driver pool: the subscription-required-area gate (Spinr Pass eligibility), the daily-quota (ride-allowance) filter, and — found by `spinr-dispatch-reviewer`'s pass on the first two fixes, not by the original triage — the vehicle-cascade pool's own subscription sub-filter. The primary candidate pool caps at 500 (the function's own "pool truncated" log fires when it does); the cascade pool is separately capped at 500 by its own `drivers` query. A `$in` filter compiles to a PostgREST `col=in.(v1,v2,…)` URL query parameter, so the id list travels in the request line, not a body; at pool-cap scale that URL is long enough that the edge proxy in front of PostgREST rejects it with a plain-text `Bad Request` — not JSON — **before PostgREST ever sees it**. `postgrest-py` can't parse that body as JSON and synthesizes the opaque `APIError` above. Same mechanism as a 2026-08-31 incident (207 rejected requests/24h) already fixed for three other call sites; these three were missed because they sit inside `matching.py`'s per-attempt quota/subscription/cascade filters rather than the admin/background-sweep call sites that incident's own regression test (`test_oversized_in_batching.py`) enumerates.

## 3. Fix / remediation

Added a module-level helper in `matching.py`, `_get_active_subscriptions_batched(driver_ids, columns)`, that loops `_deps.db_supabase.get_rows` in chunks of 150 ids (mirrors `repositories._base._IN_BATCH_SIZE`) and concatenates the results. All three call sites now go through it instead of a bare `$in` call.

**Deliberately does not reuse `repositories._base.get_rows_batched_in`** (the existing shared helper used by the three 2026-08-31 fixes) — see §4 for why.

**Process note:** the cascade call site was missed in the first pass (which only fixed the two sites directly implicated by the incident log's evidence) and caught by an adversarial `spinr-dispatch-reviewer` pass requested before treating the fix as final. It was pushed as a same-incident follow-up commit rather than a separate PR, since it's the identical bug in the identical function.

## 4. Risk & impact on existing functionality

- **Blast radius: single-surface, three call sites, one file of production code** (`backend/routes/rides/matching.py`), all inside `_match_driver_to_ride_attempt`. No schema, table, or endpoint contract changes. No other reader/writer of `driver_subscriptions` is touched — this is a read-only lookup; nothing here writes to `driver_subscriptions`, `ride_offers`, or `driver_insurance_periods`.
- **The cascade call site is the highest-severity of the three.** Unlike the quota filter (fails open — silently skips the filter), the cascade pool's subscription sub-filter fails **closed** on any exception (`_casc_pool = []`, "fail closed; non-subscriber cascade would bypass the gate"). A 400 there empties the vehicle-cascade fallback pool entirely, which feeds straight into `if not drivers_with_distance:` → a 10s retry, repeating until the stuck-ride sweeper cancels the ride after ~5 minutes. Pre-fix, a large enough cascade pool in a Spinr-Pass-required area could silently strand and auto-cancel a ride, not just skip a filter.
- **Why not the existing `get_rows_batched_in` helper:** grepped `backend/tests/test_dispatch_match_attempt_branches.py` — 13 existing tests replace `_deps.db_supabase` **wholesale** with a `MagicMock`/`patch(...)` and only stub `.get_rows` (directly, via the file's own `_rows_by_table` helper, or via a positional `side_effect=[...]` list keyed on call order). Renaming any call site to a differently-named attribute (`get_rows_batched_in`) would leave that attribute an unconfigured `MagicMock` on those mocks; awaiting it raises `TypeError`. That `TypeError` would be swallowed silently by whichever exception policy wraps each call site (fail-closed at the subscription gate and the cascade sub-filter, fail-open at the quota filter) — turning a "filtered" test expectation into a silently-wrong "unfiltered"/"emptied" one, with no visible failure to signal the mismatch. `spinr-dispatch-reviewer` independently re-derived and confirmed this exact mechanism (see §9) rather than taking it on faith. The fix instead keeps calling `_deps.db_supabase.get_rows` under its existing name, just looped — every existing test's mock keeps receiving the identical call shape for pools under 150 (a single iteration), which is 100% of current test fixtures, including a positional-`side_effect`-list test (`test_cascade_subscription_subfilter_exception_fails_closed`) where call *order*, not just call shape, had to be preserved.
- **Behavioral equivalence:** all three call sites only ever use the result for set-membership checks (`d["id"] in _subscribed_ids`, `d["id"] not in _q_exhausted`, `d["id"] in _casc_subscribed`) — never row order — so concatenating batch results out of cross-batch order is safe; independently confirmed by the reviewer tracing every downstream consumer. `limit=len(chunk)` per batch mirrors each original single call's `limit=len(_candidate_ids)`/`limit=len(_q_ids)`/`limit=len(_casc_cand_ids)` cap (all three assume ≤1 active-subscription row per driver id, an assumption this fix does not change).
- **Interaction with background loops / state machine / money:** none. This is a read-only SELECT inside a request-scoped dispatch attempt; no `driver_insurance_periods` write, no ride-state transition, no wallet/Stripe path touches this code. Confirmed by the reviewer reading the remainder of the function: the changes sit entirely in the candidate-gathering phase, before the claim / `ride_offers` insert / insurance-Period-2-write / notify phases.
- **Accepted tradeoffs, flagged by the reviewer, not acted on (see §10):** the batching loop awaits each chunk sequentially rather than via `asyncio.gather` — for a near-500-driver pool that's up to ~4 sequential round-trips added to the dispatch P95 < 2s SLA path, versus 1 before. This mirrors `get_rows_batched_in`'s own sequential style, so it's consistent with precedent, and is still strictly better than the pre-fix hard failure. `_SUBSCRIPTION_IN_BATCH_SIZE = 150` also duplicates `repositories._base._IN_BATCH_SIZE`'s value rather than importing it (deliberate — avoids a cross-module dependency on a private constant for one int), which is a minor silent-drift risk if the edge-proxy URL ceiling ever changes.
- **Known, out-of-scope sibling:** `backend/services/dispatch_service.py`'s `find_candidate_drivers` (the function `matching.py`'s own comments say this gate "mirrors") has the identical raw, unbatched `$in` pattern. Different file, not part of this incident's traced call chain — noted as a follow-up rather than folded into this fix.

## 5. User-experience effect

None when the candidate pool is small (unchanged call shape) — the overwhelming majority of dispatch attempts today. When the pool is large (busy service area, 150+ online matching drivers):

- **Quota filter (free areas, the common case):** before this fix, the request 400'd and was silently skipped (fail-open) — a driver who had exhausted their daily Spinr Pass ride allowance could occasionally still receive an offer, discovered only at accept-time (a wasted dispatch cycle + notification, not a stranded ride or a money bug). After: correctly excluded up front.
- **Vehicle-cascade subscription sub-filter (Spinr-Pass-required areas, only when the exact-vehicle-type pool is empty and a cascade to an upgrade type is attempted):** before this fix, a 400 here emptied the entire cascade pool (fail-closed), which could make a rider see repeated "searching" retries and an eventual auto-cancellation instead of matching with an available upgrade-type driver. After: the cascade pool is correctly evaluated instead of emptied.

Not visible mid-session to a rider or driver in either case — this changes which driver receives an offer (or whether cascade recovery succeeds), not fare, receipt, or already-committed ride state.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/rides/matching.py` | Added `_get_active_subscriptions_batched()`; all three `driver_subscriptions` `$in` lookups in `_match_driver_to_ride_attempt` (subscription-required gate, free-area quota filter, vehicle-cascade sub-filter) now call it instead of a bare `_deps.db_supabase.get_rows(...)` | Caps each real HTTP request's `$in` list at 150 ids so it can't 400 at the edge proxy |
| `backend/tests/test_oversized_in_batching.py` | Added `TestGetActiveSubscriptionsBatched` (3 new tests: large-pool split, small-pool single-call parity, empty-pool no-query); updated the file's own docstring to list this as a third covered instance of the bug class | Regression coverage for the new helper — covers all three call sites since they share it |

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

Vehicle-cascade subscription sub-filter — found by the reviewer's pass, fixed in a follow-up commit:

```python
# Before
_casc_subs = await _deps.db_supabase.get_rows(
    "driver_subscriptions",
    {"driver_id": {"$in": _casc_cand_ids}, "status": "active"},
    columns="driver_id,expires_at,plan_id",
    limit=len(_casc_cand_ids),
)

# After
_casc_subs = await _get_active_subscriptions_batched(
    _casc_cand_ids, "driver_id,expires_at,plan_id"
)
```

## 8. Rollback plan

`git revert` is sufficient and complete — this is a pure code change with no schema, `app_settings`/feature-flag, or migration involved, and no live data (Stripe charges, wallet deltas, ride state, insurance-period rows) is written by this path. No flag was added because the change is a strict behavioral improvement over a pre-existing silent-failure path (fail-open), not a new user-visible behavior needing staged rollout.

## 9. Verification performed

- [x] `python3 -m py_compile` on both changed files, after both the initial fix and the cascade follow-up — clean.
- [x] `ruff check` and `ruff format --check` on both changed files, after both commits — clean.
- [x] Blast-radius grep performed: searched `backend/tests/` for `driver_subscriptions` and for `_rows_by_table(`/`side_effect=[` usage in `test_dispatch_match_attempt_branches.py` to enumerate every existing test exercising the three call sites (13 found, including the two cascade-specific tests and one using a positional `side_effect` list rather than the table-keyed helper) and manually traced the highest-risk ones line-by-line to confirm the new helper produces an identical single call, in the identical position in call order, for their small pools.
- [x] Reviewed against `CLAUDE.md` conventions: query-filter rules (§"Query filters — the layer owns escaping"), "do not silently swallow errors" (this fix doesn't change the existing fail-open/fail-closed exception policy at either the quota or cascade sites, it removes a cause of hitting it), dispatch state-machine guidance (no ride-state transition touched), performance SLA table (dispatch offer→accept P95 < 2s — see the accepted sequential-batching tradeoff in §4).
- [x] Dispatched `spinr-dispatch-reviewer` via the Agent tool for an adversarial pass on the initial (two-call-site) diff. It could not install backend dependencies either (confirmed the same PyPI-unreachable constraint independently), so it **extracted the batching function body and the test monkeypatch mechanism into standalone scripts and executed them for real in bare Python** — boundary-case chunk counts (149/150/151/300/301/500), full dual-import-collision reproduction for the monkeypatch safety pattern, and a from-scratch trace of every downstream consumer of the query results to confirm order-independence. All five of its specific verification questions came back confirmed, not just plausible. It also found the third (cascade) call site as a blocker, fixed in the follow-up commit and re-verified by `py_compile`/`ruff` above (not re-reviewed by a second agent pass — see §10).
- [ ] `pytest` — **not run against the actual test files in the actual framework**, by either this session or the reviewer. This sandbox has no Python virtualenv / third-party dependencies installed (`ModuleNotFoundError: No module named 'fastapi'`, no `pytest` module) and PyPI is unreachable from it — confirmed directly by both this session and the reviewer independently, not assumed. The reviewer's standalone-script execution (above) is real code execution of the extracted logic, which is materially stronger than manual trace-through alone, but it is not equivalent to `pytest backend/tests/test_oversized_in_batching.py backend/tests/test_dispatch_match_attempt_branches.py` actually passing in CI.
- [ ] No manual staging repro — no staging access from this session.
- [ ] No production build applies (backend-only change).

## 10. What was NOT verified

- **The actual test suite (pytest, in-repo, in CI) was never executed** — see §9. This is the single biggest residual gap, even after the reviewer's standalone-script execution.
- **The cascade-pool fix (third call site) was not independently re-reviewed by the agent** — it was found and flagged by the reviewer's pass on the *first* diff (two call sites), then fixed by this session afterward using the identical, already-validated helper and pattern. `py_compile`/`ruff` are clean and the two cascade-specific existing tests were manually traced (§9), but a second adversarial pass specifically on the cascade edit itself was not run.
- **Not confirmed against the real Supabase project** that this specific fix resolves the exact production incident — the incident log has a `request_id`/`user_id` but no request path, so identifying `matching.py`'s quota filter as the culprit (vs. some other unbatched `$in` call site) is a strong-evidence inference (candidate-pool size, "pool truncated" precedent, single-user-request log signature), not a confirmed stack-trace match.
- **The sibling issue class is not fully swept.** A broader grep found ~105 files using raw (non-batched) `$in` filters codebase-wide. `backend/services/dispatch_service.py`'s `find_candidate_drivers` — explicitly named by `matching.py`'s own comments as logic this gate "mirrors" — has the identical pattern and was flagged by the reviewer as a follow-up, not fixed here (different file, not part of this incident's traced call chain; see §4). Others may exist with the same latent risk.
- **Accepted, not fixed:** the sequential (not concurrent) batch-fetch latency tradeoff and the duplicated `_IN_BATCH_SIZE`-equivalent constant — both flagged by the reviewer as non-blocking, documented in §4, deliberately left as-is to keep this change surgical.
- Whether the separate Fly "app not listening on port 0.0.0.0:8000" 502 symptom (reported alongside this log line) shares a root cause was investigated and **ruled out** — that symptom points to a `Settings()` validation crash at process boot (a different code path entirely; see `backend/core/config.py`'s `model_validator`s), not this dispatch-read issue, which fails open/closed within the request but never crashes the process. Not something this change fixes.

## Sign-off

- [x] Rollback plan is concrete and testable — yes (plain `git revert`, no data-level dependency).
- [x] Blast radius is stated, not assumed — see §4.
- [x] No silent behavior change to an already-shipped flow without the UX field filled in — see §5 (the one behavior change — quota filter starts actually filtering instead of silently no-op'ing on 400 — is stated explicitly, not left implicit).
