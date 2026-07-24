# Critical Bugs — Implementation Plan

Status: **all 17 findings verified against the codebase on this branch** (2026-07-24).
Every finding below was re-checked at the cited file/line before this plan was written; none
are false positives. Findings are regrouped into workstreams because several share one root
cause (non-atomic wallet read-modify-write) and one shared fix.

Scope: C3 (insurance-period atomicity) plus findings 1–16 from the critical-bug sweep.

---

## Verification summary

| # | Finding | File | Verified |
|---|---------|------|----------|
| C3 | Insurance-period transition non-atomic, failure swallowed | `backend/utils/insurance_periods.py:113-196` | ✅ close→insert is two `run_sync` calls; outer `except` at `:182` swallows everything |
| 1 | Circuit-breaker half-open probe leaks on deadline abort | `backend/repositories/_base.py:87-125, 208-241` | ✅ deadline paths raise `ServiceUnavailableException`, re-raised at `:237` bypassing breaker bookkeeping; `_probe_in_flight` never cleared |
| 2 | Admin wallet credit: lost-update race | `backend/routes/admin/wallet.py:144-157` | ✅ RMW with `{"id": wallet["id"]}` filter only |
| 3 | Admin wallet debit: same race | `backend/routes/admin/wallet.py:232` | ✅ identical pattern |
| 4 | Payout TOCTOU + no compensation after Stripe transfer | `backend/routes/drivers/payouts.py:590-656` | ✅ balance read → transfer → insert, no reserve/reversal (instant path at `:808` has one) |
| 5/6 | Driver cancel: no ownership check (IDOR) | `backend/routes/drivers/ride_cancel.py:57-91` | ✅ no `driver_id` guard or filter; sibling `mark_rider_noshow` has the check |
| 7 | Wrong-driver cancel corrupts insurance trail | `backend/routes/drivers/ride_cancel.py:132` | ✅ records period 1 for the *caller*, closes nothing for the real driver |
| 8 | No-show fee debit: lost-update race | `backend/routes/drivers/ride_cancel.py:265` | ✅ same RMW pattern |
| 9 | Rider object leaks `password_hash`/`fcm_token` to driver | `backend/routes/drivers/ride_reads.py:149` | ✅ 3-field blocklist over `select("*")` |
| 10 | Cancellation-fee debit: lost-update race | `backend/routes/rides/cancellation.py:136` | ✅ same RMW pattern |
| 11 | Booking pre-auth hold never released on cancel; PI overwritten | `backend/routes/rides/cancellation.py:264-268` | ✅ `cancel_authorization`'s only production caller is `payment_service.py:840` (Change-Card path) |
| 12 | `/rate` credits unbounded uncharged tips repeatedly | `backend/routes/rides/rating.py:64-87` | ✅ no dedupe, no idempotency, no `le=` cap, no payment-status check |
| 13 | Mid-trip stop edit never recomputes `grand_total`/tax | `backend/routes/rides/stops.py` | ✅ zero references to `grand_total`/`tax_amount` in the file |
| 14 | `notify_safety_team` missing from fallback import branch | `backend/routes/safety.py:19-27` | ✅ except-branch imports omit it; top-level import mode is what production runs |
| 15 | Unverified email trusted for corporate join-domain | `backend/routes/users.py` | ✅ no verification/`email_verified` anywhere in the file |
| 16 | Allowance settlement CREDITS the corporate master wallet | `backend/services/payment_service.py:419` + `migrations/29_corporate_allowance_rpc.sql:76` | ✅ `apply_rollback` → `v_master_delta := p_amount` (positive). **Active financial loss on every allowance-covered corporate ride.** |

### C3 — "did we cover this as well?"

**Partially, and the remaining gap is exactly the one the finding describes.** What exists today:

- `utils/stale_intent_reconciler.py` (15-min loop, registered in `core/lifespan.py:301-309`)
  closes the *offline* gap: a force-killed app leaves a Period 1 row open forever, and this
  loop flips the driver offline and closes the period. **But it explicitly skips drivers
  linked to an active ride** ("Active-ride guard" in its docstring) — by design, so it can
  never write a Period 0 row mid-trip.
- The partial unique index on open periods serialises racing *inserts*, and the code handles
  the 23505 case correctly.

What does **not** exist:

