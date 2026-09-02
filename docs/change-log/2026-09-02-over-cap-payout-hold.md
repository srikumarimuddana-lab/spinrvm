# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-02 |
| Author | Claude (weekly payout audit remediation) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | branch `claude/weekly-payout-audit-tsdnxg` |
| Related issue or gap ID | Gap #1 from the 2026-09-02 weekly-payout audit (spinr-money-auditor) |

## 1. Issue / gap identified

When a driver's computed weekly balance exceeds `MAX_PAYOUT_AMOUNT` ($5,000), the
weekly auto-payout batch (`utils/auto_payout.py::run_weekly_auto_payout`) skipped
that driver with only a `logger.error(...)` call and a free-text entry in the
batch row's `error_summary`. No payout row was created, no driver notification
was sent, and there was no durable, queryable per-driver state — ops had no way
to answer "who is currently over cap?" except grepping log text or `error_summary`
strings across weekly batch rows, and the driver had no idea their payout was
being withheld.

## 2. Root cause

The $5,000 circuit breaker was designed to fail safe for *that week's Stripe
call* (never send an anomalously large transfer unattended), but its
ops-surfacing was bolted onto the batch's generic error-logging path instead of
the skip-reporting path every other blocked reason (`no_stripe_account`,
`missing_gst`, `missing_sin`, `suspended`, `stripe_payouts_disabled`) already
uses — which has ops tooling (`skipped_summary`, `find_blocked_drivers`,
driver push notifications) built for exactly this shape of problem.

## 3. Fix / remediation

- Over-cap balances now get a durable `payouts` row with `status='held_over_cap'`
  (id `held-{driver_id}-{week_key}`, idempotent on insert), recorded in the
  batch's `skipped_summary` under reason `over_cap`, and the driver receives the
  same actionable push notification every other skip reason gets.
- A dedicated metric label (`spinr_bgloop_errors_total{loop="auto_payout",
  reason="over_cap"}`) replaces the generic, reason-less increment.
- `find_blocked_drivers` (the admin live-preflight, `GET
  /admin/auto-payouts/blocked-drivers`) now also surfaces this week's open
  `held_over_cap` rows, reading the already-computed amount rather than
  recomputing a balance — so it doesn't change that function's "cost scales with
  the blocked set" cost profile.
- `held_over_cap` is deliberately **not** in migration 250's
  `idx_payouts_one_inflight_per_driver` status set (`reserved|pending|
  transfer_completed`) — a held row must never block a future normal payout
  attempt for the same driver.
- **Blast-radius fix (found while implementing the above):** three other readers
  of the `payouts` table bucket "any status not in {reversed, failed}" as
  already-paid money: `routes/drivers/earnings.py::get_driver_balance`
  (`total_paid_out`), `utils/driver_statement.py::build_statement`
  (`payouts_total`/`payouts_spinr_total`, used by weekly/monthly driver
  statements), and `routes/admin/drivers.py`'s payouts-summary endpoint
  (`total_paid_out`). Left unfixed, a held-over-cap row would have shown a
  driver (and an ops admin) that money had already reached the driver's bank
  when it had not. All three now treat `held_over_cap` as "not yet sent" (same
  bucket as `pending`/`processing`), while still deducting it from
  payable/pending balance so the money stays correctly earmarked and can't be
  paid twice.
- The weekly-batch-only, non-admin-controllable `admin/auto_payouts.py` router
  also gained a `POST /run-now` endpoint in this branch (Gap #3 of the same
  audit) — unrelated to this specific fix but shipped in the same PR.

## 4. Risk & impact on existing functionality

- **Blast radius: cross-file, single-table.** Every reader of the `payouts`
  table was grepped for status-bucketing logic (`_not_money_out`,
  `not in ("reversed", "failed")`, `_sum_by_status`): `utils/auto_payout.py`
  itself, `routes/drivers/earnings.py`, `routes/admin/drivers.py`,
  `utils/driver_statement.py`. `services/stripe_payout_sync_service.py` and
  `services/legacy_payout_correction_service.py` only ever write/read their own
  `payout_type` values (`stripe_sync`, `legacy_outstanding_correction`,
  `legacy_import`) and never touch `held_over_cap` rows. `t4a_annual_job.py`'s
  T4A eligibility sum does not read `payouts` at all for app-native rows (it
  sums `rides.driver_earnings` directly) — unaffected.
