# Change Impact & Risk Log

> Copy this template into the PR description, or save a filled copy to
> `docs/change-log/YYYY-MM-DD-<short-slug>.md` for anything touching a
> live-tested surface (rides, dispatch, payments, auth, corporate, safety).
> See `CLAUDE.md` → "Change Impact & Risk Log (mandatory)" for the full policy.

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-23 |
| Author | Claude Code (agent session), for srikumarimuddana-lab |
| Surface(s) | backend (+ `metrics-agent/grafana/alert-rules.yaml`, hand-provisioned) |
| Domain (Sentry tag) | payments |
| PR / commit link | Branch `fix/orphaned-hold-detection` (not pushed) |
| Related issue or gap ID | 2026-09-22 fleet audit (`spinr-edge-case-reviewer` + `spinr-observability-reviewer`): Gap A, orphaned uncaptured hold invisible to every reconciler. Gap B, the cancelled+captured detection query is never scheduled, and reconcile discrepancy logs are untagged in Sentry. |

## 1. Issue / gap identified

**Gap A.** `routes/rides/booking.py` places the card hold before `_insert_ride_with_code`. If the INSERT fails or the pod dies in between, or a rider confirms the SCA sheet on-device and never re-books, a real `requires_capture` PaymentIntent exists with no ride row. Nothing in the system can see it.

**Gap B.** `scripts/reconcile_cancelled_captured_refunds.py`'s `find_candidates` query exactly matches the shape of the 2026-09-21 incident (a cancelled ride whose hold was captured and never refunded), but only a human ever runs it. In addition, `stripe_reconcile.py`'s discrepancy `logger.error` calls carry no `domain` tag, so they reach Sentry unfilterable by any `domain:payments` alert, and none of the discrepancy types has a metric or alert.

## 2. Root cause

- **A:** `utils/orphaned_hold_reconciler.find_orphaned_holds` only scans `rides`, so a hold with no row has nothing to query. `stripe_reconcile.py`'s `STRIPE_ORPHAN` (step 3b) skips anything that is not `status == "succeeded"`, and it only lists PIs created *yesterday*. Its "has a ride" lookup (`db_pi_to_ride`) is also built from paid rides completed yesterday, so it could not have been reused for holds anyway: a live `searching` ride's hold would have been flagged as orphaned.
- **B:** The 2026-09-21 backfill script was deliberately built human-only, because its `--apply` path moves money. Nothing split its read-only detection half out for scheduling. The untagged logs predate the `sentry_scrub.tags_from_log_extra` bridge convention. The later backstops in the same file (3c, 3d, 3f) were tagged, but 3a and 3b never were.

## 3. Fix / remediation

All in the existing daily `stripe_reconcile_loop` tick (02:00 UTC, Redis leader lock). No new loop, no lifespan change. Detection only: nothing in this change moves money or writes anything except the tick's existing single `audit_logs` row.

1. **New step 3g, `STRIPE_ORPHAN_HOLD` (`_reconcile_orphan_holds`).** Lists PIs created within the last 8 days, excluding the most recent 1 hour. Keeps `requires_capture`, skipping the same non-ride `metadata.scope` values 3b skips. Checks linkage against **all** rides by `payment_intent_id` via `db_supabase.get_rows_batched_in`, then flags the unlinked ones with `amount_capturable` and `metadata.ride_id` for triage. A Stripe or DB failure returns `None` (reported as `null` in the audit row, and counted on the check-failed metric). It never flags every hold as orphaned on a DB blip.
   - **New type rather than reusing `STRIPE_ORPHAN`:** the remediation is opposite. An orphaned *capture* is money taken with no ride (investigate, possibly refund). An orphaned *hold* has moved no money yet, and the fix is to cancel the authorization so the rider's funds free up before Stripe's 7-day expiry. One shared type would hand an operator the wrong runbook for half its rows. It is also a different Sentry message template, so the two group as separate issues.
   - **8-day lookback rather than the 1-day window:** the 1-day window is right for 3a/3b, which reconcile a daily *delta*. A hold is a *standing state* that stays on the card until someone releases it or Stripe auto-cancels it. Booking holds never request extended authorization (the only `capture_method="manual"` call site is `utils/stripe_charge.authorize_ride`), so Stripe expires them after 7 days. 8 days covers a hold's entire life plus slack, so each daily run sees the **complete** live population. A missed tick cannot let a hold age out unseen, and a "historical backlog" older than 8 days has already been released by Stripe, leaving no rider money held to recover. The 1-hour grace excludes the legitimate no-ride-yet windows: the authorize-to-INSERT gap, and the SCA two-step re-book.
