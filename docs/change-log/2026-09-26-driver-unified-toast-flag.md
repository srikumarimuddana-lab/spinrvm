# Change Impact & Risk Log: driver unified-toast flag (W2.3, backend half)

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-26 |
| Author | Claude Code, UX program W2.3 |
| Surface(s) | backend (this PR); driver-app (next PR) |
| Domain (Sentry tag) | drivers |
| PR / commit link | Branch `claude/spinr-animations-admin-ux-x7nl5x` (backend PR). The driver-app half follows in its own PR and extends this log. |
| Related issue or gap ID | `.claude/plans/2026-09-25-ux-enhancement-program.md` W2.3, decision D4; `docs/audit/2026-09-25-ux-scorecard-world-class-minimal.md` gap 3 ("two toast systems") |

**Status: backend half.** This PR adds the flag and serves it on `/drivers/config`. No app reads it yet, so nothing changes for anyone until the driver-app half ships and the flag is turned on.

## 1. Issue / gap identified

The rider and driver apps show toasts through two different implementations with different timing, positioning and dedupe behaviour. The UX program (W2.3) moves driver toasts onto the rider-style banner, dark-launched behind `driver_unified_toast_enabled`.

## 2. Root cause

The two apps grew their toasts independently:

- **driver-app:** `hooks/useToast.ts::showToast(type, title, message?)` calls `react-native-toast-message`'s `Toast.show` with `visibilityTime: 3500` and `topOffset: 60`. It renders through `components/toastConfig.tsx`, and the host `<Toast config={toastConfig} />` is mounted in `app/_layout.tsx`. It is the only entry point: nothing else in driver-app or `shared/` calls `react-native-toast-message`.
- **rider-app:** `components/Toast.tsx` plus the zustand store `store/toastStore.ts`.

There is no toast in `shared/`; only the length-cap helper `shared/utils/toastMessage.ts` is shared.

## 3. Fix / remediation

**This PR: backend flag plumbing, following the migration 487 (`android_auto_offer_tone_enabled`) precedent.**

- Migration `490_driver_unified_toast_flag.sql` adds `settings.driver_unified_toast_enabled BOOLEAN NOT NULL DEFAULT FALSE`. It uses `ADD COLUMN IF NOT EXISTS` with a 5 s `lock_timeout`, and the header carries the rollback.
- `SettingsUpdateRequest.driver_unified_toast_enabled` lets `PUT /api/admin/settings` write it.
- `GET /drivers/config` serves `driver_unified_toast_enabled`. Only an exact `True` turns it on. A settings read failure, or a row that predates migration 490, serves `False`.
- **Admin control:** the flag is set through the admin settings API or SQL. There is no admin-dashboard UI, matching 487. The `/dashboard/settings` page was deliberately not touched because it has a merge-blocking visual baseline.
- **Numbering:** this was built as 489. `main` gained `489_monitoring_connection_summary.sql` first, so it was renumbered to 490 before it was ever merged or applied.

**Next PR: the driver-app half.** Per the user's decision (2026-09-26, "driver-only copy"), it adds a driver-app toast component and store modelled on the rider app's. Nothing moves to `shared/`, and `rider-app/` is not touched.
- `hooks/useToast.ts` routes to it when the flag is on, keeping the same signature, so no caller changes.
- `hooks/useDriverDashboard.ts` sets the flag from `/drivers/config`, the same way as 487.
- `app/_layout.tsx` mounts the host.

## 4. Risk & impact on existing functionality

**Blast radius of this PR: additive, backend only.**

| Touched | Other readers/writers (grepped) | Risk |
|---|---|---|
| `settings.driver_unified_toast_enabled` (new column) | `SettingsUpdateRequest`, `get_driver_config` only | Additive, default false, no reader in any app yet. |
| `/drivers/config` response | `shared/hooks/queries/driverQueries.ts` (`useDriverConfig`, read by `useDriverDashboard`), `driver-app/lib/androidAuto/carSession.ts` (`loadDriverConfig`), `driver-app/e2e/fixtures.ts` | Additive key; today's apps ignore it. |
| `SettingsUpdateRequest` | `admin_update_settings` (the `exclude_none` save) | A save that leaves the field unset does not write it (tested). |

- **Public settings:** the flag is not added to the public, unauthenticated `GET /settings`. It follows 487's driver-only path through `/drivers/config`.
- **Driver-app half:** its blast radius (the 26 production importers of `showToast`) is in the next PR's entry.

## 5. User-experience effect

- **This PR:** none. No app reads the flag yet, and it defaults to off.
- **Once the driver-app half ships and the flag is on:** drivers see the rider-style banner. Per the user's decisions it keeps the driver app's 60 px top offset, ends clear of the SOS column, doesn't de-duplicate, and stays up for 3.5 s. The next PR describes the full effect.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/490_driver_unified_toast_flag.sql` | New BOOLEAN column, default false; rollback in header | The flag |
| `backend/routes/admin/settings.py` | `driver_unified_toast_enabled: Optional[bool]` | Admin save can write it |
| `backend/tests/test_admin_settings_write_allowlist_drift.py` | Column added to snapshot; added to the explicit-False round-trip parametrize | Drift guard + admin write |
| `backend/routes/drivers/profile.py` | Field on `/drivers/config` (`is True`) | Deliver the flag to the app |
| `backend/tests/test_drivers_shared_status_profile_coverage.py` | Parametrized flag test, absent-column default, failure default | Coverage |

## 7. Before / after

Purely additive; no existing behaviour changed.

## 8. Rollback plan

- **Behaviour:** `UPDATE public.settings SET driver_unified_toast_enabled = false WHERE id = 'app_settings';`. No app reads the flag yet.
- **Schema:** `ALTER TABLE public.settings DROP COLUMN driver_unified_toast_enabled;`, after the readers are retired. It is also in the migration header.

## 9. Verification performed

- `pytest tests/test_admin_settings_write_allowlist_drift.py tests/test_drivers_shared_status_profile_coverage.py`: 109 passed on this branch (10 + 99).
  - With the model field removed, the first file fails 3 tests: the drift test and both round-trip cases.
  - Against the pre-change `profile.py`, the second fails 6: the failure-default test, 4 parametrized cases and the absent-column case.
- `ruff check` and `ruff format --check` passed on the touched Python files, and the pre-commit hook passed.
- **Migration review:** `spinr-migration-reviewer` found no blockers and no should-fixes; verdict "safe to apply".
  - Numbering: 490 is free on `main` and not in `.known_duplicate_prefixes.json`.
  - The `ADD COLUMN ... NOT NULL DEFAULT FALSE` is metadata-only, idempotent and documented for rollback.
  - Every place 487's column is registered also registers this one, except the driver-app consumer, which is the next PR.
- **Comment fix:** the migration's header and its `COMMENT ON COLUMN` were corrected before merge. They had described the unified toast as a `shared/` component, which it is not.
- Blast-radius greps: `android_auto_offer_tone_enabled` (the precedent), `drivers/config`, `react-native-toast-message`, `hooks/useToast`, `docs/known-forks.md`.

### What was NOT verified

- The migration was not applied to any database, local or staging. The SQL was only reviewed.
- The full backend suite was not run, only the two affected test files.
- No driver-app code is in this PR, so no driver-app check applies here.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [ ] Driver-app half: next PR