- `payouts.status` has no `CHECK` constraint (plain `TEXT`, confirmed in
  `supabase_schema.sql`), so adding a new status value needed no migration.
- `_compute_payable_balance` (the function this fix's held-row insertion sits
  inside) already deducted a `held_over_cap`-shaped row correctly before this
  change too, by construction — the deduction logic (`not in
  {"reversed","failed"}` and `payout_type != "stripe_sync"`) was already
  status-agnostic. No change needed there.
- The admin `auto_payouts.py` router's two existing read-only GET endpoints are
  unchanged in behavior for all pre-existing skip reasons; only the new
  `over_cap` reason and rows are additive.

## 5. User-experience effect

- **Driver-facing:** a driver whose weekly balance exceeds $5,000 now gets a
  push notification ("Your payout needs a quick review") instead of silence.
  Their in-app balance/statement screens continue to show the money as
  "Pending" rather than "Paid Out" (previously correct only because no
  over-cap row existed at all; now explicitly correct with the new status).
- **Admin-facing:** ops can now query `payouts WHERE status = 'held_over_cap'`
  directly, and the `GET /admin/auto-payouts/blocked-drivers` preflight
  surfaces the same information without a manual SQL query.
- No mid-session visible change — this only affects the weekly batch's
  post-run state and driver-facing balance/statement screens on next load.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/auto_payout.py` | Added `_record_over_cap_hold`, `_record_and_notify_blocked`, `_held_over_cap_id_for`/`_HELD_OVER_CAP_ID_RE`, `_current_week_over_cap_entries`; wired into `run_weekly_auto_payout` and `find_blocked_drivers`; added `over_cap` to `_SKIP_NOTIFICATIONS` | Durable, ops-visible over-cap state instead of log-only |
| `backend/routes/drivers/earnings.py` | `pending_payouts` now also includes `held_over_cap` status | Stop `total_paid_out` from counting held money as sent |
| `backend/utils/driver_statement.py` | `held_over_cap` excluded from `payouts_total`/`payouts_spinr` sums, still listed in `payouts` list | Same fix for weekly/monthly driver statements |
| `backend/routes/admin/drivers.py` | `pending_in_flight` now also includes `held_over_cap` | Same fix for the admin payouts-summary endpoint |
| `backend/tests/test_auto_payout.py` | New `TestOverCapHold` class (6 tests) + updated `test_over_cap_balance_held_for_review` | Cover the new hold/notify/metric/find_blocked_drivers behavior |
| `backend/tests/test_drivers_extended.py` | New `test_over_cap_hold_deducts_from_balance_but_is_not_paid_out` | Regression test for the earnings.py blast-radius fix |
| `backend/tests/test_driver_statement.py` | New `test_build_statement_excludes_over_cap_hold_from_paid_totals` | Regression test for the driver_statement.py blast-radius fix |
| `backend/tests/test_admin_drivers_coverage.py` | New `test_held_over_cap_counts_as_money_out_but_not_paid_out` | Regression test for the admin/drivers.py blast-radius fix |

(Gap #2/#3/#4 fixes from the same audit are in earlier commits on this branch;
this entry covers Gap #1 only.)

## 7. Before / after

```python
# Before (utils/auto_payout.py)
if balance > MAX_PAYOUT_AMOUNT:
    logger.error(
        "[AUTO-PAYOUT] driver %s balance $%s exceeds cap $%s — skipped for manual review",
        driver_id, balance, MAX_PAYOUT_AMOUNT,
    )
    errors.append(f"{driver_id}: over_cap_requires_review")
    _metric_inc("spinr_bgloop_errors_total", {"loop": "auto_payout"})
    continue
```

```python
# After
if balance > MAX_PAYOUT_AMOUNT:
    skipped["over_cap"] = skipped.get("over_cap", 0) + 1
    _bump_area(area_id, skipped_n=1)
    await _record_over_cap_hold(driver, balance, week_key, skipped_drivers)
    continue
