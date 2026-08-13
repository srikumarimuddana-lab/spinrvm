# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-12 |
| Author | Claude (session_01Wk3M9NdQJWqgpATtogSjD8) |
| Surface(s) | backend, production data only — `service_areas.name`, no code change |
| Domain (Sentry tag) | rides |
| PR / commit link | branch `claude/b8-regina-airport-rename` |
| Related issue or gap ID | ACTION_ITEMS.md B8 (typo follow-up from PR #3734/#3762) |

## 1. Issue / gap identified

A production `service_areas` row was named `"Regina Airpot"` — missing the second "r" — instead of the intended `"Regina Airport"`. Discovered during the B8 vehicle-pricing investigation (PR #3734), deliberately left unfixed at the time pending its own blast-radius check.

## 2. Root cause

Unconfirmed how the typo was originally introduced (row creation predates this session). Confirmed the intended name was always "Regina Airport", not a deliberate name: `backend/routes/service_areas.py`'s code comment, `backend/tests/test_admin_service_areas_coverage.py`'s test fixture, and `backend/migrations/263_service_areas_city_backfill.sql`'s `WHERE name IN ('Regina', 'Regina Airport')` filter all reference the correctly-spelled name as the expected value.

## 3. Fix / remediation

Blast-radius check performed before touching anything (per this item's own note in ACTION_ITEMS.md that a rename needed one first):

- Grepped all 4 app surfaces (`backend/`, `admin-dashboard/`, `rider-app/`, `driver-app/`) plus migrations for any literal match on `"Regina Airport"` or `"Regina Airpot"`.
- Found two **historical, already-applied** artifacts that filtered on the *correct* spelling and would, in principle, have silently missed the actual (misspelled) row: migration 263's `city` backfill (`WHERE name IN ('Regina', 'Regina Airport')`), and the 2026-08-11 PST-enable change's own Change Impact Log, which claims (without apparent awareness of the typo) that it verified "all correctly named" rows were updated.
- **Live-checked both fields directly before renaming** rather than assuming either historical claim: `city='Regina'` and `pst_enabled=true, pst_rate=6` were **already correctly set** on the real `"Regina Airpot"` row. Whatever mechanism actually set them (not fully diagnosed — possibly a manual admin-dashboard edit, since `routes/admin/service_areas.py` reads/writes both fields independent of any name match), the current state is correct, so the typo caused no live data gap in these two specific fields.
- The only code reference found beyond documentation/comments is `routes/service_areas.py`'s public-area-listing filter, which excludes airport rows via `is_airport`/`parent_service_area_id` flags — **not** a name-string match. Confirmed by reading the route directly and by running all 44 tests in `test_service_areas_public.py` + `test_admin_service_areas_coverage.py` (both fully mock `db_supabase.get_rows` and assert on the filter dict shape, not on the row's actual name) — all 44 passed, unaffected by the row's real name either way.
- This specific row is already correctly modeled as an `is_airport=true` child of `Regina` (`parent_service_area_id` = Regina's row id) — already excluded from the public driver/rider top-level picker regardless of its name.

With the blast radius clear, ran a direct rename against production:

```sql
UPDATE service_areas SET name = 'Regina Airport' WHERE name = 'Regina Airpot';
```

No application code changed.

## 4. Risk & impact on existing functionality

**Blast radius, stated explicitly:** isolated to the single row's `name` column. No live code path matches `service_areas` rows by name string for anything this row is involved in (confirmed above — filtering uses `is_airport`/`parent_service_area_id`, joins use `id`/`parent_service_area_id`). The two historical artifacts that *did* reference the name string are one-time operations that already ran; renaming now doesn't retroactively change what they did or didn't match at the time, and both fields they cared about (`city`, `pst_enabled`/`pst_rate`) are independently confirmed correct on this row already.

**Not a mid-ride change** — `service_areas.name` isn't read by any active-ride flow; it's a display/admin/config label, not a state-machine or fare input.

**New finding surfaced by this same blast-radius check, explicitly NOT part of this fix:** the main `Regina` (non-airport) row currently shows `pst_enabled=false` despite `pst_rate=6` already being set, contradicting the 2026-08-11 PST-enable change log's claim that all 4 Saskatchewan rows were set to `pst_enabled=true`. This is unrelated to the rename (this PR's `UPDATE` never touches `pst_enabled`/`pst_rate` on any row) and is **not fixed here** — logged as ACTION_ITEMS.md B26, pending user confirmation of whether it's a live tax-collection bug or an intentional, undocumented reversal.

## 5. User-experience effect

Admin-facing only (cosmetic). The Service Areas admin editor and any other admin surface displaying this row's name will now show "Regina Airport" instead of "Regina Airpot". No rider/driver-facing surface shows this row's name directly (it's an airport sub-area, excluded from the public top-level picker per §3).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| Supabase `service_areas` table (production) — one row | `name`: `'Regina Airpot'` → `'Regina Airport'` | Fix a data-entry typo, confirmed via blast-radius check to be safe |
| `ACTION_ITEMS.md` | B8's typo follow-up marked closed; new B26 filed for the unrelated `Regina.pst_enabled=false` finding surfaced during the check | Tracking |
| `docs/change-log/2026-08-12-b8-regina-airport-rename.md` | New Change Impact Log | Required per `CLAUDE.md` — production data change on a live-tested surface |

No backend/frontend code files changed.

## 7. Before / after

```sql
-- Before
SELECT name FROM service_areas WHERE id = '34d7bbc9-9c80-4940-93fb-aa8ac42b08e0';
-- 'Regina Airpot'

-- After
-- 'Regina Airport'
```

## 8. Rollback plan

```sql
UPDATE service_areas SET name = 'Regina Airpot' WHERE name = 'Regina Airport' AND id = '34d7bbc9-9c80-4940-93fb-aa8ac42b08e0';
```

Scoped by `id`, not just `name`, in case a second "Regina Airport" row is ever created before a rollback is needed. Takes effect immediately — no cache, no deploy.

## 9. Verification performed

- [x] Blast-radius grep across all 4 app surfaces + migrations for name-string matches — see §3.
- [x] Live-checked `city`/`pst_enabled`/`pst_rate` on the actual row before renaming, rather than trusting either historical artifact's claim about what it matched.
- [x] `backend/tests/test_service_areas_public.py` + `test_admin_service_areas_coverage.py` — 44 passed, confirmed mock-based and name-independent.
- [x] Rename confirmed via the `UPDATE ... RETURNING` clause.

## 10. What was NOT verified

- Did not investigate *how* `city`/`pst_enabled`/`pst_rate` ended up correctly set on this row despite the historical name-mismatch risk — the current state is correct, and diagnosing the exact mechanism wasn't necessary to confirm the rename was safe.
- Did not fix the newly-discovered `Regina.pst_enabled=false` finding — deliberately out of scope for a rename, filed as B26 pending user input.
- No visual verification of the admin Service Areas editor rendering the corrected name — reasoned about from the data change only.

## 11. Sign-off

- [x] Rollback plan is concrete and testable (§8)
- [x] Blast radius is stated, not assumed (§4, §3)
- [x] A new, unrelated, higher-severity finding was surfaced rather than folded silently into this fix or left unmentioned (§4, §6 — filed as B26)
