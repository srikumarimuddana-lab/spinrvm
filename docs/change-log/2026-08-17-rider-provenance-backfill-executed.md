# Change Impact & Risk Log — Rider legacy-import provenance backfill (executed)

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-17 |
| Author | Claude (automated, on behalf of vikas@ngitservices.com) |
| Surface(s) | Supabase production data only — no application code changed in this entry |
| Domain (Sentry tag) | corporate — closest fit; this is rider/PIPEDA-adjacent data, not payments |
| Related | Closes the P0-A / P2-C gap documented in `docs/audit/2026-08-11-driver-rider-migration-audit.md`; same additive-metadata pattern already executed for rides in `docs/change-log/2026-08-16-gst-backfill-executed.md` |

## 1. Issue/gap identified

`rider_import_service.py` has never written `legacy_import_metadata` to any `users` row — confirmed live immediately before this write: **0 of 1,137 `users` rows** carried any provenance, for riders or otherwise. Unlike drivers (`legacy_import_metadata`, 189 rows, fully provenanced) and rides (`legacy_import_metadata`, 186 rows, fully provenanced), there was no way to answer "which riders came from the previous app" from the database at all. The source CSV (`customers.csv`) that could answer this indirectly disappears at the Oct 31 old-app decommission.

## 2. Root cause

Documentation/code gap identified in `docs/audit/2026-08-11-driver-rider-migration-audit.md` (P0-A/P2-C): `rider_import_service.py`'s `user_row`/`update_fields` construction never sets `legacy_import_metadata`, unlike every other importer in this codebase (`driver_import_service.py`, `booking_import_service.py`, `stripe_mapping_import_service.py`).

## 3. Fix/remediation

