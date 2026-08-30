# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-29 |
| Author | Claude Code (session on `staging`) |
| Surface(s) | backend (production database) |
| Domain (Sentry tag) | admin, drivers, safety |
| PR / commit link | production DB only — no repo code change |
| Related issue or gap ID | Follow-on to `2026-08-29-apply-pending-rollup-fn-migrations.md` |

## 1. Issue / gap identified

The previous change applied only the two function migrations (`370`, `371`) that
were causing live 500s, leaving **9 of the 11 pending migrations unapplied**.
This applies the remaining 9, clearing the drift between `backend/migrations/`
and production.

## 2. Root cause

Unchanged and still unfixed: **no deploy workflow runs migrations.** They are
applied by hand, so the repo and the database drift apart silently.

Migration `369`'s own comments record a second, historical cause: it once
shipped with unfilled `<DUPLICATE_INDEX_NAME_1>` placeholders — literal
non-SQL text. Because `run_migrations.py` applies files in order and hard-stops
on first failure, that file would have blocked every migration behind it. It was
corrected 2026-08-27 but the runner was never re-run afterwards.

## 3. Fix / remediation

Applied, in filename order: `363`, `364`, `365`, `366`, `367`, `368`, `369`,
`370_add_unresolved_at_completion_status_to_gap_events`,
`370_location_marker_write_gate_flag`. Recorded each in
`public.schema_migrations` with its real sha256 and reloaded the PostgREST
schema cache (required — three new `settings` columns).

**Most of this batch turned out to be already-applied-out-of-band**, which is
the single most important finding here. Verified against production *before*
applying:

| Migration | Actual state found | Effect of applying |
|---|---|---|
| `363`, `364`, `370_location_marker` | 3 `settings` columns genuinely missing | **Real change** — columns added, all default `false` |
| `365` | Duplicate FAQs already inactive | Two answer texts refreshed; deactivation was a no-op |
| `366`, `367` | All 7 seeded FAQs already present | **No-op** — `WHERE NOT EXISTS` inserted 0 rows (`faqs` stayed 67 total / 60 active) |
| `368` | All 3 indexes already existed | **No-op** — `IF NOT EXISTS` skipped all three |
| `369` | All 3 target indexes already absent | **No-op** — `DROP IF EXISTS` matched nothing |
| `370_add_unresolved` | CHECK already allowed the new value | Constraint dropped and re-added to the identical definition |

So the only substantive schema change in this batch is three boolean columns on
`settings`, all defaulting to `false`.

## 4. Risk & impact on existing functionality

**Blast radius: narrow, and mostly zero.** Six of the nine were verified no-ops
or near-no-ops before being run (table above). The three new columns are
additive with `DEFAULT false` on a single-row `settings` table, and every
reader already used `.get(...)` with a `False` default, so no read path changes
behaviour.

Specific risks considered:

- **`368` (`CREATE INDEX CONCURRENTLY`)** cannot run inside a transaction, so
  its three statements were run individually outside one rather than through
  the transactional path. All three indexes already existed, so no index build
  actually occurred and no write traffic was affected.
- **`369` (index drops)** — verified first that each target was already absent
  **and** that its covering twin exists
  (`idx_surge_pricing_area_created`,
  `idx_driver_location_history_driver_id_timestamp`,
  `idx_driver_location_history_ride_id_timestamp`). No access path was lost.
- **`370_add_unresolved`** is the only statement that took an
  `ACCESS EXCLUSIVE` lock, via `DROP CONSTRAINT` + `ADD ... NOT VALID` +
  `VALIDATE`. Checked beforehand: `ride_location_gap_events` holds **2 rows /
  80 kB**, and **0 rows** would violate the constraint, so the lock was
  momentary and validation could not fail. Run atomically in one transaction,
  so no window existed where the table was unconstrained.
- **`365` (FAQ copy)** rewrites two live answers (CRC requirements, payout
  timing) — user-visible, see §5.

No ride state, money write, wallet delta, insurance-period row, or dispatch
path is touched by any migration in this batch.

## 5. User-experience effect

- **Drivers reading the FAQ:** two answers now read differently — the CRC
  question states the renewal cadence and that an expired check blocks going
  online; the payout question states drivers keep 100% of the fare and points at
  the Earnings section. Both are the merged//canonical wording from `365`.
- **Everyone else: no change.** All three new flags default to `false`, which is
  the same behaviour every reader already had via its `.get()` default —
  public AI chat stays off, the legacy-ride badge stays hidden, and the
  location-marker write gate stays in shadow mode. Turning any of them on is a
  separate, deliberate act.