2. **New step 3h, `CANCELLED_CAPTURED_UNREFUNDED` (`_detect_cancelled_captured`).** A scheduled read-only equivalent of the script's dry run. It uses the same filter (`status='cancelled'`, `auth_status='captured'`, PI present, no `refund_amount`) and the same outcome classification as `reconcile_one(apply_changes=False)`, reading Stripe only through the existing `utils/stripe_charge.read_capture_state`. It never calls `refund_excess_capture`. Alert outcomes: `would_refund`, `already_refunded_on_stripe`, `captured_nothing`, `unknown` (the script's non-zero-exit set plus `would_refund`). Each is logged per ride as a tagged `logger.error` naming the script to run.
   - **Why not import the script:** importing it would run its import-time `logging.basicConfig` and `sys.path` mutation inside the server process and load a second `db_supabase` module copy. The fee rule (`_cc_fee_owed_cents`) and classification (`_classify_cc`) are therefore mirrored, and parity tests load the **real** script and assert identical results across 36 fee×state combinations. Mutation-checked: removing the `payment_status == "paid"` clause from the mirror makes 3 parity tests fail.
   - **Why not in `orphaned_hold_reconciler`:** that loop mutates (it releases holds), and its `OPEN_AUTH_STATES` deliberately excludes `captured`. Mixing a captured-money check into it would blur that boundary.
   - **Bounded to `cancelled_at` in the last 14 days:** found in review. Every legitimate fee-bearing partial-capture cancel (`cancellation.py`, `drivers/ride_cancel.py`) ends up `auth_status='captured'` with `refund_amount` unset, so an unbounded scan grows forever and re-reads each one on Stripe daily as `not_needed`. 14 days gives each new occurrence 14 daily detection chances. The **pre-existing backlog stays with the human-run script**, which is its documented purpose. Other details: paged (500 rows × 10 pages, ordered by `id`); an accurate one-row truncation probe; `Semaphore(5)` concurrency for Stripe reads; per-ride exception isolation, where a bad row becomes `unknown` and does not abort the tick's audit summary.
3. **Skipped on a `target_date` backfill.** 3g and 3h are current-state checks feeding P1 counters, so re-running N past days would otherwise stamp today's findings onto old audit rows and re-fire the alert N times.
4. **Metrics.** These are counters, all pre-registered at 0 in every worker on import. Without that, `increase()` never sees a series' first sample, so a real finding would never have fired.
   - `spinr_payment_cancelled_captured_unrefunded_total{outcome}`
   - `spinr_payment_stripe_orphan_hold_total`
   - `spinr_payment_reconcile_check_failed_total{check=orphan_hold|cancelled_captured}` (counts a failed or truncated check; otherwise a check failing daily would look clean)
5. **Tagging.** `extra={"domain": "payments", "ride_id": ...}` added to the `FARE_ATTRIBUTION_MISMATCH`, `DB_PAID_STRIPE_MISSING`, `DB_PAID_STRIPE_MISMATCH`, `DB_PAID_AMOUNT_MISMATCH`, `STRIPE_ORPHAN` and `COMPLETE with N discrepancies` lines, and to the two early-return failure lines (Stripe list failed, DB rides query failed). The module uses stdlib `logging`, so `extra=` is correct here (not `logger.bind`, which is loguru-only).
6. **Grafana.** Three rules added to `metrics-agent/grafana/alert-rules.yaml`, following the existing entries' exact structure:
   - Cancelled-captured (P1)
   - Orphan hold (P1)
   - Check failed (P2)

   **Deviation from the brief's "non-zero for more than one tick":** these fire on the first non-zero daily tick (`sum(increase(x[26h])) > 0`). With a once-daily tick, a two-consecutive-tick condition cannot be written as sound PromQL, because the overlapping windows drift by tick duration. It would also hold back a "rider is owed money" alert for 24h. The 1h grace windows already exclude the in-flight races a second tick would have guarded against.

## 4. Risk & impact on existing functionality

