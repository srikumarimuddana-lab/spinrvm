# Change Impact & Risk Log — Offer-expiry reaper no longer punishes the winning driver

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-05 |
| Author | Claude Code (agent session) |
| Surface(s) | backend |
| Domain (Sentry tag) | dispatch (secondary: drivers, safety — insurance periods) |
| PR / commit link | branch `claude/pickup-otp-payment-fixes-5a8dnk` |
| Related issue or gap ID | `docs/audit/2026-09-05-engineering-director-review-round3.md` §1.11 finding **D1** (Major) |

## 1. Issue / gap identified

`process_expired_offer` treats its atomic `ride_offers` `pending → expired`
claim as the single gate for every side-effect. On the **batch-offer** path that
claim does not actually establish that the driver missed the offer, because
`accept_ride` CASes the *ride* independently of the offer row and only flips the
offer to `accepted` afterwards, purely to emit a metric.

So a driver can win the ride and still lose the offer claim. The reaper then
ran, against the driver who **did** accept:

- `increment_miss_streak()` + `update_acceptance_rate(accepted=False)`, and
- at the miss threshold, force-offline + `record_period_transition(driver, 0)` —
  **a driver offline and in Insurance Period 0 mid-trip**, which CLAUDE.md calls
  a regulatory and insurance liability, or
- otherwise `set_driver_available(True)`, which never checks for an active ride,
  producing `is_available = True` on a driver already carrying a passenger.

## 2. Root cause

Two CAS gates on two different tables that are not linked:

| | claims | predicate |
|---|---|---|
| `accept_ride` (batch path) | `rides` | `{status: searching, driver_id: None}` |
| `process_expired_offer` | `ride_offers` | `{status: pending}` |

Nothing makes them mutually exclusive. The **single-offer** path is correctly
exclusive — both sides predicate on `{status: driver_assigned, driver_id: <this
driver>}` on the ride — and the reaper's docstring generalises that safety to
"the pending->expired claim is the single gate", which is only true there.

## 3. Fix / remediation

Chose the audit's option (b) — make the reaper re-check the ride — over option
(a), reverting the ride CAS. By the time the offer flip is attempted the driver
has already been told they got the ride; taking it back is worse than a missed
penalty.

After winning the offer claim, `process_expired_offer` re-reads the ride and
stands down (no penalties, returns `False`) when
`rides.driver_id == driver_id` **and** the status is in `_POST_ACCEPT_STATUSES`
(`driver_accepted`, `driver_arrived`, `in_progress`, `completed`). It also
restores the offer row `expired → accepted`, because `accept_ride`'s own flip
matched zero rows and the history would otherwise read "expired" for an offer
that was accepted.

`driver_assigned` is **deliberately excluded** from that set. On the single-offer
path a ride sits in `driver_assigned` with `driver_id` set to this driver while
its offer is still pending, and expiring that offer is the *legitimate* timeout —
keying the guard on `driver_id` alone would disable single-offer timeouts
entirely. This is the sharpest edge in the change and has its own test.

A failed re-read **fails closed** (skip penalties): a missed penalty is
recoverable, a driver offlined mid-trip with a wrong insurance period is not.

## 4. Risk & impact on existing functionality

**Blast radius: single-surface (backend), one function — but that function is on
the dispatch hot path and has two concurrent callers.**

- `process_expired_offer` callers: `_batch_offer_timeout_handler`
  (`matching.py`, in-process, per-batch) and the durable reaper
  (`utils/offer_expiry_reaper.py::_reap_tick`). Both run on every replica; the
  offer claim still makes them mutually exclusive with each other — that
  property is untouched, only *what happens after winning* changed.
- `_POST_ACCEPT_STATUSES` is new, one reader, and uses `RideStatus` members
  verified against `models/ride_status.py`. `RideStatus` is a `str` Enum so the
  membership test works against the raw strings Supabase returns.
- Writers of `is_available` / insurance periods elsewhere are untouched — this
  change only makes the reaper call them *less often*, never more.

Interactions considered:

- **Ride state machine** — no `rides.status` write added or removed.
- **Insurance periods** — strictly fewer rows, and specifically fewer *wrong*
  ones. The append-only rule is respected (nothing deleted or mutated).
- **`is_available ⇒ is_online`** — unaffected; `set_driver_available` remains the
  single writer and is simply not called in the race-lost case.
- **Background loops** — the durable reaper's tick now issues one extra
  `get_ride` per offer it actually claims (not per offer scanned). At the
  `_CANDIDATE_LIMIT` scan cap that is bounded by the number of genuinely expired
  offers, and it is a primary-key lookup.

Regression risks, stated plainly:

1. **One extra DB read per claimed offer** on a path with a < 2 s dispatch SLA.
   It runs after the claim, off the accept path, so it does not sit in the
   rider-visible latency budget — but it is new load on the reaper.
2. **If the status set is wrong, single-offer timeouts break.** Excluding
   `driver_assigned` is what prevents that; if it were ever added, drivers would
   stop being released on timeout and rides would stall in `driver_assigned`.
3. **Fail-closed on read error means a real miss can go unpenalised** during a DB
   incident. Accepted deliberately, per §3.
4. **The race is narrowed, not closed.** Self-review finding: this is a read
   *after* the offer claim, not part of it, so an ordering remains where the
   reaper claims the offer, reads the ride as still `searching`, and only then
   does `accept_ride`'s CAS land — the winner is still penalised in that
   interleaving. Closing it fully requires the offer claim and the ride CAS to
   be one atomic unit (a Postgres function, the way
   `corporate_wallet_apply_delta` does it), which is a schema change and its own
   piece of work. What the guard buys is the common case: the reaper only runs
   at/after the 15 s timeout, while the accept racing it arrived just before, so
   the CAS has normally already landed by the time the reaper reads. **This is
   a genuine remaining hole, not a theoretical one — it is just much smaller.**

