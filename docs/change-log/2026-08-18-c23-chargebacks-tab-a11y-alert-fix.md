# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-18 |
| Author | Claude Code (session) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | payments |
| PR / commit link | (added on push) |
| Related issue or gap ID | ACTION_ITEMS.md C23 — deferred accessibility follow-up from PR #4197 |

## 1. Issue / gap identified

PR #4197 (C23 dispute-pack UI wiring) explicitly deferred a dedicated
accessibility pass on its new code, flagging it as a known gap rather than
assuming it clean. Running that deferred review found two real WCAG 2.1 AA
blockers: the Chargebacks tab's two error banners (fetch failure and
evidence-pack download failure) had no `role="alert"`, so a screen-reader
user with focus elsewhere on the page would never be told the banner
appeared.

## 2. Root cause

Those two banners were written before the tab had `role="alert"` anywhere
in it; the pattern was only added later for the submit-dialog's inline
error (`submitError`, already correct) and the table-level loading spinner
(`role="status"`), but not retrofitted onto the two pre-existing/adjacent
error banners.

## 3. Fix / remediation

Added `role="alert"` to both banners:
- The top-level `error` banner ("Failed to load chargebacks...")
- The `downloadError` banner (evidence-pack download failure)

`role="alert"` is an implicit assertive live region per the ARIA spec, so
no separate `aria-live` attribute is needed.

Two new regression tests pin this: each renders the tab with a failing API
call and asserts `screen.getByRole("alert")` finds the banner with the
expected text.

**Not fixed in this PR** (per the reviewer's own verdict, these were
WARNINGS rather than blockers, and require actual manual verification this
session can't perform):
- A speculative dialog-focus-return race on successful submit (Radix's
  default close-focus-return could, in theory, target a DOM node about to
  be replaced by the "Submitted" badge) — flagged as needing a manual
  keyboard/screen-reader pass, not confirmed broken from code alone.
- No `DialogDescription` on the submit-confirmation dialog (only
  `DialogTitle`) — Radix best-practice, not a hard failure.
- The disabled "no ride linked" download button doesn't explain *why* it's
  disabled to a screen reader.

These three are noted here rather than silently dropped; a future pass
with real screen-reader/keyboard testing should confirm/close them.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated** — two `role="alert"` attribute additions and
  two new tests on the same already-reviewed component. No other file
  touched.
- No behavior change for sighted users — `role="alert"` has no visual
  effect.

## 5. User-experience effect

- **Internal admin only.** A screen-reader-using admin now gets announced
  when either error banner appears, instead of having to independently
  discover it. No effect on sighted users.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/disputes/chargebacks-tab.tsx` | Added `role="alert"` to the `error` and `downloadError` banners | WCAG 2.1 AA — screen-reader announcement of failures on a deadline-monitoring surface |
| `admin-dashboard/src/app/dashboard/disputes/chargebacks-tab.test.tsx` | 2 new tests | Pin the `role="alert"` regression |

## 7. Before / after

```tsx
# Before
{error && (
  <div className="flex items-center justify-between gap-3 border-b bg-red-50 ...">
```

```tsx
# After
{error && (
  <div role="alert" className="flex items-center justify-between gap-3 border-b bg-red-50 ...">
```

(Same change applied to the `downloadError` banner.)

## 8. Rollback plan

`git revert` — attribute-only addition, no schema/backend/logic change.

## 9. Verification performed

- [x] Automated tests: 2 new tests, both passing; full tab suite (9 tests)
  passes (`vitest run src/app/dashboard/disputes/`)
- [x] `eslint` clean (same two pre-existing, unrelated warnings as before)
- [x] **Real production build run**: `npm run build` — succeeded
- [x] `spinr-accessibility-reviewer` review: this fix directly addresses
  its two BLOCKER findings; its remaining WARNINGS (dialog focus race,
  missing DialogDescription, disabled-button reason) are explicitly noted
  above as not yet fixed, pending manual verification
- [ ] Manual screen-reader pass — not performed, no screen reader
  available in this session; the three WARNING-level items above remain
  genuinely unverified, not silently assumed clean

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed: isolated, attribute-only
- [x] No silent behavior change to an already-shipped flow — visual
  behavior unchanged, only screen-reader announcement added