- **Same code path:** `_run_reconciliation_tick` / `stripe_reconcile_loop` only, with the loop itself unchanged. Other importers of `stripe_reconcile` (grep): `core/lifespan.py` (loop spawn), and `utils/payment_retry.py` (imports `_maybe_heal_stuck_processing` and `_truthy`, both untouched). Tests that touch it: `test_stripe_reconcile.py`, `test_stripe_event_loop_offload.py`, `test_routes_main_coverage.py`, `test_corporate_kill_switch_fail_closed.py`, `test_stuck_ride_sweeper.py`, `test_e4_d10_payment_3ds_quests.py`. All pass.
- **Tables read:** `rides` (two new read-only queries). **Written:** only the existing `audit_logs` summary row, which gains the new keys `stripe_orphan_holds` and `cancelled_captured_unrefunded`. Any consumer that reads `audit_logs.details` for `action='stripe_reconciliation'` sees two extra keys (additive).
- **Stripe load:** one extra paginated `PaymentIntent.list` over 8 days of PIs daily, plus 2 reads per cancelled+captured candidate in the 14-day window. At current volume that is a handful of pages. At high volume the 8-day list becomes the dominant cost; the review suggested `PaymentIntent.search(status:'requires_capture')`, but that was **not** adopted, because it could not be verified against real Stripe here (Search is eventually consistent). Follow-up if volume grows.
- **Tick duration:** a longer tick delays `_record_heartbeat("stripe_reconcile (24h)")` and shifts the next run by the tick's length (the loop sleeps 86400s *after* each tick). This was already true before; the change makes the tick longer.
- **Metric series:** 7 new always-present series per worker (4 + 1 + 2).
- **Ride state machine / wallet / insurance:** untouched. No money moves, no ride state is written.
- **Blast radius:** single surface (backend), and only the daily reconcile job.
- **Pre-existing, not fixed (noted):** 3b's `STRIPE_ORPHAN` compares yesterday's succeeded PIs against *paid rides completed yesterday*, so a PI captured yesterday for a ride completed today, or one captured as a cancel fee, can false-flag as `STRIPE_ORPHAN`. That is out of scope here but worth a follow-up.

## 5. User-experience effect

- Nobody outside ops. Backend-only detection. Internal admins/on-call get new `domain:payments` Sentry events, and Grafana alerts once the rules are entered in the UI.
- Not visible mid-session to riders or drivers.
- No rider/driver copy change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/stripe_reconcile.py` | New 3g `_reconcile_orphan_holds`; new 3h `_detect_cancelled_captured` + `_cc_fee_owed_cents` / `_classify_cc` / `_read_capture_state`; backfill skip; 3 pre-registered counters; `domain` tags on discrepancy logs; summary keys; docstring | Gap A, Gap B, tagging |
| `backend/tests/test_stripe_reconcile_orphan_hold_and_cc.py` | New: 60 tests (orphan hold, detector, never-refunds, paging/truncation, per-row isolation, backfill skip, pre-registration, tagging, script parity) | Regression coverage |
| `metrics-agent/grafana/alert-rules.yaml` | 3 new rules + header note | Alert on the new counters |
| `docs/change-log/2026-09-23-orphaned-hold-and-detection-loop.md` | This file | Mandatory log |

`backend/scripts/reconcile_cancelled_captured_refunds.py` and the existing `backend/tests/test_stripe_reconcile.py` are **unchanged**.

## 7. Before / after

```
# Before (3b; the only Stripe-side orphan check)
for pi_id, pi in stripe_pis.items():          # yesterday's PIs only
    if pi["status"] != "succeeded":
        continue                                # requires_capture holds skipped
    ...
    logger.error("stripe_reconcile: STRIPE_ORPHAN pi=%s ...", pi_id, _orphan_cents)   # untagged
```

```
# After
    logger.error("stripe_reconcile: STRIPE_ORPHAN pi=%s ...", pi_id, _orphan_cents,
                 extra={"domain": "payments"})
...
if target_date is None:
    orphan_holds = await _reconcile_orphan_holds(_stripe)      # 8d, requires_capture, all-rides linkage
    cc_counts, cc_flagged = await _detect_cancelled_captured()  # read-only dry run, 14d
