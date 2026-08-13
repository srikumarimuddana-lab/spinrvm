# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-11 |
| Author | Claude Code (claude-sonnet-5) |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate |
| PR / commit link | branch `claude/n15e-corporate-allowance-notify` |
| Related issue or gap ID | ACTION_ITEMS.md N15, sub-items R43/R44 |

## 1. Issue / gap identified

A corporate rider's monthly ride allowance resets with zero notice (R43), and
when it's exhausted the rider only discovers it as a 4xx at their *next*
booking attempt — nothing warns them in advance or tells them at the moment
it happened (R44).

## 2. Root cause

Both code paths — `utils/allowance_reset.py`'s `run_allowance_reset_tick` and
`services/payment_service.py`'s `settle_corporate` — were built to get the
money/period-bookkeeping right and never had a notification step added,
unlike the sibling "low wallet balance" loop
(`utils/corporate_low_balance.py`, which already emails the company billing
admin) or the driver/rider push patterns established elsewhere (e.g. N5's
`routes/rides/cancellation.py`, N7's `utils/suspension_reactivation.py`).

## 3. Fix / remediation

**No money-movement logic touched.** Both changes are notifications wrapped
around existing, unmodified state transitions.

- **R43 — reset notice.** `utils/allowance_reset.py::run_allowance_reset_tick`
  now sends a best-effort push ("Corporate ride allowance reset") to the
  member immediately after a **non-rollover** reset's `apply_reset` call
  succeeds. Rollover allowances do not fire — their `used` counter is
  untouched by this reset (only the period dates roll forward), so nothing
  actually changed for the rider to be told about. Rate-limiting is free:
  the loop's existing compare-and-swap claim on `period_end` (replay-safety
  F8) already guarantees only the replica that wins the claim reaches the
  reset/notify code for a given allowance+period, so the notification fires
  at most once per period with no new DB column.
- **R44 — low/exhaustion notice.** `services/payment_service.py::
  settle_corporate` now calls a new helper, `_notify_allowance_threshold`,
  right after a ride settles (after the `payment_status="paid"` write, so a
  notification failure can never turn a successful settlement into an
  error). It compares the debit's `remaining_before`/`remaining_after`
  (values the settlement math already computes) and fires:
  - **"allowance used up"** when this debit crosses `remaining` from `> 0`
    to `<= 0`.
  - **"running low"** when this debit crosses `remaining/amount` from above
    20% to at-or-below 20% (without hitting zero).
  Both are one-shot by construction: once `remaining_after <= 0`, the next
  ride's `remaining_before` is already `<= 0` too, so the crossing condition
  cannot re-fire — same reasoning as R43's CAS-based dedup, applied here via
  the arithmetic itself instead of a new column. Unlimited allowances are
  skipped (no ceiling to warn about). Both notices use `priority="normal"` —
  reasoning in the code docstring: this isn't as time-critical as an
  offer-accept push, and `features.py`'s `"account"` tier is documented and
  reserved for a driver told their earning status changed, not a fit for a
  rider allowance dip.
- The 4xx blocks at booking time
  (`routes/rides/booking.py`'s `allowance_low` reason,
  `services/company_booking_service.py`'s `allowance_only` policy check,
  `services/corporate_policy_service.py`'s `allowed_source == "allowance_only"`
  rule) are **unchanged** — this fix adds advance warning, it does not
  remove or soften the block.

## 4. Risk & impact on existing functionality

- **Blast radius of `corporate_allowance_service.apply_reset`,
  `apply_ride_debit` and the RPC they wrap (`corporate_allowance_apply_delta`
  in migration 29/258/277): NOT MODIFIED.** Grepped every caller:
  - `apply_reset`: only `utils/allowance_reset.py` (this fix touches the
    caller after this call, not the call itself).
  - `apply_ride_debit`: only `services/payment_service.py::settle_corporate`
    (this fix touches the caller after this call, not the call itself).
  - No other module calls either function (`grep -rn "apply_reset\|
    apply_ride_debit" backend --include="*.py"` excluding tests).
