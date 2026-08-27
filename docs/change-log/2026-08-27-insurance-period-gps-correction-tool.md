# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-27 |
| Author | Claude Code (interactive session) |
| Surface(s) | backend |
| Domain (Sentry tag) | safety |
| PR / commit link | commit `95335e0a9` on `claude/migration-batch-readiness-wicr1d` (opened alongside this entry) |
| Related issue or gap ID | `ACTION_ITEMS.md` B34 (closed 2026-08-20, explicitly left the write path open), `docs/migration/2026-08-27-legacy-data-full-migration-approach.md` §5b |

## 1. Issue / gap identified

`driver_insurance_period_corrections` (migration 355) and both of its consumers
(`scripts/compliance_export.py`, `backend/routes/admin/driver_distance.py`'s
`admin_driver_distance_logs`) have been fully built, wired, and tested since 2026-08-20 — but
nothing has ever written a row into the table. The 156 legacy rides whose reconstructed Period-2
boundary (`docs/change-log/2026-08-20-insurance-period-reconstruction-verification.md`) is known
to diverge from `driverlocationlogs.csv`'s real GPS data have sat uncorrected since that
verification pass, because no write path existed to correct them.

## 2. Root cause

The verification pass that found the divergence was deliberately read-only (its own module
docstring: "This module is read-only by design") because at the time it ran, the sanctioned
corrections table didn't exist yet. B34 built that table two days later but explicitly scoped
itself to "build the table + wire the two consumers," leaving "actually correcting those 156 rows"
as a named, separate, still-open item.

## 3. Fix / remediation

New module `backend/services/insurance_period_gps_correction.py`, built directly on top of
`insurance_period_reconstruction_verification.py`'s already-shipped classification (reused
unchanged) rather than re-deriving it:

- Turns every `DIVERGES` verification result into a `driver_insurance_period_corrections` insert:
  `original_period_id` (the ride's Period-2 row), `corrected_started_at`/`corrected_ended_at`
  (both written together from the real `going_to_pickup` span), a required `reason`, and
  `corrected_by` (the operator's real `users.id` — the table's own `NOT NULL` FK, not a
  formality).
- Every other verification status (`CONFIRMED`, `NO_CSV_DATA`, `AMBIGUOUS_SPAN_COUNT`,
  `EXCLUDED_BY_MIGRATION_332`, ...) is left untouched — the existing `is_reconstructed=true`
  estimate stands, exactly as already disclosed.
- Idempotent: proactively skips any ride whose Period-2 row already has a correction on file,
  on top of migration 355's own `UNIQUE` index as the final backstop.
- New CLI `backend/scripts/apply_legacy_insurance_period_gps_corrections.py`: `--apply` opt-in
  (default is dry-run/report-only), mirrors its sibling `verify_legacy_insurance_period_
  reconstruction.py`'s contract exactly. Requires `--operator-user-id`.

**No consumer-side change needed** — both `scripts/compliance_export.py` and
`admin_driver_distance_logs` already prefer a correction over the original span when one exists
(shipped and tested in B34's original close). This tool is the only missing piece.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated for the code itself.** Two new files
  (`insurance_period_gps_correction.py`, the CLI script) plus their test file. Grepped
  `backend/` for both new module names: zero hits outside the three new files and this log.
  `insurance_period_reconstruction_verification.py` itself is imported, never modified — its
  read-only contract is untouched.
- **What this *can* affect once run**: `driver_insurance_period_corrections` (new rows only,
  append-only table, nothing else can happen to it per migration 355's trigger) and, downstream,
  what `scripts/compliance_export.py` and `admin_driver_distance_logs` display for the 156
  affected rides (their `is_corrected` flag flips true, boundaries shift by the deltas already
  quantified in the 2026-08-20 verification log — median ~9.7 min earlier Period-2 start). No
  other table, no fare/payout/wallet path, no live ride-state transition — this is historical,
  closed-row regulatory metadata only.
- **This commit does not run the tool against production.** The table remains empty
  (confirmed live, see §9) — building and validating the tool is this change; applying it is a
  separate, explicit follow-up action requiring a real admin's `users.id`.

## 5. User-experience effect

None directly — no rider/driver/corporate-admin-facing surface exists for this table. Internal
admin: once actually applied, the 156 affected rides' `/drivers/{id}/distance-logs` drill-down
(admin dashboard) and any future SGI compliance export for those rides will show the corrected,
`is_corrected=true` boundaries instead of the coarser `is_reconstructed=true` estimate. Not
mid-session-visible to anyone — historical closed-period data only.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/insurance_period_gps_correction.py` | New: `build_correction_plan`/`commit_correction_plan`/fetch helpers | The write path B34 left open |
| `backend/scripts/apply_legacy_insurance_period_gps_corrections.py` | New: CLI, `--apply` opt-in | Operational entry point |
| `backend/tests/test_insurance_period_gps_correction.py` | New: 29 unit tests (fake-supabase harness) | Coverage for the new write path |

## 7. Before / after

Not applicable — purely additive (new files); the existing `insurance_period_reconstruction_
verification.py` and both downstream consumers are unchanged.

## 8. Rollback plan

**Before `--apply` is ever run** (current state): nothing to roll back — no code path exists that
writes anything; deleting the three new files removes the capability entirely, `git-revert-safe`.
**After `--apply` runs**: the correction rows are additive and append-only (migration 355 blocks
UPDATE/DELETE unconditionally) — there is no "undo" at the data layer by design, matching the
regulatory-audit-trail intent (a correction, once made, is itself part of the permanent record).
If a specific correction is later found to be wrong, the sanctioned fix is a new, separate
decision (documented as explicitly out of scope by migration 355's own header comment: "correcting
a correction... is a new, separate design question"), not a delete/rewrite of this tool's output.

## 9. Verification performed

- [x] **Automated tests**: 29/29 passed (`backend/tests/test_insurance_period_gps_correction.py`
      — fetch helpers, `.in_()` batching, every `build_correction_plan` branch including two
      defensive-skip paths that should never fire in practice, `commit_correction_plan`'s
      empty-plan no-op and real-insert shape). `ruff check` + `ruff format --check` clean on all
      three new files.
- [x] **Real-data end-to-end validation performed** (mirrors the verification pass's own
      2026-08-20 real-export validation, delegated to a subagent to keep the bulk data out of
      this session's context): ran the actual shipped pipeline — `fetch_migration_332_candidate_
      rides`'s query shape reproduced via direct read-only SQL (project `soavhtdhefowwvforzwb`,
      `ca-central-1`), the real local `driverlocationlogs.csv`, and `build_correction_plan` — end
      to end. Results:
      - Candidates: **186** (matches). Period-2 rows: **182** (matches).
      - `driver_insurance_period_corrections` row count: **0**, confirmed live — nothing has ever
        been written.
      - `plan.counts()`: `{'DIVERGES': 156, 'AMBIGUOUS_SPAN_COUNT': 25,
        'EXCLUDED_BY_MIGRATION_332': 4, 'NO_CSV_DATA': 1}` — exact match to the 2026-08-20
        verification log's documented numbers.
      - `build_correction_plan` produced **exactly 156** `CorrectionRecord`s, zero dropped
        (`DIVERGES_BUT_NO_PERIOD_2_ROW: 0`, `DIVERGES_BUT_NO_REAL_BOUNDARY: 0`) — every DIVERGES
        ride had both a Period-2 row to attach to and a clean real boundary pair.
      - Spot-checked 3 sample records: `original_period_id` values are real, distinct period-row
        ids (not accidentally the `ride_id`); each corrected span is a plausible ~6-8 minute
        "going to pickup" window.
- [x] Reviewed against CLAUDE.md's append-only rule and the Period 0-3 model — this tool never
      writes to `driver_insurance_periods` itself, only to the dedicated corrections table;
      Period 3 is never touched (already confirmed accurate by the 2026-08-20 pass).
- [x] Blast-radius grep performed (see §4).
- [ ] **Not yet run with `--apply` against production** — see "What was NOT verified."

## 10. Sign-off

- [x] Rollback plan is concrete and stated (append-only by design; no undo path, matching the
      regulatory-audit-trail intent — flagged explicitly, not glossed over).
- [x] Blast radius is stated, not assumed (isolated code; scoped, already-tested downstream
      impact once applied).
- [x] No silent behavior change to an already-shipped flow — the two consumers' behavior when a
      correction exists was already shipped and tested in B34; this change is additive data only.

## What was NOT verified

- **The tool has not actually been run with `--apply` against production.** The table is
  confirmed empty as of this log. Running it for real requires a human (or an explicitly
  authorized follow-up session action) to supply a real `--operator-user-id` — this is a
  regulatory audit trail naming who authorized each correction, deliberately not something a
  script defaults on its own.
- **The three `>1h` Period-2-start outliers the 2026-08-20 verification log flagged** (never
  individually root-caused — could be genuine early dispatch or a `driverlocationlogs.csv`
  data-quality artifact) are included in the 156 DIVERGES set exactly as-is; this tool does not
  special-case or exclude them. Worth a human sanity-check of those specific rides before
  `--apply`, per that log's own recommendation, still standing.
- **No production build was run** — backend-only Python change, no frontend surface touched.
- **This tool does not decide *whether* to apply the corrections** — that remains the explicit,
  separate call the 2026-08-27 migration plan doc's §5b already recorded as approved in
  principle; running it for real against the live 156-row backlog is the next concrete action,
  not assumed to have happened by this log's existence.
