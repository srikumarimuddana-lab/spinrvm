# Change Impact & Risk — reconciler for card holds already orphaned (T2c)

**Date:** 2026-07-30 · **Branch:** `claude/critical-security-pipeda-breach-pn67ww`
**Surface:** backend (new background loop + operator script, money path) · **Risk:** high — autonomously cancels Stripe authorizations
**Related:** T2b (`b413371d`), extraction (`1f10c1ce`), `migrations/156_ride_preauth_columns.sql`

> **The money has not been returned yet.** This change builds and tests the
> reconciliation; it could not be *run*, because this sandbox has no Supabase or
> Stripe credentials. See "Running it" below — it is one command.

---

## Issue / gap identified

T2b stopped the stuck-ride sweeper from orphaning new card holds. It returned nothing
to riders already affected, and it left one hole open:

1. **Historical backlog.** Every ride the sweeper auto-cancelled before T2b left
   `grand_total + RIDE_AUTH_BUFFER_CAD` reserved on the rider's card until Stripe
   expired the authorization (~7 days). Those rows are still in the table with
   `status='cancelled'` and `auth_status` in `('authorized','fare_only')`.
2. **Ongoing release failures.** `release_open_hold` can return `failed` — Stripe
   down, call timed out, pod died between the cancel and the bookkeeping write.
   **Nothing retried it.** Neither the sweeper nor the interactive cancel path has a
   retry, so a single transient Stripe error turned a 5-minute inconvenience into a
   permanent week-long hold on a real card, silently.

The second point is why this is a recurring loop and not the one-off script the task
originally called for. A script clears the backlog once; only a loop closes the
failure path T2b introduced.

## Root cause

The hold lifecycle had a release path but no *reconciliation* path. Every other money
flow in this codebase has one — `utils/preauth_capture` for uncaptured holds on
completed rides, `utils/stripe_reconcile` for stuck-processing rides,
`utils/payment_retry` for failed settlements. Cancelled-with-open-hold was the one
state with no sweeper watching it, which is exactly why T2b's leak could persist
undetected.

## Fix / remediation

`utils/orphaned_hold_reconciler.py` — a 15-minute loop, registered as the 17th
startup loop in `core/lifespan.py`, plus `backend/scripts/reconcile_orphaned_holds.py`
for an immediate operator-run pass.

**Deliberately conservative about what it will touch.** It only considers
`status='cancelled'` — a terminal, non-billable state:

> `'completed'` is excluded on purpose. A completed ride's open hold is money **owed
> to the driver**, and it belongs to `utils/preauth_capture`, which *captures* it.
> Releasing those would refund fares for rides that actually happened, and on a
> 0%-commission product the driver eats that loss directly. This is the expensive
> mistake this loop could make, so it is the mutation tested first.

**Replay-safety** (CLAUDE.md background-loop contract; runs on every replica):

| Layer | Role |
|---|---|
| Redis leader lock (`SET NX`) | Best-effort throttle. **Not** the safety net — `utils/redis_client` falls back to an in-process dict when `REDIS_URL` is unset, in which case every replica runs. |
| `updated_at` compare-and-swap | The real guard. Only the replica whose UPDATE returns a row acts, and it holds with Redis down. |
| Stripe idempotency key | `ride-cancelauth-{ride}-{pi}` — even if two replicas won a claim race, Stripe dedupes. |

`updated_at` is the version column because migration 156 pins `auth_status` to four
values with a CHECK constraint, so there is no intermediate "claiming" state to flip
to without a schema change. A crash between claim and release is safe: `auth_status`
is still open, so the next tick re-finds the ride.

## Running it

```bash
# 1. Size the backlog. Reads only — no Stripe calls, no writes.
python backend/scripts/reconcile_orphaned_holds.py

# 2. Release them.
python backend/scripts/reconcile_orphaned_holds.py --apply

# Large backlog, bounded batches of 50:
python backend/scripts/reconcile_orphaned_holds.py --apply --all
```

