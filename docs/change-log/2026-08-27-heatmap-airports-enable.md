# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-27 |
| Author | Claude (production data change requested by vikas@ngitservices.com) |
| Surface(s) | backend (production data only — no code/schema change), driver-app (feature now live for two more areas) |
| Domain (Sentry tag) | drivers |
| PR / commit link | this doc only — see below for why |
| Related issue or gap ID | Third rollout step, following `docs/change-log/2026-08-27-heatmap-saskatoon-canary-enable.md` (Saskatoon) and `docs/change-log/2026-08-27-heatmap-regina-enable.md` (Regina) |

## 1. Issue / gap identified

Not a bug fix — a deliberate, requested rollout step. After Saskatoon and Regina ran with no
reported issues, the user asked to enable the driver demand heatmap for "the airport zones"
too.

## 2. Root cause

N/A — intentional rollout step, not a defect.

## 3. Fix / remediation

Ran a direct, targeted `UPDATE` against the production `service_areas` table (Supabase
project `soavhtdhefowwvforzwb`, `ca-central-1`) at the user's explicit request:

```sql
UPDATE public.service_areas
SET show_demand_heatmap = true
WHERE name IN ('Regina Airport', 'Saskatoon Airport')
RETURNING id, name, show_demand_heatmap, is_active;
-- {"id":"34d7bbc9-...","name":"Regina Airport","show_demand_heatmap":true,"is_active":true}
-- {"id":"23509b35-...","name":"Saskatoon Airport","show_demand_heatmap":true,"is_active":true}
```

Deliberately scoped to the two Saskatchewan airport areas only. `riyadh` and `riyadh airport`
— two other `service_areas` rows in this project, unrelated to the Saskatchewan rollout this
conversation has been carrying out — were left untouched, as they have been at every prior
step in this rollout; "the airport zones" was read as the airports belonging to the cities
already enabled, not every area whose name contains "airport".

## 4. Risk & impact on existing functionality

- **Blast radius: two additional service areas, same single feature gate as the prior two
  rollout steps.** `show_demand_heatmap` is read in exactly one place,
  `get_demand_heatmap` in `backend/routes/drivers/profile.py`. No interaction with the
  already-live Regina/Saskatoon rollout — each area's data is scoped by `service_area_id` in
  the query and cached per-area (`spinr:heatmap:{area_id}:...`).
- Depends on the k-anonymity floor fix already applied and verified in an earlier step of
  this rollout (`heatmap_k_floor=3`) — unchanged since, confirmed still in place.
- `driver_heatmap_v2_enabled` remains `true` fleet-wide (unchanged, pre-existing), so drivers
  in these two airport areas get the same v2 experience already live in Regina/Saskatoon.
- Airport zones have their own sub-zone polygon overlay (`useAirportZones` / HM-21, blue
  dashed outlines) which is an entirely separate feature from the demand heatmap and is
  unaffected by this change — the two overlays are independent and were already rendering
  (or not) regardless of `show_demand_heatmap`.
- No interaction with background loops, ride state machine, or money/wallet deltas.

## 5. User-experience effect

- **Driver-facing, Regina Airport and Saskatoon Airport only, visible mid-shift.** A driver
  already online in either airport area will see the demand heatmap layer, legend, forecast
  strip, and hotspot chips appear on their next poll (≤90s) — without needing to restart the
  app. Same effect already observed in Saskatoon and Regina.
- No rider-facing or corporate-admin-facing change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| *(production Supabase, `service_areas` table, Regina Airport and Saskatoon Airport rows)* | `show_demand_heatmap`: `false` → `true` | Third step of the driver demand heatmap rollout, requested after Saskatoon and Regina |
| `docs/change-log/2026-08-27-heatmap-airports-enable.md` | This log | Audit trail for a production feature-toggle change, per CLAUDE.md policy |

## 7. Before / after

```
# Before
service_areas.show_demand_heatmap (Regina Airport)   = false
service_areas.show_demand_heatmap (Saskatoon Airport) = false

# After
service_areas.show_demand_heatmap (Regina Airport)   = true
service_areas.show_demand_heatmap (Saskatoon Airport) = true
```

## 8. Rollback plan

Single-statement `UPDATE`, immediate effect, true full revert (same mechanism as the prior
two rollout steps):

```sql
UPDATE public.service_areas SET show_demand_heatmap = false WHERE name IN ('Regina Airport', 'Saskatoon Airport');
```

## 9. Verification performed

- [x] Queried production `service_areas` before and after via `mcp__Supabase__execute_sql`
      (`UPDATE ... RETURNING`, then a full-table `SELECT`) — confirmed only the two airport
      rows changed; Regina, Saskatoon, riyadh, and riyadh airport are exactly as they were.
- [x] Confirmed the k-anonymity floor fix remains in place (`heatmap_k_floor=3`) before this
      toggle was flipped.
- [ ] Not applicable: no code/migration changed, so no test suite or build was run.
- [ ] Did not independently confirm a real driver's app renders the overlay post-toggle in
      either airport area (no device/simulator access in this session) — same limitation as
      the prior two rollout steps.

## What was NOT verified

- Did not confirm how many drivers are assigned to either airport `service_area_id` or how
  many are online right now.
- Did not verify end-to-end rendering on a real device.
- Did not verify interaction with the separate airport sub-zone polygon feature (HM-21)
  beyond confirming it reads a different data source (`useAirportZones`, not
  `show_demand_heatmap`) and is therefore structurally independent.

## 10. Sign-off

- [x] Rollback plan is concrete and immediate (single `UPDATE`, full behavioral revert).
- [x] Blast radius is stated, not assumed — two additional service areas, same read-gate
      already exercised twice before, no other surface touched.
- [x] No silent behavior change — this is a requested, intentional enablement, and the
      driver-facing effect (heatmap appears in both airport areas) is exactly what was asked
      for.
