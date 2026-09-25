# Change Impact & Risk Log — admin toggles for destination mode and saved-place shortcuts

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code |
| Surface(s) | backend / admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/admin-flag-toggles` |
| Related issue or gap ID | C136 follow-up (migration 482); `docs/change-log/2026-09-25-fix-saved-addresses.md` follow-up |

## 1. Issue / gap identified

Two feature switches had no dashboard control. `destination_mode_enabled` (migration 482) was admin-writable through the API but had no toggle in the UI. `saved_place_shortcuts_enabled` had no DB column at all, so turning the rider Home/Work shortcuts off needed a backend deploy.

## 2. Root cause

- Migration 482 and its API field shipped without the dashboard half.
- The saved-places fix deliberately shipped the rider switch as a code-only default (`routes/settings.py`, `.get(..., True)`). It left the column and the admin toggle as a follow-up.

## 3. Fix / remediation

- **Migration 484** adds `settings.saved_place_shortcuts_enabled BOOLEAN NOT NULL DEFAULT TRUE`. It is additive, has a COMMENT, and has a `-- Rollback:` header. It is **not applied** by this change.
- `SettingsUpdateRequest` (`routes/admin/settings.py`) gains `saved_place_shortcuts_enabled: Optional[bool]`. This follows the `destination_mode_enabled` pattern.
- `routes/settings.py`: comment only. The stale "no column exists yet" note now points to 484. Behaviour is unchanged.
- **Admin dashboard:** a new "Rider & Driver Features" card on Settings > **Operations**. It has two switches:
  - Destination mode (drivers): `checked={destination_mode_enabled === true}`, so it defaults off.
  - Saved-place shortcuts (riders): `checked={saved_place_shortcuts_enabled !== false}`, so it defaults on.
- The switch is **not** added to `AppSettings` (`backend/schemas.py`). This is deliberate:
  - The dashboard saves the whole settings object it got from GET.
  - If `AppSettings` defaulted this key, GET would return it before 484 is applied. Every save would then write a column that doesn't exist yet: PostgREST PGRST204, and the whole save fails with a 503 (`DatabaseError`; the PostgREST error is logged server-side).
  - Left out of `AppSettings`, the key appears in GET only once the column exists. Before that, it is sent only if an admin actually flips that one switch.

**Alternative considered:** put both switches in the existing "Kill Switches" card. Rejected because that card says "All default on", and destination mode defaults off. A separate card keeps the copy accurate.

## 4. Risk & impact on existing functionality

- **Blast radius: backend**
  - `SettingsUpdateRequest` is used only by `PUT /api/admin/settings`.
  - The new field is `Optional`, and the handler uses `model_dump(exclude_none=True)`, so a save that doesn't set the field never writes the column.
- **Blast radius: DB**
  - Readers of `saved_place_shortcuts_enabled`:
    - `routes/settings.py` `get_public_settings` (`.get(..., True)`).
    - `rider-app/app/_layout.tsx` (`!== false`), which reads it once from GET /settings when the app starts.
  - Readers of `destination_mode_enabled` are unchanged. See `2026-09-25-destination-mode-flag.md`.
- **Blast radius: frontend**
  - The change is limited to `admin-dashboard/src/app/dashboard/settings/page.tsx`.
  - It adds new JSX only, inside the Operations `TabsContent`. The shared `update()` helper and the save path are unchanged.
  - No shared component was modified.
- **Deploy-order risk:** until migration 484 is applied, flipping the Saved-place shortcuts switch and saving returns a 503 ("Database operation failed"; the underlying PGRST204 is logged at ERROR server-side). Nothing is written, and the admin sees the save-error toast. This matches `destination_mode_enabled` (migration 482), which shipped with the same behaviour. Saves that don't touch that switch are unaffected. Fix: apply 484 before telling admins the toggle exists.
- **Visual regression:**
  - The `dashboard-settings` Playwright baseline captures `/dashboard/settings` full-page on the **default tab (Integrations)**.
  - Radix `TabsContent` does not mount inactive tabs, so the Operations card is not in the captured DOM.
  - The baseline is expected to be unaffected, so no re-seed should be needed.

## 5. User experience effect

- **Internal admin:** Settings > Operations shows a new card with two switches. Nothing changes until an admin flips one and clicks Save Changes.
- **Rider/driver:** no change on deploy.
  - The column default (TRUE) matches the code default.
  - Destination mode stays at its current value.
  - When an admin flips a switch:
    - The backend settings cache takes up to about 60s to pick it up.
    - Rider-app picks up the shortcut change on its next GET /settings, which happens when the app starts.
    - Destination mode takes effect on the next dispatch/endpoint call after the cache refreshes.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| backend/migrations/484_settings_saved_place_shortcuts_enabled.sql | New column, default TRUE, with COMMENT and rollback header | Gives the switch a place to persist |
| backend/routes/admin/settings.py | `saved_place_shortcuts_enabled: Optional[bool]` on `SettingsUpdateRequest` | Lets admins write it |
| backend/routes/settings.py | Comment only | The stale "no column yet" note |
| backend/tests/test_admin_settings_write_allowlist_drift.py | Snapshot gains the column. Adds tests: both flags round-trip True/False, and GET includes `destination_mode_enabled` | Drift guard plus write-path coverage |
| admin-dashboard/src/app/dashboard/settings/page.tsx | New "Rider & Driver Features" card on the Operations tab | Adds the UI toggles |
| admin-dashboard/src/__tests__/dashboard/settings-feature-toggles.test.tsx | New: checks defaults, and that an untouched shortcut key is never sent | Regression coverage |

## 7. Before / after

Additive only: a new model field and a new card. No existing behaviour changed. The one behaviour-relevant line:

```python
# before: SettingsUpdateRequest had no saved_place_shortcuts_enabled (extra="ignore" dropped it)
# after:
saved_place_shortcuts_enabled: Optional[bool] = None
```

## 8. Rollback plan

- **Behavioural (no deploy):**
  - `UPDATE settings SET saved_place_shortcuts_enabled = true WHERE id = 'app_settings';`
  - For destination mode, set `destination_mode_enabled` to the value you want in the same way, or flip it back in the dashboard.
- **Schema:** `ALTER TABLE settings DROP COLUMN IF EXISTS saved_place_shortcuts_enabled;`
  - This is safe because the code reads the key with a True default.
  - Revert the backend field first, so saves don't send a column that no longer exists.
- **UI:** revert the commit. The card is purely additive.

## 9. Verification performed

- **Backend:** `python -m pytest -q -p no:cacheprovider -o addopts="" -k "settings or admin_settings or destination or saved_place"` (with `--ignore=tests/rls --ignore=tests/direct_pool`): **1023 passed, 1 skipped**.
- **Targeted backend tests:** drift test, `test_settings_column_parity.py` (auto-discovers 484's column) and `test_public_settings_saved_place_shortcuts.py`: 44 passed. `test_audit_migration_drift.py` and `test_admin_migration_status.py`: 8 passed.
- **Lint:** `ruff check` and `ruff format --check` on the changed Python files are clean.
- **admin-dashboard vitest:** `settings-feature-toggles.test.tsx` plus `pages.smoke.test.tsx`: 30 passed.
- **admin-dashboard:** `npx tsc --noEmit` is clean. A real production `npm run build` (Next.js, Turbopack) succeeded, with `/dashboard/settings` in the route table.

## 10. What was NOT verified

- Migration 484 was **not applied** to any database, and there was no check against live Supabase. The write path was tested only with mocked `db_supabase`.
- The Playwright visual-regression suite was not run locally. The claim that the baseline is unaffected is reasoned from Radix Tabs unmount behaviour and the default tab, not from a screenshot.
- The rider-app and driver-app runtime reaction to flipping the switches was not exercised here. Those read paths are unchanged and are covered by their own earlier change logs.