Did **not** re-run the rider importer (no rider CSV batch has ever executed against production with a recoverable batch ID, and the importer code itself still has the underlying gap — that's a separate, later fix). Instead, reconstructed the historical match set directly from the legacy source and stamped provenance only, mirroring the GST backfill's posture:

1. Loaded `customers.csv` (1,121 rows) from the legacy MongoDB export, normalized every phone with the exact `normalize_phone()` logic `rider_import_service.py` uses (10-digit → `+1XXXXXXXXXX`, 11-digit leading `1` → `+1XXXXXXXXXX`), producing 1,115 distinct valid `+1XXXXXXXXXX` numbers.
2. Joined against live `users` filtered to `role='rider'` — **918 matches**.
3. Pre-flight check before writing: of those 918, **0** had a PII-protected status (`pending_deletion`/`deleted` — the P0-C guard class), **0** already carried non-empty `legacy_import_metadata`. All 918 were safe to stamp.
4. Ran a single guarded `UPDATE`:

```sql
UPDATE users u
SET legacy_import_metadata = u.legacy_import_metadata || jsonb_build_object(
      'rider_csv_import', jsonb_build_object(
        'batch', to_char(now(), 'YYYYMMDDHH24MISS'),
        'source', 'legacy_rider_csv_import',
        'imported_at', now()
      )
    )
FROM legacy_phones lp
WHERE u.phone = lp.phone
  AND u.role = 'rider'
  AND u.status NOT IN ('pending_deletion','deleted')
  AND u.legacy_import_metadata = '{}'::jsonb
RETURNING u.id;
```

**918 rows returned by `RETURNING` — 1:1 match with the pre-flight count, no partial application.**

## 4. Risk & impact on existing functionality

- **What changed:** exactly one new JSONB key, `legacy_import_metadata->'rider_csv_import'`, added to 918 existing `users` rows. No other column — `email`, `first_name`, `last_name`, `stripe_customer_id`, `status`, `is_rider`, everything else — was touched. Verified: the `UPDATE`'s `SET` clause only ever assigns `legacy_import_metadata`, and the merge (`||`) only adds a key onto the existing (empty) object; the `WHERE u.legacy_import_metadata = '{}'::jsonb` guard means it's structurally impossible for this statement to have overwritten an existing key on any row.
- **PII-protection guard honored, not bypassed:** `u.status NOT IN ('pending_deletion','deleted')` was applied in the same statement, matching the P0-C guard `rider_import_service.py` itself uses. Pre-flight confirmed 0 of the 918 candidates were in a protected status, so the guard was inert for this run, but it's in the query so a future re-run against a different match set can't accidentally reactivate a scrubbed account's data trail.
- **Blast radius — who else reads `users.legacy_import_metadata`:**
  - `rider_import_service.py` (writer for future CSV imports) — unaffected; a future real import batch would now see these 918 rows as "already has metadata" via its own P0-C-adjacent matching logic and skip re-stamping them, which is correct (idempotent by construction).
  - `stripe_mapping_import_service.py` — merges its own `stripe_migration` sub-key onto the same column; this write only ever touches the `rider_csv_import` sub-key, so any existing `stripe_migration` key on these rows (if present) is preserved untouched by the `||` merge.
  - Admin dashboard user list/detail (`routes/admin/users.py`) — does not currently render `legacy_import_metadata` for riders in any screen (confirmed by the same grep sweep documented in the 2026-08-11 audit); this write has no observable UI effect today.
  - No route, service, or background loop currently reads `legacy_import_metadata->'rider_csv_import'` — grepped, zero consumers exist yet, same posture as the GST backfill's `old_payout_gst_amount` write. Inert metadata until something is built to read it.
- **Does not touch wallet balance, ride history, auth, or any money-moving path.** This is a pure provenance/audit-trail write on the `users` table.

## 5. User experience effect

None. No rider, driver, corporate-admin, or internal-admin screen currently reads this field. This is purely a data-preservation step ahead of the Oct 31 old-app decommission, when `customers.csv` stops being recoverable.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| *(none — application code unchanged)* | — | This entry documents a direct production data write, not a code change |
| `docs/change-log/2026-08-17-rider-provenance-backfill-executed.md` | This file | Mandatory Change Impact Log for a write to a live-tested table (`users`) |

## 7. Before/after snippet

Before (all 918 rows):
```json
"legacy_import_metadata": {}
```

After (example row):
```json
"legacy_import_metadata": {
  "rider_csv_import": {
    "batch": "20260817023332",
    "source": "legacy_rider_csv_import",
    "imported_at": "2026-08-17T02:33:32.609939+00:00"
  }
}
```

## 8. Rollback plan

Additive JSONB key, single batch, single timestamp — trivially reversible without touching any other field:

```sql
UPDATE users
SET legacy_import_metadata = legacy_import_metadata - 'rider_csv_import'
WHERE legacy_import_metadata->'rider_csv_import'->>'batch' = '20260817023332';
```

No Stripe charge, wallet delta, or auth/session state is created or touched by this write — nothing external to unwind, no second deploy needed to revert.

## 9. Verification performed

- [x] Pre-flight: confirmed 918 phone-matched `role='rider'` candidates, 0 PII-protected, 0 already-stamped, immediately before writing (not relying on the earlier session's reconciliation numbers going stale)
- [x] Ran the guarded `UPDATE` with `RETURNING` — got exactly 918 IDs back, matching the pre-flight count
- [x] Post-write query: `legacy_import_metadata != '{}'` count on `users` is now 918 (was 0); all 918 carry the `rider_csv_import` key; single distinct batch value; single timestamp (one atomic statement)
- [x] Confirmed no other column moved: total user count (1,137), role distribution (924 riders / 212 drivers), and status distribution (0 pending_deletion, 0 deleted) are identical before and after
- [ ] Not run through the backend's own test-mocked `mock_supabase_client` fixtures — this was a direct one-time production data write, not a code path exercised by the test suite (same posture as the GST backfill)

## 10. What was NOT verified

- The underlying `rider_import_service.py` code gap (P0-A/P2-C) is still unfixed — this backfill closes the *data* gap for the 918 rows that already exist, not the *code* gap that would prevent this from recurring on the next real rider import batch. That's a separate, small code change (stamp `legacy_import_metadata` in `build_plan`/`commit_plan`), not done here.
- The 197 unmatched/other-role legacy customer phones (131 unmatched anywhere, 66 matched a non-rider role) were deliberately left unstamped — they either have no Spinr account or aren't riders, so stamping them would misrepresent the data.
- Whether any future feature that reads `rider_csv_import` will interpret its shape correctly — no consumer exists yet, so this is unverified by construction, same caveat as the GST backfill's `old_payout_gst_amount`.
