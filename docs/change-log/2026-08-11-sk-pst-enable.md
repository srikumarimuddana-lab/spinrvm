# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-11 |
| Author | Claude (automated, on behalf of vikas@ngitservices.com) |
| Surface(s) | backend (fare calculation), production data (`service_areas`) |
| Domain (Sentry tag) | payments |
| PR / commit link | (filled in on PR creation, for the code-comment fix; the data change itself was applied directly, see §8) |
| Related issue or gap ID | P1-B, `docs/audit/2026-08-11-driver-rider-migration-audit.md` (surfaced during investigation; the actual finding is broader than the audit anticipated) |

## 1. Issue / gap identified

While investigating the audit's P1-B finding ("legacy PST likely folded silently into the fare line on imported historical receipts"), tracing the current fare engine surfaced a live, ongoing issue: `features.py`'s `calculate_all_fees` carried an explicit code comment claiming *"Saskatchewan rideshare is GST 5% only — PST does NOT apply to rideshare here"*, and the live `service_areas` rows for Saskatoon, Saskatoon Airport, Regina, and Regina Airport all had `pst_enabled: false`. This directly contradicts `.claude/context/regulatory-sk.md`'s tax-display rule (*"PST (6%, SK) on fare where applicable — ride-share currently PST-applicable in SK"*) and CLAUDE.md's regulatory section.

**User confirmed (2026-08-11): PST does apply to Saskatchewan rideshare and the code was wrong.** This means every completed ride in Saskatoon/Regina up to this point was under-collecting PST by 6% of the taxable fare — a live, ongoing tax-compliance gap, not a legacy-import artifact.

## 2. Root cause

Unconfirmed — the code comment reads as a deliberate, confident assertion ("PST does NOT apply... here"), not an oversight or a TODO, so this wasn't simply an unset default. Whoever wrote it may have been working from an earlier or incorrect understanding of SK's PST-on-rideshare rules, or the rule may have changed after the code was written. Not investigated further — the user's determination that PST applies now takes precedence regardless of how the prior state came to be.

## 3. Fix / remediation

- **Immediate (2026-08-11, applied directly to production per explicit user confirmation)**: `service_areas.pst_enabled` set to `true` and `pst_rate` set to `6` for the 4 real Saskatchewan rows (Saskatoon, Saskatoon Airport, Regina, Regina Airport). The non-Saskatchewan rows in the same table ("riyadh", "riyadh airport" — evidently test/other-market data) were **not** touched.
- Effective **immediately for new fare quotes** — `calculate_all_fees` reads `pst_enabled`/`pst_rate` live from `service_areas` on every quote, no code deploy needed for this part.
- **No backdating** — already-completed rides keep their original (GST-only) `tax_amount`/`tax_breakdown`; only rides quoted from this point forward include PST. This was an explicit choice, not an oversight (see §8 for what that means for prior under-collection).
- **Code fix (this PR)**: corrected the now-factually-wrong comment in `features.py` so a future engineer doesn't read it and "fix" PST back off. No logic change — the `pst_enabled`/`pst_rate` per-area lookup was already correctly wired; only the (wrong) comment and the (wrong) data were the problem.
- Added 3 new unit tests (`test_calculate_all_fees_tax.py`) directly exercising the GST-only, GST+PST, and HST-override branches of the tax calculation — none existed before; every prior test that touched this function only mocked it as a black box.

## 4. Risk & impact on existing functionality

- **Blast radius**: `service_areas.pst_enabled`/`pst_rate` feed exactly one consumer — `features.calculate_all_fees`'s ride-fare tax calculation (confirmed by grep). **Not** shared with Spinr Pass subscription tax, which reads a completely separate `service_areas.subscription_tax_config` JSONB column (migration 185) — verified no overlap before making this change.
- Every new ride fare quote in Saskatoon/Regina from this point forward is **6% higher** on the taxable portion (fare + fees) than it was a moment before. This is a real, immediate, user-visible price change — not a bug fix in the sense of "restoring intended behavior silently"; riders will see a materially different total.
- Driver earnings (`base_fare`/`distance_fare`/`time_fare`) are unaffected — PST is a tax on top of the fare, not a change to driver payout math. Confirmed by reading `calculate_all_fees`: `tax_amount`/`tax_breakdown` are computed independently of and additively to the fare/fee subtotal that funds `driver_earnings`.
- Surge pricing, promo codes, and corporate billing were not touched and should apply exactly as before, with PST now correctly layered into the final `grand_total` the same way GST already was.