## 5. User-experience effect

- **Driver (visible, mid-session):** a driver who wins a batch offer no longer
  risks being knocked offline, having their acceptance rate dinged, or being
  shown as available while driving a passenger. Previously this could happen on
  a ride they had just started.
- **Rider:** indirectly better — a driver force-offlined mid-trip is a stuck
  ride. No direct UI change.
- **Internal admin:** `ride_offers` rows for this race now read `accepted`
  rather than `expired`, so offer history matches what happened. Acceptance-rate
  figures stop being polluted by wins recorded as misses.
- No copy changes, no new notification.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/rides/matching.py` | Adds `_POST_ACCEPT_STATUSES`; `process_expired_offer` re-reads the ride after winning the offer claim, stands down and restores the offer row when this driver already holds the ride, and fails closed on a read error | Stop the reaper penalising / offlining / mis-classifying the driver who won |
| `backend/tests/test_offer_expiry_accept_race.py` | New | Pins the stand-down, the mid-trip offline case, the offer restore, fail-closed, and every still-penalised case |
| `backend/tests/test_offer_timeout.py` | Pins `get_ride` in the four existing `process_expired_offer` tests that reach the new read | The function gained a dependency; without this those tests would depend on whatever the unpatched client returns |

## 7. Before / after

```python
# Before — routes/rides/matching.py::process_expired_offer
claimed = ...update ride_offers SET status='expired' WHERE status='pending'...
if not (getattr(claimed, "data", None) or []):
    return False

miss_count = await increment_miss_streak(driver_id)      # <- runs on the winner
await update_acceptance_rate(driver_id, accepted=False)
...auto-offline + Period 0, or set_driver_available(True) + Period 1...
```

```python
# After
if not (getattr(claimed, "data", None) or []):
    return False

try:
    _ride_now = await _deps.db_supabase.get_ride(ride_id)
except Exception:
    logger.opt(exception=True).error(...)      # fail closed
    return False

if _ride_now and _ride_now.get("driver_id") == driver_id \
        and _ride_now.get("status") in _POST_ACCEPT_STATUSES:
    ...restore the offer row to "accepted"...
    return False                                # no penalties

miss_count = await increment_miss_streak(driver_id)
```

Concrete scenario (gate 4 dry run) — batch offer to 3 drivers, 15 s timeout.
Driver B accepts at t=14.9 s; the reaper's tick lands at t=15.0 s and wins B's
offer row before `accept_ride` flips it.

| | Before | After |
|---|---|---|
| `rides.driver_id` | B (accepted) | B (accepted) |
| `ride_offers` row for B | `expired` | `accepted` (restored) |
| B's miss streak | **+1** | unchanged |
| B's acceptance rate | **counted as a miss** | unchanged |
| At threshold: B `is_online` | **False, mid-trip** | unchanged (online) |
| B's insurance period | **0 — with a passenger aboard** | stays 2/3 |

## 8. Rollback plan

No migration, no schema change, no live-data mutation beyond the offer-row
restore (which only ever moves a row from `expired` to `accepted` for a ride the
driver demonstrably holds). A `git revert` is a complete rollback.

No feature flag: the change strictly *removes* incorrect writes, and a flag
whose "off" position re-enables offlining drivers mid-trip and writing a false
Period 0 is not a state worth being able to reach.

Insurance-period rows already written wrongly by this bug are **not** corrected
here — `driver_insurance_periods` is append-only by regulatory rule, so the
remediation is a compensating entry plus a note in the audit trail, not a
delete. Affected rows are findable as `period = 0` transitions for a driver
whose ride at that timestamp was in a post-acceptance state. **That
reconciliation is not performed here and is left for a human**, and it matters
more than usual because these rows are the 7-year commercial-insurance audit
record.

## 9. Verification performed

- [x] Blast-radius grep performed — `process_expired_offer` callers (in-process
      batch handler + durable reaper), `set_driver_available` writers,
      `record_period_transition` call sites, `RideStatus` members verified
      against `models/ride_status.py`.
- [x] Confirmed `matching.py` uses **loguru**, so the new log lines use `{}`
      f-strings and `logger.opt(exception=True)` rather than `%s` / `exc_info=`
      (CLAUDE.md; `test_loguru_call_conventions.py` fails the suite otherwise).
- [x] Single-offer non-regression reasoned through explicitly and given its own
      test (`driver_assigned` excluded from the post-accept set).
- [x] Before/after dispatch scenario written out above (gate 4).
- [x] `ruff check` and `ruff format --check` clean.
- [ ] **Automated tests NOT run** — see below.

## What was NOT verified

**No tests were executed.** PyPI is blocked by this environment's network policy
(403), so backend dependencies could not be installed and `pytest` could not run.
`backend/tests/test_offer_expiry_accept_race.py` is written but **has never been
run**.

More importantly, **the four edits to `test_offer_timeout.py` are unverified**.
Those tests already passed before this change; the added `get_ride` patches are
required because the function under test gained a dependency, but whether each
patch landed in the right `with` block — and whether the two tests I judged not
to need one (`ride_c`, which returns before the new read on a lost claim) are
right — was determined by reading, not by running. **If anything in this change
breaks the existing suite, that file is where it will show.**

Also not verified: the race itself was never reproduced — no concurrent
accept-vs-reaper run was performed against a real database, so the ordering
described in §7 is derived from reading both code paths, not observed. The
offer-row restore was not exercised against a real PostgREST update, and no
staging or load-test run was done to measure the extra `get_ride` against the
< 2 s dispatch SLA.
