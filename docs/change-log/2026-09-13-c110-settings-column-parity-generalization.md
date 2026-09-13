# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-13 |
| Author | Claude Code (session `session_013hMEsuEPVu7gwAk21XB4hF`) |
| Surface(s) | backend (tests + one additive migration) |
| Domain (Sentry tag) | admin |
| PR / commit link | this change |
| Related issue or gap ID | `ACTION_ITEMS.md` C110 (fixed), C111 (found and closed in the same pass) |

## 1. Issue / gap identified

`backend/tests/test_settings_column_parity.py::test_every_api_field_has_a_column` only
cross-checked a hardcoded `_EXPECTED_313_COLUMNS` set (the 24 fields migration 313 added) against
declared migration columns. Any `SettingsUpdateRequest` field added after 313 was invisible to the
regression loop — confirmed for both `directions_proxy_enabled` (migration 415) and
`driver_turn_by_turn_enabled` (migration 418), which only stayed safe because each PR happened to
also ship its own bespoke, field-specific test.

## 2. Root cause

The check was written against a point-in-time snapshot (313's 24 fields) instead of the live field
set on `SettingsUpdateRequest`, so it could never see anything declared afterward — the same
manual-discipline gap migration 313 itself was created to close, one layer up.

**Alternative considered:** keep requiring a bespoke per-field test (the status quo two later PRs
already relied on) instead of generalizing the shared check. Rejected — that's exactly the
"depends on each PR's author remembering" failure mode this ticket exists to eliminate, and the
generic parsing helper (`_declared_settings_columns()`) already existed and already worked.

## 3. Fix / remediation (revised after adversarial review — see below)

`test_every_api_field_has_a_column` now checks **every** field on `SettingsUpdateRequest` against
`_declared_settings_columns() | _baseline_settings_columns()`, not just the pinned 313 set.

**First draft, and what was wrong with it:** the first version of this fix made
`_baseline_settings_columns()` a hand-typed, 22-entry allowlist for fields that predate migration
tracking, reasoned about from call-site defaults and CLAUDE.md's documented conventions. Per
CLAUDE.md's pre-merge gate #10, that draft was sent to `spinr-test-coverage-reviewer` before
committing. It found a real problem: **7 of the 22 hand-typed entries had zero schema evidence
anywhere in the repo** — not in any migration, not in the bootstrap schema file — and two of them
(`new_ride_requests_enabled`, `dispute_stripe_evidence_submission_enabled`) are kill switches whose
own Change Impact Logs explicitly admit they were never exercised against a real Supabase row
(`docs/change-log/2026-08-22-g5-new-ride-requests-kill-switch.md` §10:
*"Not exercised against a real Supabase `app_settings` row — mocked throughout"*;
`docs/change-log/2026-08-18-c23-dispute-evidence-pack-and-submission.md`: similar). "Reviewed
against production usage" was true for 15 of the 22 and false for the other 7 — an unsafe claim to
have made uniformly. It also found that the 3 fields I'd flagged as an "unconfirmed, needs a live
DB check" open question (`driver_matching_algorithm`, `min_driver_rating`, `search_radius_km`,
filed as C111) were resolvable *without* live DB access: `backend/supabase_schema.sql` (the
bootstrap "run this in the Supabase SQL Editor" DDL) has an explicit `CREATE TABLE settings (...)`
block listing all three verbatim, alongside 12 other baseline entries — a file I had not checked.

**Final fix, addressing both findings:**

1. `_baseline_settings_columns()` now **mechanically parses** `backend/supabase_schema.sql`'s
   bootstrap `CREATE TABLE settings (...)` block, the same way `_declared_settings_columns()`
   parses migrations' `ADD COLUMN` — replacing the hand-typed guess with something that can't drift
   from what the file actually says. This independently confirms 15 fields and closes C111 in the
   same pass (cited the file directly instead of filing a live-DB follow-up).
