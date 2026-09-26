# Change Impact & Risk Log: admin toast policy (errors stay until dismissed, up to 3 at once)

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-26 |
| Author | Claude Code session (claude.ai/code) |
| Surface(s) | admin-dashboard (shared toast system, which the company portal uses too) |
| Domain (Sentry tag) | admin (feedback UI only) |
| PR / commit link | UX program W2.2 — branch `claude/spinr-animations-admin-ux-x7nl5x` |
| Related issue or gap ID | Plan W2.2 ("errors persist until dismissed, limit 3, role=status/alert"); scorecard "one feedback system per surface" |

## 1. Issue / gap identified

- **One toast at a time:** `TOAST_LIMIT = 1`, so a later toast replaced the current one. A success toast right after an error erased the error.
- **Errors timed out:** every toast disappeared after 5 s, errors included, so an admin looking away missed a failed save.
- **Same announcement for everything:** all toasts were announced the same way (Radix default "foreground", assertive), including routine confirmations.
- **Close button:** it had no accessible name (an icon-only X) and stayed invisible until hover or focus.

## 2. Root cause

The shadcn toast scaffold's defaults (limit 1, one duration, no per-variant type) were never tuned for an admin console.

## 3. Fix / remediation

| File | Change |
|---|---|
| `use-toast.ts` | `TOAST_LIMIT` 1 → 3. The oldest drops off beyond 3. |
| `toaster.tsx` | Per-variant defaults (below). A caller's own `duration` or `type` still wins, e.g. the 1.5 s "Summary copied" toasts. |
| `toast.tsx` | Close button: `aria-label="Dismiss notification"`, and always visible on error toasts. Other toasts keep the hover/focus reveal. |

**Per-variant defaults in `toaster.tsx`:**
- **Error** (`variant: "destructive"`): `duration={Infinity}`, so it stays until dismissed (Radix skips the timer for `Infinity`); `type="foreground"`, announced assertively.
- **Everything else:** `type="background"`, announced politely, with the default 5 s.

**Alternative considered:** replacing the toast system with a different library (e.g. sonner). Rejected: 72 files call `toast()`, and the policy fits in the existing Radix props.

## 4. Risk & impact on existing functionality

- **Blast radius:** every `toast()` call in admin-dashboard: 72 files, 245 of them error toasts. The company portal renders the same `Toaster`. `use-crud-toast.ts` goes through the same path.
- **Behaviour change, persistence:** error toasts no longer disappear by themselves. An admin must close them (the X, Escape while focused, or swipe), or a newer toast pushes them out beyond 3.
- **Behaviour change, stacking:** up to 3 toasts stack in the viewport where 1 showed before.
- **Behaviour change, announcements:** success and info toasts are announced politely instead of interrupting.
- **Explicit durations are honoured:** a caller that sets `duration` keeps it. Only the 5 bulk-operations "Summary copied" toasts do this today (1.5 s, non-error).
- **Visual baselines:** the baselined pages don't render toasts in their captured state; CI's visual job confirms.
- **Error text colour:** the text of error toasts is fixed separately in W2.2a (white on red).

## 5. User-experience effect

- **Staff and company-portal users:** an error message stays visible until you close it, with a visible, labelled X. Up to 3 messages can show at once, so a quick "Saved" no longer wipes out an earlier error.
- **Screen-reader users:** errors are announced immediately, and routine confirmations politely.
- **Mid-session:** applies on the next page load after deploy.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/components/ui/use-toast.ts` | `TOAST_LIMIT` 1 → 3 | A new toast doesn't erase an error |
| `admin-dashboard/src/components/ui/toaster.tsx` | Per-variant `duration`/`type` defaults | Errors persist and are assertive; others are polite |
| `admin-dashboard/src/components/ui/toast.tsx` | Close button labelled; always visible on errors, at full-strength white | Persistent toasts must be easy to dismiss |
| `admin-dashboard/src/components/ui/toaster.test.tsx` | New, 6 tests | Persist, auto-dismiss, caller override, limit, announcements, close button |
| `admin-dashboard/src/hooks/useConfirm.tsx` | Comment only | No longer claims the destructive text colour is undefined (W2.2a defines it) |

## 7. Before / after

```tsx
// Before (toaster.tsx)
<Toast key={id} {...props}>
// use-toast.ts
const TOAST_LIMIT = 1;
```

```tsx
// After
const isError = props.variant === "destructive"
<Toast key={id} duration={isError ? Infinity : undefined} type={isError ? "foreground" : "background"} {...props}>
// use-toast.ts
const TOAST_LIMIT = 3;
```

## 8. Rollback plan

**No feature flag:** the plan gates W2.2 as "none", and the change is to feedback UI only. **Rollback:** revert the PR; admin redeploys through Vercel. No data is touched.

## 9. Verification performed

- [x] **New tests:** 6. Persist, limit, announcement and close-button fail on the old code; auto-dismiss and caller-override pass on both, as regression guards.
- [x] **Full admin suite:** 96 files, 795 tests pass on this branch, rebuilt from `main` after W2.2a merged.
- [x] **Typecheck:** `tsc --noEmit` passes.
- [x] **Production build:** `npm run build` succeeds.
- [x] **Lint:** ESLint on the touched files is clean.
- [x] **Accessibility review:** `spinr-accessibility-reviewer` ran on W2.2a and W2.2 (code-read).
  - **Blocker:** none.
  - **Should-fix, done:** the always-visible close icon used 70% white, which is 2.98:1 on red, under SC 1.4.11's 3:1. It now uses full white (4.83:1), and the test guards it.
  - **Should-fix, not changed; recorded in §10:** on narrow screens the toast viewport sits at the top, full width. Three persistent error toasts could cover header controls until dismissed.
  - **Confirmed:**
    - Keyboard dismissal works (the close button is tab-reachable; Radix provides the F8 hotkey and Escape).
    - No poller fires error toasts. The 4 pages with polling loops catch those errors in local state, and their error toasts come only from user actions.
    - All existing `text-destructive-foreground` uses sit on red.
  - **Nit, done:** the stale `useConfirm` comment.
- [x] **Radix behaviour checked in source** (`@radix-ui/react-toast` 1.2.23):
  - `startTimer` returns early for `Infinity`
  - the announcer uses `aria-live="assertive"` for `foreground` and `"polite"` for `background`

## 10. What was NOT verified

- **No real-browser or screen-reader pass** (NVDA or VoiceOver) of the announcements.
- **No screenshot** of three stacked toasts at narrow widths.
- **Narrow screens:** below the `sm` breakpoint the viewport is full-width at the top (`fixed top-0 … max-h-screen`, no scroll). Up to 3 persistent error toasts could cover header controls until dismissed with the X or a swipe. Not checked on a device.
- **All 387 toast call sites were not audited.** The reviewer checked the polling pages, and none auto-fires error toasts.