- Atomicity between close (step 1) and insert (step 2). If the insert fails with anything
  other than a unique violation — network blip, deadline exhaustion, DB restart — the outer
  `except Exception` at `insurance_periods.py:182` logs and swallows, leaving the driver with
  their previous period **closed and no open period** while demonstrably on a trip.
- Any reconciler that rebuilds expected open periods **from active rides**. The comment at
  `:184` ("Operations can backfill from the rides table if needed") describes a manual
  process that has no code behind it. The stale-intent reconciler cannot cover this because
  of its active-ride guard.

So: C3 is **not covered** by existing work and stays in this plan as WS-7.

---

## Systemic themes and patterns

Many findings share a root cause. Addressing these patterns centrally will clear large
batches of individual items and prevent recurrence — point-fixing each finding without the
central fix guarantees the next sweep finds the same classes again. Counts are the number
of findings (across the full audit, not just the 17 critical ones above) touching each
pattern.

| Pattern / root cause | Findings | Central fix | Lands in |
|---|---|---|---|
| Swallowed / fail-open DB or auth errors (warn-and-continue) | 57 | Replace warn-and-continue with `logger.error` + 5xx; never fail open. | **WS-13** (new) — sweep + CI guard; WS-3 fixes the worst instance |
| Receipts / tax lines do not reconcile (missing discount, wrong GST/PST) | 26 | Recompute tax at settlement; emit a discount line everywhere. | **WS-14** (new) — settlement-side recompute; WS-10 fixes the stops instance |
| Filter/paginate applied after fetch; silent row caps | 16 | Push filters into the DB query; alert when a row cap is hit. | **WS-15** (new) |
| Stripe idempotency key omits amount / missing idempotency | 15 | Include amount in every Stripe idempotency key. | **WS-16** (new) — key-builder helper; WS-9 fixes the `/rate` instance |
| Raw GPS / PII risk in logs, analytics, or third parties | 15 | Central PII scrubber for logs/analytics; proxy 3rd-party calls. | **WS-17** (new); WS-2 fixes the driver-app leak instance |
| Missing ownership check / IDOR on id-taking endpoints | 13 | Add an ownership guard helper and apply on all id-taking endpoints. | **WS-18** (new) — `require_ride_owner()` helper; WS-1 fixes the cancel instance |
| Missing admin audit-log on money/config actions | 13 | Wrap admin mutations in a shared `log_admin_action` decorator. | **WS-19** (new) |
| Background-loop replay hazard (flag/notify after side-effect, lock TTL) | 12 | Claim-before-side-effect; lock TTL below tick interval. | **WS-20** (new) — audit all 16 loops against the Background Loop Recipe |
| Non-atomic wallet / balance writes (read-modify-write, no row lock) | 9 | Route every balance change through one row-locking Postgres function. | **WS-6** (already planned — `wallet_apply_delta` RPC + CI grep guard) |
| Insurance-period ledger not recorded / misclassified | 6 | Record period transition on every driver state change. | **WS-12** (already planned — atomic RPC + ride-based reconciler makes every miss self-healing) |

Two of the ten patterns (wallet atomicity, insurance ledger) are already fully owned by
Phase 1/3 workstreams above — their central fixes were designed as pattern-killers, not
point fixes. The remaining eight get dedicated hardening workstreams:

**WS-13: Fail-closed error handling (57 findings)** — the largest pattern in the audit.
1. Sweep: `grep -rn "logger.warning" backend/` filtered to DB/auth/payment/dispatch call
   sites; classify each as (a) genuine recoverable anomaly → keep, or (b) swallowed
   critical error → convert to `logger.error(..., exc_info=True)` + `HTTPException`
   (503 DB / 502 upstream) per the CLAUDE.md "do not silently swallow errors" rules.
   For `DatabaseError`, include `e.details["original"]`.
2. CI guard: pre-commit check flagging `except ... logger.warning` (no re-raise) in
   `routes/`, `services/`, `repositories/`, `db_supabase.py` — new instances need an
   explicit `# fail-open-reviewed:` annotation with a reason to pass.
3. Batch the conversions ~10 call sites per commit (batch-size rule), highest-blast-radius
   first: auth → payments → dispatch → rest.

**WS-14: Receipt/tax reconciliation at settlement (26 findings)**
1. Single source of truth: settlement (`payment_service`) recomputes GST/PST and
   `grand_total` from the final fare components at charge time — never trusts a
   booking-time snapshot that mid-trip edits (stops, waypoints, promo) may have invalidated.
   WS-10's stop-edit recompute becomes redundant defense rather than the only line.
