# Change Impact & Risk Log: staff confirmations use an in-app dialog instead of the browser pop-up

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-26 |
| Author | Claude Code session (claude.ai/code) |
| Surface(s) | admin-dashboard (staff dashboard; the company portal is W2.1b) |
| Domain (Sentry tag) | admin (confirmation UI only) |
| PR / commit link | UX program W2.1 — branch `claude/spinr-animations-admin-ux-x7nl5x` |
| Related issue or gap ID | Plan W2.1; scorecard "one feedback system per surface"; depends on W2.0 (shared-Radix AlertDialog) |

## 1. Issue / gap identified

10 staff actions asked for confirmation with the browser's `window.confirm()` pop-up, while the rest of the dashboard uses its own dialog. The pop-up can't be styled, sits outside the app's focus management, and looks like a browser warning.

| Page | Action |
|---|---|
| Venues | delete venue |
| Service areas → Incentives | delete incentive |
| Drivers | bulk Stripe KYC refresh; bulk payout-history sync; rewrite statement totals |
| Driver detail sheet | reveal SIN |
| Topbar account menu | sign out everywhere |
| Users | sync rider emails to Stripe |
| Earnings → Payouts | retry failed payouts; close payout period |

## 2. Root cause

These were written before the dashboard adopted `AlertDialog`. Several sit inside async flows (preview, then confirm, then apply), where a state-driven dialog would have meant splitting each function in two.

## 3. Fix / remediation

**A new `useConfirm()` hook** (`src/hooks/useConfirm.tsx`) wraps the shared `AlertDialog`:
- `await confirm({ title, description, confirmLabel, destructive })` resolves `true` only on the confirm button.
- Cancel, Escape, a newer `confirm()` call and unmounting all resolve `false`.

Each call site changes from `if (!window.confirm(msg)) return;` to `if (!(await confirm({...}))) return;`. The wording is unchanged, each action's control flow is identical, and each confirm button names its action ("Delete", "Reveal SIN", "Rewrite totals").

**Red confirm buttons** use the Button's own destructive variant. They are used for deletes, the SIN reveal, sign-out-everywhere, the Stripe email sync (sends personal data to a US processor), the statement-totals rewrite and closing a payout period.

**Long previews** (statement totals, Stripe email sync) scroll inside a dialog capped at 90% of the viewport height.

**Alternative considered:** a per-site `open` state with an inline `AlertDialog`, the pattern already used elsewhere. Rejected because the preview-then-confirm flows (Stripe email sync, statement totals) and the SIN reveal would each have to be split in two, which adds regression risk on money and personal-data paths.

**Deferred to after W2.2:** the 5 `alert()` error messages. A toast would disappear on its own where an alert must be dismissed, so they move once W2.2 makes error toasts stay until dismissed.

## 4. Risk & impact on existing functionality

- **Blast radius:** 9 files, all admin-dashboard.
  - `topbar.tsx` renders on every dashboard page, including all 6 baselined pages.
  - `drivers/page.tsx` is a baselined page.
  - A closed `AlertDialog` renders no DOM, so the baselines should be unchanged; CI's visual-regression job confirms.
- **Behaviour change, keyboard:** focus starts on Cancel, so Enter now cancels. With `window.confirm()`, Enter confirmed. This is deliberately safer for destructive actions, but a keyboard habit changes.
- **Behaviour change, busy state:** `window.confirm()` blocked the page; the dialog is modal but asynchronous. Where a flow sets a "running" state before confirming (statement totals, Stripe email sync), the button shows its spinner while the dialog is open. The `finally` blocks reset it on Cancel.
- **Race checked:** a second request while one is pending resolves the first `false`, so it never runs. Radix calls the confirm button's `onClick` before its own close handler, so a confirm click can't be turned into a cancel. Both were traced in Radix source by the security review, and the first is tested.
- **Found and fixed during W2.1:**
  - Opening the dialog from the topbar's account menu left `pointer-events: none` on `<body>` after Cancel, freezing the dashboard. W2.0 fixes the cause (the separate Radix copy), and `topbar.test.tsx` guards it.
  - The existing `bg-destructive text-destructive-foreground` pattern drops the button's white text, because `text-destructive-foreground` isn't a theme colour. The hook avoids it.

## 5. User-experience effect

