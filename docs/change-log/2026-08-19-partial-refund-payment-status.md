# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-19 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | (this branch) |
| Related issue or gap ID | Fare & Payout Audit finding #2 (2026-08-19, published artifact) |

## 1. Issue / gap identified

`charge.refunded`'s webhook handler set `payment_status = "refunded"` on **any** Stripe refund — partial or full — based only on `amount_refunded`, with no comparison against the original charge amount. A partial refund (e.g. a $5 goodwill refund on a $40 ride) was recorded identically to a full one.

## 2. Root cause

The handler read `charge.amount_refunded` in isolation and never checked it against `charge.amount` (the original charge total) or Stripe's own `charge.refunded` boolean (which Stripe sets `true` only once cumulative refunds reach the full amount). There was no distinct status for "partially refunded" in the application's `payment_status` vocabulary.

## 3. Fix / remediation

The webhook handler now classifies a refund as full or partial — preferring Stripe's own `refunded` boolean, falling back to `amount_refunded >= amount` when that boolean isn't present, and defaulting to the prior full-refund behavior only when neither field is present (real Stripe payloads always send both; this fallback only matters for incomplete synthetic test data). A partial refund is now recorded as `payment_status = "partially_refunded"`, a new, additive status value — `"refunded"` still means what it always meant: fully refunded, $0 net collected.

The two other places that treat `"refunded"` as terminal (the `invoice.paid` re-settlement guard, and both of `admin/rides.py`'s payable-invoice terminal-state checks) were updated to treat `"partially_refunded"` as terminal too, for the same underlying reason each already documented for `"refunded"` — re-settling or re-invoicing over an existing partial refund is unsafe.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to the refund-status write path and its 3 known readers.** Grepped every `payment_status` check against `"refunded"` in `backend/` (`webhooks.py:263`, `admin/rides.py:1545`, `admin/rides.py:1718` — all three updated consistently) and confirmed no other backend file, and no rider-app/driver-app/admin-dashboard file, branches on `payment_status == "refunded"`.
- `admin-dashboard`'s dispute stats (`total_refunded`) sum from the separate `disputes.refund_amount`/`disputes.status` fields, not `rides.payment_status` — unaffected.
- **Did not** make the payable-invoice endpoint (`admin/rides.py`'s `send-invoice`) able to collect the un-refunded remainder on a partially-refunded ride — that endpoint invoices the ride's full original total (`grand_total + tip_amount`), not a remainder-aware amount, so extending it to `partially_refunded` rides today would double-bill the portion already correctly collected. Left blocked with an explicit comment explaining why, rather than silently allowing an over-charge. A remainder-aware invoicing flow is a separate, larger change if that need comes up.
- No interaction with the 16 background loops, the ride state machine, or wallet deltas — this only changes what string gets written to `rides.payment_status` on a refund and which strings 3 existing guards recognize as terminal.
- `record_refund_event`'s ledger/tax-reversal math is unchanged — it already recorded the true refunded amount regardless of full/partial classification.

## 5. User-experience effect

None directly visible to riders or drivers — the rider-facing refund push notification (`"Your refund of $X.XX has been processed..."`) already stated the actual dollar amount, not an implied full-refund message, so no copy change was needed. Internal-admin-facing only: a partially-refunded ride's payment status now reads accurately in any admin view/report that surfaces it, instead of misleadingly showing `"refunded"`.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/webhooks.py` | `charge.refunded` handler now classifies full vs. partial and writes `"partially_refunded"` for the latter; `invoice.paid` terminal-state guard now also recognizes `"partially_refunded"` | Fix the core mislabeling; close the same erasure risk on the invoice-paid path |
| `backend/routes/admin/rides.py` | Both terminal-payment-state checks in the payable-invoice flow now also block `"partially_refunded"`, with a comment explaining the remainder-aware-amount limitation | Prevent the invoice endpoint from double-billing an already-partially-collected fare |
| `backend/tests/test_routes_webhooks_coverage.py` | Added a partial-refund regression test and a cumulative-full-refund-via-`refunded=True` test | Cover the new branch and the full-refund boundary condition |
| `backend/tests/test_admin_rides_coverage.py` | Added `"partially_refunded"` to the existing terminal-status parametrized test | Cover the new terminal-state case with the existing test pattern |

## 7. Before / after

```python
# Before
refunded_cents = int(charge.get("amount_refunded", 0))
...
await db_supabase.update_one("rides", {"id": ride_id}, {
    "payment_status": "refunded",   # set on ANY refund amount
    "refund_amount": str(refunded_amount),
})
```

```python
# After
refunded_cents = int(charge.get("amount_refunded", 0))
...
if isinstance(charge.get("refunded"), bool):
    is_full_refund = charge["refunded"]
elif charge.get("amount") is not None:
    is_full_refund = refunded_cents >= int(charge["amount"])
else:
    is_full_refund = True
new_payment_status = "refunded" if is_full_refund else "partially_refunded"
await db_supabase.update_one("rides", {"id": ride_id}, {
    "payment_status": new_payment_status,
    "refund_amount": str(refunded_amount),
})
```

## 8. Rollback plan

`git-revert-safe`. No migration involved — `payment_status` has no DB-level CHECK constraint (confirmed by grepping `backend/migrations/` for one; it's an application-level free-text column), so `"partially_refunded"` is a purely additive value with no schema change to unwind. A revert simply stops writing/recognizing that value; any ride rows already written with `payment_status = "partially_refunded"` would need a one-time manual reclassification back to `"refunded"` if a revert were ever actually needed, but the code itself reverts cleanly with a plain `git revert`.

## 9. Verification performed

- [x] Automated tests run: `pytest tests/test_routes_webhooks_coverage.py tests/test_webhooks_coverage_gap.py tests/test_webhooks_main.py tests/test_admin_rides_coverage.py tests/test_admin_extended.py` — 286 passed, 0 failed, 0 regressions.
- [x] `ruff check` on all 4 changed files — clean.
- [x] Blast-radius grep performed: every `payment_status`-against-`"refunded"` check across `backend/` (3 call sites, all updated) and confirmed no rider-app/driver-app consumer branches on this value; confirmed admin-dashboard's refund total is sourced from a different table entirely.
- [x] Reviewed against `CLAUDE.md`'s "Do not silently swallow errors" / additive-over-destructive convention — new status is additive, not a repurposing of an existing column's meaning.
- [ ] Not run against a real Stripe webhook or live Supabase — mocked/unit-tested only, consistent with this repo's existing webhook test conventions (all sibling tests in this file are structured the same way).
- Feature flag: not applicable — this is a bug fix to existing webhook logic, not new user-visible behavior; not staged behind a flag.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no migration)
- [x] Blast radius is stated: isolated to 3 backend call sites, confirmed no other consumer
- [x] No silent behavior change to an already-shipped flow without the UX field filled in — UX impact is internal-admin-only and stated above

## What was NOT verified

- Not exercised against a real Stripe test-mode webhook delivery — only against mocked event payloads matching Stripe's documented `charge.refunded` shape (`amount`, `amount_refunded`, `refunded`).
- The proportional tax-reversal math in `record_refund_event` (a second partial refund on the same ride, a refund exceeding what's left after a prior one) was flagged as out-of-scope by the originating audit and remains unaddressed here — this fix only corrects the `payment_status` classification, not the tax-reversal calculation.
- Did not build a remainder-aware re-invoicing flow for partially-refunded rides — deliberately left blocked with a comment rather than attempting that larger change in this fix.