2. The 7 fields with **no evidence anywhere** got migration
   `419_settings_missing_columns_round2.sql` instead of a baseline guess — the direct fix for a
   genuinely missing column, matching migration 313/415/418's own precedent, reviewed by
   `spinr-migration-reviewer` before committing (see that migration file's header for full
   reasoning and defaults sourced from each field's existing Python-level fallback).
3. Added `_regressed_settings_fields()` (the exact logic the real test asserts on, factored out) so
   `test_a_field_with_no_column_and_no_baseline_entry_is_caught` exercises the real
   `SettingsUpdateRequest.model_fields` read via `monkeypatch`, not just the extracted
   `_fields_missing_columns` set-arithmetic helper in isolation — the reviewer's second finding was
   that the first draft's version of this test would not have caught a regression reintroduced at
   the real call site.
4. `test_baseline_and_declared_do_not_overlap` replaces the earlier
   `test_baseline_set_has_no_dead_entries` (renamed/refocused now that the baseline is parsed, not
   hand-maintained — a "dead manual entry" can no longer happen by construction, but a genuine
   double-declaration between the bootstrap file and a migration is still worth surfacing).

## 4. Risk & impact on existing functionality

- **Blast radius**: `backend/tests/test_settings_column_parity.py` (test-only) plus one new,
  additive migration (`419_settings_missing_columns_round2.sql`). No existing production code
  path was edited — the migration only adds columns with defaults matched to each field's current
  Python-level fallback (see the migration file), so no currently-running behavior changes when it
  applies.
- **Could this regress a working flow?** No. For the test file: full suite passes (33 tests). For
  the migration: `ADD COLUMN IF NOT EXISTS ... DEFAULT <value>` is additive and idempotent; every
  default was sourced from the code's own existing fallback (not invented), so a row that already
  omits these columns reads identically before and after the migration applies. The two kill-switch
  fields default to the value that keeps current behavior unchanged
  (`new_ride_requests_enabled` → `true`, matching `schemas.py`'s existing `bool = True`;
  `dispute_stripe_evidence_submission_enabled` → `false`, matching its own ship-dark default).
- **Money/Stripe-adjacent**: `dispute_stripe_evidence_submission_enabled` gates a real,
  irreversible Stripe evidence-submission call (`routes/admin/dispute_evidence_submission.py`).
  This migration only makes the flag *persistable* at its already-documented safe default — it
  does not change the endpoint's own guard logic, super-admin gate, or idempotency claim.

## 5. User-experience effect

None directly visible. Before this migration, an admin attempting to change any of the 7 newly
backed fields via `PUT /api/admin/settings` for the first time would have gotten a 500
(PGRST204) that also silently dropped every other field submitted in the same request — this fix
prevents that from ever being hit, rather than changing anything a user currently sees.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/tests/test_settings_column_parity.py` | Generalized `test_every_api_field_has_a_column` via new `_regressed_settings_fields()`; rewrote `_baseline_settings_columns()` to parse `supabase_schema.sql` instead of a hand-typed list; added `_fields_missing_columns()` helper and 2 regression tests | C110 fix |
| `backend/migrations/419_settings_missing_columns_round2.sql` | New — adds the 7 confirmed-missing `settings` columns | C110 fix (the part a baseline entry could not safely cover) |
| `ACTION_ITEMS.md` | C110 closed (revised); C111 closed (resolved via `supabase_schema.sql`, no live DB check needed) | Backlog accuracy |

## 7. Before / after

```python
# Before (original code, prior to any change this session)
def _baseline_settings_columns() -> set[str]:
    return set()

def test_every_api_field_has_a_column():
    declared = _declared_settings_columns() | _baseline_settings_columns()
    api_fields = set(SettingsUpdateRequest.model_fields.keys()) - _NOT_PERSISTED
    known_recent = _declared_settings_columns()
    regressed = sorted(
        f for f in api_fields
        if f in _EXPECTED_313_COLUMNS and f not in known_recent  # only ever checks 24 fields
    )
    assert not regressed, ...
```

```python
# After (final, post-review)
def _baseline_settings_columns() -> set[str]:
    # mechanically parses backend/supabase_schema.sql's CREATE TABLE settings (...)
    ...  # returns 15 columns, not a hand-typed guess

def _regressed_settings_fields() -> list[str]:
    declared = _declared_settings_columns()  # now includes migration 419's 7 columns
    baseline = _baseline_settings_columns()
    api_fields = set(SettingsUpdateRequest.model_fields.keys())  # every field, not just 313's
    return _fields_missing_columns(api_fields, declared, baseline)

def test_every_api_field_has_a_column():
    regressed = _regressed_settings_fields()
    assert not regressed, ...
```

## 8. Rollback plan

- Test file: `git revert` — no migration, no data write, no flag.
- Migration 419: additive-only (`ADD COLUMN IF NOT EXISTS ... DEFAULT <value>`); rollback is
  `ALTER TABLE public.settings DROP COLUMN IF EXISTS <each column>` (full statement in the
  migration's own header comment). Safe to revert without a second deploy: dropping these columns
  only removes the ability to persist a value an admin has never successfully set before (per the
  "no schema evidence" finding), so no live-observed state is lost.

## 9. Verification performed

- [x] `cd backend && python3 -m pytest tests/test_settings_column_parity.py -q --no-cov` — **33
  passed**, including the strengthened regression-guard test (now exercises the real
  `SettingsUpdateRequest.model_fields` read via `monkeypatch`, not just the extracted helper) and
  the parser-overlap sanity test.
- [x] `ruff check` + `ruff format --check` on the touched Python file — clean.
- [x] A probe script (not committed) confirmed `_regressed_settings_fields()` returns `[]` against
  the current 131-field `SettingsUpdateRequest` — full coverage, zero remaining unbacked fields,
  zero remaining hand-typed guesses.
- [x] Migration syntax cross-checked against migration 313's own established
  multi-column-`ALTER TABLE` pattern (same file already in the repo) rather than invented fresh.
- [x] `spinr-test-coverage-reviewer` reviewed the first draft (see §3) and the fixes above address
  every finding it raised. `spinr-migration-reviewer` reviewed migration 419 before this commit,
  per CLAUDE.md's pre-merge gate #10 and its own "use PROACTIVELY on any new migration" mandate.

**Not verified**: this migration has not been applied against a real Supabase instance (no DB
access from this environment) — verified statically (parser confirms it's the only source of
these 7 columns; defaults cross-checked against each field's existing Python-level fallback) but
not by running `run_migrations.py` against a live database.