2. Discount/promo must appear as its own signed line item on every receipt (the "every
   charge maps to a disclosed line item" product rule); add an invariant check
   `sum(line_items) == grand_total == amount_charged` that logs at `error` + metric
   `spinr_receipt_reconcile_failed_total` on mismatch.
3. Golden-file receipt tests per fare branch: base/distance/time, surge, promo, corporate,
   GST-only vs GST+PST.

**WS-15: DB-side filtering and pagination (16 findings)**
1. Sweep for post-fetch filtering (`[r for r in rows if ...]` after `get_rows`) and
   unpaginated list endpoints; push predicates into the Supabase query (`.eq/.in_/.gte`)
   and add `limit/offset` (or keyset) params to list endpoints.
2. Every place a hard row cap exists (e.g. `limit=1000` defaults), emit
   `spinr_db_row_cap_hit_total{site=...}` when the result size equals the cap — a silent
   cap is a correctness bug waiting for scale (dashboards showing partial money totals).

**WS-16: Stripe idempotency key discipline (15 findings)**
1. Shared helper `stripe_idempotency_key(scope, entity_id, amount_cents, attempt_salt)` —
   including the amount means a retried request with a *different* amount can never silently
   reuse a stale key and charge the old amount (Stripe returns the first call's result for
   a reused key, even if params changed — it errors, but only sometimes, and the error is
   the good case).
2. Sweep every `stripe.` call site: no `idempotency_key` → add one; key without amount →
   rebuild via the helper. One commit per payment surface (charges, transfers, refunds,
   holds).
3. Tests: same scope+entity, changed amount → different key; genuine retry → same key.

**WS-17: Central PII scrubbing (15 findings)**
1. `utils/pii_scrubber.py`: a logging filter + Sentry `before_send` hook that redacts the
   CLAUDE.md never-log list (raw lat/lng → geohash, phone → last-4, email/names → user_id,
   PANs, addresses). Install on the root logger and the Sentry init so every existing and
   future log line passes through it — the sweep then removes sources, the scrubber
   guarantees the floor.
2. Third-party/analytics payloads go through the same scrub function before dispatch;
   any direct third-party SDK call that ships device/user data gets proxied through the
   backend so the scrubber sits in the path.
3. Tests: scrubber property tests (every never-log pattern in, redacted out); CI grep guard
   for `latitude`/`longitude`/`phone` inside `logger.*(` calls.

**WS-18: Ownership guard helper (13 findings)**
1. `dependencies.require_ride_party(ride, user, role)` (and sibling for wallets/documents):
   one helper that 403s unless the caller is the ride's rider / assigned driver, used both
   as the in-memory check *and* by contributing the `driver_id`/`rider_id` predicate to the
   atomic update filter (the WS-1 pattern, generalized).
2. Sweep every route taking an entity id in the path (`{ride_id}`, `{wallet_id}`,
   `{document_id}`, ...): apply the helper. Table the exceptions (admin routes) explicitly.
3. Tests: parametrized IDOR test hitting each id-taking endpoint as a non-owner → 403/404.

**WS-19: Admin audit-log decorator (13 findings)**
1. `@log_admin_action(action, entity_type)` decorator that writes the `audit_logs` row
   (actor, role, entity, before/after summary, reason) in the same request — applied to
   every mutating `routes/admin/*` endpoint. The manual `insert_one("audit_logs", ...)`
   blocks (e.g. `admin/wallet.py:170`) migrate into it so coverage can't drift.
2. Audit-write failure = request failure for money/config actions (fail closed — an
   unauditable admin money action must not proceed silently); log at `error`.
3. CI guard: admin route files with a mutating verb and no decorator fail the check.

**WS-20: Background-loop replay audit (12 findings)**
1. Audit each of the 16 loops in `core/lifespan.py` against the Background Loop Recipe:
   claim-before-side-effect ordering, idempotency key coverage, and every Redis leader
   lock's TTL strictly below the tick interval (a TTL ≥ interval means an expired-then-
   reacquired lock can run two leaders in the same tick).
2. Fix ordering violations (flag/notify written *after* the side-effect → move the atomic
   claim first); one loop per commit.
3. Add a shared `leader_lock(name, ttl)` helper that asserts `ttl < interval` at
   registration time, so the invariant is structural.

