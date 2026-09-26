# Change Impact & Risk Log: the last browser alert() error messages become in-app errors

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-26 |
| Author | Claude Code session (claude.ai/code) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin / rides / surge (error display only) |
| PR / commit link | UX program W2.1c — branch `claude/spinr-animations-admin-ux-x7nl5x` |
| Related issue or gap ID | Plan W2.1; deferred until W2.2 made error toasts persist |

## 1. Issue / gap identified

5 staff flows reported errors with the browser's `alert()` pop-up:
- sign-out-everywhere failure
- venue save failure
- venue delete failure
- admin ride force-cancel failure
- saving a surge above 2.5× without a written justification

## 2. Root cause

These pre-date the dashboard's toast system. They were left as `alert()` in W2.1 because, before W2.2, a toast would have disappeared on its own where an alert had to be acknowledged.

## 3. Fix / remediation

**Four sites use an error toast** (`variant: "destructive"`, which W2.2 keeps until dismissed and announces assertively). Each has a short title, and the original message is now the description:

| Site | Title |
|---|---|
| Topbar sign-out-everywhere failure | "Couldn't sign out other sessions" |
| Venue save failure | "Couldn't save venue" |
| Venue delete failure | "Couldn't delete venue" |
| Surge above 2.5× without justification | "Justification required" |

**Sign-out-everywhere:** the local session is still cleared and the user is still sent to `/login`. The toast survives the redirect, because the `Toaster` lives in the root layout and the navigation is client-side.

**Ride force-cancel shows its error inside the dialog instead**, as `role="alert"` text: "Couldn't cancel the ride: …". The force-cancel Dialog stays open on failure, and a modal Radix Dialog hides the rest of the page, including the toast viewport, from screen readers. So a toast there would have been a step back from `alert()`. The typed reason is kept for a retry, and the error clears on retry or reopen.

## 4. Risk & impact on existing functionality

- **Blast radius:** 4 files (`topbar.tsx`, `venues/page.tsx`, `rides/_components/ride-detail-modal.tsx`, `service-areas/_components/general-tab-form.tsx`), with error display only.
  - The sign-out, save, delete, cancel and surge-save logic is unchanged.
  - The surge save is still blocked by the same early `return`.
- **Behaviour change:** these errors no longer block the page with a browser dialog. Toasts sit at the top right (or top on narrow screens) until closed; the ride-cancel error sits inside its dialog.
- **Surge rules:** the 2.5× justification requirement and its enforcement are untouched; only how the message appears changed.
- **Visual baselines:** none of the 6 baselined pages shows these errors in its captured state.

## 5. User-experience effect

- **Staff:** these errors appear inside the dashboard instead of as browser pop-ups. The ride-cancel failure appears in the cancel dialog itself, so the admin can retry without retyping the reason.
- **Mid-session:** applies on the next page load after deploy.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/components/topbar.tsx` | `window.alert` → error toast | In-app, persistent error |
| `admin-dashboard/src/components/__tests__/topbar.test.tsx` | +1 test | Failure path: error toast, local sign-out still happens |
| `admin-dashboard/src/app/dashboard/venues/page.tsx` | 2 × `alert` → error toast | In-app, persistent error |
| `admin-dashboard/src/app/dashboard/rides/_components/ride-detail-modal.tsx` | `alert` → inline `role="alert"` error in the dialog | Readable by screen readers while the modal is open |
| `admin-dashboard/src/app/dashboard/service-areas/_components/general-tab-form.tsx` | `alert` → error toast | In-app, persistent error |
| `admin-dashboard/src/app/dashboard/service-areas/_components/general-tab-form.test.tsx` | New, 1 test | An above-cap save without justification is blocked and shows the toast |

## 7. Before / after

```tsx
// Before (general-tab-form.tsx)
alert("A written justification is required for surge multipliers above 2.5× (regulatory + reputational risk).");
return;
```

```tsx
// After
toast({
  title: "Justification required",
  description: "A written justification is required for surge multipliers above 2.5× (regulatory + reputational risk).",
  variant: "destructive",
});
return;
```

## 8. Rollback plan

**No feature flag:** error display only, with the same messages and control flow. **Rollback:** revert the PR; admin redeploys through Vercel. No data is touched.

## 9. Verification performed

- [x] **Tests:** 2 new. The surge test fails on the old form.
- [x] **Full admin suite:** 97 files, 797 tests pass on this branch, rebuilt from `main` after W2.2 merged.
- [x] **Typecheck:** `tsc --noEmit` passes.
- [x] **Production build:** `npm run build` succeeds.
- [x] **Lint:** ESLint on the changed files has 0 errors. Its 29 warnings are all pre-existing, and none is on a changed line.
- [x] **No `alert()` left:** a grep finds no remaining browser `alert()` in admin-dashboard source.
- [x] **Accessibility review:** `spinr-accessibility-reviewer` ran (code-read, including Radix source).
  - **Blocker:** none.
  - **Should-fix, done:** a toast raised while the modal force-cancel Dialog is open is hidden from screen readers, so that site shows the error inside the dialog instead.
  - **Confirmed:**
    - the sign-out toast survives the `/login` navigation
    - the topbar and venue confirm dialogs close before their requests can fail
    - the copy is clear
  - **Pre-existing nits, logged as follow-ups:** the surge on/off toggle has no accessible name; the multiplier label isn't tied to its input; the justification textarea has no accessible name.

## 10. What was NOT verified

- **The ride-cancel inline error has no test.** The ride detail modal needs its map and several API mocks to render.
- **No screen-reader pass.** Whether error toasts fired from inside other modal dialogs elsewhere in the app are announced depends on Radix's `aria-hidden` handling and needs a real screen-reader check. That's a pre-existing, app-wide question, logged in the plan.
