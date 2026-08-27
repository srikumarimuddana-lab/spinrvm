# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-27 |
| Author | Claude (production data change requested by vikas@ngitservices.com) |
| Surface(s) | backend (production data only — no code/schema change), driver-app (feature now live for a second area) |
| Domain (Sentry tag) | drivers |
| PR / commit link | this doc only — see below for why |
| Related issue or gap ID | Follow-up to `docs/change-log/2026-08-27-heatmap-saskatoon-canary-enable.md` (PR #4620) and `docs/change-log/2026-08-27-heatmap-k-anonymity-floor-remediation.md` (PR #4619) |

## 1. Issue / gap identified

Not a bug fix — a deliberate, requested rollout step. After the Saskatoon canary (#4620) ran
with no reported issues, the user asked to enable the driver demand heatmap for Regina next.

## 2. Root cause

N/A — intentional rollout step, not a defect.

## 3. Fix / remediation

Ran a direct, targeted `UPDATE` against the production `service_areas` table (Supabase
project `soavhtdhefowwvforzwb`, `ca-central-1`) at the user's explicit request:

```sql
UPDATE public.service_areas
SET show_demand_heatmap = true
WHERE name = 'Regina'
RETURNING id, name, show_demand_heatmap, is_active;
-- {"id":"d5bc6871-7c6d-4a5f-a194-679463f255ca","name":"Regina","show_demand_heatmap":true,"is_active":true}
```

Verified afterward across all 6 service areas: Regina and Saskatoon are now `true`; Regina
Airport, Saskatoon Airport, riyadh, and riyadh airport remain `false`. "Regina" and "Regina
Airport" are distinct `service_areas` rows — only the base Regina area was enabled, as asked.

## 4. Risk & impact on existing functionality

- **Blast radius: one additional service area, same single feature gate as #4620.**
  `show_demand_heatmap` is read in exactly one place, `get_demand_heatmap` in
  `backend/routes/drivers/profile.py`. Enabling Regina does not touch dispatch, fare calc,
  ride state, or any other driver/rider-facing behavior, and has no interaction with the
  already-live Saskatoon rollout (each area's data is scoped by `service_area_id` in the
  query, and cached per-area: `spinr:heatmap:{area_id}:...`).
- Depends on the k-anonymity floor fix already applied and verified in #4619
  (`heatmap_k_floor=3`) and unchanged since — confirmed still in place before this toggle.
- `driver_heatmap_v2_enabled` remains `true` fleet-wide (unchanged, pre-existing), so Regina
  drivers get the same v2 experience (layer selector, forecast strip, hotspot chips) that
  Saskatoon drivers already have.
- No interaction with background loops, ride state machine, or money/wallet deltas.

## 5. User-experience effect

- **Driver-facing, Regina only, visible mid-shift.** A driver already online in Regina will
  see the demand heatmap layer, legend, forecast strip, and hotspot chips appear on their
  next poll (≤90s) — without needing to restart the app. Same effect already observed in
  Saskatoon under #4620.
- No rider-facing or corporate-admin-facing change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| *(production Supabase, `service_areas` table, Regina row)* | `show_demand_heatmap`: `false` → `true` | Second step of the driver demand heatmap rollout, requested after the Saskatoon canary |
| `docs/change-log/2026-08-27-heatmap-regina-enable.md` | This log | Audit trail for a production feature-toggle change, per CLAUDE.md policy |

## 7. Before / after

```
# Before
service_areas.show_demand_heatmap (Regina) = false

# After
service_areas.show_demand_heatmap (Regina) = true
```

## 8. Rollback plan

Single-column `UPDATE`, immediate effect, true full revert (same mechanism as #4620):

```sql
UPDATE public.service_areas SET show_demand_heatmap = false WHERE name = 'Regina';
```

## 9. Verification performed

- [x] Queried production `service_areas` before and after via `mcp__Supabase__execute_sql`
      (`UPDATE ... RETURNING`, then a full-table `SELECT`) — confirmed only Regina changed
      (Saskatoon's prior `true` from #4620 untouched, all other areas still `false`).
- [x] Confirmed the k-anonymity floor fix (#4619) remains in place (`heatmap_k_floor=3`)
      before this toggle was flipped.
- [ ] Not applicable: no code/migration changed, so no test suite or build was run.
- [ ] Did not independently confirm a real Regina driver's app renders the overlay
      post-toggle (no device/simulator access in this session) — same limitation as #4620.

## What was NOT verified

- Did not check whether the Saskatoon canary (#4620) surfaced any driver feedback or issue
  before proceeding to Regina — the user requested this step directly, without a stated
  canary-observation window, so this was treated as their call to make, not gated on an
  independent "canary looked fine" confirmation from this session.
- Did not confirm how many drivers are assigned to the Regina `service_area_id` or how many
  are online right now.
- Did not verify end-to-end rendering on a real device (see §9).

## 10. Sign-off

- [x] Rollback plan is concrete and immediate (single `UPDATE`, full behavioral revert).
- [x] Blast radius is stated, not assumed — one additional service area, same read-gate
      already exercised by #4620, no other surface touched.
- [x] No silent behavior change — this is a requested, intentional enablement, and the
      driver-facing effect (heatmap appears in Regina) is exactly what was asked for.
