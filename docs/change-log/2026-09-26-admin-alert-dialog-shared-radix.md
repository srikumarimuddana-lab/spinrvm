# Change Impact & Risk Log: confirmation dialogs inside a side sheet take focus, and Escape closes only them

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-26 |
| Author | Claude Code session (claude.ai/code) |
| Surface(s) | admin-dashboard (shared `components/ui/alert-dialog.tsx`) |
| Domain (Sentry tag) | admin |
| PR / commit link | UX program W2.0 — branch `claude/spinr-animations-admin-ux-x7nl5x` |
| Related issue or gap ID | Found while building W2.1 (in-app confirmations); fix approach chosen by the user on 2026-09-26 ("Switch AlertDialog source") |

## 1. Issue / gap identified

A confirmation dialog opened from inside a side sheet or dialog misbehaves. Two places do this today, both inside the driver detail sheet: deleting a driver note, and the driver action bar. Confirmed in real Chromium:

| | Before | After |
|---|---|---|
| Keyboard focus when the dialog opens | stays on the button behind the dialog | moves to Cancel |
| Escape | closes the dialog **and the driver sheet** | closes only the dialog |

For a keyboard or screen-reader user, the confirm and cancel buttons were not reachable in the expected order (WCAG 2.1 SC 2.4.3 focus order, 2.1.1 keyboard). For every admin, Escape threw away the sheet they were working in.

## 2. Root cause

`components/ui/alert-dialog.tsx` imported the standalone `@radix-ui/react-alert-dialog` (1.1.23). That package bundles its own copies of Radix's focus trap (`react-focus-scope` 1.1.16) and dismissable layer (`react-dismissable-layer` 1.1.19). Sheet, Dialog and DropdownMenu come from the `radix-ui` package, which uses 1.1.7 and 1.1.11.

Radix tracks the stack of open layers and focus traps in module-level registries. With two copies, the sheet can't see the dialog above it:
- its focus trap pulls focus back into the sheet
- it treats Escape as meant for itself

## 3. Fix / remediation

`alert-dialog.tsx` imports `AlertDialog` from `radix-ui`, like the other wrappers. That is the `AlertDialog` bundled with `radix-ui` 1.4.3 (version 1.1.15), which shares the sheet's registries. The wrapper's markup, classes and API are unchanged.

**Alternatives considered:**
- **Upgrade the whole `radix-ui` package** so every component moves to the newer internals. It's also a root-cause fix, but a dependency upgrade across every menu, sheet, select and tooltip.
- **Leave it and avoid nesting.** It keeps the existing bug.

The user chose this option.

## 4. Risk & impact on existing functionality

- **Blast radius:** every admin confirmation dialog, about 480 `AlertDialog` references across the dashboard and company portal. All go through this one wrapper; a grep finds no other importer of `@radix-ui/react-alert-dialog`.
- **Version change:** `AlertDialog` moves from 1.1.23 to 1.1.15, a patch-level difference with the same API.
  - Typecheck passes against the new types.
  - All 88 admin test files (763 tests) pass, including existing tests that open and confirm dialogs.
  - The patch notes between those versions were not read, but the review found the two versions' type definitions byte-identical.
- **Top-level dialogs:** their behaviour doesn't change. They were never nested, so the registry split didn't affect them.
- **Visual:** the markup and Tailwind classes are identical. The 6 baselined pages render no open dialog, so the visual-regression job should be unchanged.
- **Dependency:** `@radix-ui/react-alert-dialog` stays in `package.json` but is no longer imported. Removing it is a separate lockfile change and a follow-up; leaving it is harmless.
- **Not changed, observed only:** `@radix-ui/react-toast` also bundles its own dismissable layer (1.1.19). Toasts shown over an open sheet were not tested and could have a similar interaction.

## 5. User-experience effect

- **Admins using the driver detail sheet:** when a confirmation opens, focus moves to it. Escape now cancels only the confirmation and leaves the sheet open.
- **Everywhere else:** no visible change.
- **Mid-session:** applies on the next page load after deploy.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/components/ui/alert-dialog.tsx` | Import from `radix-ui` instead of `@radix-ui/react-alert-dialog`; comment explains why | One shared focus and layer registry with Sheet, Dialog and DropdownMenu |
| `admin-dashboard/src/components/ui/alert-dialog.test.tsx` | New, 3 tests: focus moves in; Escape closes only the dialog; confirm keeps the sheet open | Regression guard |

## 7. Before / after

```tsx
// Before
import * as AlertDialogPrimitive from "@radix-ui/react-alert-dialog"
// After
import { AlertDialog as AlertDialogPrimitive } from "radix-ui"
```

## 8. Rollback plan

**No feature flag:** it's a one-line import in a shared UI wrapper, with no data or API involvement. **Rollback:** revert the commit; admin redeploys through Vercel.

## 9. Verification performed

- [x] **Regression test:** 3 new tests pass. On the old import the test file never completes: the two focus traps recurse until `Maximum call stack size exceeded`, and the run was killed by a 60 s timeout.
- [x] **Real browser:** a harness page bundled with esbuild from the app's own `Sheet` and `AlertDialog` wrappers, driven in Chromium with Playwright.
  - **Before:** focus stayed on the trigger, and Escape closed the sheet.
  - **After:** focus is on Cancel, Escape closes only the dialog, confirm runs the action with the sheet still open, and there are no console errors.
- [x] **Full admin suite:** 88 files, 763 tests pass.
- [x] **Typecheck:** `tsc --noEmit` passes.
- [x] **Lint:** ESLint on both files is clean.
- [x] **Production build:** `npm run build` succeeds.
- [x] **Accessibility review:** `spinr-accessibility-reviewer` ran on the diff (code-read, plus the new test file).
  - **Blocker:** none.
  - **Confirmed:**
    - The type definitions of the two `react-alert-dialog` versions (standalone 1.1.23, bundled 1.1.15) are **byte-identical**.
    - No AlertDialog in the app uses a version-specific prop. The only hit is `asChild` on the trigger, which both versions support.
    - The `role="alertdialog"` and labelling wiring is unchanged.
    - This fixes two real nested cases: `driver-action-bar.tsx` and `driver-notes.tsx`, both inside the driver detail sheet.
  - **Should-fix, deferred as the logged follow-up:** remove the unused `@radix-ui/react-alert-dialog` from `package.json`, so no one imports the separate copy again. It's kept out of this PR because it rewrites the lockfile.

## 10. What was NOT verified

- **The real driver detail sheet was not driven end to end;** it needs a logged-in admin and a backend. The harness used the same `Sheet` and `AlertDialog` wrappers with a stand-in body.
- **The Radix patch notes** for `react-alert-dialog` 1.1.15 through 1.1.23 were not reviewed.
- **Toasts over an open sheet** were not tested (see §4).
- **No screen-reader pass** (NVDA or VoiceOver).