- One real capability restored: an admin toggling the location-marker write gate
  would previously have errored on write (no column). That write now succeeds.
- Not visible mid-ride to anyone.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `docs/change-log/2026-08-29-apply-remaining-pending-migrations.md` | This log | Required for a live-tested-surface change |

No code or migration file changed — this is a database-state change only. All 9
migration files already existed in the repo, unmodified.

## 7. Before / after

Not applicable — no behaviour-changing code diff. The database state change is
the table in §3.

## 8. Rollback plan

Per-migration, and only the three column adds are worth reverting:

```sql
ALTER TABLE public.settings DROP COLUMN IF EXISTS ai_public_chat_enabled;
ALTER TABLE public.settings DROP COLUMN IF EXISTS legacy_ride_badge_enabled;
ALTER TABLE public.settings DROP COLUMN IF EXISTS location_marker_write_gate_enabled;
DELETE FROM public.schema_migrations WHERE filename IN (
  '363_settings_add_ai_public_chat_enabled.sql',
  '364_settings_add_legacy_ride_badge_enabled_flag.sql',
  '370_location_marker_write_gate_flag.sql');
NOTIFY pgrst, 'reload schema';
```

Dropping them is safe *only* while all three remain `false` — every reader
falls back to its `.get(..., False)` default. If any has been switched on,
switch it off before dropping.

`366`–`369` inserted/dropped nothing, so there is nothing to roll back.
`370_add_unresolved` left the constraint exactly as it found it. `365` changed
FAQ copy; reverting it means restoring the two previous answer strings, which
are not preserved anywhere — recoverable only from a database backup. It is
customer-facing copy, not behaviour, so a revert is unlikely to be urgent.

No deploy is required for any of the above — this is database-only.

## 9. Verification performed

- [x] **Every migration's state checked against production before applying** —
      that is how the six no-ops in §3 were identified rather than assumed.
- [x] Post-apply verification in one query: 3/3 `settings` columns present;
      7/7 seeded FAQs present; `faqs` unchanged at 67 total / 60 active
      (confirming `366`/`367` inserted nothing); 3/3 `driver_documents` indexes
      present; 0/3 dropped indexes remaining; `ride_location_gap_events` CHECK
      reads `('open','resolved','unresolved_at_completion')`.
- [x] All three new flags confirmed `false` on the single `settings` row.
- [x] `369`'s covering twin indexes confirmed present before dropping.
- [x] `370_add_unresolved` pre-checked for row count, size, and violating rows.
- [x] All 9 recorded in `schema_migrations` with real sha256 (`applied_by =
      claude-session-apply`), so a future `run_migrations.py` treats them as
      applied and skips them.
- [x] PostgREST schema cache reloaded after the column adds.
- [x] **Full drift sweep:** 460 migration files on disk vs 455 rows in
      `schema_migrations`. Traced all five differences (prefixes 26, 70, 78,
      137, 299) and verified each is **already in effect** despite having no
      tracking row — `rider_email_verification_otp` exists (`299`), both PII
      functions exist with `search_path` pinned (`78`, `137`), `financial_events`
      has RLS enabled (`70`), and all six of `26`'s target tables have RLS on.
      None is a live gap; they are untracked, not unapplied.

### What was NOT verified

- **No endpoint or app surface was exercised.** No service-role key in this
  container, so everything is verified at the database layer only. The two
  rewritten FAQ answers were not viewed in the driver app, and the admin
  settings screen was not loaded to confirm the location-marker toggle now
  saves.
- **No pytest run** — PyPI is 403 under this environment's network policy, so
  backend deps cannot be installed. No Python changed in this commit.
- **`365`'s previous FAQ answer text was not captured** before the `UPDATE`, so
  a revert depends on a database backup.
- **Separate pre-existing finding, not addressed:** 4 of 125 `public` tables
  have RLS disabled — `document_files`, `driver_bank_import`,
  `driver_csv_import`, `settings`. None is a target of any migration in this
  batch (migration `26` covers six different tables, all already enabled), so
  this predates the drift and was left alone. Worth its own review, since the
  backend reaches these through the service role (which bypasses RLS regardless)
  but a leaked anon/authenticated key would not be stopped by table policy.
- The root cause — no migration step in the deploy pipeline — remains
  **reported, not fixed**. The drift recurs on the next migration-bearing deploy.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow without the UX field filled in
