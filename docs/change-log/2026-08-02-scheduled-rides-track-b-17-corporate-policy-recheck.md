# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate, dispatch, payments |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Scheduled Rides gap review — Finding #17 |

## 1. Issue / gap identified

Corporate policy and allowance are evaluated once, at booking time, against
the pickup time — correct at that instant, but never re-checked again. A
scheduled ride can sit for up to 7 days between booking and dispatch; in
that window a company's policy could tighten, its allowance could be
exhausted by other spend, or its account could be suspended (Finding #16
now closes the pure suspension case, but a policy/allowance change short of
suspension was still completely unguarded). The ride would dispatch
unconditionally regardless.

## 2. Root cause

`scheduled_rides.py`'s dispatcher had zero corporate-awareness at all — no
call to `evaluate_policy_for_ride`, no allowance check, nothing. Every
corporate-specific decision was made once, at booking, and never revisited.

## 3. Fix / remediation

New `_corporate_policy_still_allows_dispatch(ride)` in
`backend/utils/scheduled_rides.py`, called at the very start of
`_dispatch_scheduled_ride` — **before** the atomic scheduled→searching
claim, not after:
- No-ops (`True`) immediately for any non-`company_allowance` ride, and for
  a corporate ride missing a `corporate_account_id`.
- Otherwise re-runs `evaluate_policy_for_ride` (the same function
  `booking.py` calls, unmodified) with `pickup_time=now` (dispatch is
  happening right now).
- On pass: proceeds exactly as before.
- On failure: the claim is **never attempted** — the ride stays in
  `scheduled` for a later tick to re-check (mirrors Finding #03's
  "defer, don't destroy" pattern for the active-ride-conflict case). Notify
  once (Redis NX, 24h TTL — this re-checks every tick, so without a dedupe
  guard it would re-notify every ~60s for as long as the policy stays
  failed): a rider push, an admin broadcast (`scheduled_ride_policy_blocked`,
  carrying `failed_rules`), and a new metric
  (`spinr_dispatch_scheduled_corporate_policy_blocked_total`).
- On an evaluation error: **fails open** (dispatches anyway), explicitly
  matching `evaluate_policy_for_ride`'s own documented contract ("never
  raises — a DB failure returns a permissive PolicyResult... a transient
  outage cannot silently block every work ride"). A policy-service hiccup
  must not strand every corporate scheduled ride in the fleet.

Gating **before** the claim (not after) was a deliberate design choice: a
claim-then-unwind approach would need to revert an already-committed
`status='searching'` write on failure, adding a second write and a real
race window. Checking first means a blocked ride is left in exactly the
state `check_scheduled_rides()` already found it in — no partial state,
no unwind logic needed.

## 4. Risk & impact on existing functionality

- **Blast radius: the entry of `_dispatch_scheduled_ride`, and the
  candidate-query columns in `check_scheduled_rides()`.** The SELECT
  gained four columns (`payment_method`, `corporate_account_id`,
  `grand_total`, `total_fare`) needed to evaluate the gate before the claim
  (previously only fetched via the post-claim `claimed` row, too late for
  a pre-claim check). Grepped for other readers of that SELECT's result —
  only the loop body in the same function; no other consumer.
- **Non-corporate rides (the overwhelming majority) are unaffected** — the
  gate returns `True` on its first line for them, adding one cheap
  attribute check per tick, no DB/service call.
- Re-uses `evaluate_policy_for_ride` unmodified — no change to its
  behavior, its own fail-open contract, or the booking-time call sites in
  `booking.py`.
- No interaction with money movement directly — this only decides whether
  dispatch proceeds; the actual allowance debit still happens at
  settlement (unchanged, per the original gap review's B.2 finding that no
  reservation/hold exists for corporate rides at any stage).

## 5. User-experience effect

