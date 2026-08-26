# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-20 |
| Author | Claude (agent session), on behalf of vikas@ngitservices.com |
| Surface(s) | backend |
| Domain (Sentry tag) | safety |
| PR / commit link | (worktree branch `worktree-agent-aeefbab677966e41c`, not yet pushed/PR'd) |
| Related issue or gap ID | ACTION_ITEMS.md B34 |

## 1. Issue / gap identified

`.claude/context/domain-safety.md` describes corrections to `driver_insurance_periods` as
going into a separate `driver_insurance_period_corrections` table — that table never
existed. Combined with migration 64's immutability trigger, which unconditionally blocks
`UPDATE` on any closed `driver_insurance_periods` row, there was no sanctioned way to fix
a wrong insurance-period row once written.

## 2. Root cause

Migration 64 (M-5a) built the append-only `driver_insurance_periods` audit table and its
immutability trigger correctly, but the corrections-table half of the design documented
in `domain-safety.md` was never implemented — a "documented but never built" gap (the same
pattern already flagged elsewhere in `domain-safety.md`'s own "Corrected 2026-08-16"
sections for emergency contacts and night-ride protections). It surfaced now because this
session's earlier verification pass (`docs/change-log/2026-08-20-insurance-period-
reconstruction-verification.md`) needed a real destination for a genuinely-diverging
Period-2 reconstruction and found none existed.

## 3. Fix / remediation

- New migration `355_driver_insurance_period_corrections.sql` creates
  `driver_insurance_period_corrections`: `original_period_id` (FK to
  `driver_insurance_periods`, UNIQUE — one correction per original row),
  `corrected_started_at`/`corrected_ended_at`, required non-blank `reason`,
  `corrected_by`, `corrected_at`, `created_at`. RLS enabled with a SELECT-only policy
  (service-role-only writes) and an unconditional-block-on-UPDATE/DELETE immutability
  trigger — both patterns copied directly from migration 64's own
  `driver_insurance_periods` precedent, not invented fresh.
- `scripts/compliance_export.py`'s `_scan()` now looks up any corrections for the scanned
  period ids and substitutes `corrected_started_at`/`corrected_ended_at` for the original
  row's `started_at`/`ended_at` before redaction; the export gains an `is_corrected` column
  (same "never present indistinguishably from the original" rule already used for
  migration 332's `is_reconstructed`).
- `backend/routes/admin/driver_distance.py`'s `admin_driver_distance_logs` does the same
  substitution on the spans it reads from `driver_insurance_periods` before building the
  per-span drill-down, and surfaces the same `is_corrected` flag per row.
- Both lookups short-circuit to no query when there are zero period ids to look up
  (never issue an all-empty `$in`, per CLAUDE.md's query-filter rules).
- The 156 legacy rides flagged as diverging by the earlier verification pass are
  **not** touched by this change — filing the table is not itself a decision to correct
  any of them; that stays a separate, explicit call (ACTION_ITEMS.md B34's own acceptance
  criteria).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated, additive.** `driver_insurance_periods` itself is not altered —
  no columns added, no rows changed, no trigger/policy touched. Only a new table is added.
- **Consumers of `driver_insurance_periods` grepped for**, to confirm both readers named in
  the ticket were found and no third reader was missed:
  `grep -rln "driver_insurance_periods" backend/ scripts/` found: `scripts/
  compliance_export.py` (wired), `backend/routes/admin/driver_distance.py` (wired),
  `backend/migrations/64_…` and `332_…` (schema, untouched),
  `backend/services/insurance_period_reconstruction_verification.py` and
  `backend/scripts/verify_legacy_insurance_period_reconstruction.py` (the verification
  tooling that surfaced this gap — **intentionally not wired to the new table** in this
  change; ACTION_ITEMS.md B34 itself calls out that `apply_verification_plan()`'s no-op
  "should be revisited" only as a follow-up once the table exists, not as part of filing
  it), and `backend/utils/insurance_periods.py` / `record_period_transition()` (writes new
  rows, no correction-reading involved, untouched).
- **No other route or script reads `driver_insurance_periods` for a regulator- or
  admin-facing display** beyond the two named consumers — confirmed by the grep above.
- Both wired functions are read-only additions to an existing read path: on the
  no-correction-exists case (the overwhelming majority of rows today, since zero
  corrections have ever been written), behavior is provably unchanged — see the
  "before/after" tests below, which assert the original `started_at`/`ended_at` still
  flow through untouched when no correction row is found.
- One extra `get_rows` call per invocation of each consumer, but only when at least one
  period row was scanned (empty-scan short-circuits) — negligible latency add on an
  admin/regulator-export path, not a rider/driver-facing SLA-bound path.
- The new table's RLS is deliberately locked to service-role writes only, same as
  `driver_insurance_periods` — no anon/authenticated Supabase key can insert a correction,
  so this cannot be exploited by a compromised rider/driver client to rewrite insurance
  history.

## 5. User-experience effect

- No rider- or driver-facing change of any kind.
- Internal-admin-facing: `GET /drivers/{id}/distance-logs` gains an `is_corrected` boolean
  per row (default `false`) — additive field, not a behavior change to any existing field.
  Not visible mid-session to anyone (this is an admin drill-down report, not a live view).
- Regulator-facing: the SGI/subpoena export (`scripts/compliance_export.py`, run manually
  by an admin, never automated) gains the same `is_corrected` column. No output row is
  removed or reshaped; only present when a correction exists (none do yet).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/355_driver_insurance_period_corrections.sql` | New table + RLS + immutability trigger | ACTION_ITEMS.md B34 — sanctioned correction destination |
| `scripts/compliance_export.py` | `_scan()` now looks up and prefers corrections; `redact_row()`/`FIELDNAMES` gain `is_corrected` | Regulator export must prefer a correction when one exists |
| `backend/routes/admin/driver_distance.py` | `admin_driver_distance_logs` now looks up and prefers corrections per span; output gains `is_corrected` | Admin drill-down must prefer a correction when one exists |
| `backend/tests/test_compliance_export_script.py` | New `TestCorrectionPreference` class (4 tests); existing table-agnostic mocks made table-aware | Prove the preference; keep pre-existing tests passing against the new two-table read |
| `backend/tests/test_admin_driver_distance.py` | 3 new tests (correction preferred, no correction kept, no-id short-circuit) | Prove the preference for the admin consumer |
| `docs/change-log/2026-08-20-b34-insurance-period-corrections-table.md` | This file | Change Impact Log per CLAUDE.md (safety-adjacent surface) |

## 7. Before / after

**`scripts/compliance_export.py` — `_scan()` return value, when a correction exists:**

```python
# Before (no corrections table existed — could not have returned this)
{"id": "p1", "started_at": "2026-07-05T10:00:00Z", "ended_at": "2026-07-05T10:20:00Z", ...}
# redact_row() output: period_started_at = "2026-07-05T10:00:00Z" (the only value that ever existed)
```

```python
# After — a sanctioned correction on file for id "p1" is preferred
{"id": "p1", "started_at": "2026-07-05T09:45:00Z", "ended_at": "2026-07-05T10:20:00Z",
 "is_corrected": True, ...}
# redact_row() output: period_started_at = "2026-07-05T09:45:00Z", is_corrected = True
```

**`admin_driver_distance_logs` — same substitution, applied to `spans` before the
per-span drill-down loop runs**, so `from`/`to`/`seconds` in the response reflect the
corrected boundaries transparently; `is_corrected` is added alongside the existing
`is_reconstructed` field on each log entry.

Non-correction case (the current state for 100% of existing rows) is provably unchanged —
see `test_original_values_kept_when_no_correction_on_file` and
`test_distance_logs_keeps_original_when_no_correction_on_file`.

## 8. Rollback plan

- **Migration**: `DROP TRIGGER IF EXISTS driver_insurance_period_corrections_no_mutate ON
  driver_insurance_period_corrections; DROP FUNCTION IF EXISTS
  _driver_insurance_period_corrections_immutable(); DROP TABLE IF EXISTS
  driver_insurance_period_corrections;` (stated in the migration file's own header
  comment). Safe any time before a correction row exists and a downstream consumer has
  relied on it; since zero corrections have been written as of this change, the rollback
  is a pure no-op today.
- **Code**: a `git revert` of the two consumer-wiring commits is a complete rollback —
  both changes are additive reads gated behind "did we find any period ids at all", with
  no write path and no state persisted outside the new table. No feature flag was added
  because there is nothing live-tested to flag: this ships the mechanism only, with zero
  correction rows in existence, so there is no user-visible behavior to gate.
- This migration was **not applied to production** as part of this task, per the repo's
  standing convention (CLAUDE.md, `ACTION_ITEMS.md` A39) — it ships as a file for the
  normal deploy pipeline; a human applies it via `run_migrations.py` separately.

## 9. Verification performed

- [x] Automated tests run — unit only (Supabase fully mocked via `mock_supabase_client`-style
      direct `get_rows`/`insert_one` patches, this repo's convention for these two modules):
      `python3 -m pytest backend/tests/test_compliance_export_script.py
      backend/tests/test_admin_driver_distance.py -q --no-cov` → **28 passed** (17 + 11,
      including 4 new correction-preference tests + 3 new admin-distance tests).
      `python3 -m pytest backend/tests/test_migration_ordering.py -q --no-cov` → **3 passed**
      (confirms migration 355 doesn't collide with the numbering/ordering checks).
- [x] Migration syntax/behavior verified against a **real local PostgreSQL 16** instance
      (not just read): applied migration 64 + the schema-affecting half of migration 332
      (its data backfill INSERT failed only because the scratch `rides` stub table
      intentionally lacked unrelated columns — irrelevant to this migration) to reproduce
      the actual `driver_insurance_periods` shape, then applied migration 355 cleanly.
      Confirmed live: (1) a correction with a blank/whitespace `reason` is rejected by the
      CHECK constraint; (2) a second correction for the same `original_period_id` is
      rejected by the UNIQUE index; (3) `UPDATE` on an existing correction row is rejected
      by the immutability trigger; (4) `DELETE` is rejected by the same trigger; (5) RLS is
      enabled on the table with exactly one policy (`SELECT`-only) — no
      INSERT/UPDATE/DELETE policy exists for `authenticated`/`anon`.
- [x] Blast-radius grep performed — see section 4 above (full command and results stated).
- [x] Reviewed against relevant CLAUDE.md conventions: migration append-only/RLS/naming
      conventions (`backend/migrations/CLAUDE.md`), insurance-period rules, query-filter
      empty-`$in` guard rule, dual-import pattern (no dual-import needed — these are
      read-only route/script changes, not new modules).
- [x] Self-applied `spinr-migration-reviewer` checklist (filename ordering, append-only,
      RLS coverage, reversibility, index-with-query-pattern, forward-compatibility) — see
      the session's chat-turn report for the itemized findings; no blocking issues found.
- [ ] Manual repro in staging — **not performed**, no staging environment reachable from
      this sandboxed worktree.
- [ ] Feature-flagged — not applicable; see rollback-plan section for why (mechanism-only
      change, zero correction rows exist, nothing user-visible to gate).

## 10. Sign-off

- [x] Rollback plan is concrete and testable (DROP-based migration rollback; code rollback
      is a plain revert with no data-migration entanglement since no corrections exist yet).
- [x] Blast radius is stated, not assumed (grep command + results in section 4).
- [x] No silent behavior change to an already-shipped flow: both consumers' non-correction
      output is provably byte-identical to before (new tests assert this explicitly), and
      the only new field (`is_corrected`) defaults `false` and is purely additive.

## What was NOT verified

- **Not applied against the real Supabase/production schema** — only against a scratch
  local PostgreSQL 16 instance with hand-built stub `users`/`drivers`/`rides` tables and a
  stubbed `auth.uid()` function. The RLS policy's actual behavior under a real
  `authenticated`/`service_role` Supabase JWT was not exercised end-to-end (no live
  Supabase project reachable from this sandbox) — only that RLS is enabled and exactly one
  SELECT policy exists, which is the same shape as migration 64's own policy.
- **`run_migrations.py --dry-run`/`--status` was not run** — it requires `DATABASE_URL`
  pointed at a real Postgres with the existing `schema_migrations` table, which this
  sandbox does not have. Migration ordering/prefix-collision was instead verified via
  `ls backend/migrations | sort -V | tail -1` (354 was highest; 355 is free) and
  `backend/tests/test_migration_ordering.py` (passed).
- **`backend/services/insurance_period_reconstruction_verification.py`'s
  `apply_verification_plan()` no-op was NOT wired to the new table.** ACTION_ITEMS.md B34
  explicitly frames that as a follow-up ("should be revisited"), not part of filing the
  table — left untouched, out of scope for this change by the ticket's own text.
- **The 156 legacy rides found diverging by the earlier verification pass are untouched.**
  No correction rows were written for them, and none should be without a separate,
  explicit decision — this change only builds the mechanism.
- **No production build was run** — this change touches no `admin-dashboard`/`rider-app`/
  `driver-app` frontend code, so the "real production build" verification requirement
  does not apply here.