Needs `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY`. **Stripe keys are not read from
the environment** — they live in the `app_settings` table, which
`cancel_authorization` resolves per ride. So pointing this at a database points it at
the matching Stripe account: aiming it at production *is* moving production money,
with no second switch to forget.

Dry run is the default. Safe to run while the background loop is also running (the
CAS handles it) and safe to re-run (Stripe idempotency).

## Risk & impact on existing functionality

**This is the highest-risk change in this branch.** It cancels Stripe authorizations
autonomously, on a schedule, with no human in the loop. Mitigations are the state
filter, the CAS, and the outcome accounting — but the honest summary is that a bug in
`_TERMINAL_STATES` or in `has_open_hold` would move real money incorrectly.

- **Blast radius by table**: reads and writes `rides` (`auth_status`, `updated_at`
  only). Grepped every other `auth_status` writer: `utils/preauth_capture`
  (`captured`), `utils/card_hold_release` (`released`), `routes/rides/cancellation.py`
  (`released`), `services/payment_service`. Writing `released` moves a row *out* of
  the open-hold partial index, which is what all of those readers want for a
  cancelled ride. Before this change those rows sat in that index permanently while
  being uncapturable (wrong `status`), so this also removes standing junk from it.
- **Interaction with `preauth_capture`**: disjoint by construction — it filters
  `status='completed'`, this filters `status='cancelled'`. A ride cannot be both.
  There is a test asserting the query, because a widening edit here is the failure
  that costs a driver a fare.
- **Interaction with the T2b sweeper**: both now call the same
  `release_open_hold`, so the semantics cannot drift. The sweeper handles the ride it
  just cancelled; this handles anything the sweeper missed or failed on.
- **Load**: one indexed query per 15 minutes, bounded to 50 rows, covered by
  `idx_rides_open_authorization`. Once the backlog drains, steady state is a query
  returning zero rows.
- **17th startup loop.** More startup surface, and `core/lifespan.py`'s existing
  per-loop try/except means a failed import degrades to a logged error rather than a
  boot failure.

## User experience effect

**Rider-facing, strictly an improvement, and retroactive.** Riders whose money has
been reserved since before T2b get it back on the first run. No screen, copy, or API
change — a pending authorization disappears, which is a bank-side event.

No driver-, corporate-admin-, or internal-admin-facing change. **Explicitly no
driver-facing effect**, which is the point of excluding `completed` rides: no fare a
driver has earned is touched.

## Files modified

| File | What changed | Why |
|---|---|---|
| `backend/utils/orphaned_hold_reconciler.py` | New — finder, CAS claim, tick, loop | Drain the backlog and heal ongoing failures |
| `backend/core/lifespan.py` | Registered the loop (17th) | Continuous healing, not a one-shot |
| `backend/scripts/reconcile_orphaned_holds.py` | New — operator CLI, dry-run default | Clear the backlog now, before a deploy |
| `backend/tests/test_orphaned_hold_reconciler.py` | New — 18 tests | Pin the filters, the CAS, dry run, failure handling, wiring |
| `docs/change-log/2026-07-30-orphaned-card-hold-reconciler.md` | New — this file | Required by CLAUDE.md |

## Rollback plan

`git revert` stops future reconciliation and is safe for the code. **It does not and
should not undo releases already performed** — cancelling an authorization returns the
rider's own funds and creates no charge, no refund, and no Stripe fee, so there is
nothing to reverse and no reason to.

To stop it *without* a deploy: the loop is spawned in `core/lifespan.py` behind a
try/except, so there is no runtime flag. The operational lever is a restart with the
import removed, or — faster — revoke nothing and simply accept it, since the only
action it takes is releasing money on cancelled rides. A dedicated `app_settings`
flag was considered and judged unnecessary for a loop that can only ever *return*
money; if one is wanted later, `reconcile_tick` is the single call site to gate.

