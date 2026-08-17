# Change Impact & Risk Log — Saskatoon pickup venues (seeded dark)

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-13 |
| Author | Claude Code (session 01BCcM6c) |
| Surface(s) | backend (migrations + operator script). No app code changed. |
| Domain (Sentry tag) | rides |
| PR / commit link | PR #3883 — `020ef23`, `71d05bc`, `046db71`, `76883d4`, `73d1f4b`, `3b10af6` |
| Related issue or gap ID | none — new venue coverage requested in-session |

## 1. Issue / gap identified

Saskatoon had 2 curated pickup venues (airport, Midtown Plaza) from migration 135, so
riders at every other high-traffic location — malls, hospitals, the university, big-box
stores, bars — dropped a raw pin with no named meeting point. Seeding 38 more surfaced a
second, worse problem: **the seed's coordinates were not accurate enough to expose to
riders.** Found by self-review, not by QA or a bug report.

## 2. Root cause

Two distinct causes.

*The coverage gap* is straightforward: nobody had seeded venues past the initial four.

*The accuracy problem* is the one worth recording. The venue centers were sourced from
public geo databases where an entry existed and **estimated from a street address
otherwise**, and all 98 pickup points were **hand-authored as small offsets from the
venue center** rather than geocoded. There was no verification step — nothing in the
repo reads the seed migrations, so no test, lint, or CI gate could have caught it. Three
concrete defects followed:

1. Saskatchewan Polytechnic was placed at ~22nd St, ~1.9 km from a 1130-block Idylwyld
   Dr N address. Provable from the diff alone: its "33rd Street entrance" sat 1921 m from
   the geocoded FreshCo row at 302 33rd St W.
2. 15 pairs of detection circles overlapped. `/maps/pickup-points` returns the **nearest
   center** among all active radius matches, so an overlap makes one venue silently
   answer for another — a rider outside The Rook & Raven resolved to Delta Bessborough
   (67 m) over Downtown Nightlife (105 m) and would have been offered the hotel's doors.
3. Brand coverage was arbitrary, bounded by which branches a web search surfaced rather
   than by enumeration (Canadian Tire 0 of 3, Co-op 1 of 4+, Giant Tiger 2 of 3).

## 3. Fix / remediation

Take the venues dark and **gate activation on verification** rather than leaving
unverified coordinates live.

> **Amended 2026-08-13 (post-merge).** The original plan flipped `is_active` to `false`
> inside 307/308/309. That was written while those files were unmerged; PR #3883 then
> merged them **as-is**, with all 38 rows `is_active = true` and the Polytechnic center
> still wrong. Editing them afterwards would have been not just an append-only violation
> but **inert**: `scripts/migrate.py` keys `schema_migrations` on the full filename, so an
> already-applied migration never re-runs and the edit would have silently done nothing.
> The fix is therefore a new migration, **310_deactivate_unverified_saskatoon_venues.sql**,
> and 307/308/309 are left exactly as merged.

- Migration 310 sets `is_active = false` for all 38 seeded venues by name.
  `/maps/pickup-points` filters on `is_active`, so no rider can reach them.
- Migration 310 also corrects the Polytechnic center and drops its contradictory entrance
  (removes 4 of the 15 overlaps). The remaining 11 are harmless while dark.
- **Non-destructive:** every statement in 310 is guarded on
  `updated_at <= created_at + interval '5 seconds'` — i.e. the row is untouched since the
  seed inserted it. `routes/admin/venues.py` stamps `updated_at` on every edit, so a venue
  an admin has already corrected and deliberately activated is left exactly as they left
  it. That guard also makes 310 idempotent.
- The 4 venues from migration 135 (Regina Airport, Cornwall Centre, Saskatoon Airport,
  Midtown Plaza) are **not** in 310's list — they predate this seed and stay live. A test
  asserts the list matches the seed exactly in both directions, so 310 can neither miss a
  seeded venue nor take down one it does not own.
- Added `backend/scripts/geocode_seed_venues.py`, which corrects centers against Places
  API (New), replaces fabricated entrance lists with a single geocoded main entrance,
  discovers every location of a brand instead of hand-listed ones, and **refuses to
  activate a venue whose circle overlaps a live one**.
- Added `backend/tests/test_seed_venue_geometry.py` so the invariants are enforced in CI
  instead of rediscovered.

## 4. Risk & impact on existing functionality

**Blast radius: isolated.** `venues` is a leaf table. Grepped for every reader/writer
across `backend/`, `rider-app/`, `driver-app/`, `admin-dashboard/src/`:

