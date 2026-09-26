# Change Impact & Risk Log: admin dashboard honours Reduce Motion; alert-feed rows fade out

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code session (claude.ai/code) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | UX program W1.3 — branch `claude/spinr-animations-admin-ux-x7nl5x` |
| Related issue or gap ID | `docs/audit/2026-09-25-ux-motion-admin-website-research.md` A2; scorecard P8 |

## 1. Issue / gap identified

The admin dashboard had one Reduce Motion check, in the alert feed. Everywhere else, animations and transitions ran regardless of the OS setting:
- 82 `animate-pulse` skeletons
- dialog and sheet slides
- hover transitions

Separately, alert-feed rows animated in but vanished instantly on removal or "Clear all".

## 2. Root cause

There was no global Reduce Motion rule; animation came piecemeal from Tailwind utilities and `tw-animate-css`. The alert feed set an `initial` animation but no `exit`.

## 3. Fix / remediation

- **Reduce Motion rule:** `globals.css` gains a `@media (prefers-reduced-motion: reduce)` rule.
  - It shortens animations and transitions to 0.01 ms, runs each animation once, and turns off smooth scrolling.
  - Loading spinners (`.animate-spin`, 194 uses) keep turning, because a stopped spinner reads as a frozen screen.
  - A static test guards the rule.
- **Alert-feed rows:** they fade out over 0.15 s on removal, and not at all under Reduce Motion.
- **Considered and skipped:** a `MotionConfig reducedMotion="user"` wrapper. Its only consumer, the alert feed, already calls `useReducedMotion()`, so adding a wrapper now would be speculative per CLAUDE.md's "simplicity first".

## 4. Risk & impact on existing functionality

- **Blast radius:** app-wide CSS, but only for admins with Reduce Motion on. Nothing changes for anyone else.
- **Animation-end events:** Radix presence (dialogs, sheets, toasts) still receives its animation-end events, because the animations still run at 0.01 ms rather than being removed.
- **Visual regression:** the Playwright job doesn't emulate `prefers-reduced-motion`, so the 6 baselined pages are unaffected.
- **Alert-feed exit:** it adds 0.15 s before a removed row disappears. "Clear all" now fades the rows instead of dropping them instantly.

## 5. User-experience effect

- **Admins with OS Reduce Motion on:** skeletons, dialogs, sheets and hover effects appear instantly instead of animating; spinners still turn.
- **All admins on the Monitoring page:** Live Events rows fade out on removal.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/globals.css` | Reduce Motion media rule | Respect the OS setting everywhere |
| `admin-dashboard/src/app/reduced-motion.test.ts` | New static guard, 3 tests | Keep the rule and the spinner exemption |
| `admin-dashboard/src/app/dashboard/monitoring/alert-feed.tsx` | `exit` fade, skipped under Reduce Motion | Rows no longer vanish abruptly |

## 7. Before / after

```css
/* Before: no rule */
/* After */
@media (prefers-reduced-motion: reduce) {
  *:not(.animate-spin), *::before, *::after {
    animation-duration: 0.01ms !important; animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important; scroll-behavior: auto !important;
  }
}
```

## 8. Rollback plan

**No feature flag.** The CSS rule only applies to users who opted into Reduce Motion (accessibility precedent), and the exit fade is 0.15 s of polish.

**Rollback:** `git revert`; the admin dashboard redeploys through Vercel.

## 9. Verification performed

- [x] **New guard test:** 3 tests.
- [x] **Full admin suite:** 86 files, 758 tests pass.
- [x] **Typecheck:** `tsc --noEmit` passes.
- [x] **Production build:** `npm run build` succeeds.
- [x] **Lint:** ESLint on the touched files is clean.
- [x] **Accessibility review:** `spinr-accessibility-reviewer` ran on the diff (code-read only). Verdict: likely compliant with WCAG 2.1 SC 2.3.3.
  - **Blocker:** none.
  - **Confirmed:**
    - The rule reaches `animate-pulse` skeletons, the Radix dialog, sheet, alert-dialog and toast enter/exit animations, and `transition-*` utilities.
    - Using 0.01 ms rather than `animation: none` keeps Radix unmounting on `animationend`. No app code listens for `animationend` or `transitionend`.
    - The alert-feed exit really runs: rows sit inside `AnimatePresence` with stable ids, on "Clear all" and when the 50-row cap drops the oldest.
    - `/track`'s inline car-heading transition snaps instead of tweening, and nothing waits on it.
  - **Should-fix, checked and kept as is:** the pulsing dot beside "Live Ride Monitoring" (`animate-ping`, the only use in the app) stops pulsing and shows as a plain dot. It is decorative; the Radio icon and the heading text carry "live". Stopping the pulse is what Reduce Motion asks for.
  - **Nit, not changed:** the `::before`/`::after` part of the selector also applies inside a spinner. No spinner animates a pseudo-element (all are Lucide `Loader2`), so it has no effect today.

## 10. What was NOT verified

- **No real-browser check with Reduce Motion enabled:** jsdom can't evaluate media queries. The rule was reasoned about, and its presence is guarded statically.
- **Needs a person:** turn Reduce Motion on at the OS level and confirm, in Chrome and Safari:
  - skeletons, dialogs and sheets appear instantly
  - spinners still turn
  - the Monitoring alert feed rows appear without animation