```

```python
# Before (routes/drivers/earnings.py)
pending_payouts = sum(
    (_d(p.get("amount") or 0) for p in payout_rows if str(p.get("status") or "").lower() == "pending"),
    Decimal("0"),
)
```

```python
# After
_not_yet_sent = {"pending", "held_over_cap"}
pending_payouts = sum(
    (_d(p.get("amount") or 0) for p in payout_rows if str(p.get("status") or "").lower() in _not_yet_sent),
    Decimal("0"),
)
```

## 8. Rollback plan

No feature flag gates this (the $5,000 circuit breaker itself is a hardcoded
constant, unflagged, same as before this change). Rollback is a plain code
revert of these commits — safe because:
- No migration was added (no schema change to roll back).
- Any `held_over_cap` rows already written by the time of a rollback stay
  harmless: they are excluded from the in-flight index (so they never block a
  payout) and `_compute_payable_balance`'s deduction logic (unchanged by this
  fix) already treated them as money-out by construction, so a reverted
  `auto_payout.py` would keep deducting them correctly even without the new
  helper functions that created them. The only user-visible regression on
  revert is that `earnings.py`/`driver_statement.py`/`admin/drivers.py` would
  go back to (incorrectly) counting any pre-existing `held_over_cap` rows as
  "paid out" until those rows are eventually resolved by ops — an acceptable,
  narrow, temporary regression for a revert scenario, not a data-loss risk.
- If a bad `held_over_cap` row needs correcting after the fact, it's a plain
  `UPDATE payouts SET status = ... WHERE id = 'held-...'` — no Stripe call was
  ever made for that row (that's the entire point of the circuit breaker), so
  there is no live-data money-movement to unwind.

## 9. Verification performed

- [x] Automated tests run (unit): `backend/tests/test_auto_payout.py` (63
      tests), `backend/tests/test_drivers_extended.py` (121 combined with
      `test_driver_statement.py`), `backend/tests/test_admin_drivers_coverage.py`
      (new test passes in isolation and within the full file — see below),
      `backend/tests/test_earnings_coverage.py`, `backend/tests/test_payout_toctou.py`
      — all green.
- [ ] Manual repro / staging check — **not performed**, no staging environment
      available in this session.
- [x] Blast-radius grep performed: searched for every reader bucketing
      `payouts.status` (`_not_money_out`, `not in ("reversed","failed")`,
      `_sum_by_status`) across `backend/`; found and fixed the three additional
      readers listed above; confirmed `stripe_payout_sync_service.py`,
      `legacy_payout_correction_service.py`, and `t4a_annual_job.py` are
      unaffected (payout_type-scoped or don't read `payouts` at all).
- [x] Reviewed against CLAUDE.md conventions: Decimal-only money math (no
      float introduced — `amount` is written as a `Decimal`, matching every
      other writer in `auto_payout.py`), the query-filter/`payouts.amount`
      NUMERIC(10,2) convention (B28, already applied), append-only ledger
      rules (not touched — this is the `payouts` table, not
      `financial_events`), no silent error-swallowing (the held-row insert's
      `except Exception` still logs loudly via `logger.exception`, only the
      `DuplicateRecordError` case is intentionally silent/idempotent).
- [ ] Feature-flagged — **not flagged**. Justification: this is a strict
      additive/corrective change to an existing, already-live circuit breaker
      (the $5,000 cap itself is unflagged and unchanged); the new behavior
      only fires in the rare case a driver's weekly balance exceeds $5,000,
      which was already being silently skipped before this change — there is
      no new *risk* being introduced that a flag would let ops dark-launch,
      only better visibility into an existing skip path.
- [ ] Production build (`npm run build`) — **not applicable**, no frontend
      surface touched by this change (backend Python only).

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain code revert; no migration;
      existing held rows stay safe under either code version)
- [x] Blast radius is stated, not assumed (grepped and fixed 3 additional
      readers beyond the primary fix)
- [x] No silent behavior change to an already-shipped flow without the UX
      field filled in — driver-facing and admin-facing effects both stated
      above