**Rider-facing (corporate riders only)**: a rider whose scheduled ride is
blocked by a since-changed company policy now gets a push explaining it,
instead of the ride silently dispatching against a policy it would fail if
evaluated fresh — or (the case this actually prevents) instead of it
dispatching at all when it shouldn't. **Internal admin-facing**: a new,
currently-unconsumed WS event type (`scheduled_ride_policy_blocked`) — same
"signal on the wire, no dashboard UI yet" situation already noted for
Finding #03's `scheduled_ride_stuck` broadcast.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/scheduled_rides.py` | New `_corporate_policy_still_allows_dispatch()`; wired in before the atomic claim; SELECT columns widened | Implement the re-check without needing a claim-then-unwind pattern |
| `backend/tests/test_scheduled_dispatch_cr.py` | New `TestCorporatePolicyRecheck`: non-corporate/missing-account no-ops, pass, failure (notify+block), dedupe, fail-open on error, and that the claim is never attempted when blocked | Cover the gate's every branch and its pre-claim placement specifically |

## 7. Before / after

```python
# Before
async def _dispatch_scheduled_ride(ride: dict):
    ...
    try:
        claimed = await db.update_one("rides", {"id": ride_id, "status": "scheduled"}, {...})
    ...
```

```python
# After
async def _dispatch_scheduled_ride(ride: dict):
    ...
    try:
        if not await _corporate_policy_still_allows_dispatch(ride):
            return  # left exactly as check_scheduled_rides() found it
        claimed = await db.update_one("rides", {"id": ride_id, "status": "scheduled"}, {...})
    ...
```

## 8. Rollback plan

Plain code change, no migration, no data written by the gate itself (the
notify/broadcast side effects are non-durable — a Redis dedupe key and a
WS event, neither of which needs unwinding). `git revert` fully restores
prior (unconditional-dispatch) behavior. No feature flag — this closes a
real corporate-authorization gap in an existing dispatch path; the
"blocked" outcome only ever fires when the ride genuinely *shouldn't*
dispatch per the company's own current policy, so there's no meaningful
dark-ship version distinguishable from "policy re-evaluated correctly."

## 9. Verification performed

- [x] Automated tests: `backend/tests/test_scheduled_dispatch_cr.py`
      (32 passed, 25 prior + 7 new) and re-ran
      `backend/tests/test_scheduled_preauth.py` (3 passed, unaffected —
      its ride fixtures have no `payment_method`, so the new gate no-ops
      for them) via the session's venv.
- [x] `ruff check` on both touched files — clean.
- [ ] Manual repro in staging — not performed, no staging access. This
      touches a real corporate-money-adjacent decision path; a staging
      dry run against a real policy/allowance configuration is
      recommended before this reaches production traffic at scale.
- [x] Blast-radius grep performed (see §4).
- [x] Reviewed against CLAUDE.md's corporate-billing and background-loop
      conventions — the pre-claim gate placement specifically avoids the
      "claim then unwind" anti-pattern the review calls out as risky.
- [x] Dry-run scenario (money/state-machine gate): a rider books a
      scheduled ride today against a company allowance with headroom. Three
      days later, the company tightens its `max_fare_per_ride` policy below
      this ride's fare. At dispatch time, `evaluate_policy_for_ride`
      returns `passed=False, failed_rules=["max_fare_per_ride"]` — the
      claim is never attempted, the ride stays `scheduled`, the rider gets
      a push, ops gets a broadcast. If the company later raises the limit
      again (or the rider switches payment method), the very next tick's
      re-check passes and the ride dispatches normally — no manual
      intervention required for the ride to recover on its own.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed — pre-claim gate, one function,
      four widened SELECT columns, non-corporate rides untouched
- [x] No silent behavior change to a working flow — a ride that would have
      passed a fresh policy check dispatches exactly as before; only a ride
      that would now fail is affected, and that's the fix's entire purpose

## What was NOT verified

Not tested against a live/staging Supabase instance or a real corporate
policy/allowance configuration — only a mocked `evaluate_policy_for_ride`.
No admin-dashboard UI was built for the new `scheduled_ride_policy_blocked`
broadcast (same gap already noted for Finding #03's `scheduled_ride_stuck`
event — the value today is in logs/metrics, not a clickable dashboard
surface). Did not verify what happens if a blocked scheduled ride is *also*
past its window for Finding #01's notice-window cancellation fee were it
enabled — the two features weren't tested together, though they touch
different code paths (booking-time-scheduled cancel vs. dispatch-time
policy) and I don't believe they interact.
