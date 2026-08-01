# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-01 |
| Author | Claude Code |
| Surface(s) | backend (new standalone script, no app/API surface) |
| Domain (Sentry tag) | admin (compliance/regulatory tooling; not a Sentry-instrumented path — CLI script) |
| PR / commit link | (this PR) |
| Related issue or gap ID | Follow-up to #3100; closes the `scripts/compliance_export.py` gap documented in `docs/compliance/sgi-quarterly.md` §1/§4/§7 |

## 1. Issue / gap identified

`.claude/context/regulatory-sk.md` obligates Spinr to produce trip records (distance + insurance-period linkage) within 14 days of an SGI/regulator subpoena or request, target run <30 min against prod. No such tooling existed — `docs/compliance/sgi-quarterly.md` had already documented this as a tracked gap (`scripts/compliance_export.py` referenced but absent).

## 2. Root cause

The script was referenced in the regulatory checklist before it was ever built — a forward-reference to planned tooling, not a bug. `docs/compliance/sgi-quarterly.md` §5 lists 6 open questions (SGI submission format/channel, PII boundary per report, etc.) that block the *periodic* (quarterly/annual) reports specifically. The *on-demand* trip-record obligation this script covers already had a confirmed SLA (≤14 days, <30 min) and doesn't depend on those open questions, since it's handed over per the specific subpoena/request rather than through a standing SGI channel — so it was buildable now without waiting on legal/founder sign-off for the other two reports.

## 3. Fix / remediation

Added `scripts/compliance_export.py`: a read-only, paginated export of `driver_insurance_periods` (periods 2/3 only — the ride-linked, commercial-coverage legs) joined to `rides` via a PostgREST embedded select, scoped by `--start`/`--end` and optionally `--driver-id`/`--ride-id`. Output is CSV or JSON, to a file or stdout. Applies a strict, non-overridable PII boundary (driver_id/ride_id only — no rider identity, no raw address, no coordinates). Writes one row to the existing `compliance_export_events` table (migration 263, already RLS-gated to admin/super_admin read, append-only, 7-year retention) per invocation, under a new `report_type` value (`trip_record_subpoena_export`) alongside the three that table already supports.

Also updated `docs/compliance/sgi-quarterly.md` and `.claude/context/regulatory-sk.md` to mark this row as built (was previously corrected in #3114 to say "not yet built" — now genuinely isn't).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** New standalone script under top-level `scripts/`, invoked manually by an operator — no route, no background loop, no scheduled job, nothing else calls it.
- **`driver_insurance_periods` and `rides`:** read-only (`get_rows` only). Grepped for other consumers of `driver_insurance_periods`: `services/data_transfer/*` (a different, existing PIPEDA bulk-export/import feature — unaffected, no write collision since this script never writes to that table), `routes/admin/drivers.py`, and the ride/driver state-machine writers in `utils/insurance_periods.py` (also unaffected — this script never mutates the table, so there's no contention with `record_period_transition`'s open-row invariant).
- **`compliance_export_events`:** the one write this script makes. Existing writer is `routes/admin/compliance.py`'s `_log_compliance_export` (gst_pst_remittance / insurance_period_audit / dsar_lookup). This script writes directly via `db.insert_one` with a new `report_type` value (`trip_record_subpoena_export`) — purely additive, no existing row shape or reader changed. The table has no `report_type` CHECK constraint, so no migration was needed for the new value.
- **No shared component/hook/utility was modified** — this is new code only, not a change to an existing file.

## 5. User-experience effect

None. Backend-only CLI tooling for internal/compliance operators; no rider, driver, corporate-admin, or internal-admin-dashboard UI surface touches this.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `scripts/compliance_export.py` | New file | The export script itself |
| `backend/tests/test_compliance_export_script.py` | New file | Unit tests: redaction shape, PII-boundary assertion, filter construction, pagination, audit-row write, CSV/JSON output |
| `docs/compliance/sgi-quarterly.md` | Updated tooling-status table (§1, §4), export-shape section (§6), definition-of-done (§7) | Mark the on-demand row built; keep quarterly/annual gaps clearly still open |
| `.claude/context/regulatory-sk.md` | Updated the on-demand bullet from "not yet built" to "built"; also corrected an adjacent stale note (quarterly template doc already exists) and added the annual-roster form-fill tooling pointer | Keep the domain doc in sync with the new script, avoid re-drifting |

## 7. Before / after

Pure additive code — no existing behavior changed. `.claude/context/regulatory-sk.md`'s on-demand bullet:

```
# Before
- **On-demand** trip record production within 14 days of subpoena or regulator request,
  target run < 30 min against prod — **not yet built**: `scripts/compliance_export.py`
  is referenced here but doesn't exist; see `docs/compliance/sgi-quarterly.md` §7 ...
```

```
# After
- **On-demand** trip record production within 14 days of subpoena or regulator request,
  target run < 30 min against prod — built: `scripts/compliance_export.py`
  (see `docs/compliance/sgi-quarterly.md` §1/§6 for the PII boundary and export shape it implements)
```

## 8. Rollback plan

`git revert` is sufficient and complete here — this is a new, standalone, read-only script with no scheduled invocation, no migration, and no mutation of existing data. Reverting removes the file; the one new `report_type` value in `compliance_export_events` is inert (no CHECK constraint, no other code depends on its presence) so no data-level cleanup is needed even for rows already written by a prior run.

## 9. Verification performed

- [x] Automated tests run — unit: `pytest backend/tests/test_compliance_export_script.py` (11 tests, all passing), exercising: redaction shape and PII-boundary (no rider/address/coordinate fields), correct `$and`-composed date-range + period filter construction (verified the `_apply_filters` `elif` chain would otherwise silently drop one bound of a naive single-dict range filter — see code comment), pagination stop condition, `compliance_export_events` audit-row content, CSV and JSON output to file.
- [x] Manual repro: ran `python3 scripts/compliance_export.py --help` to confirm the CLI parses; ran `ruff check` on both new files (clean).
- [ ] Not run against real Supabase/staging — exercised only against mocked `get_rows`/`insert_one`, per the repo's stated unit-test tier convention. No integration-tier test added.
- [x] Blast-radius grep performed — see §4 (searched for other `compliance_export_events` and `driver_insurance_periods` consumers).
- [x] Reviewed against relevant CLAUDE.md conventions: dual-import pattern (top-level `scripts/` importing `backend.db_supabase`, mirroring `scripts/manage_admin.py`), money arithmetic (`Decimal` + `ROUND_HALF_UP`, no float), PIPEDA PII rules (no raw GPS/address/name/email in output), audit-trail-not-a-log-sink distinction (writes to `compliance_export_events`, not a log line).
- [ ] Feature flag: not applicable — CLI script with no user-facing surface, not gated behind `app_settings`.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data cleanup required)
- [x] Blast radius is stated, not assumed: isolated, read-only against existing tables, one additive audit-row type
- [x] No silent behavior change to an already-shipped flow — this is new functionality, not a modification of one; no "User experience effect" applies since there is no UI surface