Rows already marked `auth_status='released'` are correct — the corresponding Stripe
intents really are cancelled — and must not be reverted.

## Verification performed

- **18 new tests**, all passing. Weighted toward the *negative* cases, because the
  expensive failure is releasing a hold that was owed, not missing one.
- **Mutation-verified — six mutations, all caught:**

  | Mutation | Failing tests |
  |---|---:|
  | Widen `_TERMINAL_STATES` to include `completed` (would refund earned fares) | 1 |
  | Drop the CAS claim entirely | 3 |
  | Drop `updated_at` from the claim filter (CAS becomes unconditional) | 1 |
  | Make `dry_run` still cancel | 2 |
  | Skip the `has_open_hold` filter | 4 |
  | Unregister the loop from `lifespan.py` | 1 |

- **The operator script was actually executed**, both paths, against a stubbed DB —
  not just argument-parsed:
  - dry run → found 2, **0 Stripe calls, 0 DB writes**, exit 0
  - `--apply` → found 2, released `['pi_a','pi_b']`, exit 0
  - no credentials → exit 2 with an explicit message
  - `--all` without `--apply` → refused
- **PIPEDA**: a test plants `rider_phone` and `rider_name` on a candidate row and
  asserts neither appears in the dry-run log output, while the `ride_id` does (it is
  how this gets triaged).
- **API contracts verified against source, not assumed**: `redis_set_nx(key, value,
  ttl)`, `get_rows(table, filters, order=, limit=, columns=)`, and that
  `update_one` returns `None` on a zero-row match (`_single_row_from_res`) — which is
  what makes the CAS sound. Also confirmed `update_one` accepts both a bare update
  dict and a `{"$set": …}` wrapper (`update.get("$set", update)`), which is why T2b's
  bare-dict call was already correct.
- **Full suite:** `pytest -m "not slow"` → **5906 passed, 8 skipped, 1 xfailed,
  1 failed** (5888 before; +18). The failure is the same pre-existing
  `test_compliance_reports.py` timestamp mismatch, proven unrelated in the T1 log.
- **Lint:** `ruff check` clean on all four changed files.

## What was NOT verified

- **THE RECONCILIATION HAS NOT BEEN RUN.** No Supabase or Stripe credentials exist in
  this sandbox (`SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `PG_CONNECTION_STRING`,
  `DATABASE_URL`, `STRIPE_SECRET_KEY` all unset; no `backend/.env`). **The backlog
  size is therefore unknown, and no rider has had money returned.** Running the dry
  run is the immediate next step and it is read-only.
- **Never exercised against real Stripe.** `cancel_authorization` is mocked in every
  test and in both script runs. Its behaviour on a genuinely stale authorization —
  one Stripe already expired days ago — is **assumed to return an error that surfaces
  as `failed`**, not observed. That case is expected to be *common* in the historical
  backlog (holds older than 7 days are already gone), so a large `failed` count on
  the first run is probably benign expiry rather than a fault. **The dry run will not
  distinguish them**; only the first `--apply` will, and the counts should be read
  with that in mind. This is the single biggest unknown here.
- **No production-like data test.** The stub rows were hand-written; there is no
  fixture of real cancelled-with-open-hold rides, so the query's selectivity and the
  real backlog shape are unverified.
- **The `--all` drain loop's convergence is reasoned, not observed.** It stops when a
  pass releases nothing, with a 200-pass backstop. Against real data where most
  releases *fail* due to expiry, it will stop after the first pass by that rule —
  intentional (it avoids spinning on unfixable rows) but it means `--all` may not
  actually drain everything, and the operator should re-check with a dry run
  afterwards.
- **No load measurement.** The 15-minute indexed query on a bounded batch was judged
  negligible against a hot `rides` table rather than measured.
- **No production build applies** — backend-only.
- **CI has not run any of this.** Local `pytest` and `ruff` only.