| Consumer | Interaction | Affected? |
|---|---|---|
| `backend/routes/maps_proxy.py:289` `/maps/pickup-points` | reads `{"is_active": True}` | No — every new row is `false` |
| `backend/routes/admin/venues.py` | admin CRUD | New rows appear in the admin list, inactive |
| `admin-dashboard` venues page / `lib/api/pricing.ts` | calls the admin CRUD | Renders 38 more rows |
| `rider-app/app/confirm-pickup.tsx` | consumes `/maps/pickup-points` | No — endpoint returns nothing new |
| `backend/scripts/geocode_seed_venues.py` | new; reads/writes `venues` | Operator-run only, dry-run by default |

No background loop in `core/lifespan.py` touches `venues`. No interaction with the ride
state machine, wallet/allowance deltas, Stripe, or insurance-period rows. No RLS change —
134's lockdown (service-role only) still applies, and these rows are operational config,
not user PII.

The one real regression risk is **activation**, not this diff: turning on a venue whose
circle overlaps a live one silently changes which door a driver is sent to. That is why
activation is gated by the script's overlap check and by a CI test, rather than left to a
`is_active` toggle in the dashboard.

## 5. User-experience effect

**Nobody sees a difference today.** No rider, driver, corporate admin, or internal admin
behaviour changes while the venues are dark — the only visible effect is 38 additional
inactive rows in Dashboard → Pickup Venues.

When a venue is later activated, the change **is** visible mid-session: a rider whose pin
falls in its radius gets a bottom-sheet chooser at confirm-pickup where previously they
went straight through. That is the intended feature, and it is why activation is a
separate, deliberate step per venue rather than a side effect of this deploy. No copy or
notification changes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/307_seed_saskatoon_pickup_venues.sql` | 21 venues (malls, hospitals, university, landmarks, transit). **Merged; left untouched** | Coverage. Append-only — editing it would not re-run |
| `backend/migrations/308_seed_saskatoon_stores.sql` | 12 grocery/big-box venues. **Merged; left untouched** | Coverage |
| `backend/migrations/309_seed_saskatoon_retail_nightlife.sql` | 5 retail + nightlife-district venues. **Merged; left untouched** | Coverage |
| `backend/migrations/310_deactivate_unverified_saskatoon_venues.sql` | New. Deactivates all 38 by name and corrects the Polytechnic row, both guarded on `updated_at` | The only mechanism that actually takes applied rows offline |
| `backend/scripts/geocode_seed_venues.py` | New. Geocode, discover, report, gated activate | Makes the rows verifiable and completes brand coverage |
| `backend/tests/test_seed_venue_geometry.py` | New. Geometry + stays-dark invariants | Nothing read the seeds before |

## 7. Before / after

What changes is the row's state after all migrations run, not the seed text.

```sql
-- Before — 307 (merged, unchanged) leaves this live on a center ~1.9km from
-- its own address, with an entrance that contradicts the rest of the seed.
SELECT 'Saskatchewan Polytechnic (Saskatoon)', 52.12833, -106.66028, 250,
    '[{"name":"Main entrance (Idylwyld Dr)","lat":52.12860,"lng":-106.66000},
      {"name":"33rd Street entrance","lat":52.12900,"lng":-106.66100}]'::jsonb,
    _sa_id, true
```

```sql
-- After — 310 corrects and darkens it, skipping any row an admin already edited.
UPDATE venues
   SET center_lat = 52.14000,
       center_lng = -106.66200,
       pickup_points = '[{"name":"Main entrance (Idylwyld Dr N)","lat":52.14020,"lng":-106.66170}]'::jsonb,
       updated_at = now()
 WHERE name = 'Saskatchewan Polytechnic (Saskatoon)'
   AND updated_at <= created_at + interval '5 seconds';