```

## 8. Rollback plan

- No money or ride state is written by this change, so there is no data to remediate. The only writes are two extra keys in the daily `audit_logs` summary row.
- No feature flag. This is detection-only logging and metrics with no user-visible behaviour, so it was judged not to need one. If the new checks misbehave (Stripe rate-limit pressure, noisy alerts), a redeploy of the reverted commit is the path. Until then, silence/disable the three Grafana rules in the UI (`spinr-payment-cancelled-captured-unrefunded`, `spinr-payment-stripe-orphan-hold`, `spinr-payment-reconcile-check-failed`) and resolve the Sentry issues. The daily tick itself is resilient: each new check catches its own Stripe/DB errors and per-row errors, so it cannot stop the pre-existing checks from writing their summary.

## 9. Verification performed

- [x] Automated tests run: `backend/tests/test_stripe_reconcile.py`, `test_stripe_reconcile_orphan_hold_and_cc.py` (new), `test_reconcile_cancelled_captured_refunds.py`, `test_stripe_event_loop_offload.py`, `test_routes_main_coverage.py`, `test_corporate_kill_switch_fail_closed.py`, `test_stuck_ride_sweeper.py`, `test_e4_d10_payment_3ds_quests.py`, `test_loguru_call_conventions.py`, `test_orphaned_hold_reconciler.py`, plus `scripts/test_payment_failure_alert.py` (alert YAML). All pass. `ruff check` and `ruff format --check` are clean on the touched Python files.
- [x] Dry run against mocked fixtures (release gate 4). The concrete scenario, as exercised in `test_tick_reports_orphan_hold_and_cc_in_audit_summary`:
  - **Before:** a `requires_capture` PI with no ride row, plus a cancelled ride with `auth_status='captured'`, $2.10 captured, no refund. The daily tick reported 0 discrepancies for both.
  - **After:** the audit row shows `stripe_orphan_holds: 1`, `cancelled_captured_unrefunded: {"would_refund": 1}`, and both types in `discrepancy_detail`. `update_one` was never awaited, and `insert_one` was awaited exactly once (the audit row).
  - `test_cc_detector_never_touches_the_refund_path` patches `refund_excess_capture` to raise if called.
- [ ] Manual repro in staging: **not done.**
- [x] Blast-radius grep: importers of `stripe_reconcile` / `_run_reconciliation_tick`; `capture_method` call sites (one: `authorize_ride`); `request_extended_authorization` (none); tests loading the script; alert-rules consumers (`scripts/test_payment_failure_alert.py`); metrics-registry resets in tests (none).
- [x] Reviewed against CLAUDE.md money conventions: cents via `utils.money.to_decimal`/`dollars_to_cents`, no float. Also checked observability naming (`spinr_payment_*_total`), stdlib vs loguru tagging, and background-task replay safety (read-only, already leader-locked).
- [x] Not feature-flagged: detection-only, no user-visible behaviour.

**Adversarial review.** The `spinr-money-auditor` / `spinr-observability-reviewer` agents were unavailable in this session (no Agent tool), so `/code-review` at high effort was run instead. It raised 10 findings:
- **Fixed (8):** #1 increase() first-sample blindness, fixed with pre-registration. #2 unbounded candidate growth, fixed with the 14-day `cancelled_at` window. #3 truncation invisible to alerts and the exact-ceiling false positive. #6 backfill re-firing alerts. #7 one bad row aborting the tick. #8 hand-rolled `$in` chunking, replaced with `get_rows_batched_in`. #9 missing change log / dead runbook link. #10 a failing check was indistinguishable from a clean one, fixed with the check-failed counter.
- **Partially fixed:** #4 serial Stripe reads, now `Semaphore(5)`; the per-ride `app_settings` read inside `read_capture_state` is unchanged.
- **Declined, with reason:** #5 Stripe Search API, see §4.

**What was NOT verified:**
- Not run against real Stripe or real Supabase. Every Stripe and PostgREST interaction is mocked, including that `get_rows` accepts the `cancelled_at` `$gte` filter together with `order`/`offset` in production, which is inferred from other call sites.
- The 7-day Stripe authorization expiry is taken from Stripe's documented default for online card payments, not re-confirmed against this account's settings.
- The Grafana rules are **not loaded anywhere**: per the file header, rules are entered by hand in the Grafana Cloud UI. Nothing verified the PromQL against live series, or that the scrape actually carries these counters from whichever worker or provider (Fly vs Railway) wins the leader lock.
- Not checked: whether the `/metrics` scrape sees every uvicorn worker's registry.
- No production build is relevant (backend-only).

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow without the UX field filled in
