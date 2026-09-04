# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-04 |
| Author | Claude, follow-up to a `/design Spinr Apps` audit finding asking to either kill or finish the dormant `.theme-v2` "Quiet Console" CSS |
| Surface(s) | admin-dashboard (investigation only — **no code changed**) |
| Domain (Sentry tag) | admin |
| PR / commit link | PR following this log, branch `claude/kill-dormant-theme-v2-css` |
| Related issue or gap ID | `/design Spinr Apps` audit finding: "Kill the dormant `.theme-v2` 'Quiet Console' CSS... or ship it" |

**Outcome: no code removed, nothing shipped further. The audit finding is stale — case (b) from this task's own instructions ("reachable behind a flag that could be flipped, with a real settings toggle") applies, so per CLAUDE.md's pre-merge gates this cleanup task correctly stops here rather than forcing an outcome.**

## 1. Issue / gap identified

The design audit (dated 2026-08-28, before the work described below) characterized `.theme-v2` in `admin-dashboard/src/app/globals.css` as dormant, half-built dead code: "a second, half-built theme system... sitting unused is pure risk." It recommended either finishing the migration or deleting it.

## 2. Root cause

The audit finding predates a substantial, already-completed body of work on this exact flag. On 2026-08-31, three stages of a deliberately-scoped 4-stage "Quiet Console" rollout were built and merged under the *same* `admin_theme_v2_enabled` flag (retargeted from an earlier, abandoned 2026-08-21 restyle direction rather than adding a second flag) — see `docs/change-log/2026-08-31-quiet-console-stage-1-3.md`. Stage 4 (canary, then flip the flag on) is explicitly documented there as "a human decision, not part of this PR" — i.e. the system is deliberately left mid-rollout, waiting on a human, not abandoned. On 2026-09-03 the flag's admin Settings description and code comments were further corrected (`docs/change-log/2026-09-03-admin-theme-flag-canary-copy-fix.md`) because someone was actively discussing flipping it. So as of this investigation the "dormant" characterization is out of date by about a week of active, documented work — not evidence of neglect.

## 3. Fix / remediation

**None applied.** Per this task's own instructions (step 5): "If it IS reachable (e.g. a real settings toggle exists): do NOT attempt to finish a whole second theme system yourself... Instead, stop, document exactly what you found... and leave the code untouched." That condition is met (see blast-radius section below), so no CSS, component, or backend file was touched.

## 4. Risk & impact on existing functionality — blast-radius grep results

Grepped the entire repo (not just admin-dashboard) for `theme-v2` / `Quiet Console` (case-insensitive) and, separately, for the flag key `admin_theme_v2_enabled`. Every hit and its disposition:

