# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-27 |
| Author | Claude (production data fix requested by vikas@ngitservices.com) |
| Surface(s) | backend (production data only — no code/schema change) |
| Domain (Sentry tag) | safety / drivers (PIPEDA, driver demand heatmap) |
| PR / commit link | this doc only — see below for why |
| Related issue or gap ID | Follow-up from admin/driver Heat Map audit (this conversation, 2026-08-26/27) |

## 1. Issue / gap identified

Production `settings.heatmap_k_floor` (project `spinrmobileapp`, `soavhtdhefowwvforzwb`,
`ca-central-1`) was set to **1**. Migration 311's own column comment states: *"PIPEDA
k-anonymity floor: minimum rides per cell before it may appear in any heatmap payload...
do not default below 3."* At `k_floor=1`, a single historical ride is enough for its pickup
cell (~445m × 410m at Saskatoon's latitude) to be emitted in the driver demand-heatmap
payload — including a rider's own home address if they've only ever ridden from there once.

Found while auditing why the driver-app demand heatmap displays nothing in production (see
the rest of this conversation): querying `service_areas.show_demand_heatmap` for all 6
active areas (Regina, Regina Airport, Saskatoon, Saskatoon Airport, riyadh, riyadh airport)
showed all `false`, so the k-anonymity gap was **dormant** — nothing was being served to any
driver at the time this was found. But `driver_heatmap_enabled` (fleet kill switch) was
already `true` and `driver_heatmap_v2_enabled` was already `true` fleet-wide, so the gap
would have gone live immediately and silently the moment anyone flipped a single service
area's `show_demand_heatmap` toggle on, with no code change or review gate in between.

## 2. Root cause

Not confirmed. `heatmap_k_floor` is writable via `PUT /api/admin/settings` (the field is
allow-listed with `ge=1 le=50` bounds in `HeatmapSettingsRequest`,
`backend/routes/admin/settings.py`), so `1` is a valid value the admin UI would accept
without any warning that it drops below the documented PIPEDA floor of 3 — most likely set
during earlier testing of the heatmap feature (dev/staging exploration) and never reset
before/after the settings row was live in production. No migration or code path sets it to
1 by default (migration 311 defaults it to 3), so this was a manual write, not a bug in the
migration or the backend.

## 3. Fix / remediation

Ran a direct, targeted `UPDATE` against the production `settings` row via Supabase (project
`soavhtdhefowwvforzwb`) at the user's explicit request, restoring `heatmap_k_floor` to `3`
(the documented minimum) — this session has no `apply_migration`/DDL need, since this is a
one-row config value already covered by an existing column, not a schema change:

```sql
UPDATE public.settings
SET heatmap_k_floor = 3
WHERE id = 'app_settings';
-- Verified: {"id":"app_settings","heatmap_k_floor":3}
```

`show_demand_heatmap` on every service area was left untouched (still `false` on all 6) —
the user explicitly chose not to enable any area in this pass, only to close the privacy gap
before anyone does.

No application code or migration changed. This doc exists to leave an audit trail for a
production data fix on a PIPEDA-relevant column, per this repo's Change Impact & Risk Log
policy — it's committed on its own branch/PR rather than folded into a code change, since
there isn't one.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated, and currently inert.** `heatmap_k_floor` is read in exactly one
  place, `backend/routes/drivers/profile.py`'s `get_demand_heatmap` (both the v1 aggregate
  suppression and the v2 per-component floor). It has no other readers (grepped
  `heatmap_k_floor` repo-wide). Because `show_demand_heatmap` is `false` on every service
  area right now, this endpoint returns `{"enabled": false}` before `k_floor` is even read —
  so this change has **zero observable effect today**. Its effect is entirely forward-looking:
  it raises the floor that will apply the moment any area's toggle is switched on.
- Raising the floor from 1 to 3 can only ever **suppress more** cells, never fewer — so this
  change cannot introduce a new privacy leak; it closes one. The only possible regression is
  a heatmap that looks sparser than before once enabled (working as intended: fewer
  low-volume cells qualify).
- No interaction with background loops, ride state machine, or money/wallet deltas.

## 5. User-experience effect

- No user-visible effect today (feature is off everywhere). Once `show_demand_heatmap` is
  enabled for an area, drivers there will see a demand heatmap with high-privacy-risk
  low-count cells suppressed — a difference from what they *would* have seen at `k_floor=1`,
  but nobody has seen `k_floor=1` output yet, so there is no regression from an
  already-shipped, already-observed behavior.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| *(production Supabase, `settings` table, `app_settings` row)* | `heatmap_k_floor`: `1` → `3` | Restore the documented PIPEDA k-anonymity minimum before the driver heatmap feature is enabled for any service area |
| `docs/change-log/2026-08-27-heatmap-k-anonymity-floor-remediation.md` | This log | Audit trail for a production data fix on a privacy-relevant column, per CLAUDE.md policy |

## 7. Before / after

```
# Before
heatmap_k_floor = 1   -- 1 ride is enough to appear on the heatmap

# After
heatmap_k_floor = 3   -- matches migration 311's documented PIPEDA minimum
```

## 8. Rollback plan

Single-column `UPDATE` either direction:

```sql
UPDATE public.settings SET heatmap_k_floor = 3 WHERE id = 'app_settings';  -- this fix
UPDATE public.settings SET heatmap_k_floor = 1 WHERE id = 'app_settings';  -- revert (NOT recommended — reintroduces the PIPEDA gap)
```

Reverting is technically trivial but not a real rollback option: it would knowingly restore
a k-anonymity floor below the documented minimum. If `k_floor=3` turns out to be too sparse
operationally, the fix is to reconsider cell size or decay tuning (also exposed in the same
admin Settings → Heat Map tab), not to lower the anonymity floor.

## 9. Verification performed

- [x] Queried production `settings` row before and after via `mcp__Supabase__execute_sql`
      (SELECT then UPDATE ... RETURNING) — confirmed `heatmap_k_floor` is now `3`.
- [x] Confirmed via the same query pass that `show_demand_heatmap` is `false` on all 6
      active service areas, so this change has no live effect today (see §4).
- [x] Grepped the backend for other readers of `heatmap_k_floor` — only one call site.
- [ ] Not applicable: no code/migration changed, so no test suite or build was run.

## What was NOT verified

- Did not investigate who/what set `heatmap_k_floor` to 1 or when — `settings` is a
  single-row table with no history/audit trail of its own field-level changes, and this
  session has no access to a broader audit log for that write (see "Root cause" above).
- Did not check whether `heatmap_internal_driver_ids` (`["DRV-QZGQVT"]`) — the v2 dark-launch
  allowlist — was ever actually exercised against real driver traffic while `k_floor` was 1;
  `show_demand_heatmap` being `false` fleet-wide means the allowlist path was also gated off,
  but this wasn't independently confirmed beyond that gate.

## 10. Sign-off

- [x] Rollback plan is concrete (documented above) though deliberately not recommended.
- [x] Blast radius is stated, not assumed — isolated to one settings column, currently inert.
- [x] No silent behavior change to an already-shipped flow — the feature this affects has
      never been enabled for any driver.
