# Change Impact & Risk Log

> **Correction (2026-09-13, at merge time):** this PR was built independently and had
> reimplemented its own `reserve_budget()`/Lua script (`_RESERVE_LUA` in the sections below)
> before discovering, at merge time, that `docs/change-log/2026-09-12-atomic-budget-reserve-c104.md`
> (PR #5299, merged first) had already added the same atomic primitive to `main` — under the
> name `_RESERVE_BUDGET_LUA` — with its own adversarial review that found and fixed a real
> latency-cascade bug. Resolving the merge conflict kept PR #5299's already-reviewed primitive
> as canonical rather than ship two competing implementations of the same function; every
> section below describing `_RESERVE_LUA`, `_SKU_ORDER`, or this PR's own concurrency test
> suite describes that **discarded** implementation, kept here only as the historical record
> of what this PR actually built and tested before reconciliation — it is not what shipped.
> **What this PR's work actually contributed to the final, merged state:** migrating the
> remaining `check_budget()`+`record_call()` call sites (`maps_proxy.py`'s 4 endpoints,
> `route_distance.py`, `maps_eta.py`, `address_verification.py`) onto PR #5299's primitive,
> plus finding and fixing a real double/phantom-counting bug in `ai/tools_booking.py`'s gate
> conversion (§11 below — that section describes the real, shipped fix). See `ACTION_ITEMS.md`
> C104's entry for the authoritative, reconciled account of both passes.

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-13 |
| Author | Claude Code (session_016N2vRqybAY6LqEr8Yg7RUB) |
| Surface(s) | backend |
| Domain (Sentry tag) | ai (also touches `dispatch`/`rides` indirectly via `routes/rides/_shared.py`, `utils/maps_eta.py`) |
| PR / commit link | (filled in after push — see PR description) |
| Related issue or gap ID | C104 (`ACTION_ITEMS.md`) |

## 1. Issue / gap identified

`backend/utils/maps_budget.py`'s daily Google-Maps-API-spend circuit breaker used a
non-atomic check-then-increment: `check_budget()` (a read) was called at one point in a
request, and `record_call(sku)` (a write) was called later, after the actual Google HTTP
call. Under concurrent requests, many callers could each read the same "under budget"
total before any of them incremented, all get admitted, and collectively blow past
`MAPS_DAILY_BUDGET_USD`.

## 2. Root cause

`check_budget()` and `record_call()` are two separate Redis round trips with the actual
(unbounded-latency) Google HTTP request sitting in between them at every call site. Redis'
own `INCR` is atomic on a single key, but that doesn't help here — the race is in the GATE
(the read), not in the counter itself. Nothing serialized the read against other callers'
reads, so N concurrent callers near the cap boundary could all pass the gate simultaneously.

## 3. Fix / remediation

Added `reserve_budget(sku)` to `utils/maps_budget.py`: an atomic check-and-increment that
folds the read and the write into one round trip.

- **Redis-backed** (production): a single Lua script (`_RESERVE_LUA`) reads every SKU
  bucket's current count, sums the pre-call total, and only if that total is still under
  budget does it `INCR` the target SKU's bucket and return `allowed=True` — all inside one
  `EVAL`, which Redis executes atomically (no other command interleaves mid-script). No
  rollback logic is needed: the check happens before the increment, both in the same atomic
  step, so a rejected reservation never touches a counter at all.
- **In-process-dict fallback** (`REDIS_URL` unset — dev/test only): `redis_eval()` has no
  local Lua interpreter and raises `RuntimeError`; `reserve_budget()` catches that
  specifically and falls back to the equivalent Python read-then-increment sequence, now
  serialized through a new module-level `asyncio.Lock` (`_LOCAL_RESERVE_LOCK`) so concurrent
  callers can't interleave between the read and the write. (Aside: this fallback path's
  individual functions never actually suspend the event loop absent real I/O, so the window
  was arguably already closed in practice for a single process even without the lock — but
  that relied on an internal implementation detail of `redis_client._get_redis()`, not a
  documented contract, so the explicit lock was added anyway per the task's own guidance.)
- Every call site that previously did `check_budget()` immediately before a network call
  and `record_call(sku)` immediately after was converted to a single `reserve_budget(sku)`
  call right before the network request, with the old `record_call(sku)` line removed
  (calling both would double-count).
- **One exception, `backend/ai/tools_booking.py`**: its `_places_available()` gate admits a
  flow that may go on to make zero, one, or several *differently-priced* Google calls
  (geocode, `text_search_new`, or N concurrent `directions` calls in
  `_rank_named_place_candidates_by_route`) — there is no single known SKU/amount to reserve
  for at that one call site the way every other converted site has. `_places_available()`'s
  gate was converted to `reserve_budget("geocode")` (a conservative nominal admission
  charge — geocode is this module's cheapest, most common downstream call), which closes the
  coarse version of the race (many concurrent AI-tool invocations all admitted at once), but
  the individual `record_call(sku)` calls deeper in that file (5 call sites) were left
  untouched — they remain non-atomic bookkeeping, same as before this fix. See "What was NOT
  verified" and "Risk" below.

## 4. Risk & impact on existing functionality

**Blast radius — every caller found by grepping `check_budget|record_call|maps_budget` across
`backend/`, not just the 3 named in the ticket:**

| File | Call site(s) | Converted? |
|---|---|---|
| `backend/routes/maps_proxy.py` | 4 endpoints (autocomplete, details, reverse-geocode, directions) via shared `_ensure_budget()` | Yes — now takes an `sku` param |
| `backend/routes/rides/_shared.py` (via `routes/rides/_deps.py` re-export) | `_fetch_directions_route` (fare-estimate Directions, highest-volume Directions call site in the app) | Yes |
| `backend/utils/route_distance.py` | `_compute_route_via_google` (live-route fallback) + `compute_navigation_steps` (turn-by-turn) — **2 call sites, not named in the ticket's "known" list** | Yes, both |
| `backend/utils/address_verification.py` | `verify_address_matches_coordinate` | Yes |
| `backend/utils/maps_eta.py` | `get_ride_eta_seconds` + `batch_get_etas` (dispatch-matching hot path, `routes/rides/matching.py`) — **2 call sites, not named in the ticket's "known" list** | Yes, both |
| `backend/ai/tools_booking.py` | `_places_available()` (coarse gate) + 5 individual `record_call()` sites inside `find_place`/`get_rider_location` | Gate only — see exception above |

`check_budget()` and `record_call()` themselves are **not removed** — they remain used
by: `ai/tools_booking.py`'s 5 individual bookkeeping call sites, and anywhere a coarse,
non-atomic pre-check is still appropriate. No other module calls them.

**Behavior change (all converted sites):** the budget reservation now happens
*before* the outbound Google HTTP request instead of after it returns. Previously, a
request that reached Google but got a non-2xx/error response was sometimes *not* charged
(e.g. `maps_proxy.py`'s `places_autocomplete`/`places_details`, which only recorded after
`raise_for_status()` succeeded). Now every reserved call counts against the budget
regardless of the eventual HTTP outcome, since the whole point of an atomic reservation is
that the decision has to be made before the network call, not after. This is a
conservative/safe-direction change — consistent with this module's own documented
philosophy ("for a circuit breaker, overestimating spend trips early (safe); underestimating
lets real spend hide past the ceiling") — but it does mean the breaker can now trip
marginally earlier than before under a burst of upstream Google errors. No fare/pricing
logic is affected; this only gates whether the Maps proxy call is attempted at all.

**Latency:** the old pattern was 2 Redis round trips on the happy path (`check_budget()`'s
`MGET` + `record_call()`'s `INCR`); the new `reserve_budget()` is 1 round trip (`EVAL`).
This is a latency *improvement*, relevant on `utils/maps_eta.py`'s `batch_get_etas`, which
sits inside the dispatch-matching hot path's purpose-built 1.2s timeout window
(`routes/rides/matching.py`).

**Corporate/dispatch/payments:** none of these call sites touch corporate wallet, fare
calculation, or the ride state machine directly — they only decide whether a Maps API call
is allowed to proceed. Not applicable.

## 5. User-experience effect

None visible under normal operation. The only user-facing effect is what already existed:
when the daily Maps budget is exhausted, the affected endpoint/tool degrades to its existing
fallback (haversine distance instead of road route, "place lookup not available" message,
etc.) or returns a 503 — this fix does not change *what* happens on exhaustion, only *how
precisely* the exhaustion threshold is enforced under concurrent load. Not visible mid-session
in any new way.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/maps_budget.py` | Added `reserve_budget(sku)` (Lua script + `asyncio.Lock`-guarded local fallback); imports `redis_eval`, `asyncio` | The atomic primitive itself |
| `backend/routes/maps_proxy.py` | `_ensure_budget()` takes `sku`; calls `reserve_budget`; removed 4 `record_call()` lines | Close the race at all 4 endpoints |
| `backend/routes/rides/_deps.py` | Re-exports `reserve_budget` instead of `check_budget`/`record_call` | Shared import point for `_shared.py` |
| `backend/routes/rides/_shared.py` | `_fetch_directions_route` uses `reserve_budget("directions")`; removed `record_call()` | Close the race on the highest-volume Directions call site |
| `backend/utils/route_distance.py` | Both Directions call sites use `reserve_budget("directions")`; removed both `record_call()` calls | Close the race on the live-route fallback + nav-steps |
| `backend/utils/address_verification.py` | Uses `reserve_budget("geocode")`; removed `record_call()` | Close the race on address-verification geocoding |
| `backend/utils/maps_eta.py` | Both Distance Matrix call sites use `reserve_budget("distance_matrix")`; removed both `record_call()` calls | Close the race on ETA/dispatch-matching Maps calls |
| `backend/ai/tools_booking.py` | `_places_available()` uses `reserve_budget("geocode")` instead of `check_budget()` | Close the coarse admission race (see exception in §3) |
| `backend/tests/test_maps_budget.py` | New: concurrency regression tests for `reserve_budget()` (including a reference "old pattern" demo proving the race, plus reject/allow/fail-open unit tests) | Task requirement — prove the race is closed |
| `backend/tests/test_maps_proxy_coverage.py`, `test_directions_route.py`, `test_route_distance_coverage.py`, `test_compute_route_fallback.py`, `test_navigation_steps.py`, `test_address_verification.py`, `test_maps_eta_budget_gate.py` | Mock/patch targets renamed `check_budget`→`reserve_budget`; dead `record_call` patches removed; two tests' separate check/record fakes merged into one `reserve_budget` fake | Keep existing coverage passing against the new call shape |
| `backend/tests/test_ai_tools_booking.py` | `_patch_budget()` helper now patches `reserve_budget` instead of `check_budget` | Only reference to the old gate name in this file |

## 7. Before / after

```python
# Before (routes/maps_proxy.py, representative of every converted site)
async def _ensure_budget() -> None:
    allowed, spent, budget = await check_budget()
    if not allowed:
        raise HTTPException(503, ...)

async def places_autocomplete(...):
    await _ensure_budget()
    ...
    resp = await client.post(...)          # <-- unbounded network latency here
    ...
    await record_call("autocomplete")      # <-- increment happens AFTER the gate
```

```python
# After
async def _ensure_budget(sku) -> None:
    allowed, spent, budget = await reserve_budget(sku)   # atomic check+increment
    if not allowed:
        raise HTTPException(503, ...)

async def places_autocomplete(...):
    await _ensure_budget("autocomplete")
    ...
    resp = await client.post(...)
    ...
    # (no separate record_call — already recorded atomically above)
```

## 8. Rollback plan

No feature flag — this is a pure implementation-detail fix to an internal cost-control
primitive with no schema/migration/data component and no user-visible surface. Rollback is
a plain `git revert` of the commit: `reserve_budget()` is additive (a new function; the old
`check_budget()`/`record_call()` remain in `maps_budget.py`, untouched and still exported),
so reverting the call-site changes alone (or the whole commit) restores the exact prior
behavior with no data migration, no Stripe/wallet interaction, and no ride-state interaction
to unwind. The daily Redis budget counters (`maps:budget:<day>:<sku>`) are read/written the
same way by both the old and new code paths, so there's no counter-format incompatibility
between versions to worry about mid-rollback.

## 9. Verification performed

- [x] Automated tests run (unit, mocked-Redis, in-process-dict-fallback path):
  - `pytest backend/tests/test_maps_budget.py` — 11 passed (6 new: concurrency-race
    demonstration + 5 `reserve_budget()` unit tests)
  - `pytest backend/tests/test_maps_proxy.py backend/tests/test_maps_proxy_coverage.py
    backend/tests/test_directions_route.py backend/tests/test_route_distance_coverage.py
    backend/tests/test_compute_route_fallback.py backend/tests/test_navigation_steps.py
    backend/tests/test_address_verification.py backend/tests/test_maps_eta_budget_gate.py`
    — 174 passed
  - `pytest backend/tests/test_ai_tools_booking.py` — 110 passed
  - `ruff check` and `ruff format --check` on every changed `.py` file — clean
- [x] Blast-radius grep performed: `grep -rn "check_budget\|record_call\|maps_budget"
  backend/` (documented in full in §4) — found 2 call sites (`utils/route_distance.py`,
  `utils/maps_eta.py`, 2 each) beyond the 3 files named in the ticket.
- [x] Reviewed against relevant CLAUDE.md conventions: dual-import pattern preserved in
  every touched module (including re-fixing two cases where an automated formatter hook
  stripped the except-branch's copy of a not-yet-used import mid-edit); Redis-transparency
  fallback handled explicitly per the task's own instruction; no money/Decimal law applies
  (this module already used `float` pre-existing pricing constants — not fare/payment code
  under the pre-commit Decimal hook, and this fix does not change that).
- [x] Concurrency/race dry run: `test_reserve_budget_never_overshoots_by_more_than_one_call_under_concurrency`
  fires 40 concurrent `reserve_budget()` calls against a tiny budget and asserts the admitted
  count matches exactly what a correct sequential caller would admit (never more), and total
  spend never exceeds the cap by more than one call's worth. A companion test
  (`test_old_check_then_act_pattern_overshoots_budget_under_concurrency`) reproduces the OLD
  check-then-act pattern with an explicit yield standing in for the real Google HTTP call and
  shows it admits all 40 (the bug), for contrast.
- [ ] Manual repro steps followed in staging — not performed (see below).
- [ ] Feature-flagged — not applicable (see rollback plan; this is not a user-visible change).

## 10. What was NOT verified

- **No real Redis was available in this sandbox.** All testing ran with `REDIS_URL` unset,
  which exercises the `asyncio.Lock`-guarded in-process-dict fallback path, not the
  production Lua-script (`redis_eval`) path. The Lua script's syntax and logic were reasoned
  through by hand (traced the KEYS/ARGV indexing and the before/after-total arithmetic
  against the test cases) but never executed against a real Redis instance. Recommend a
  manual staging check: fire a burst of concurrent requests against `/api/v1/maps/*` with
  `MAPS_DAILY_BUDGET_USD` set very low, and confirm the admitted count matches expectations
  the way this PR's unit tests confirm it does for the fallback path.
- **`ai/tools_booking.py`'s residual gap is a real, if narrower, concurrency exposure**,
  not fully closed by this fix. Its 5 individual `record_call()` sites (inside
  `_geocode_with_locality_retry`, `_lookup_place_candidates`, `_rank_named_place_candidates_by_route`,
  `get_rider_location`) are still non-atomic bookkeeping calls made after the fact, same as
  before this fix — a burst of *concurrent AI-chat tool calls whose `_places_available()`
  gate all cleared before any of them recorded a downstream geocode/text-search/directions
  spend* could still modestly overshoot, in the same shape as the original bug, scoped to
  this one file's lower-volume, chat-driven call pattern. Converting those individual call
  sites was deliberately out of scope for this fix (see §3) because: (a) each is tied to a
  network call whose exact SKU/count isn't known until execution (a 1-of-2 geocode retry, or
  N concurrent `directions` calls in `asyncio.gather`), unlike every other converted site's
  clean 1:1 check-before/record-after pairing; and (b) this file's existing test suite
  (~40 call sites in `test_ai_tools_booking.py`) patches `record_call` as an inert no-op with
  no assertion in almost every case, relying on the global (not-per-test-reset) in-process
  Redis fallback dict staying effectively unused across the whole session — converting those
  individual sites to a *gating* atomic call risked introducing test-order-dependent
  flakiness across dozens of tests for a proportionally smaller real-world race window
  (AI-chat booking tool calls are far lower volume than the rider-facing Maps proxy or the
  dispatch-matching ETA path). Recommend a follow-up `ACTION_ITEMS.md` entry to close this
  narrower residual gap with a dedicated pass through that file's test suite.
- **No visual/UI regression tooling applies** — this is a pure backend change with no
  rider-app/driver-app/admin-dashboard surface touched.
- **No staging/production traffic replay** — verification is unit-level only, against
  mocked/fallback Redis, per the sandbox's constraints (no live Supabase/Redis/Stripe
  reachable here).
- **Diff size**: this PR is ~560 changed lines across 17 files, over CLAUDE.md's "~200
  lines, else split" batch-size guideline. It was kept as one commit/PR because the atomic
  primitive and its call-site conversions are one indivisible logical change — landing a
  subset of the call-site conversions without the others (or without the primitive itself)
  would leave some call sites silently still racy while the PR title claims the fix is done,
  which is worse than one reviewable, cohesive commit.
- **`spinr-money-auditor` self-review**: this session's environment did not expose a
  Task/Agent tool capable of invoking the repo's `.claude/agents/` subagents directly, so
  the auditor's own checklist (`.claude/agents/spinr-money-auditor.md`) was applied manually
  against the diff instead of via the Agent tool the task asked for. Findings:
  - Scope check: none of this diff's files match `area:money` (`services/fare_*`,
    `services/corporate_*`, `routes/payments`, `routes/wallet`, `routes/corporate*`,
    `routes/fares`, `routes/tips`, `routes/payouts`, `utils/surge_engine`,
    `utils/payment_retry`) or any of the auditor's "key files" (`fare_service.py`,
    `payments.py`, `wallet.py`, `webhooks.py`, `surge_engine.py`, `payment_retry.py`,
    `stripe_charge.py`, `corporate_wallet_service.py`, `corporate_allowance_service.py`).
    `utils/maps_budget.py` is an internal cost-estimation circuit breaker, not customer
    money — no fare, wallet, Stripe, or receipt path is touched.
  - Grep sweep from the auditor's own "How to audit" §3: `\bfloat\(|\.0\s*\*|\*\s*0\.` finds
    exactly one hit, `float(spent), float(returned_budget)` in `reserve_budget()` — this is
    the same float-typed `(bool, float, float)` return shape `check_budget()` already used
    (pre-existing convention in this module, not fare/payment code under the Decimal rule).
    No `round(` (vs `_round(`), no `claim_stripe_event` (none expected — no webhook code
    touched), no `platform_share` hits, no `logger.warning` on a payment/stripe/fare/wallet
    path.
  - IMPACT MISMATCHES: none — no PR-body money-touching box applies here.
  - VERDICT: SAFE TO MERGE (out of this auditor's scope — no fare/payment/wallet/Stripe
    code touched).

## 11. Follow-up fix (2026-09-13, same PR): `_places_available()` reverted to non-charging

An independent `spinr-security-auditor` review of this PR (run after the above, verified
against a real local Redis instance, not just the fallback path) found a real, previously
undisclosed regression in `backend/ai/tools_booking.py`'s `_places_available()` gate: it had
been converted from a non-charging `check_budget()` read to a charging `reserve_budget("geocode")`
call, intended as a conservative admission charge to close the coarse version of this PR's race
for that one ambiguous, multi-path gate (see the function's original docstring). In practice this
double-counted spend whenever the flow went on to make a real geocode call (charged once at the
gate, again by the existing downstream `record_call("geocode")`), and phantom-charged when the
flow took a different path (e.g. `text_search_new`) that never touched the geocode SKU at all.

**Verified independently, not just taken on the reviewer's word**: read `_places_available()`
and its 4 call sites (`find_place`, `get_rider_location`, and two more), confirmed the double/
phantom-charge pattern is real for this file's actual control flow.

**Fix**: reverted `_places_available()` to a non-charging `check_budget()` read (the exact
function this file's other individual call sites already use for their own `record_call()`-based
bookkeeping) rather than the alternative of stripping the now-redundant downstream `record_call()`
calls — the revert is strictly simpler, and per §3's own reasoning above, this file's coarse
admission race was already accepted as an out-of-scope residual gap for this PR (the "finer-grained,
multiple-calls-per-admission case" the docstring already named), so reverting gives up no
guarantee this PR actually established, while removing a real, verified accounting defect that
would otherwise ship silently.

**Files touched by this follow-up**: `backend/ai/tools_booking.py` (`_places_available()` body
+ docstring, both dual-import branches' `from ...maps_budget import ...` lines — `reserve_budget`
dropped since it's no longer used anywhere in this file, `check_budget` added), `backend/tests/test_ai_tools_booking.py`
(`_patch_budget()` helper now patches `check_budget` instead of `reserve_budget` — this one shared
helper gates ~37 test cases in this file, all still passing).

**Verification**: `ruff check` + `ruff format --check` clean on both files;
`pytest tests/test_ai_tools_booking.py tests/test_maps_budget.py` — 121 passed, 0 failed.

**What this follow-up does NOT change**: the 6 "clean" 1:1 call-site conversions (`maps_proxy.py`,
`_shared.py`, `route_distance.py`×2, `address_verification.py`, `maps_eta.py`×2) are untouched —
the reviewer confirmed those have no double-counting issue, only this one ambiguous multi-path
gate did.