- **`run_allowance_reset_tick` callers**: only `allowance_reset_loop`, spawned
  once from `backend/core/lifespan.py` as one of the 18 background loops.
  Isolated.
- **`settle_corporate` callers**: `routes/rides/payments.py` (rider-initiated
  `/process-payment`), `services/payment_service.py::auto_settle_guest_corporate`
  (guest auto-settle + the payment-retry sweep's replay of it). Both already
  exercised by the new/updated tests below; neither caller's own return-value
  contract changed (`PaymentResult` shape is identical).
- **`send_push_notification` itself: NOT MODIFIED.** This fix is purely two
  new call sites into the existing, already-shared function (used by dozens
  of other call sites per prior N5/N7 audits). No risk to any other consumer.
  Per its own docstring, every call also writes an in-app notification-inbox
  row as a side effect — pre-existing behavior, now additionally exercised
  from these two sites.
- **Existing tests that call `settle_corporate`/`process_payment` without
  mocking `send_push_notification`** would now execute the *real* function
  (which itself calls `db_supabase.insert_one` for the notification-inbox
  row) unless mocked. Found and fixed one such regression during
  development: `tests/test_corporate_ride_payment.py::
  test_company_allowance_splits_when_allowance_partial` asserted on the
  *last* `insert_one` call args, which shifted once a real (unmocked) push
  call added a further `insert_one`. Fixed by adding
  `send_push_notification` to both shared per-test-file dependency-mock
  helpers (`_mock_process_payment_deps`, `_settle_patches`) in that file, so
  every test in the file — old and new — now controls it explicitly. Grepped
  every other test file that calls into `settle_corporate`/corporate
  `process_payment` (`test_allowance_cap_fallback.py`,
  `test_corporate_allowance_cap_race.py`, `test_company_guest_booking.py`,
  `test_corporate_settle_section_spend.py`,
  `test_corporate_settle_suspended_audit_flag.py`, `test_guest_auto_settle.py`,
  `test_guest_corporate_receipt.py`) and ran them — all pass unmodified
  (their fixture scenarios don't happen to cross a threshold, so the real
  `send_push_notification` runs but degrades gracefully the same way
  production would if a push token is missing — see `features.py`'s
  documented best-effort behavior for `priority="normal"`).
- **Race/replica safety**: unchanged from the existing patterns — R43 rides
  on the pre-existing period CAS; R44 rides on `allowance_applied` only being
  `True` when `apply_ride_debit` actually succeeded under the RPC's row lock
  (an `allowance_cap_exceeded` contention retry, which reroutes the whole
  fare to the master wallet instead, correctly leaves `allowance_applied`
  `False` and skips notification — no new crossing genuinely occurred on
  this replica's view).

## 5. User-experience effect

**Rider-facing only**, and only for riders on a corporate work profile with a
non-unlimited allowance:
- After their allowance resets for a new period (fixed_recurring,
  non-rollover), they get a push: "Corporate ride allowance reset."
- After a ride that drops their remaining allowance to ≤20% (and it wasn't
  already ≤20%), they get: "Corporate ride allowance running low — $X.XX
  left."
- After a ride that exhausts their remaining allowance to $0, they get:
  "Corporate ride allowance used up."

None of this is visible **mid-ride** — both fire only at settlement
(ride-complete) or on the hourly reset tick, never during an in-progress
trip. This is **net-new, additive** behavior: previously nothing notified
the rider on either path, so there is no existing notification flow this
could regress or conflict with. The existing 4xx-at-booking experience for a
genuinely exhausted allowance under an `allowance_only` policy is unchanged.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/allowance_reset.py` | Import `send_push_notification` (dual-import pattern, both branches); after a successful non-rollover `apply_reset`, push the member a reset notice wrapped in its own try/except (`logger.warning` on failure) | R43 |
| `backend/services/payment_service.py` | New `_ALLOWANCE_LOW_THRESHOLD_RATIO` constant and `_notify_allowance_threshold` helper; called from `settle_corporate` right before its final success return, guarded by `allowance_applied` and non-unlimited type, wrapped in try/except (`logger.debug` on failure, matching this file's existing push-failure pattern) | R44 |
| `backend/tests/test_corporate_allowance_reset.py` | 3 new tests: reset fires the push with correct args, rollover does not fire, a push failure doesn't abort the tick's `processed` count | Prove R43 |
| `backend/tests/test_corporate_ride_payment.py` | Added `send_push_notification` to both shared dependency-mock helpers (`_mock_process_payment_deps`, `_settle_patches`); 6 new tests: exhausted-crossing fires, low-crossing fires, comfortably-above fires nothing, already-exhausted-before-this-ride fires nothing, unlimited fires nothing, a push failure doesn't fail settlement | Prove R44 + fix the pre-existing test that broke once the real function started firing |
| `ACTION_ITEMS.md` | Marked R43/R44 done under N15 with a dated sub-entry; N15's own checkbox left unchecked (R8/R19/R31/R33/R34/R35/R37/R38 remain open) | Tracking |

## 7. Before / after

```python
# Before — utils/allowance_reset.py, inside run_allowance_reset_tick's per-row loop
            if not r.get("rollover"):
                await apply_reset(
                    wallet_id=wallet["id"],
                    allowance_id=r["id"],
                    member_id=r["member_id"],
                    actor_user_id=None,
                    notes=f"period reset {new_start} -> {new_end}",
                )
            processed += 1
```

```python
# After
            if not r.get("rollover"):
                await apply_reset(
                    wallet_id=wallet["id"],
                    allowance_id=r["id"],
                    member_id=r["member_id"],
                    actor_user_id=None,
                    notes=f"period reset {new_start} -> {new_end}",
                )
                _member_user_id = member.get("user_id")
                if _member_user_id:
                    try:
                        await send_push_notification(
                            _member_user_id,
                            "Corporate ride allowance reset",
                            "Your company ride allowance has reset for the new period.",
                            data={"type": "corporate_allowance_reset"},
                            priority="normal",
                            target_app="rider",
                        )
                    except Exception as _notify_err:
                        logger.warning(
                            "allowance reset push failed for member %s: %s", r.get("member_id"), _notify_err
                        )
            processed += 1
```

```python
# Before — services/payment_service.py, end of settle_corporate
    await db_supabase.update_ride(
        ride_id,
        {
            "payment_status": "paid",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            **_tip_ride_update(ride, tip_amount),
        },
    )
    return PaymentResult(success=True, charged_amount=_money_str(total_charge))
```

```python
# After
    await db_supabase.update_ride(
        ride_id,
        {
            "payment_status": "paid",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            **_tip_ride_update(ride, tip_amount),
        },
    )

    if allowance_applied and allowance.get("type") != "unlimited":
        try:
            await _notify_allowance_threshold(
                membership=membership,
                allowance_amount=_d(str(allowance.get("amount") or 0)),
                remaining_before=remaining,
                remaining_after=remaining - allowance_debit,
            )
        except Exception as _notify_err:
            logger.debug(f"Allowance threshold push to rider failed: {_notify_err}")

    return PaymentResult(success=True, charged_amount=_money_str(total_charge))
```

## 8. Rollback plan

**`git revert`-safe.** Both changes are purely additive application code
(new imports + new best-effort side-effecting calls placed strictly *after*
the money-moving/state-changing work each function already did). No schema
change, no migration, no mutation of any already-applied DB state (no new
column, no new table). Reverting the commit(s) removes the two notification
call sites and restores the prior silent behavior exactly; the reset and
settlement logic they sit after is byte-for-byte unchanged either way, so a
revert cannot leave any live allowance/wallet data in an inconsistent state.
No feature flag was added: the failure mode of a bug here is a missing,
duplicated, or malformed push notification — never a wrong dollar amount,
never a broken reset, never a blocked ride. That asymmetry (notification-only
blast radius) is why a flag wasn't judged necessary per CLAUDE.md's gate 7.

## 9. Verification performed

- [x] Automated tests run (unit only — no integration/e2e tier exists for
  either module): `tests/test_corporate_allowance_reset.py` (7 tests, 3 new),
  `tests/test_c_allowance_reset_atomic.py` (2, unmodified, still green),
  `tests/test_allowance_reset_coverage.py` (7, unmodified, still green),
  `tests/test_corporate_ride_payment.py` (24 tests, 6 new + 1 pre-existing
  fixed) — all green. Broader sweep: `pytest -k "corporate or allowance or
  payment_service or settle"` → **970 passed, 3 skipped**, no regressions.
- [x] `ruff check` and `ruff format --check` on all 4 touched Python files:
  clean (the repo-wide `ruff check .` / `ruff format --check .` runs surface
  ~38 pre-existing lint findings and 118 pre-existing unformatted files
  elsewhere in the tree, unrelated to this change — confirmed by scoping the
  check to only the files this PR touches).
- [x] Blast-radius grep performed: `apply_reset`/`apply_ride_debit` callers
  (see §4), `settle_corporate` callers (see §4), every test file reaching
  `settle_corporate`/corporate `process_payment` (see §4), all named
  explicitly.
- [x] Reviewed against relevant CLAUDE.md conventions: money arithmetic
  (`_d`/`_round`/`_money_str` used for the threshold math; the RPC/debit
  amounts themselves are untouched), "do not silently swallow errors" (the
  notification try/except is a deliberate, documented best-effort exception
  — not a masked DB/auth/payment error; the underlying settlement/reset
  logic still surfaces its own errors exactly as before), background-loop
  replay-safety (R43 piggybacks on the existing CAS claim), corporate
  billing layer note ("sits on top of the consumer ride product without
  modifying ride/driver logic" — no ride/driver logic touched).
- [ ] Manual repro in staging: not performed (no staging environment
  available in this session).
- [ ] Feature-flagged: not applicable — see §8.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`, no data cleanup
  needed — see §8).
- [x] Blast radius is stated, not assumed: every caller of every touched
  function is named in §4, including the one pre-existing test regression
  found and fixed during development.
- [x] No silent behavior change to an already-shipped flow: both touched
  flows (`run_allowance_reset_tick`, `settle_corporate`) previously did
  nothing user-visible on this axis, so there is no existing UX to have
  silently changed — the UX field states this explicitly.

## What was NOT verified

- **No live FCM/Expo push was actually sent.** All verification is against
  `unittest.mock.AsyncMock`-patched `send_push_notification` calls; the real
  device-delivery path was not exercised.
- **No real production or staging traffic** — only local unit tests were run
  in this session.
- **The 20% "running low" threshold is not sourced from a product/UX
  decision** — it's a reasonable first-cut default, explicitly called out as
  such in the code comment and in ACTION_ITEMS.md, not a validated number.
- **Multi-replica concurrency for R44 was not exercised against a live
  Postgres row lock** — the `allowance_cap_exceeded` contention-retry path
  that skips notification is covered by existing, unmodified test coverage
  of that branch's control flow, but not by a real concurrent-replica test
  against a live DB.
- **No visual regression tooling exists for this change** — not applicable
  here (backend-only, no admin-dashboard/rider-app/driver-app files
  modified, so no `npm run build` was run or needed).
- **Copy was not reviewed by a human/product owner.**
- **In-app Notifications-inbox rendering of the new `type` values**
  (`corporate_allowance_reset`, `corporate_allowance_low`,
  `corporate_allowance_exhausted`) was not verified against the rider-app's
  notification-list UI — only that `send_push_notification` (which writes
  that inbox row as an existing, unmodified side effect) is called with the
  right arguments.
