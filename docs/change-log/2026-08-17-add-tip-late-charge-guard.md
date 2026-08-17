# Change Impact & Risk Log — `add_tip` payment-status guard

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-17 |
| Author | Claude Code (session) |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | (filled in on PR creation) |
| Related issue or gap ID | Finding 1, `docs/proposals/2026-08-17-tip-capture-stripe-cost-minimization-strategy.md` |

## 1. Issue / gap identified

`POST /rides/{id}/tip` (`add_tip`) can be called on any `status == completed` ride with no
`payment_status` check. When it fires after the ride is already `payment_status == 'paid'`, it
credits `tip_amount`/`driver_earnings` (and the T4A-feeding earnings snapshot) without ever
charging the rider — the driver is paid for money that was never collected.

## 2. Root cause

`add_tip`'s only tip guard is "has a tip already been recorded" (R-P1-20, one tip per ride) — it
never checks whether the ride's fare settlement already ran. This is reachable in production, not
theoretical: the rider-app's offline action queue (`rider-app/store/rideStore.ts:648-662`) replays
a queued `tip` request on reconnect with no time bound, and tipping from ride history days later
is an intentionally-supported product feature (`backend/utils/driver_statement_job.py:48-54`
documents the 2-day statement grace window specifically to let these late tips land). Once
`payment_status` flips to `'paid'`, the ride's booking-time hold or settlement charge is already
captured — Stripe forbids capturing the same PaymentIntent twice — so the original code path had
no way to actually collect the money and simply didn't try.

## 3. Fix / remediation

`add_tip` now checks `ride.payment_status` before touching the DB:
- **Not yet paid** (the common/majority path — tip added before or during normal settlement):
  unchanged, byte-for-byte the same behavior as before this PR.
- **Already paid, card ride**: charges a fresh, tip-amount-only Stripe PaymentIntent via a new
  `services/payment_service.py::charge_late_tip` helper (mirrors the existing over-buffer-tip
  fresh-charge pattern already used at settlement) and writes a `financial_events` ledger row
  tagged `source="late_tip"` (not `"process_payment"`, so the 7-year ledger doesn't mislabel a
  tip-only charge as a fare settlement). `tip_amount`/`driver_earnings` are only persisted when
  the charge actually succeeds.
- **Already paid, wallet or company_allowance ride**: refused with a clear `400` — there is no
  equivalent "debit the delta after the fact" mechanism for those payment methods yet (building
  one is out of scope for this fix; see §4). Refusing loudly is strictly safer than the prior
  silent no-op.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** Three files touched, all in the `add_tip` call chain
  (`routes/rides/payments.py::add_tip`, its `_deps.py` re-export, and the new
  `payment_service.py::charge_late_tip` helper). No other route or background loop calls
  `add_tip` or the new helper.
- **`record_payment_event` / `_charge_event_metadata` signature change**: added an optional,
  keyword-only `source: str = "process_payment"` parameter, defaulting to the exact previous
  behavior. Grepped every call site (`services/payment_service.py` ×2, `routes/webhooks.py` ×2,
  and 4 test call sites) — all pass `ride=`/`tip_amount=` as keywords or omit them entirely, none
  pass a positional 3rd/4th argument, so this is additive and non-breaking.
- **`utils/driver_statement_job.py`'s late-tip grace window**: unaffected. That job keys off
  `ride_completed_at` and re-scans for tips landing after a period closes; this fix does not
  change *when* a tip can be added, only what happens (charge vs. refuse) once it lands on an
  already-paid ride. A successful late-card-tip charge still updates `tip_amount`/
  `driver_earnings` exactly as before, so the statement job's read of those fields is unchanged.
- **`utils/stripe_reconcile.py`**: its `_expected_capture_cents`/processing-ride healer path is
  untouched — it operates on rides stuck in `'processing'`, not on already-`'paid'` rides, so it
  has no interaction with this fix either direction.
- **Idempotency**: the existing `@idempotent_endpoint(scope="ride_tip")` decorator on the route
  is unchanged and still applies. On top of it, `charge_ride`'s own Stripe idempotency key
  (`ride-charge-{ride_id}-{amount_cents}-{payment_method_id}`) prevents a double Stripe charge on
  retry, and the pre-existing `ERR_TIP_DUPLICATE` guard (unchanged, still runs first) prevents a
  second `/tip` call from reaching the new charge path at all once a tip is recorded.
- **Not touching**: wallet/`company_allowance` late-tip collection is *refused*, not *fixed* — the
  same underlying gap exists for those payment methods (a late tip there still can't be collected),
  but it's now a loud, correct `400` instead of a silent, incorrect success. Building real
  wallet/corporate debit-after-the-fact collection needs its own design (which ledger function to
  reuse, insufficient-balance handling, `corporate_wallet_apply_delta` row-locking) and is
  deliberately out of scope here — flagged rather than silently left for a follow-up.

## 5. User-experience effect

- **Rider, card, late tip**: previously saw a silent "tip added" success with no charge. Now sees
  either the same success (charge succeeded) or a clear payment error (card declined / needs
  re-verification) — this IS a visible behavior change, but it corrects a bug where the app
  falsely reported success. Not visible mid-ride (only reachable after a ride is already
  completed and paid).
- **Rider, wallet/corporate, late tip**: previously saw a silent "tip added" success. Now sees a
  `400` with "Adding a tip after payment isn't supported yet for this payment method. Please
  contact support." This is a regression in apparent functionality (a button that used to "work"
  now shows an error) but the prior "success" was never real — the driver was credited, the rider
  was never charged.
