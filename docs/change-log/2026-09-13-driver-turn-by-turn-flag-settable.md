# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-13 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | admin |
| PR / commit link | (branch `claude/vehicle-icon-movement-animation-8tys8o`) |
| Related issue or gap ID | Follow-up to Phase 1 of `docs/proposals/2026-09-01-driver-in-app-turn-by-turn-navigation.md` (PRs A/B/C/D, #5289/#5292/#5294/#5295, all merged) |

## 1. Issue / gap identified

`driver_turn_by_turn_enabled` (the dark-launch flag for the whole turn-by-turn navigation feature) could be **read** correctly since PR A, but could not be **set** by anyone — not through the admin dashboard (no UI existed for it) and not even via a raw `PUT /api/admin/settings` API call. The feature shipped fully built and fully unreachable.

## 2. Root cause

`PUT /api/admin/settings` builds its update payload strictly from `SettingsUpdateRequest` (`backend/routes/admin/settings.py`) — a separate, hand-maintained Pydantic model, not auto-generated from `AppSettings`. PR A added the flag to `AppSettings` (`backend/schemas.py`) but never touched `SettingsUpdateRequest`, since PR A's own scope had no reason to write the flag — it only ever needed to *read* it. `SettingsUpdateRequest`'s `model_config = ConfigDict(extra="ignore")` means any field not declared there is silently dropped from a save with no error — the request succeeds, the audit log shows `changed_keys: []`, and the operator has no indication anything went wrong. This exact failure class is a recognized, previously-hit issue in this codebase: `test_settings_column_parity.py`'s own module docstring documents 24 fields that were previously in this same state (found and fixed by migration 313) — this makes it 25.

## 3. Fix / remediation

- **`backend/routes/admin/settings.py`**: added `driver_turn_by_turn_enabled: Optional[bool] = None` to `SettingsUpdateRequest`, following the exact pattern of every other plain dark-launch boolean flag in that model (e.g. `admin_theme_v2_enabled`, `dispatch_direct_pool_enabled`) — not a credential, no super-admin gate.
- **`backend/migrations/417_settings_driver_turn_by_turn_enabled.sql`** (new): adds the matching `driver_turn_by_turn_enabled BOOLEAN NOT NULL DEFAULT false` column to `public.settings`. This is not optional — `settings` is a fixed-flat-column table with no JSON catch-all (migration 313's own header), so a field `SettingsUpdateRequest` accepts without a matching column 500s the *entire* save (PostgREST `PGRST204`) the first time any admin tries to change it, including any other unrelated fields sent in the same save. `test_settings_column_parity.py` statically enforces this pairing in CI.
- **`backend/tests/test_driver_turn_by_turn_flag_settings.py`** (new): 7 tests mirroring `test_dispatch_direct_pool_flag_settings.py`'s established shape for this exact class of flag.
- **No admin-dashboard UI toggle added** — a deliberate scope decision, not an oversight. Checked the dashboard's existing dark-launch flags of this same shape (`dispatch_direct_pool_enabled`, `directions_proxy_enabled`, `driver_discreet_sos_enabled`, `rideless_sos_enabled`): **none** of them have a dashboard toggle. Only two flags in the whole settings page get a UI control at all — `admin_theme_v2_enabled` and `stale_in_progress_ride_alert_enabled` — and both are ongoing operational toggles a general admin might flip routinely, not one-time feature-rollout gates meant to be flipped once by engineering/ops during a staged verification. `driver_turn_by_turn_enabled` matches the no-UI convention, not the UI one. Building a dashboard card for it would be speculative UI ahead of the actual rollout decision — the fix here is that the flag is now settable via a direct API call (see §5), matching how every other flag in this exact category already works.

## 4. Risk & impact on existing functionality

- **Blast radius**: `SettingsUpdateRequest` is a large, actively-used model — grepped for every other consumer: only `admin_update_settings` (the single PUT handler) reads it, and it iterates `model_dump(exclude_none=True)` generically, so adding one more `Optional[bool] = None` field cannot affect how any other field is parsed or persisted. The new migration is a single additive `ADD COLUMN IF NOT EXISTS ... DEFAULT false` — no data migration, no existing row touched beyond gaining the new column at its default.
- **This PR's flag stays functionally identical to before for every existing reader**: `get_app_settings()`'s schema-default merge already returned `driver_turn_by_turn_enabled: false` for any row that predates this column; after the migration, the same row now has the column explicitly, same value, same behavior. No behavior changes for any of PR A/B/C/D's already-shipped, already-tested code paths.
- **What this unblocks, not what it changes**: after this PR, the flag can be flipped to `true` for the first time — but doing so is a manual admin action nobody has taken yet. This PR's merge alone changes nothing live.

## 5. User-experience effect

**None today** — no dashboard UI added, and the flag's default (`false`) is unchanged. Once an admin/ops person deliberately flips it (via a direct `PUT /api/admin/settings` call, e.g. `curl -X PUT https://<backend-host>/api/admin/settings -H "Authorization: Bearer <admin JWT>" -H "Content-Type: application/json" -d '{"driver_turn_by_turn_enabled": true}'`, or the equivalent from Postman/the browser dev console while logged into the admin dashboard), the already-shipped Phase 1 behavior (PRs A–D) activates: the turn-by-turn banner, camera approach-zoom, and off-route refetch all begin working for every driver in an active ride.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/settings.py` | Added `driver_turn_by_turn_enabled: Optional[bool] = None` to `SettingsUpdateRequest` | Makes the flag settable via the admin API — it was previously read-only |
| `backend/migrations/417_settings_driver_turn_by_turn_enabled.sql` | New: adds the matching `settings` column | Required pairing per `test_settings_column_parity.py` — without it, setting the flag 500s the whole save |
| `backend/tests/test_driver_turn_by_turn_flag_settings.py` | New: 7 tests (schema default, round-trip both directions, omitted-field-preserves-value, not-a-credential, migration/column pairing) | Proves the fix; mirrors `test_dispatch_direct_pool_flag_settings.py`'s established pattern for this flag class |

## 7. Before / after

```python
# Before — the field existed on AppSettings (read side) but not here (write side).
admin_theme_v2_enabled: Optional[bool] = None
# driver_turn_by_turn_enabled was NOT declared -- any admin save containing
# it silently dropped the value (extra="ignore"), no error surfaced.
admin_command_palette_enabled: Optional[bool] = None
```

```python
# After — declared, so it now persists.
admin_theme_v2_enabled: Optional[bool] = None
driver_turn_by_turn_enabled: Optional[bool] = None
admin_command_palette_enabled: Optional[bool] = None
```

## 8. Rollback plan

`git-revert-safe` — reverting this commit returns the flag to its prior (also off, also unsettable) state; no data was written by this migration beyond a defaulted column every row already implicitly had via the schema-default merge. If the migration itself needs reverting: `ALTER TABLE public.settings DROP COLUMN IF EXISTS driver_turn_by_turn_enabled;` (stated in the migration's own header, per `backend/migrations/CLAUDE.md`'s "always reversible on paper" rule).

## 9. Verification performed

- [x] New unit tests: `backend/tests/test_driver_turn_by_turn_flag_settings.py` — 7/7 passing.
- [x] `backend/tests/test_settings_column_parity.py` re-run — 31/31 passing, confirming this PR introduces no new regression against the fields that test actually checks. **Correction, found by `spinr-migration-reviewer`:** that test's regression check (`test_every_api_field_has_a_column`) only re-verifies a hardcoded set of 24 fields pinned from migration 313 — any `SettingsUpdateRequest` field added after 313, including `directions_proxy_enabled` (415) and this PR's `driver_turn_by_turn_enabled` (417), is invisible to that loop. If this PR had shipped the `SettingsUpdateRequest` field without migration 417, that generic test would still have passed. The actual protection against that mistake comes from this PR's own bespoke `test_migration_417_adds_the_column_with_false_default` (in `test_driver_turn_by_turn_flag_settings.py`), which regexes migration 417's SQL directly — not from the shared parity file. This means the shared gate's docstring promise ("keeps them in step") is narrower than it reads, and every future flag of this shape depends on its author remembering to write the same bespoke test, unless `test_settings_column_parity.py`'s regression check is later extended to cover all fields rather than the pinned 313 set — worth its own `ACTION_ITEMS.md` entry, out of scope for this PR.
- [x] Broader regression: `pytest -k "settings or admin_settings" -m "not slow"` — 304 passed, 1 skipped (pre-existing, unrelated), 0 failed.
- [x] `ruff check` and `ruff format --check` clean on both changed/new Python files.
- [x] Blast-radius grep performed: `SettingsUpdateRequest` has exactly one consumer (`admin_update_settings`).

**What was NOT verified:** a real `PUT /api/admin/settings` call against a live Supabase instance — verified only via the mocked-Supabase unit tests (this repo's standard backend unit-test convention). The migration itself was not applied against a real database in this environment (no `DATABASE_URL` available here) — its SQL was verified by direct comparison against migration 415's already-applied, identical-shape pattern, and by the static column-parity test, but not by an actual `ALTER TABLE` run.

## 10a. Migration renumbering note

This PR's migration was originally drafted and tested as `416_settings_driver_turn_by_turn_enabled.sql`. Before this PR merged, a separate, concurrently-running Claude Code session merged its own, unrelated migration as `416_corporate_accounts_rls_super_admin_fix.sql` (PR #5307, an RLS parity fix on `corporate_accounts` — no overlap in tables, columns, or purpose with this PR). Per `CLAUDE.md`'s migration-numbering rule ("if two PRs conflict on a number, the second one renames to the next free slot before merge"), this PR — being the one still open — renamed its file to `417_settings_driver_turn_by_turn_enabled.sql` and updated every internal reference (the migration's own header comment, `backend/routes/admin/settings.py`'s comment, and every reference in `backend/tests/test_driver_turn_by_turn_flag_settings.py`) accordingly. No SQL content changed, only the filename/number and its cross-references. Re-verified via `git ls-tree origin/main -- backend/migrations` immediately before renumbering that 417 was in fact the next free slot at that moment.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (revert-safe; explicit `DROP COLUMN` stated)
- [x] Blast radius is stated, not assumed (grepped `SettingsUpdateRequest`'s one consumer; confirmed no other flag of this shape has dashboard UI, so no UI-consistency regression either)
- [x] No silent behavior change to an already-shipped flow (§5: default unchanged; this only adds the *ability* to change it, which nobody has exercised yet)