- **Staff:** these 10 confirmations appear as the dashboard's own dialog, with a clear title, the same explanation and an action-named button, instead of a grey browser pop-up.
- **Keyboard users:** Enter cancels rather than confirms.
- **Mid-session:** applies on the next page load after deploy.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/hooks/useConfirm.tsx` | New hook | One in-app confirmation for async flows |
| `admin-dashboard/src/hooks/__tests__/useConfirm.test.tsx` | New, 7 tests | Confirm, Cancel, Escape, Enter, unmount, supersede, red button and scroll cap |
| `admin-dashboard/src/app/dashboard/venues/page.tsx` | 1 site | Venue delete |
| `admin-dashboard/src/app/dashboard/service-areas/_components/incentives-tab.tsx` | 1 site | Incentive delete |
| `admin-dashboard/src/app/dashboard/drivers/page.tsx` | 3 sites | Bulk KYC, payout sync, statement totals |
| `admin-dashboard/src/app/dashboard/drivers/page.bulk-confirm.test.tsx` | New, 2 tests | Statement totals never applies on Cancel |
| `admin-dashboard/src/components/topbar.tsx` | 1 site | Sign out everywhere |
| `admin-dashboard/src/components/__tests__/topbar.test.tsx` | New, 2 tests | Cancel keeps the page clickable and makes no API call |
| `admin-dashboard/src/app/dashboard/drivers/_components/driver-detail-sheet.tsx` | 1 site | SIN reveal |
| `admin-dashboard/src/app/dashboard/users/page.tsx` | 1 site | Stripe email sync |
| `admin-dashboard/src/app/dashboard/earnings/_components/payouts-tab.tsx` | 1 site | Retry failed payouts |
| `admin-dashboard/src/app/dashboard/earnings/_components/payouts-compliance.tsx` | 1 site | Close payout period |
| `admin-dashboard/src/app/dashboard/earnings/_components/payouts-compliance.test.tsx` | New, 2 tests | Period close never runs on Cancel |

## 7. Before / after

```tsx
// Before
if (!window.confirm(`Close ${periodLabel}? This writes an audit-log entry ...`)) return;
```

```tsx
// After
const { confirm, dialog: confirmDialog } = useConfirm();
if (!(await confirm({
    title: `Close ${periodLabel}?`,
    description: "This writes an audit-log entry ...",
    confirmLabel: "Close period",
    destructive: true,
}))) return;
// ...and {confirmDialog} rendered once in the component.
```

## 8. Rollback plan

**No feature flag:** staff-only confirmation UI with the same wording and control flow; the plan gates W2.1 as "none; staff-only UX note". **Rollback:** revert the PR; admin redeploys through Vercel. No data is touched.

## 9. Verification performed

- [x] **New tests:** 13 in 4 files. The topbar, period-close and statement-totals tests fail on the old code.
- [x] **Full admin suite:** 92 files pass (775 tests before the review follow-ups added 3 more).
- [x] **Typecheck:** `tsc --noEmit` passes.
- [x] **Production build:** `npm run build` succeeds.
- [x] **Lint:** ESLint on each touched file matches before; no new warnings.
- [x] **Chromium check** (harness using the real `DropdownMenu` and `useConfirm`), for a dialog opened from a menu item by mouse and by keyboard:
  - focus lands on Cancel and is still there 500 ms later
  - Escape returns focus to the menu trigger
  - the page stays clickable
  - no errors
- [x] **Security review:** `spinr-security-auditor` ran. Verdict: safe to merge.
  - **Blocker:** none.
  - **Confirmed:**
    - no path runs a guarded action without a confirm click (the Radix handler order was traced in source)
    - all 12 dialog texts match the old wording word for word, with no new personal data (the SIN value is never in the dialog)
    - every `return` sits in the same place relative to state setters and `try`/`finally`
  - **Should-fix, both done:** a test that a newer request resolves the older one `false`; a test for a Drivers-page bulk money action.
  - **Nit, kept:** the SIN reveal uses the driver selected when the button was clicked, which is the intended target.
- [x] **Accessibility review:** `spinr-accessibility-reviewer` ran.
  - **Blocker:** none.
  - **Should-fix, handled:**
    - **The red button's text colour:** confirmed broken (the class merge drops the white text) and fixed in the hook.
    - **Long descriptions could overflow:** fixed with a 90vh cap and scrolling.
    - **Focus might snap back to the menu trigger:** checked in Chromium and it does not reproduce.
  - **Nit, done in W2.1b:** the reversible section archive shouldn't be red.
  - **Nit, not changed:** `--primary` and `--destructive` are both reds. That's a design-token question for W6.

## 10. What was NOT verified

- **The SIN reveal and the Stripe email sync** are covered by the hook's tests and the reviews, not by a test of their own page. The driver sheet needs about 60 props to render in a test.
- **Screenshots:** none of the dialogs were captured, and the visual-regression baselines don't open any dialog.
- **No screen-reader pass** (NVDA or VoiceOver).
- **Pre-existing, not fixed here:** 24 other files use `bg-destructive text-destructive-foreground`, so their red buttons have the same inherited-text-colour problem. Fixing them centrally means defining `--color-destructive-foreground`, which would change text colour on baselined pages. Logged as a follow-up.