- **Driver**: no visible change on the success path. On a wallet/corporate late-tip refusal, the
  driver will no longer see a phantom "you got a tip!" notification for money that was never
  collected — this is a correction, not a new gap.
- Not flagged behind `app_settings`: considered, but gating a money-undercollection bugfix behind
  a default-off flag would mean the leak continues silently until someone remembers to flip it.
  The fix is narrow (only the late-tip edge case, not the primary settlement flow) and covered by
  15 new/existing targeted tests plus the full 838-test payments/tip/ledger regression pass (see
  §9), so shipping directly was judged the safer choice over shipping dark.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/payment_service.py` | Added `charge_late_tip()`; added optional `source` kwarg to `_charge_event_metadata()`/`record_payment_event()` | Real Stripe charge for a card tip added after settlement; accurate ledger labeling |
| `backend/routes/rides/_deps.py` | Re-export `charge_late_tip` from `payment_service` | Makes the helper available to the split `routes/rides/` package, same pattern as `settle_card` |
| `backend/routes/rides/payments.py` | `add_tip` now branches on `payment_status`/`payment_method` when the ride is already paid | Closes Finding 1 |
| `backend/tests/test_charge_late_tip.py` | New — unit tests for `charge_late_tip` (success, declined, requires_action, unconfigured ×2, generic failure) | Direct coverage of the new helper's branches |
| `backend/tests/test_rides_payments_coverage.py` | New tests for `add_tip`'s paid-card/paid-wallet/paid-corporate/not-yet-paid branches | Endpoint-level coverage of the guard |

## 7. Before / after

```python
# Before (routes/rides/payments.py::add_tip, abridged)
existing_tip = _d(ride.get("tip_amount") or 0)
if existing_tip > 0:
    raise HTTPException(status_code=400, detail="ERR_TIP_DUPLICATE")

existing_earnings = _d(ride.get("driver_earnings") or 0)
new_tip = _round(existing_tip + tip_amount)
new_driver_earnings = _round(existing_earnings + tip_amount)
# ... straight to persisting tip_amount/driver_earnings, regardless of
# whether the rider was ever actually charged for it.
```

```python
# After (abridged)
existing_tip = _d(ride.get("tip_amount") or 0)
if existing_tip > 0:
    raise HTTPException(status_code=400, detail="ERR_TIP_DUPLICATE")

if (ride.get("payment_status") or "").lower() == "paid":
    payment_method = (ride.get("payment_method") or "card").lower()
    if payment_method != "card":
        raise HTTPException(status_code=400, detail="Adding a tip after payment isn't "
                             "supported yet for this payment method. Please contact support.")
    charge_result = await charge_late_tip(ride, ride_id, current_user["id"], tip_amount)
    if not charge_result.success:
        raise HTTPException(status_code=charge_result.status_code, detail=...)
    # only now do we fall through to persisting tip_amount/driver_earnings

existing_earnings = _d(ride.get("driver_earnings") or 0)
new_tip = _round(existing_tip + tip_amount)
new_driver_earnings = _round(existing_earnings + tip_amount)
```

## 8. Rollback plan

**`git-revert-safe`.** No migration, no schema change, no backfill, and nothing about this fix
retroactively touches already-recorded `financial_events` rows or already-completed Stripe
charges. A `git revert` of this PR restores the exact prior code path (silent no-op on a late
tip) — safe to do because reverting doesn't undo any money movement this PR caused; it only stops
the *new*, correct charging behavior going forward. If a revert is ever needed, no data-level
remediation is required.

## 9. Verification performed

- [x] Automated tests run: `pytest tests/test_charge_late_tip.py tests/test_rides_payments_coverage.py -q --no-cov` → **27 passed**. Broader regression sweep `pytest tests/ -k "tip or payment or settle_card or webhooks or ledger" -q --no-cov` → **838 passed, 1 skipped** (the skip is pre-existing and unrelated), **0 failed**.
- [x] `ruff check` on all 5 changed/added files → clean, no findings.
- [ ] Manual repro in staging — **not performed** (no staging/Supabase access in this session; see "What was NOT verified" below).
- [x] Blast-radius grep performed: every caller of `record_payment_event`/`_charge_event_metadata` (2 in `payment_service.py`, 2 in `routes/webhooks.py`, 4 in tests), every caller of `add_tip`'s route (rider-app `walletStore.ts`, `rideStore.ts`'s offline queue), and `driver_statement_job.py`'s documented assumption about `add_tip`'s late-tip contract.
- [x] Reviewed against CLAUDE.md conventions: Decimal-only money arithmetic (all amounts flow through existing `_d`/`_round`/`_f`/`ledger_service.to_cents` helpers, no new float arithmetic), Stripe idempotency (reuses `charge_ride`'s existing idempotency-key scheme, no new Stripe call site bypasses it), "do not silently swallow errors" (every failure branch returns a loud, typed error instead of a generic fallback).
- [ ] Feature-flagged — deliberately not; see §5 for the reasoning.

## What was NOT verified

- No manual/staging repro against a real Supabase + Stripe test-mode account — this session has
  no live environment access. The unit/integration tests mock Stripe and the DB throughout.
- No `npm run build` / production build was run — this is a backend-only (Python) change; no
  `admin-dashboard`/`rider-app`/`driver-app` build is implicated.
- Wallet/`company_allowance` late-tip collection is refused, not implemented — explicitly a
  follow-up, not verified because it isn't built (see §4).
- Did not verify against real production data whether the late-tip path (offline-queue replay,
  or tipping from ride history) fires often enough to matter at scale — no production DB access
  in this session. The fix closes the gap regardless of frequency; sizing the actual dollar
  impact would need a query against `financial_events`/`rides` in a real environment.