## 5. User-experience effect

**Rider-facing, immediate, live.** A rider booking in Saskatoon or Regina from this point forward sees a higher total fare (taxable amount × 6% more) than they would have a moment earlier, and the receipt now shows a separate PST line alongside GST (per CLAUDE.md's "must appear as separate line items" rule — confirmed `tax_breakdown` already supports multiple named entries; PST simply wasn't one of them before). This is a mid-session-relevant change: a rider who had an open fare estimate before this change and re-quotes after it will see a different (higher) number.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `service_areas` table (production data, not a file) | `pst_enabled: false→true`, `pst_rate: 0→6` for Saskatoon, Saskatoon Airport, Regina, Regina Airport | Enable PST collection per user-confirmed regulatory determination |
| `backend/features.py` | Corrected the stale/wrong comment above the tax-calculation block | Prevent a future engineer from "fixing" PST back off based on outdated reasoning |
| `backend/tests/test_calculate_all_fees_tax.py` | New — 3 tests for the GST-only, GST+PST, and HST-override tax branches | No direct test of this logic existed before; pins the fix and prevents silent regression |

## 7. Before / after

```python
# Before (features.py comment — factually wrong, now corrected)
# Calculate taxes — Decimal end-to-end so the receipt line items
# reconcile to the cent. Saskatchewan rideshare is GST 5% only — PST does
# NOT apply to rideshare here, so pst_enabled defaults off. ...
```

```sql
-- Before (production service_areas)
-- Saskatoon: pst_enabled=false, pst_rate=0
-- Regina:    pst_enabled=false, pst_rate=0

-- After
-- Saskatoon: pst_enabled=true, pst_rate=6
-- Regina:    pst_enabled=true, pst_rate=6
-- (+ Saskatoon Airport, Regina Airport identically)
```

## 8. Rollback plan

- **Code (comment fix)**: `git revert` — no logic changed, purely a comment correction.
- **Data (PST enablement)**: revert by setting `pst_enabled = false` on the same 4 `service_areas` rows (or restore `pst_rate = 0`) via the admin dashboard's Service Areas settings or a direct Supabase update — this is the existing `app_settings`-in-DB-style config pattern CLAUDE.md already prefers, no migration or redeploy needed either way.
- **What a rollback does NOT undo**: any ride quoted/completed while PST was enabled keeps its PST charge — reverting the toggle only stops new quotes from including it, exactly mirroring how enabling it doesn't retroactively touch prior rides. If PST turns out to have been enabled in error, the affected rides' actual PST collections would need a separate, explicit remediation (refund or credit), not just a toggle flip.

## 9. Verification performed

- [x] Confirmed the `pst_enabled`/`pst_rate` change only affects ride-fare tax, not subscription tax (grep + code read of both consumers, `features.calculate_all_fees` vs. `routes/admin/subscriptions.py`'s separate `subscription_tax_config`)
- [x] Verified via direct Supabase query that only the 4 real Saskatchewan `service_areas` rows were updated (`UPDATE ... RETURNING` showed exactly 4 rows, all correctly named)
- [x] New unit tests pin the GST-only, GST+PST-combined, and HST-override tax-calculation branches
- [x] Full backend test suite run after the change (see PR)
- [ ] Did not verify against a live fare-estimate API call with a real Saskatoon pickup coordinate — verified at the `calculate_all_fees` unit level instead, and via direct inspection of the `service_areas` config the function reads at request time
- [ ] Did not check whether any current in-flight ride (already quoted, not yet completed) needs special handling — assumed "effective for new quotes only" fully covers this since fare is computed fresh at estimate/booking time, not carried forward from an earlier session

## What was NOT verified

- Whether Saskatchewan's actual PST rate is exactly 6% with no reduced/exempt categories for ride-share specifically — took the rate from `.claude/context/regulatory-sk.md`'s existing documented figure and the user's confirmation, not from a fresh regulatory-authority lookup.
- Whether any historical remediation (refunding/crediting the gap for past under-collected PST) is needed or intended — explicitly out of scope per the user's "no backdating" instruction, but flagged here so it isn't silently assumed resolved.
- No visual verification of the rider-app receipt UI actually rendering a new "PST" line correctly — verified only that the backend `tax_breakdown` payload now includes it; did not screenshot the rendered receipt.