| File | What's there | Conclusion |
|---|---|---|
| `admin-dashboard/src/app/globals.css:199-274` | The `.theme-v2` class + `html:not(.dark) .theme-v2` block itself: quiet-neutral palette (light-mode only, dark deliberately untouched), retargeted `--radius` (0.625rem → 0.375rem), new `--shadow-card` token | **Not dead.** Extensively commented, WCAG-contrast-verified, actively maintained (last touched 2026-08-31) |
| `admin-dashboard/src/app/dashboard/layout.tsx:16,23` | `DashboardShell` reads `useFeatureFlag("admin_theme_v2_enabled")` and applies the `theme-v2` class to the shell `<div>` | **Live wiring**, not dead code — this is the actual activation path |
| `admin-dashboard/src/app/dashboard/settings/page.tsx:1318-1344` | A real, rendered `<Switch>` labeled "Enable refreshed admin theme" bound to the flag, calling `update("admin_theme_v2_enabled", v)` | **Reachable today** — any admin with settings-write access can flip this in production right now, no deploy needed |
| `admin-dashboard/src/hooks/useFeatureFlag.tsx` | Flag plumbing: reads `GET /api/admin/settings`, explicit allowlist includes `admin_theme_v2_enabled`, defaults `false` | Real, tested plumbing shared with `admin_command_palette_enabled` (a working, unrelated feature — confirms the pattern itself is sound, not a dead experiment) |
| `admin-dashboard/src/app/layout.tsx:6-13` | Loads the Plus Jakarta Sans variable font unconditionally so the flag can toggle typography client-side with no re-render | Deliberate, documented prerequisite for the flag, not leftover |
| `admin-dashboard/src/components/sidebar.tsx` (active-nav rule), `page-header.tsx` (heading weight), `card.tsx` (`--shadow-card` token consumption), `badge.tsx` (5 new `outline-*` variants) | Flag-gated Stage 1/2 component changes, each verified byte-identical to today when the flag is off | Live, working, additive-only code — not unreachable |
| 17 files under `dashboard/{drivers,staff,bulk-operations,monitoring,quests,rides,safety,sentry-logs,service-areas,support-tickets}/**` (`drivers/page.tsx`, `drivers/_components/driver-notes.tsx`, `drivers/_components/driver-statements-panel.tsx`, `staff/page.tsx`, `bulk-operations/page.tsx`, `monitoring/ride-panel.tsx`, `monitoring/driver-panel.tsx`, `monitoring/page.tsx`, `quests/page.tsx`, `rides/_components/ride-list.tsx`, `rides/_components/ride-detail-modal.tsx`, `safety/page.tsx`, `sentry-logs/page.tsx`, `service-areas/page.tsx`, `support-tickets/page.tsx`, `support-tickets/tickets/page.tsx`, `support-tickets/tickets/[id]/page.tsx`) | Stage 3: flag-gated `Badge`-variant alternates for ad-hoc status/category color pills, mapped by real semantic meaning | Deliberate, already-shipped Stage 3 work (2026-08-31), not speculative scaffolding |
| `backend/schemas.py`, `backend/routes/admin/settings.py`, `backend/migrations/269_settings_admin_theme_v2.sql`, `backend/tests/test_kill_switch_flags.py`, `backend/tests/test_admin_settings_write_allowlist_drift.py` | Backend schema field, admin-settings write-allowlist entry, the migration that added the column (`DEFAULT FALSE`), and tests covering the flag | Real, tested, production-applied backend support — confirms the flag is a first-class citizen of the existing `app_settings`-in-DB pattern (per CLAUDE.md's "Settings in DB" convention), not a one-off |
| `admin-dashboard/src/components/command-palette.tsx`, `admin-dashboard/src/lib/command-palette-routes.ts` | Matched the combined grep only via the sibling `admin_command_palette_enabled` flag, no `theme-v2` reference | **Unrelated** — different feature, out of scope |

**Blast radius: cross-surface but entirely inert with the flag off (its current, unchanged production value).** No caller anywhere activates `.theme-v2` except the one real Settings-page switch, which no automated process touches. Nothing here reads/writes ride, dispatch, payment, corporate, or safety state.

## 5. User-experience effect

None — no code changed. If a super-admin were to flip the flag (independent of this investigation), the effect is exactly what `docs/change-log/2026-08-31-quiet-console-stage-1-3.md` §5 already documents: quieter light-mode neutrals, tighter corners, flat cards, a thin nav rule, lighter header weight, and consistent status-badge coloring across 17 files — all internal-admin-facing only.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `docs/change-log/2026-09-04-remove-dormant-theme-v2-css-investigation-only.md` | This log (new file) | Records the investigation outcome per task instructions |

No other file in the repository was modified by this change.

## 7. Before / after

Not applicable — no behavior-changing diff.

## 8. Rollback plan

Not applicable — nothing was applied. (For completeness: the flag itself already has a documented, no-deploy rollback — flip `admin_theme_v2_enabled` back to `false`, its default — per `docs/change-log/2026-08-31-quiet-console-stage-1-3.md` §8.)

## 9. Verification performed

- [x] Blast-radius grep across the **entire repo** (not just admin-dashboard) for `theme-v2`, `Quiet Console`, and `admin_theme_v2_enabled` — 18+ frontend files and 5 backend files found and individually triaged (table above).
- [x] Read `admin-dashboard/src/app/globals.css:1-280`, `dashboard/layout.tsx`, `hooks/useFeatureFlag.tsx`, and `dashboard/settings/page.tsx` in full to confirm the flag is genuinely wired end-to-end (backend column → settings endpoint → frontend hook → real UI switch → CSS class application), not a stub.
- [x] Cross-checked `backend/migrations/269_settings_admin_theme_v2.sql` — confirms the column defaults `FALSE` in production today, consistent with the audit's "unused in production" observation, but reachable-not-dead per the toggle above.
- [x] Read prior change-log entries `2026-08-31-quiet-console-stage-1-3.md` and `2026-09-03-admin-theme-flag-canary-copy-fix.md` in full to confirm this is documented, deliberate, in-progress work (not silently abandoned) with Stage 4 (canary) explicitly named as the pending human decision.
- [x] No code changed, so `tsc --noEmit` / `npm run build` / the test suite were not run for this change — there is nothing for them to catch. (The most recent code touching this flag, 2026-09-03, already has its own build/lint verification on record in the log cited above.)

## What was NOT verified

- No new build/test run was performed, because no code changed — see above.
- Did not attempt to determine whether Stage 4 (canary rollout) should now proceed; that is an explicit human/product decision per the 2026-08-31 log, outside a cleanup task's scope, and outside what was asked here.
- Did not check whether the design-audit finding was based on a checkout that predated 2026-08-31's work, or on an actually-different observation; either way, the current repository state does not match "dormant/half-built" as of 2026-09-04.

## 10. Sign-off

- [x] Rollback plan is concrete and testable — not applicable (no change applied); the flag's own rollback is already documented elsewhere.
- [x] Blast radius is stated, not assumed — full table above, every grep hit triaged individually.
- [x] No silent behavior change to an already-shipped flow — none occurred; this is a documentation-only investigation record.