Phase placement: WS-13 (fail-closed) and WS-18 (ownership guard) are P0-adjacent — start
their sweeps in parallel with Phase 1, since both are exploit-class. WS-16, WS-19, WS-20
ride with Phase 2. WS-14, WS-15, WS-17 ride with Phase 3. Every new-instance-prevention
guard (the CI checks in WS-13/15/17/19 and WS-6's wallet grep) ships with its workstream —
the guard is the deliverable as much as the sweep; the sweep clears today's findings, the
guard is what stops the next audit from finding 57 more.

---

## Workstreams and priority order

Ordering principle: (P0-hotfix) small, migration-free, actively-exploitable or
actively-losing-money fixes ship first as individual commits; (P0) fixes needing migrations
follow; (P1) correctness/compliance hardening last. Each numbered task below is one commit
(per the repo's batch-size rule). Migration numbers start at **248** (current max is 247 —
re-verify with `ls backend/migrations | sort -V | tail -1` before each merge).

### Phase 0 — same-day hotfixes (no migrations, ~4 small PRs)

**WS-1: Driver-cancel IDOR (findings 5, 6, 7)** — `ride_cancel.py`
1. After loading the ride, reject non-owners:
   `if ride.get("driver_id") != driver["id"]: raise HTTPException(403, "Not your assigned ride")`
   (mirror `mark_rider_noshow` at `:190`). A `searching` ride has no assigned driver — a
   driver-side cancel of an unassigned ride is never legitimate, so 403 there too.
2. Add `"driver_id": driver["id"]` to `_cancel_filter` so the atomic update is also
   ownership-scoped (defense in depth; the in-memory check alone can race reassignment).
3. Finding 7 (insurance-trail corruption for uninvolved drivers) closes automatically once
   `driver["id"] == ride["driver_id"]` is guaranteed. No period-log repair code needed —
   but see WS-7's reconciler, which would also have caught the damage.
4. Tests: 403 for wrong driver, 403 for unassigned ride, happy path unchanged, and assert
   `record_period_transition` is *not* called on the 403 paths.

**WS-2: Rider-object PII leak (finding 9)** — `ride_reads.py:149`
1. Replace the blocklist with an allowlist projection:
   `safe_rider = {k: raw.get(k) for k in ("id", "first_name", "photo_url", "rating") if k in raw}`
   (confirm exact field names the driver app actually renders before finalizing — grep
   `driver-app/` for `ride.rider.` usages; add fields only with a stated purpose, per PIPEDA
   data-minimization).
2. Sweep for the same blocklist pattern elsewhere: `grep -rn "stripe_customer_id" backend/routes/`
   — fix every `select("*")`-plus-blocklist site in the same way (separate commits per file).
3. Test: response snapshot asserting `password_hash`, `fcm_token`, `phone`, `email` absent.

**WS-3: Safety notify NameError (finding 14)** — `safety.py:24-27`
1. Add `from features import notify_safety_team` to the except-ImportError branch.
2. Regression test that imports `routes.safety` in top-level mode and asserts
   `notify_safety_team` is bound (this is the sprint's "SOS silent failure" class — the
   broad `except` at `:109` masked a NameError returning success while never paging anyone).
3. Follow-up (same PR, separate commit): narrow that `except Exception` so a NameError-class
   programming error logs at `error` with `exc_info` and increments a
   `spinr_safety_notify_failed_total` metric — a dead safety page must be loud.

**WS-4: Circuit-breaker probe leak (finding 1)** — `repositories/_base.py`
1. Add `_CircuitBreaker.release_probe()`: if `_state == "half_open"` and `_probe_in_flight`,
   treat as failure — set `_state = "open"`, refresh `_opened_at`, clear `_probe_in_flight`.
2. In `run_sync`, capture `allowed_as_probe = _breaker.state in ("open→half_open", "half_open")`
   at the `should_allow()` call, and wrap the body so every exit path that is neither
   `record_success()` nor `record_failure()` — the deadline pre-check raise (`:211-213`),
   the `wait_for` timeout raise (`:222-228`), and the `ValueError` early-exit (`:240-241`) —
   calls `release_probe()` in a `finally`-style guard. Simplest shape: a `try/finally` with a
   `probe_settled` flag set by the success/failure bookkeeping.
3. Tests: simulate half-open + probe aborted by deadline → next `should_allow()` after
   `OPEN_DURATION` must release a new probe (today it never does; process-wide 503 wedge).

### Phase 1 — money correctness (migrations 248–250)

**WS-5: Corporate allowance settlement sign bug (finding 16)** — *the most urgent money fix:
every allowance-covered corporate ride currently credits the company instead of charging it.*
1. Migration `248_corporate_allowance_ride_debit.sql`: extend `corporate_allowance_apply`
   (or add a sibling `SECURITY DEFINER`, `search_path`-pinned function) with a `ride_debit`
   branch: `v_master_delta := -p_amount; v_used_delta := +p_amount`, same floor check and
   dual ledger inserts, `ride_id` recorded in notes/metadata. Keep `allowance_rollback`
   reserved for genuine grant-undo. Rollback plan in the header comment (function replace is
   trivially reversible by re-applying migration 29's body).
2. `corporate_allowance_service`: add `apply_ride_debit(...)`; change
   `payment_service.settle_corporate:419` to call it. The compensation path at `:440-446`
   (which currently re-grants on master-debit failure) must be re-derived for the new sign.
3. Tests: replace the mocked-`apply_rollback` tests with sign-asserting tests — after a $50
   allowance-covered ride, master balance **decreases** by $50 and `used` increases by $50;
   mixed allowance+fallback split; floor-violation path.
4. **Backfill/audit (separate task, coordinated with finance):** every
   `corporate_wallet_transactions` row with `type='allowance_rollback'` and notes matching
   `ride:%:allowance` is a mis-signed settlement — total impact = 2× the amount (credited
   instead of debited). Produce a per-company report first; apply correcting deltas via the
   new RPC with an idempotent backfill key. Do not silently mutate balances — this is a
   customer-facing billing correction and needs sign-off.

**WS-6: Atomic consumer-wallet mutations (findings 2, 3, 8, 10)** — one root cause, one fix.
1. Migration `249_wallet_apply_delta_rpc.sql`: `wallet_apply_delta(p_wallet_id, p_delta,
   p_type, p_reference_id, p_actor_id, p_metadata, p_floor)` — `SECURITY DEFINER`, pinned
   `search_path`, `SELECT ... FOR UPDATE` on the wallet row, floor check (reject or clamp per
   caller flag, to preserve the no-show path's clamp-at-zero semantics), writes the
   `wallet_transactions` row in the same transaction, idempotent on `p_reference_id`
   (unique partial index + `ON CONFLICT DO NOTHING` returning the existing row). Mirrors
   `corporate_wallet_apply_delta`.
2. Add `db_supabase.wallet_apply_delta(...)` wrapper; route the four call sites through it,
   one commit each:
   a. `routes/admin/wallet.py` credit (`:144`) — reference = the existing `idempotency_key`.
   b. `routes/admin/wallet.py` debit (`:232`) — sufficiency check moves into the RPC floor.
   c. `routes/drivers/ride_cancel.py` no-show fee (`:265`) — reference = `noshow:{ride_id}`.
   d. `routes/rides/cancellation.py` cancel fee (`:136`) — reference = `cancelfee:{ride_id}`.
3. Then sweep: `grep -rn 'update_one("wallets"' backend/` and migrate any remaining
   balance-writing site (top-up webhook, refunds) so the RPC becomes the *only* writer;
   add a CI grep guard (pre-commit) rejecting new direct `wallets.balance` writes.
4. Tests: concurrent credit+debit property test against the RPC (two racing deltas both
   land); idempotent replay returns the first transaction; floor rejection.

**WS-7: Payout TOCTOU + missing compensation (finding 4)** — `drivers/payouts.py`
1. Reorder to reserve-then-transfer, mirroring the instant path (`:808-826`):
   insert the `payouts` row `status='reserved'` **before** `Transfer.create`, with a partial
   unique index (migration `250_payouts_reserve_guard.sql`) on
   `(driver_id) WHERE status IN ('reserved','pending')` so two concurrent requests cannot
   both reserve. Balance sufficiency is then checked *after* the reserve row exists, counting
   reserved rows against available balance (or better: make `get_driver_balance` subtract
   open reservations).
2. Transfer second; on transfer failure, mark the row `failed` (releases the reservation).
   On success, flip to `completed` with the transfer ID; if that terminal write fails,
   reverse the transfer exactly as `request_instant_payout` does, and log at `error`.
3. Tests: two concurrent payout requests → exactly one transfer; transfer-succeeds/
   persist-fails → reversal called; reserve row leaks are cleaned by marking `failed`.

### Phase 2 — payment lifecycle & abuse (migration 251)

**WS-8: Pre-auth hold release on cancellation (finding 11)**
1. Migration `251_ride_cancel_fee_pi_column.sql`: add `rides.cancel_fee_payment_intent_id`
   (nullable) + index on `(auth_status) WHERE status = 'cancelled'` for the sweeper.
2. In `cancel_ride_rider` (`cancellation.py`) and `ride_search_timeout` (`matching.py:1296`):
   before writing any payment fields, if `auth_status in ('authorized','fare_only')` call
   `cancel_authorization(ride_id, payment_intent_id)` and set `auth_status='released'`.
   Store the fee PI in the new column; **never overwrite `payment_intent_id`**.
3. Reconciliation sweep: extend `utils/preauth_capture.py` (or the daily
   `stripe_reconcile` loop) to scan `status='cancelled' AND auth_status IN
   ('authorized','fare_only')` and release stragglers — this also cleans up the existing
   backlog of stranded holds (they auto-expire in ~7 days on Stripe's side, but riders see
   the pending charge until then; sweep makes it minutes, and catches PIs we still hold a
   reference to). For already-overwritten PIs (fee PI replaced the hold PI), recover via
   Stripe search on `metadata.ride_id` — `authorize_ride` sets it — in a one-off backfill.
4. Tests: card-ride cancel releases the hold; fee PI lands in the new column; sweeper
   releases a seeded stranded hold; auto-cancel path covered.

**WS-9: `/rate` tip abuse (finding 12)** — `rating.py`, `schemas.py`
1. `schemas.py` `RideRatingRequest.tip_amount`: add `le=500` (parity with `TipRequest`).
2. In `rate_driver`: reject (structured error `ERR_ALREADY_RATED`) if `rider_rating` is
   already set; ignore-with-error the tip if `tip_amount > 0` already on the ride (mirror
   `ERR_TIP_DUPLICATE`).
3. Only accumulate the tip when `payment_status in ('pending','failed')` — i.e. when
   settlement will actually collect it. Otherwise return a structured error pointing the
   client at the charged `/tip` flow (`payments.py:54`), which is idempotent and actually
   charges the card.
4. Add `@idempotent_endpoint(scope="ride_rate")` so the rider-app offline queue's replay
   (`rideStore.ts:590`) dedupes server-side.
5. Tests: double-call accumulates once; tip on paid ride rejected; `le=500` enforced;
   replay with same idempotency key is a no-op. Note for finance: `driver_earnings` /
   T4A snapshots may already contain uncharged tips — include in the WS-5 audit sweep.

**WS-10: Mid-trip stop fare integrity (finding 13)** — `stops.py`
1. In `_reestimate_fare_for_stops`, after computing `total_fare`, run `calculate_all_fees`
   on the new subtotal and include `tax_amount`, `tax_breakdown`, area fees, `grand_total`,
   `driver_earnings`, `admin_earnings`, and a refreshed `fare_breakdown_snapshot` in the
   same single ride update (atomic — never write fares and totals in two steps).
2. Receipt-transparency check: the rider must see the new price when the stop is added
   (WS event already fires for stop changes — include the new `grand_total` in the payload),
   consistent with the "no hidden fees / surge visible before booking" product rules.
3. Tests: add-stop then settle → charged amount equals driver-shown amount equals
   `grand_total`; GST/PST recomputed on the new subtotal; remove-stop path symmetric.

### Phase 3 — identity & compliance (migrations 252–253)

**WS-11: Email verification before corporate trust (finding 15)**
1. Migration `252_users_email_verified.sql`: add `users.email_verified boolean not null
   default false` + `email_verified_at`. Any *change* to `users.email` resets the flag
   (enforce in the route, assert in tests).
2. `POST /users/profile`: persist the email but unverified; kick off an email OTP (reuse the
   existing `hash_otp` machinery — SHA-256 at rest, 5-fail lockout) and add
   `POST /users/verify-email` to confirm.
3. `corporate_rider.py` `/corporate/join-domain` (`:143-159`): require
   `current_user["email_verified"]` before domain-matching; return a structured
   `ERR_EMAIL_UNVERIFIED` the app can route to the verify flow. Audit existing corporate
   memberships created via domain-join against unverified emails (report to admin, don't
   auto-evict).
4. Tests: join-domain with unverified email → 403; verify → join succeeds; email change
   resets the flag and corporate join is re-gated.

**WS-12: Atomic insurance-period transitions + ride-based reconciler (C3)**
1. Migration `253_insurance_period_transition_rpc.sql`:
   `record_insurance_period_transition(p_driver_id, p_new_period, p_ride_id)` —
   single transaction: close the open row (`ended_at = now()` where `ended_at IS NULL`)
   **and** insert the new row; on unique-violation (concurrent open) the function returns a
   `noop`/`race` discriminator instead of leaving a half-applied state. `SECURITY DEFINER`,
   pinned `search_path`. Keep the existing DB trigger (close-only mutation) and partial
   unique index — the RPC composes with both. Enforce `p_new_period = 3 ⇒ p_ride_id IS NOT
   NULL` in SQL too, not just Python.
2. `insurance_periods.record_period_transition` becomes a thin RPC call. Keep the
   fire-and-forget swallow-at-the-edge behavior (the ride state machine must not block on an
   audit write — that judgment call stands) **but** the failure metric
   `spinr_insurance_period_write_failed_total` gets an alert, because with the reconciler
   below a failure is now self-healing rather than a permanent audit gap.
3. New background loop `utils/insurance_period_reconciler.py` (register in `lifespan.py`,
   follow the Background Loop Recipe — this makes loop #17, replay-safe by construction
   since it's a pure convergence job): every 10 min, derive the *expected* period for every
   driver with an active ride or `is_online=true` (period from ride state per the CLAUDE.md
   table — this is the "derive from ride state, not driver UI" rule made executable) and
   compare against the open `driver_insurance_periods` row:
   - open row missing → open the expected one via the RPC (`started_at = now()`, plus a
     `reconciled=true` marker column or notes field so audit can distinguish backfilled rows
     — append-only is preserved; we add rows, never mutate or delete).
   - open row has the wrong period → close+open via the RPC.
   - open row exists for a driver with no active ride and `is_online=false` → leave to the
     existing stale-intent reconciler (don't duplicate its Redis-presence safeguards).
   Emit `spinr_insurance_period_reconciled_total{action=opened|corrected}` — this metric
   trending nonzero is the signal that a write path is failing.
4. Tests: kill the insert mid-transition (mock RPC failure) → reconciler restores the open
   period on next tick; wrong-period row corrected; reconciler is a no-op on a healthy
   fleet; period-3-requires-ride enforced at the SQL layer.

---

## Sequencing & effort

| Phase | Workstreams | Migrations | Est. effort | Gate |
|-------|-------------|-----------|-------------|------|
| 0 (same-day) | WS-1..4 | none | 1–2 dev-days | security review on WS-1/WS-2 diffs |
| 1 | WS-5, WS-6, WS-7 | 248–250 | 3–4 dev-days | `spinr-money-auditor` + `spinr-migration-reviewer` agents; finance sign-off on WS-5 backfill |
| 2 | WS-8, WS-9, WS-10 | 251 | 2–3 dev-days | money-auditor; rider-app coordination for WS-9 error codes |
| 3 | WS-11, WS-12 | 252–253 | 2–3 dev-days | migration-reviewer; regulatory note for WS-12 backfilled-row marker |
| P0-adjacent (parallel w/ Phase 1) | WS-13, WS-18 | none | 3–4 dev-days | security review; batched commits (~10 sites each) |
| w/ Phase 2 | WS-16, WS-19, WS-20 | none | 2–3 dev-days | money-auditor on WS-16 |
| w/ Phase 3 | WS-14, WS-15, WS-17 | none | 3–4 dev-days | golden-file receipt tests land before promo/discount changes ship |

Cross-cutting rules for every workstream:
- One logical change per commit; regression test in the same commit as the fix.
- All new SQL functions: `SECURITY DEFINER` + pinned `search_path`; backend-only callers.
- Migrations: rollback plan in header comment; RLS unaffected (no new user-data tables
  except columns on existing RLS-covered tables); re-verify the next free migration number
  at merge time — two of these phases will race each other on numbering.
- Coverage floors apply: payments/fare ≥90%, rides/dispatch ≥80%.
- No silent error-softening: every new failure path is `logger.error` + structured
  HTTPException or a counted metric — never `warning`-and-continue on money/auth/DB.
