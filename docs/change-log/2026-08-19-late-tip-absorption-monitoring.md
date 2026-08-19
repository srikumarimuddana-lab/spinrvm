# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-19 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | (this branch) |
| Related issue or gap ID | Fare & Payout Audit finding #4 (2026-08-19, published artifact) — "Cap on late-tip absorption exposure" |

## 1. Issue / gap identified

When a late tip on an already-paid wallet or `company_allowance` ride can't be fully collected (insufficient wallet balance, exhausted corporate allowance), the platform silently absorbs the shortfall and still credits the driver in full — a deliberate, signed-off product decision (2026-08-17). Per-event exposure is bounded by `TipRequest`'s $500 cap, but there was no limit or visibility on how many absorption events a single rider could accumulate — the aggregate exposure was completely unmonitored.

## 2. Root cause

Not a bug — a deliberate policy with no accompanying observability. `charge_late_wallet_tip`/`charge_late_corporate_tip` return the amount actually collected, and the caller (`routes/rides/payments.py`) already logs each individual shortfall at `info` level, but nothing tracked or alerted on the *cumulative* absorbed amount per rider over time.

## 3. Fix / remediation

Explicitly scoped as **monitoring only, not a hard cap** — confirmed with the product owner before implementing (see the audit's decision log: "Still absorb + alert" was the chosen option over "stop absorbing past the cap"). The tip-never-errors policy is completely unchanged; no rider will ever see a rejected or reduced tip because of this change.

Added `_record_late_tip_absorption()`: on every partial/zero-collection outcome, increments a Redis counter (cents) keyed per rider, in a 30-day rolling window (mirrors `routes/auth.py`'s existing `_record_otp_failure` pattern — increment, set expiry only on the first write in a fresh window). Once a rider's cumulative absorbed amount in the window crosses $50 CAD (a tunable monitoring threshold, not a business rule), it logs at `logger.error` — which this repo's existing convention routes to Sentry (see the OTP lockout comment this pattern mirrors) — and increments a new metric, `spinr_payment_late_tip_absorption_alert_total`. Entirely best-effort: any Redis failure is caught and logged, never raised, so a monitoring outage can never block or fail a tip.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to a new code path in `routes/rides/payments.py`'s `add_tip`.** No existing function's signature, return value, or behavior changed — the new call is purely additive, invoked only inside the existing `outcome != "success"` branch that was already there.
- Uses the shared Redis client (`utils/redis_client.py`) with its existing in-process-dict fallback when `REDIS_URL` is unset — same tradeoff as OTP lockout state (lost on restart in that mode), acceptable for a monitoring signal.
- No new table, no migration, no schema change — a Redis key is not durable state the rest of the system depends on.
- Does not touch `charge_late_wallet_tip`, `charge_late_corporate_tip`, `wallet_apply_delta`, or `corporate_allowance_apply_delta` — none of the actual money-movement functions were modified.

## 5. User-experience effect

None. No rider or driver ever sees any difference — the alert is purely internal (a log line + Sentry event + metric), never surfaced in any API response, push notification, or receipt.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/rides/payments.py` | Added `_record_late_tip_absorption()` helper and its 3 module constants (Redis key template, window, alert threshold); wired one call into the existing `outcome != "success"` branch of `add_tip` | Track cumulative per-rider absorption and alert past a threshold, without changing the tip's own outcome |
| `backend/tests/test_rides_payments_coverage.py` | Added 4 tests: threshold-crossing alert fires, first-event sets window expiry, Redis failure never blocks the tip, a fully-collected tip never even attempts the monitoring write | Cover every new branch |

## 7. Before / after

```python
# Before
_metric_inc("spinr_payment_late_tip_total", {"outcome": outcome})
if outcome != "success":
    logger.info(f"... absorbing the rest per product decision ...")
```

```python
# After
_metric_inc("spinr_payment_late_tip_total", {"outcome": outcome})
if outcome != "success":
    absorbed_this_event = _round(tip_amount - collected)
    logger.info(f"... absorbing the rest per product decision ...")
    await _record_late_tip_absorption(current_user["id"], ride_id, absorbed_this_event)
```

## 8. Rollback plan

`git-revert-safe`. No migration, no schema, no durable state beyond a Redis counter with a 30-day TTL that expires on its own even without a revert. Reverting simply stops the tracking/alerting; it has zero effect on any tip, wallet balance, corporate allowance, or driver payout, since none of those write paths were touched.

## 9. Verification performed

- [x] Automated tests run: `test_rides_payments_coverage.py`, `test_charge_late_tip.py`, `test_charge_late_corporate_tip.py`, `test_charge_late_wallet_tip.py`, `test_rides_extended.py`, `test_coverage_rides.py` — 263 passed, 0 failures, 0 regressions.
- [x] 4 new regression tests specifically cover: threshold-crossing alert, first-event window-expiry set, Redis-failure-never-blocks-the-tip, and fully-collected-tip-skips-monitoring-entirely.
- [x] `ruff check` on both changed files — clean.
- [x] Reviewed against `CLAUDE.md`'s "Do not silently swallow errors" convention: the Redis-failure catch here is a deliberate, explicit exception — this is a *monitoring* side-effect, not a DB/auth/payment operation the convention is protecting; logged loudly (`logger.error` with `exc_info=True`) rather than silently dropped.
- [ ] Not run against a real Redis instance or Sentry — mocked/unit-tested only, consistent with this repo's existing test conventions for Redis-backed features (see `test_otp_lockout`-style tests).

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no migration, no durable state beyond a self-expiring Redis key)
- [x] Blast radius is stated: isolated to one new helper and one call site, no existing function's contract changed
- [x] No silent behavior change to an already-shipped flow — the tip-never-errors policy is explicitly unchanged; this is pure additive observability, confirmed with the product owner as the chosen option before implementing

## What was NOT verified

- Not exercised against a real Redis instance — relies on the existing, already-tested `utils/redis_client.py` fallback behavior rather than re-verifying it here.
- Not exercised against a real Sentry project — relies on this repo's existing `logger.error` → Sentry routing convention (already used identically by `routes/auth.py`'s OTP lockout), not independently re-confirmed to actually reach Sentry in this session.
- The $50/30-day threshold is a starting value, not a data-driven number — there's no historical absorption data to calibrate against yet (per the existing finding that no native ride has completed a real payment in production). Should be revisited once real absorption volume exists.
