# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-16 |
| Author | Claude (automated, on behalf of vikas@ngitservices.com) |
| Surface(s) | Supabase production data only — no application code changed in this entry |
| Domain (Sentry tag) | payments |
| Related | Executes the backfill planned (not run) in `docs/change-log/2026-08-16-gst-backfill-and-stripe-crosscheck.md` §1b, using `backend/services/legacy_gst_backfill_service.py` |

## 1. Issue/gap identified

0 of the (then) 186 already-migrated legacy rides in production carried `old_payout_gst_amount` in `legacy_import_metadata`, even though the source CSV (`bookings.csv`) has this field populated for all of them. PR #3963 fixed the importer to preserve this field going forward, but did nothing for rows imported before it shipped.

## 2. Root cause

The pre-fix version of `booking_import_service.py` never read `payout_gst_amount` from the legacy export at all — it was dropped silently during import, not missing from source.

## 3. Fix/remediation

Re-confirmed live immediately before writing (per A34's standing caution that legacy-migration row counts have shown an unexplained gap and should not be assumed stable): **186** legacy rides present, **0** with the field, matching the dry-run plan exactly. Joined all 186 rows to `bookings.csv` by `old_booking_id` — 186/186 resolved, 0 unmatched. Ran a single guarded `UPDATE`:

```sql
UPDATE rides r
SET legacy_import_metadata = r.legacy_import_metadata || jsonb_build_object('old_payout_gst_amount', v.amt)
FROM v
WHERE r.id = v.id::text
  AND r.legacy_import_metadata->>'source' = 'legacy_mongo_booking_import'
  AND NOT (r.legacy_import_metadata ? 'old_payout_gst_amount')
RETURNING r.id;
```

`v` was a `VALUES` list of `(ride_id, payout_gst_amount)` pairs for all 186 rows, sourced from the CSV join. 186 rows returned by `RETURNING` — 1:1 match, no partial application. Post-write verification: **186/186 rides now carry the field**, and `sum(old_payout_gst_amount) = $102.09`, matching the dry-run's precomputed total exactly.

## 4. Risk & impact on existing functionality

- **What changed:** exactly one new JSONB key, `legacy_import_metadata->>'old_payout_gst_amount'`, added to 186 existing `rides` rows. Every other column on every row — `tax_amount`, `tax_breakdown`, `total_earnings`, `payable_balance`, ride state, everything — is byte-for-byte unchanged. Verified: the `WHERE` clause only ever `SET`s `legacy_import_metadata`, and the merge (`||`) only adds a key that didn't previously exist (guarded by `NOT (... ? 'old_payout_gst_amount')`), so it cannot overwrite anything.
- **Blast radius — who else reads `legacy_import_metadata`:**
  - `booking_import_service.py` (writer, future imports only) — no interaction, this batch is already imported.
  - `legacy_gst_backfill_service.py` (the tool that produced this data) — now a no-op against these 186 rows on any future re-run (idempotent by construction, confirmed by its own test suite).
  - `utils/legacy_rides.py` (`EXCLUDE_LEGACY_RIDES`) — filters on `legacy_import_metadata->>'source'`, untouched by this write, exclusion behavior for earnings/payout math is unaffected.
  - Admin/driver-earnings endpoints (`routes/drivers/earnings.py`, `routes/admin/*`) — none read `old_payout_gst_amount`; it's inert metadata until something is built to read it.
  - No route, service, or background loop currently reads `old_payout_gst_amount` — grepped, zero consumers exist yet. This write has no observable effect on any live-tested screen today.
- **Does not touch the open D1 decision** (what `tax_amount` should be for these 186 rides) — that figure is untouched, by design, exactly as planned.

## 5. User experience effect

None. No rider, driver, corporate-admin, or internal-admin screen currently reads this field. This is purely a data-preservation step ahead of the Oct 31 old-app decommission (source CSVs disappear after that date).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| *(none — application code unchanged)* | — | This entry documents a direct production data write, not a code change |
| `docs/change-log/2026-08-16-gst-backfill-executed.md` | This file | Mandatory Change Impact Log for a write to a live-tested table (`rides`) |

## 7. Before/after snippet

Before (all 186 rows):
```json
"legacy_import_metadata": {
  "source": "legacy_mongo_booking_import",
  "old_booking_id": "6a023ea1173f9129709e2a64",
  ...
}
```

After (example row, `old_payout_gst_amount` added):
```json
"legacy_import_metadata": {
  "source": "legacy_mongo_booking_import",
  "old_booking_id": "6a023ea1173f9129709e2a64",
  "old_payout_gst_amount": 0.73,
  ...
}
```

## 8. Rollback plan

Additive JSONB key on an otherwise-unread field — reversible without touching any dollar figure that's ever been read by the app:

```sql
UPDATE rides
SET legacy_import_metadata = legacy_import_metadata - 'old_payout_gst_amount'
WHERE legacy_import_metadata->>'source' = 'legacy_mongo_booking_import';
```

No Stripe charge, wallet delta, or ride-state transition is created or touched by this write — nothing external to unwind, no second deploy needed to revert.

## 9. Verification performed

- [x] Re-confirmed row count (186) and 0-of-186 field presence live, immediately before writing (not relying on a stale earlier count, given A34's open finding that this count has drifted before without explanation)
- [x] Joined all 186 rows against `bookings.csv` by `old_booking_id` — 186/186 resolved, 0 unmatched, computed sum ($102.09) before writing
- [x] Ran the guarded `UPDATE` with `RETURNING` — got exactly 186 IDs back, matching the join
- [x] Post-write query: 186/186 rows now carry the field; `sum(old_payout_gst_amount) = $102.09`, matching the pre-write computed sum exactly
- [x] Confirmed via direct query that no other column changed (guard clause structurally prevents it — `SET` only ever touches `legacy_import_metadata`)
- [ ] Not run through the backend's own test-mocked `mock_supabase_client` fixtures — this was a direct one-time production data write, not a code path exercised by the test suite

## 10. What was NOT verified

- Whether any future feature that reads `old_payout_gst_amount` will interpret it correctly — no consumer exists yet, so this is unverified by construction; whoever builds one should re-read this doc for the field's meaning (payout-side GST, not commission-side GST — see `2026-08-15-legacy-import-gst-preservation.md`). **Also note (flagged by money-audit review, 2026-08-17):** the value is stored as a bare JSON number inside `legacy_import_metadata` (JSONB), shown as `0.73` in the before/after snippet above — reading it back through `supabase-py` deserializes it as a Python `float`, not `Decimal`. Any future consumer must wrap it as `to_decimal(str(value))` before doing money arithmetic with it, never use the raw deserialized value.
- The D1 tax-treatment decision remains open; this write does not resolve or presuppose an answer to it.