```

## 8. Rollback plan

No data-level remediation is needed — nothing here touches money, ride state, or
insurance-period rows, and the seeded rows are inert while inactive.

- **Undo the deactivation, no deploy:** 310's header carries the exact inverse —
  `UPDATE venues SET is_active = true WHERE name IN (…)` plus the statement restoring the
  Polytechnic row's original center and both original pickup points. Running it returns
  the table to its pre-310 state precisely.
- **Immediate, no deploy, per venue:** flip a venue in Dashboard → Pickup Venues. The dark
  state *is* the safe state, so the risky direction is activation, and reversing it is a
  single field flip.
- **Full removal, no deploy:** 307/308/309 each carry a
  `DELETE FROM venues WHERE name IN (…)` block in their header, listing every name seeded.
- **Code:** `git revert` is sufficient for the script and test, neither of which runs
  automatically. Note it is *not* sufficient for 310 — reverting the file does not
  re-activate rows already updated; use the SQL above.

## 9. Verification performed

- [x] **Automated tests run.** `pytest tests/test_seed_venue_geometry.py tests/test_admin_venues_crud.py` — 177 passed (164 new, 13 pre-existing venue CRUD). `ruff check` + `ruff format --check` clean on both new Python files.
- [x] **Mutation-checked, not assumed.** Removing one venue from 310's deactivation list fails three independent tests: the seed↔310 sync check, that venue's stays-dark case, and the overlap check (the un-deactivated Delta Bessborough sits 380 m from the live Midtown Plaza, radii 150+250). A parse guard asserts the regex matches ≥1 venue per file and ≥1 name in 310, so a format change cannot make the suite pass vacuously.
- [x] **Effective end state asserted, not assumed.** The test computes what the database is actually left in after every migration; the resulting active set is exactly the 4 venues from migration 135, with all 38 seeded rows dark.
- [x] **Blast-radius grep performed.** Searched `"venues"` / `'venues'` / `INTO venues` / `/venues` across `backend/`, `rider-app/`, `driver-app/`, `admin-dashboard/src/`; results tabled in §4. Also confirmed no `core/lifespan.py` loop references the table.
- [x] **Migration-runner compatibility confirmed by reading the code**, not assumed: `scripts/migrate.py::_split_sql_statements` tracks dollar-quoted state, so the `DO $$ … END $$;` blocks are passed as single statements rather than shredded on inner semicolons.
- [x] **Reviewed against CLAUDE.md conventions** — migration naming/append-only (307-309 are new, unique prefixes), RLS (no change; 134's service-role-only lockdown holds), PIPEDA (operational config, no user PII; no GPS logged), error handling (script raises on lookup failure rather than degrading, per "do not silently swallow errors").
- [x] **Feature-gated.** Not via `app_settings`, but via the stronger per-row `is_active` gate the table already has, exercised by the script's `--activate`. Every row ships off.

## 10. What was **not** verified

State plainly, so silence does not imply coverage:

- **Whether these venues are live in production right now is unconfirmed.** 307/308/309
  merged with `is_active = true` and this session cannot reach the database, so if those
  migrations have already been applied, **38 venues are currently live on fabricated
  coordinates and riders may be getting curated pickup points today.** Migration 310 is
  what closes that window; until it is applied, the exposure is real. `--report` on the
  geocoding script will show the true state. This should be treated as the operationally
  urgent item in this entry.
- **No coordinate has been checked against reality.** The geocoding script has never been
  executed — the Maps key is in `app_settings` and this session had no working Places
  access (the MCP key was rejected). Every center and every pickup point in the three
  migrations is still an estimate. This is precisely why the rows must go dark, and the
  script's first real run should be treated as the actual verification step.
- **The script's network paths are untested.** Its pure helpers (`haversine_m`,
  `in_saskatoon`, `_overlaps`, and the activation clash-detection comprehension) were
  exercised directly; `_text_search`, `cmd_geocode`, `cmd_discover` and `cmd_activate`
  have no unit tests and have never made a real Places call or DB write.
- **Not run against a database.** The migrations were validated by parsing and by reading
  the runner's splitter — not applied to a live or staging Supabase, so `DO $$` execution,
  the `service_areas` lookup, and the idempotent `WHERE NOT EXISTS` guards are unproven
  in practice.
- **The 11 remaining overlaps are documented, not resolved.** They are inert while dark
  and blocked at activation, but re-centering is still outstanding — the downtown cluster
  (Bessborough / City Hall / bus terminal / nightlife district) needs a deliberate layout
  decision, not another estimate.
- **No admin-dashboard build was run** (`npm run build`), because no frontend file changed.
- **`venues` still has no unique index on `name`.** The seeds dedupe with
  `WHERE NOT EXISTS`, which is not race-safe against two concurrent runners; a CI test now
  catches duplicates across seed files, but the database does not enforce it.

## 11. Sign-off

- [x] Rollback plan is concrete and testable — a single `is_active` flip, no deploy.
- [x] Blast radius is stated from a grep, not assumed — table in §4.
- [x] No silent behavior change to an already-shipped flow: nothing is user-visible while
      dark, and the mid-session effect of a future activation is described in §5.
