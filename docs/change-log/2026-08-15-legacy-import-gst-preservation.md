# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-15 |
| Author | Claude (automated, on behalf of vikas@ngitservices.com) |
| Surface(s) | backend (`services/booking_import_service.py`) — future legacy imports only |
| Domain (Sentry tag) | payments |
| PR / commit link | (filled in on PR creation) |
| Related issue or gap ID | Follow-up to `docs/change-log/2026-08-15-legacy-payout-correction-plan.md`'s tax-field finding |

## 1. Issue / gap identified

Confirmed 2026-08-15 against all 186 already-migrated legacy rides: `tax_amount` is populated from `bookings.csv`'s `gst` column, which is exactly `commission_gst_amount` — GST on Spinr's own small platform commission fee, not GST on the rider-facing fare. A separate, larger, fare-scaling `payout_gst_amount` column exists in the source export and was never read by the importer at all — silently dropped on every import.

## 2. Root cause

`build_plan()` parses `gst = parse_money(b.get("gst", ""))` and writes it straight to `tax_amount`/`tax_breakdown`, with no awareness that the source export splits GST into two distinct, non-interchangeable components (commission-side vs. payout-side). The field name `gst` reads as "the tax," which is what led to the original mismap.

## 3. Fix / remediation

**Deliberately narrow scope** — this fix does NOT change what `tax_amount` is set to. Whether the historical rider-facing GST for the 186 already-migrated rows should be backfilled, estimated, or left as a documented gap is a business/legal decision this code has no basis to make (see the linked plan doc's §1.3 addendum). What this PR does:

1. **Preserves the previously-dropped `payout_gst_amount`** as `legacy_import_metadata->>'old_payout_gst_amount'` on every future import — so that decision, whenever it's made, doesn't need to re-derive the number from the CSV again.
2. **Adds the missing `rate: 5.0`** to `tax_breakdown.GST` — Canada's actual GST rate, factually correct regardless of which base it's applied to. Fixes a cosmetic gap where the receipt line rendered as "GST" instead of "GST (5%)".
3. **Documents, in code, three previously-unverified-in-comments facts**, all confirmed against the source export on 2026-08-15 rather than assumed:
   - `tax_amount` is commission-GST, not fare-GST (the core finding above)
   - The permanent distance/time fare-split limitation (the old export never recorded one; any per-component analytics on legacy rows will show $0 there, which is expected, not a bug)
   - `surge_multiplier: 1.0` is verified correct for every historical row — the old app's surge-schedule config was present but never once actually configured/used (empty time slots, empty surcharge history)

## 4. Risk & impact on existing functionality

- **Blast radius**: `booking_import_service.py`'s `build_plan()` only. Grepped for other callers of `parse_money(b.get("gst"...`, `payout_gst_amount`, and `legacy_import_metadata` construction in this file — none exist elsewhere; the admin route (`routes/admin/booking_import.py`) and the CLI wrapper both call into this same `build_plan()`, no separate code path to also fix.
- **Does not touch the 186 already-migrated rows** — this only changes what happens on the *next* import batch. No UPDATE statement, no data migration, nothing applied to production data in this PR.
- **`tax_amount`'s value is unchanged** — every existing downstream consumer (admin dashboards, `admin_ride_money_rollup`, `admin_payouts_overview_aggregates`, receipt rendering) sees the exact same number as before for both old and new imports. Only the `rate` key (additive to a dict) and a new `legacy_import_metadata` key (additive) are new.
- **`test_fees_and_tax_land_in_their_own_columns`** (existing test) asserted the old `tax_breakdown` shape without a `rate` key — updated to match; this was the only assertion in the repo depending on the old shape (grepped `tax_breakdown ==` across `backend/tests/`).

## 5. User-experience effect

Rider-facing: for any *future* legacy-batch import, the receipt's GST line will show "GST (5%)" instead of "GST" — cosmetic only, dollar amount unchanged. No effect on any already-shipped receipt (186 existing rows untouched). No effect on any native (non-imported) ride.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/booking_import_service.py` | Parses and preserves `payout_gst_amount` raw (not merged into `tax_amount`); adds `rate: 5.0` to `tax_breakdown.GST`; adds 3 documentation comments confirming previously-assumed facts | Stop the tax-field mismap from recurring silently on every future batch; preserve the dropped data point for the pending business decision |
| `backend/tests/test_booking_import_service.py` | Updated one assertion for the new `tax_breakdown` shape; added a test pinning that `payout_gst_amount` is preserved raw and NOT merged into `tax_amount` | Lock in the "preserve, don't guess" behavior so a future edit can't silently start merging the two GST components without a test failing |

## 7. Before / after

```python
# Before
gst = parse_money(b.get("gst", ""))
...
"tax_amount": float(gst),
"tax_breakdown": {"GST": {"amount": float(gst)}} if gst > ZERO else {},
...
"legacy_import_metadata": {
    "batch": batch, "source": IMPORT_SOURCE, "old_booking_id": old_id,
    "old_booking_code": code, "old_customer_id": ..., "old_driver_id": ...,
    "imported_at": now_iso,
},

# After
gst = parse_money(b.get("gst", ""))
payout_gst_amount = parse_money(b.get("payout_gst_amount", ""))
...
"tax_amount": float(gst),                                              # unchanged value
"tax_breakdown": {"GST": {"rate": 5.0, "amount": float(gst)}} if gst > ZERO else {},  # + rate
...
"legacy_import_metadata": {
    "batch": batch, "source": IMPORT_SOURCE, "old_booking_id": old_id,
    "old_booking_code": code, "old_customer_id": ..., "old_driver_id": ...,
    "imported_at": now_iso,
    "old_payout_gst_amount": float(payout_gst_amount),                 # + preserved raw
},
```

## 8. Rollback plan

`git revert` — no data written, no migration, purely additive fields on future imports. Nothing to clean up in production even after revert, since this PR alone never runs against real data (it changes what the *next* import batch would produce, and no batch has been run with this code yet).

## 9. Verification performed

- [x] Ran `pytest backend/tests/test_booking_import_service.py` — all 40 tests pass, including the new preservation test and the updated `tax_breakdown` shape assertion
- [x] Grepped for every other consumer of `tax_breakdown`'s shape and `legacy_import_metadata`'s keys across `backend/` — no other code depends on the old shape breaking
- [x] Did not run the full backend suite in this pass (isolated, single-file change, no cross-module surface touched) — recommend CI run it regardless
- [ ] Not a `rider-app`/`driver-app`/`admin-dashboard` change — no `npm run build` applicable

## What was NOT verified

- **Whether/how to correct the 186 already-migrated rows** — explicitly out of scope, flagged as a business/legal decision in `docs/change-log/2026-08-15-legacy-payout-correction-plan.md` §1.3 addendum, not resolved here.
- **Whether `payout_gst_amount`, once preserved, is even the right number to eventually add** — the earlier finding in this session flagged that assuming `gst + payout_gst_amount` equals "the true historical GST" is itself an unverified assumption, not a fact the source data establishes. This PR only preserves the number; it does not decide what to do with it.
