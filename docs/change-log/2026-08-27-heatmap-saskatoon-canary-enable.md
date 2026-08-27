# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-27 |
| Author | Claude (production data change requested by vikas@ngitservices.com) |
| Surface(s) | backend (production data only — no code/schema change), driver-app (feature now live for one area) |
| Domain (Sentry tag) | drivers |
| PR / commit link | this doc only — see below for why |
| Related issue or gap ID | Follow-up to `docs/change-log/2026-08-27-heatmap-k-anonymity-floor-remediation.md` (PR #4619) |

## 1. Issue / gap identified

Not a bug fix — a deliberate, requested feature enablement. Following the k-anonymity floor
fix in #4619 (`heatmap_k_floor` 1 → 3), the user asked to enable the driver demand heatmap
for Saskatoon as a canary, ahead of a wider rollout.

## 2. Root cause

N/A — this is an intentional rollout step, not a defect.

## 3. Fix / remediation

Ran a direct, targeted `UPDATE` against the production `service_areas` table (Supabase
project `soavhtdhefowwvforzwb`, `ca-central-1`) at the user's explicit request:

```sql
UPDATE public.service_areas
SET show_demand_heatmap = true
WHERE name = 'Saskatoon'
RETURNING id, name, show_demand_heatmap, is_active;
-- {"id":"361d17bb-ec55-4561-943f-e3bbee5d7a55","name":"Saskatoon","show_demand_heatmap":true,"is_active":true}
```

Verified afterward that every other service area (Regina, Regina Airport, riyadh, riyadh
airport, **Saskatoon Airport**) is still `false` — only the requested area changed. Note
"Saskatoon" and "Saskatoon Airport" are two distinct `service_areas` rows; only the base
Saskatoon area was enabled, as asked.

## 4. Risk & impact on existing functionality

- **Blast radius: single service area, one feature.** `show_demand_heatmap` is read in
  exactly one place, `backend/routes/drivers/profile.py`'s `get_demand_heatmap`, which is
  the sole gate for whether that endpoint returns real heatmap data or
  `{"enabled": false}`. It does not touch dispatch, fare calc, ride state, or any other
  driver/rider-facing behavior. Drivers in Saskatoon whose `driver.service_area_id` resolves
  to this area will now receive live cells on their next poll (≤90s, `heatmap_refresh_seconds`);
  drivers in every other area are unaffected.
- Depends on the already-applied fix in #4619: `heatmap_k_floor=3` means any cell built from
  fewer than 3 historical/live/scheduled rides is suppressed before it reaches the payload —
  this was verified true *before* this toggle was flipped, so this rollout does not reopen
  the k-anonymity gap.
- `driver_heatmap_v2_enabled` was already `true` fleet-wide (pre-existing production state,
  not changed by this action), so Saskatoon drivers get the v2 experience (layer selector,
  forecast strip, hotspot chips) immediately, not just the v1 base overlay. This was a
  pre-existing setting, not something this change decided — flagging it here since it means
  the canary is broader than "just the heat overlay."
- No interaction with background loops, ride state machine, or money/wallet deltas.

## 5. User-experience effect

- **Driver-facing, Saskatoon only, visible mid-shift.** A driver already online in Saskatoon
  will see the demand heatmap layer, legend, and (since v2 is on) forecast strip / hotspot
  chips appear on their next poll — without needing to restart the app. This is the intended,
  expected effect of the toggle, not a side effect.
- No rider-facing or corporate-admin-facing change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| *(production Supabase, `service_areas` table, Saskatoon row)* | `show_demand_heatmap`: `false` → `true` | Canary-enable the driver demand heatmap for one area, requested after the k-anonymity floor fix landed |
| `docs/change-log/2026-08-27-heatmap-saskatoon-canary-enable.md` | This log | Audit trail for a production feature-toggle change on a driver-facing, privacy-adjacent surface, per CLAUDE.md policy |

## 7. Before / after

```
# Before
service_areas.show_demand_heatmap (Saskatoon) = false

# After
service_areas.show_demand_heatmap (Saskatoon) = true
```

## 8. Rollback plan

Single-column `UPDATE`, immediate effect (drivers stop receiving heatmap data on their next
poll, no cache flush needed — the endpoint itself gates on this column before touching the
Redis cache):

```sql
UPDATE public.service_areas SET show_demand_heatmap = false WHERE name = 'Saskatoon';
```

This is a true rollback (unlike the k-floor change) — turning the toggle back off fully
restores prior behavior with no data-level cleanup needed.

## 9. Verification performed

- [x] Queried production `service_areas` before and after via `mcp__Supabase__execute_sql`
      (`UPDATE ... RETURNING`, then a full-table `SELECT`) — confirmed only Saskatoon changed.
- [x] Confirmed the k-anonymity floor fix (#4619) was already applied and verified before
      this toggle was flipped, so no cell built from fewer than 3 rides can be emitted.
- [ ] Not applicable: no code/migration changed, so no test suite or build was run.
- [ ] Did not independently confirm a real Saskatoon driver's app actually renders the
      overlay post-toggle (no device/simulator access in this session) — verified via the
      backend gating logic and the DB state only, not an end-to-end observation.

## What was NOT verified

- Did not confirm how many drivers are currently assigned to the Saskatoon `service_area_id`
  or how many are online right now — this change takes effect for all of them simultaneously,
  not a further-scoped subset (the "canary" here is area-level, not driver-level; a
  driver-level canary would use `heatmap_internal_driver_ids` instead, which was not touched).
- Did not verify end-to-end rendering on a real device (see §9).

## 10. Sign-off

- [x] Rollback plan is concrete and immediate (single `UPDATE`, full behavioral revert).
- [x] Blast radius is stated, not assumed — one service area, one read-gate, no other
      surface touched.
- [x] No silent behavior change — this is a requested, intentional enablement, and the
      driver-facing effect (heatmap appears) is exactly what was asked for.
