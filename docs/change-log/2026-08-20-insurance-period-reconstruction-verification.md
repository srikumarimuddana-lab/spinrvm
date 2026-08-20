# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-20 |
| Author | Claude Code (session `session_01CNZUmXp7X7h8fNSgnESyUd`), on behalf of vikas@ngitservices.com |
| Surface(s) | backend |
| Domain (Sentry tag) | safety |
| PR / commit link | `d815c3fd4` on `claude/spinr-mongodb-migration-u9y6iz` (no PR opened per this task's scope) |
| Related issue or gap ID | `ACTION_ITEMS.md` A41, Oct 30 checklist item #5(a) (`docs/runbooks/legacy-migration-playbook.md`) |

## 1. Issue / gap identified

Migration 332 reconstructed `driver_insurance_periods` rows for 186 legacy-imported rides using
`rides.driver_arrived_at` as a proxy for the true Period-2 (en route to pickup) start, because the old
app's real phase-transition timestamps (`driverlocationlogs.csv`, part of the raw MongoDB export) were not
consulted at the time. The Oct 30 checklist's item #5(a) asked for this to be redone with the real data.

## 2. Root cause

At the time migration 332 was written, `driverlocationlogs.csv` was known to exist (referenced in the
legacy-migration inventory as future source material) but had not been cross-referenced against the 186
rides. `driver_arrived_at` was the best available *ride-level* timestamp, but it marks the moment the
driver arrived at pickup, not the moment they were dispatched/started driving there — migration 332's own
header comment explicitly disclosed this as a known limitation ("the true Period 1->2 boundary is
understated for these rides"), not an oversight.

## 3. Fix / remediation

This change does **not** re-run migration 332 or write any new rows. It adds a read-only verification
pass (`backend/services/insurance_period_reconstruction_verification.py` +
`backend/scripts/verify_legacy_insurance_period_reconstruction.py`) that:

1. Streams `driverlocationlogs.csv` (7,948 rows, 148 MB — mostly `way_points` data this pass never reads)
   with the standard `csv` module, filtered to the 186 rides' old booking ids only.
2. Enumerates the real distinct `phase` values present (`idle`, `going_to_pickup`, `on_ride` — only 3;
   no separate "arrived" phase exists in this export) and maps them to CLAUDE.md's Period 0-3 model:
   `going_to_pickup` → Period 2 (the old app never separately tracked assigned/accepted/arrived, so this
   one phase covers the whole Period 2 window), `on_ride` → Period 3, `idle` → not ride-linked (never has
   a `ride_id` in the real data), not used for reconstruction.
3. Classifies each of the 186 rides by comparing the real phase-boundary timestamps against migration
   332's already-inserted rows, and reports the divergence — it does not write a correction anywhere.

**Why no rows are written — the immutability-trigger question (task step 4).** Re-read migration 332's
own `_driver_insurance_periods_immutable()` trigger function directly (not assumed): the `UPDATE` branch
unconditionally raises once `OLD.ended_at IS NOT NULL` — *"row % is already closed and cannot be
modified"* — with no carve-out for which column is being changed. Every row migration 332 wrote for these
186 rides is closed (its own "no row ever left open" invariant, verified again live: all `is_reconstructed
= true` rows queried have `ended_at` set). So correction-via-UPDATE is not possible, full stop — the first
option in the task's preference order (insert a competing new row, decide on coexistence) was evaluated
next, and rejected:

- `.claude/context/domain-safety.md` already names the intended fix for exactly this shape of problem —
  *"Corrections go into a separate `driver_insurance_period_corrections` table with justification"* — but
  that table has never been built. Confirmed by `grep -rl driver_insurance_period_corrections backend/
  docs/` (zero hits) and by a live `information_schema.tables` query against production
  (`soavhtdhefowwvforzwb`, `ca-central-1`, read-only, via Supabase MCP) for any table matching
  `%insurance_period%`: only
  `driver_insurance_periods` itself exists.
- Without that table, the only place a "corrected" Period 2 span could go is `driver_insurance_periods`
  itself, alongside migration 332's original row for the same `ride_id`/`period`. Nothing in the schema
  says which of two rows for the same ride+period is authoritative — a regulator or auditor querying this
  table for a specific ride would see two overlapping, disagreeing spans with no way to tell them apart.
  Making that interpretable would require teaching every consumer (`scripts/compliance_export.py`,
  `backend/routes/admin/driver_distance.py`'s `admin_driver_distance_logs`, and any future coverage-gap
  check) to prefer one source over another — a real, cross-surface change of its own, not something to
  fold silently into a verification pass on a regulatory-compliance surface.

So this pass stays strictly read-only. `apply_verification_plan()` exists for CLI-shape symmetry with the
other legacy backfill scripts but always raises with the reasoning above; the CLI's `--apply` flag is
refused before it can even build a plan. Building `driver_insurance_period_corrections` properly (new
migration + RLS + immutability trigger + wiring into the two existing consumers above) is recommended as
a new backlog item, `ACTION_ITEMS.md` B34, rather than attempted in this pass.

**This is deliberately not a "migration 332 was basically accurate" verification pass** — see the real
numbers below. It is a documented, quantified confirmation of a divergence migration 332 already
disclosed as a known limitation, now sized precisely enough for a human/compliance decision.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** Two new files (`backend/services/insurance_period_reconstruction_
  verification.py`, `backend/scripts/verify_legacy_insurance_period_reconstruction.py`) plus their test
  file. Neither is imported by any existing module — grepped `backend/` for both new filenames and
  `insurance_period_reconstruction_verification`: zero hits outside the three new files themselves.
- **No write path exists**, so there is no risk to `driver_insurance_periods`, its immutability trigger,
  the SGI-facing compliance export, or the admin dashboard's per-row `is_reconstructed` display — none of
  those were touched.
- **Does not modify migration 332** (append-only history, per CLAUDE.md — not edited) or any of its
  already-inserted rows.
- The read-only Supabase queries run during this session's verification (via the Supabase MCP tool, not
  the backend's own `SUPABASE_SERVICE_ROLE_KEY` path) were `SELECT`-only against `rides` and
  `driver_insurance_periods` — no `UPDATE`/`INSERT`/`DELETE` executed at any point.

## 5. User-experience effect

None. Backend-only, no write path, nobody-facing (no rider/driver/corporate-admin/internal-admin surface
changed). The admin dashboard's `is_reconstructed` badge (already shipped per item #5(b)) is unaffected.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/insurance_period_reconstruction_verification.py` | New: streaming CSV reader, phase→period mapping, pure classification (`build_verification_plan`), Supabase candidate fetch, `apply_verification_plan` (always raises) | Core verification logic |
| `backend/scripts/verify_legacy_insurance_period_reconstruction.py` | New: CLI wrapper, `--apply` unconditionally refused | Mirrors the shape of `backfill_legacy_vehicle_history.py` for operational consistency |
| `backend/tests/test_insurance_period_reconstruction_verification.py` | New: 14 unit tests | Phase mapping, classification (confirmed/diverges/ambiguous/no-data/unknown-phase/driver-mismatch), fake-Supabase fetch, apply-always-raises, `way_points` never surfaced |
| `docs/runbooks/legacy-migration-playbook.md` | Item #5(a) annotation updated | Reflect new status |
| `ACTION_ITEMS.md` | A41 checklist item #5(a) annotation updated; new item B34 filed | Reflect new status; recommend the `driver_insurance_period_corrections` table as the real prerequisite for ever applying a correction |

## 7. Before / after

Not applicable — purely additive (new files); no existing behavior-changing diff.

## 8. Rollback plan

Delete the three new files (`git rm` the service, script, and test); nothing else references them. No
migration, no DB write, nothing to revert at the data layer — there is no write path to roll back in the
first place.

## 9. Verification performed

- [x] **Automated tests run**: `pytest backend/tests/test_insurance_period_reconstruction_verification.py`
  — 14 passed (confirmed locally; see report below for the exact command/output).
- [x] **Real-export verification performed** (mirrors the vehicle-history backfill's 308/355 crosswalk
  check): ran the actual shipped `stream_driverlocationlogs_phase_spans` + `build_verification_plan`
  functions (not a throwaway script) against the real 148 MB `driverlocationlogs.csv` and the real 186
  candidate rides (fetched read-only from production via Supabase MCP, `soavhtdhefowwvforzwb`,
  `ca-central-1`). Results:

  | Status | Count | Meaning |
  |---|---|---|
  | `EXCLUDED_BY_MIGRATION_332` | 4 | Migration 332's own exclusions, unchanged |
  | `NO_CSV_DATA` | 1 | No `driverlocationlogs.csv` row at all for that booking id |
  | `AMBIGUOUS_SPAN_COUNT` | 25 | Not exactly one `going_to_pickup` + one `on_ride` span for the ride's driver (0, 2+, or split across a phase gap) |
  | `DIVERGES` | 156 | Cleanly reconstructable, but at least one real boundary differs from migration 332's proxy by > 60s |
  | `CONFIRMED` | 0 | (none — see below) |
  | **Total** | **186** | |

  Divergence detail for the 156 `DIVERGES` rides (all times: real CSV boundary minus migration 332's
  proxy value):
  - **Period 2 start** (`going_to_pickup.start` vs `driver_arrived_at`): median **-579.8s** (~9.7 min
    earlier), min -37,976.2s (~10.5h, one clear outlier), max -36.4s. Every one of the 156 rides diverges
    here by more than 60s — this is the dominant, systematic finding: migration 332's proxy was known to
    understate Period 2's true start, and the real data confirms it does so by roughly 10 minutes typically.
  - **Period 2 end** (`going_to_pickup.end` vs `started_at`): median -27.2s, min -447.1s, max -4.6s —
    smaller but still real, consistent with pickup-phase logging ending slightly before the trip-start
    timestamp was recorded.
  - **Period 3 end** (`on_ride.end` vs `ride_completed_at`): median **0.6s**, min -4.2s, max 1.8s — this
    boundary was accurate; migration 332's Period 3 reconstruction is not in question.
  - **Zero rides landed as `CONFIRMED`** at the 60s tolerance — Period 2's start divergence alone puts
    every reconstructable ride over that threshold, so this is honestly a "migration 332's disclosed
    Period 2 limitation is real and quantifiable" finding, not a "reconstruction confirmed accurate"
    finding.
- [x] **Blast-radius grep performed**: `grep -rl "insurance_period_reconstruction_verification\|verify_legacy_insurance_period_reconstruction"` across `backend/` — only the three new files themselves. `grep -rl driver_insurance_period_corrections backend/ docs/` — zero hits (confirms the table does not exist in code). Live `information_schema.tables` query (read-only, Supabase MCP) for `%insurance_period%` — only `driver_insurance_periods`.
- [x] Reviewed against CLAUDE.md's append-only rule, the Period 0-3 model, and PIPEDA GPS-logging rule.
- [ ] Manual repro in staging — not applicable; no user-facing flow to repro.
- [ ] Feature-flagged — not applicable; no write path, nothing to flag.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (delete 3 files; no data-layer effect exists to revert).
- [x] Blast radius is stated, not assumed (isolated — zero external callers, confirmed by grep).
- [x] No silent behavior change to an already-shipped flow (nothing existing was touched).

## What was NOT verified

- **`going_to_pickup.start` is treated as the true Period-2 start, but that is itself an assumption, not
  something independently confirmed against the old app's dispatch logic.** The old app's phase log has no
  separate "assigned" event to check this against — `going_to_pickup.start` could itself lag the real
  driver-assignment moment (e.g., if the phase-log row is written only once the driver's client actually
  begins navigation, not at the moment of assignment/offer-accept). If so, this pass's "real" Period-2
  start is still a proxy, just a measurably tighter one than `driver_arrived_at` — not a ground-truth
  value. This module's own docstring states the "no separate assigned/accepted/arrived phase" reasoning as
  the basis for the mapping; it does not claim to have verified the old app's dispatch-to-phase-log latency
  independently, and no such verification was attempted in this pass.
- **The 186-row candidate list, and every timestamp compared against it, came from a single live,
  read-only Supabase query run once during this session (via the Supabase MCP tool against project
  `soavhtdhefowwvforzwb`, `ca-central-1`), not from a repeatable, checked-in fixture.** If production data
  for these
  rides changes before anyone re-runs `verify_legacy_insurance_period_reconstruction.py` for real, the
  numbers in this log will be stale. The script itself re-fetches live each run, so this only affects the
  specific numbers quoted here, not the tool's correctness going forward.
- **The three `>1h` Period-2-start outliers were not individually root-caused.** They could be genuine
  (driver assigned far in advance, or dispatched then idle before actually departing) or a `driverlocationlogs.csv`
  data-quality artifact (e.g., a stale `going_to_pickup` row that was never properly closed and got reused).
  No further investigation was done beyond flagging them as outliers in the divergence stats above — a
  human reviewing the 156-row divergence detail (written to this session's scratch output, not committed,
  since it is per-ride timestamp data with no independent value once the code that generates it is
  merged) should sanity-check these specifically before deciding what "confirmed" should mean for a
  future correction.
- **The 25 `AMBIGUOUS_SPAN_COUNT` rides were not manually inspected one by one.** The script reports *why*
  each is ambiguous (`n_going_to_pickup`/`n_on_ride` counts) but does not attempt a heuristic to pick "the
  right" span when there's more than one, or to explain why some rides have zero spans of one phase (e.g.,
  a ride that may have been assigned to a driver already mid-`going_to_pickup` from a prior booking, or a
  genuine data gap in the old app's own logging). Treated conservatively — excluded from divergence
  reporting — per the task's explicit instruction not to guess.
- **No production build was run** — this task touches only `backend/` Python; there is no
  `admin-dashboard`/`rider-app`/`driver-app` change, so the "real production build" requirement in
  CLAUDE.md's Change Impact Log template does not apply here. `ruff check` and `ruff format --check` were
  run instead (both clean) alongside the pytest suite.
- **This is a verification tool, not a decision.** Whether/how to actually correct migration 332's Period
  2 boundaries (build `driver_insurance_period_corrections`? accept the disclosed gap as permanent, given
  the direction of the error makes Period-2 *coverage* wider than currently recorded, not narrower, which
  is the safer direction for a TNC liability question but still means an SGI-facing export understates how
  much time these 156 rides' drivers spent under Period 2 coverage?) is explicitly **not decided by this
  change** — it is a business/legal/compliance call, flagged here and in `ACTION_ITEMS.md` B34, not made
  unilaterally.
- **`spinr-insurance-period-auditor`/`spinr-migration-reviewer`/`spinr-regulatory-compliance-checker`
  agents were not available via the Agent tool in this session** (checked via `ToolSearch`, no match) —
  the review this task's step 13 requires was done as a rigorous self-review instead, covering the same
  checklist those agents would (append-only compliance, no fabrication, the immutability-trigger
  interaction, correct Period 0-3 phase mapping, no raw GPS in logs). See the commit history / session
  transcript for that review's findings rather than a separate sub-agent report, since none ran.
