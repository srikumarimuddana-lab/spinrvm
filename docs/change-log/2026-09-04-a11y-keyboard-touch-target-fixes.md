# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-04 |
| Author | Claude Code (session 01Sspqro7zzjKdTbUh6D61wQ), executing a user-approved follow-up task from a design/UX audit |
| Surface(s) | rider-app, driver-app, admin-dashboard |
| Domain (Sentry tag) | admin, drivers, rides |
| PR / commit link | branch `claude/a11y-keyboard-touch-target-fixes` |
| Related issue or gap ID | Design/UX audit finding "accessibility — fix the keyboard/touch-target gaps before anything else" |

## 1. Issue / gap identified

Two binary WCAG-adjacent accessibility gaps, found by a design/UX audit and independently re-verified against the live code before fixing anything:

- **admin-dashboard**: clickable `<TableRow onClick=...>` rows were mouse-only — a screen-reader or keyboard-only user could not activate them (WCAG 2.1.1 Keyboard failure). Only one row in the whole codebase (`drivers/page.tsx:1047`) had the correct `tabIndex`/`aria-label`/`onKeyDown` pattern.
- **rider-app / driver-app**: several icon-only `TouchableOpacity` buttons render smaller than the apps' own `MIN_TOUCH` constant (44pt, `shared/utils/responsive.ts`), making them harder to hit precisely.

## 2. Root cause

- The keyboard pattern was never promoted into the shared `TableRow`/`sortable-table.tsx` component, so each page that added a clickable row had to remember to add it by hand — one did, twelve others didn't.
- The mobile touch-target gap is a case-by-case styling omission (no lint/test enforcement exists for touch-target size in either app).

## 3. Fix / remediation

**admin-dashboard (12 files, 4 commits):** added `tabIndex={0}`, a descriptive `aria-label`, and an `onKeyDown` handler (Enter/Space triggers the same action as `onClick`) to every clickable `TableRow` that lacked it: `driver-offers-panel.tsx`, `auto-payouts-panel.tsx`, `cloud-messaging/page.tsx`, `venues/page.tsx`, `quests/page.tsx`, `disputes/page.tsx`, `support/_tabs/tickets.tsx`, `support/_tabs/complaints.tsx`, `support/_tabs/flags.tsx`, `audit-logs/page.tsx`, `users/page.tsx`, `drivers/appeals/page.tsx`. Two rows (`auto-payouts-panel.tsx`, `audit-logs/page.tsx`) are only conditionally clickable — their new attributes are conditional to match, so a non-clickable row doesn't become a spurious tab stop. Did **not** attempt the "promote into the shared table component" refactor the audit floated as an option — 12 different data shapes need 12 different `aria-label` strings, so a generic default would be worse than no default; per-page fixes were the safer, smaller change. `TableRow` (`components/ui/table.tsx`) is a thin `<tr {...props}>` wrapper, confirmed to forward these props with no change needed there.

**rider-app / driver-app (2 commits, 4 files):** rather than trust the audit's four specific citations at face value, traced each one to its actual `TouchableOpacity`/View boundary first (this repo's blast-radius-before-fix convention). Result:
- **2 of the audit's 4 citations were false positives** — `profile-setup.tsx`'s avatar-edit badge and ToS checkbox are both small *visual icons* nested inside much larger touchables (a 90×90 avatar circle; a full checkbox+multi-line-text row), so the cited 24–28px size isn't the real tap target. Left unchanged.
- **1 more citation (`driver-app/.../profile.tsx`'s "32px back button") was dead code** — the `backBtn` style is defined but never referenced anywhere in the file. Nothing renders it, so nothing to fix; left in place per this repo's "notice dead code, don't delete unless asked" convention.
- **1 citation was real** (`driver-app/.../stripe-onboarding.tsx`'s 36×36 back button) — fixed.
- A wider grep beyond the audit's citations found **3 more real, previously-uncited violations**: `rider-app/app/profile-setup.tsx`'s header back button (24px icon + 8px padding ≈ 40px effective), `rider-app/app/manage-cards.tsx`'s delete-card button (34×34, a real consequential action), and `driver-app/.../profile.tsx`'s gender-picker sheet close button (32×32). All three fixed.

All four real fixes use `hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}` (expands the tappable area without changing anything visual), matching this codebase's existing hitSlop precedent (6–12px/side elsewhere in both apps).

## 4. Risk & impact on existing functionality

- **admin-dashboard**: additive-only attributes (`tabIndex`, `aria-label`, `onKeyDown`) on existing `<tr>` elements. No `onClick` handler logic changed — the new `onKeyDown` calls the exact same function the existing `onClick` already calls. No other code reads these table rows' DOM attributes. Blast radius: isolated to the 12 touched files; `TableRow`/`sortable-table.tsx` itself untouched.
- **rider-app / driver-app**: `hitSlop` only expands the invisible tap-catching area around an existing button — it does not change layout, does not overlap adjacent elements in any of the 4 touched screens (all 4 buttons are either alone in a header or have clear surrounding space), and does not change any `onPress` logic. Blast radius: isolated to the 4 touched files, each a single-purpose screen/modal.
- No change to ride state, dispatch, fares, auth, or any backend-reachable path anywhere in this diff.

## 5. User-experience effect

- **admin-dashboard**: internal-admin-facing only. Keyboard/screen-reader users can now activate all 13 clickable table rows (previously only 1 of 13); mouse users see no change at all (identical visual/hover behavior).
- **rider-app / driver-app**: rider- and driver-facing. The 4 fixed buttons (2 back buttons, 1 delete-card, 1 modal-close) become slightly easier to tap accurately; no visual change, no copy change, nothing different for anyone not specifically trying to tap near the edge of these buttons. Not reachable mid-session in a way that would confuse an already-using rider/driver — these are static header/list controls, not live ride-state UI.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/components/analytics/driver-offers-panel.tsx` | Added tabIndex/aria-label/onKeyDown to clickable row | Keyboard access |
| `admin-dashboard/src/components/auto-payouts-panel.tsx` | Same, conditional on `hasDetail` | Keyboard access |
| `admin-dashboard/src/app/dashboard/cloud-messaging/page.tsx` | Same, scheduled-messages table | Keyboard access |
| `admin-dashboard/src/app/dashboard/venues/page.tsx` | Same | Keyboard access |
| `admin-dashboard/src/app/dashboard/quests/page.tsx` | Same | Keyboard access |
| `admin-dashboard/src/app/dashboard/disputes/page.tsx` | Same | Keyboard access |
| `admin-dashboard/src/app/dashboard/support/_tabs/tickets.tsx` | Same | Keyboard access |
| `admin-dashboard/src/app/dashboard/support/_tabs/complaints.tsx` | Same | Keyboard access |
| `admin-dashboard/src/app/dashboard/support/_tabs/flags.tsx` | Same | Keyboard access |
| `admin-dashboard/src/app/dashboard/audit-logs/page.tsx` | Same, conditional on `isLong` | Keyboard access |
| `admin-dashboard/src/app/dashboard/users/page.tsx` | Same | Keyboard access |
| `admin-dashboard/src/app/dashboard/drivers/appeals/page.tsx` | Same | Keyboard access |
| `rider-app/app/profile-setup.tsx` | Added hitSlop to header back button | Undersized touch target |
| `rider-app/app/manage-cards.tsx` | Added hitSlop to delete-card button | Undersized touch target |
| `driver-app/app/driver/stripe-onboarding.tsx` | Added hitSlop to back button | Undersized touch target |
| `driver-app/app/driver/(tabs)/profile.tsx` | Added hitSlop to gender-picker sheet close button | Undersized touch target |

## 7. Before / after

```tsx
// admin-dashboard, all 12 files — before
<TableRow key={x.id} className="cursor-pointer" onClick={() => setSelected(x)}>

// after
<TableRow key={x.id} className="cursor-pointer" onClick={() => setSelected(x)}
  tabIndex={0} aria-label={`...`}
  onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setSelected(x); } }}>
```

```tsx
// rider-app / driver-app, all 4 files — before
<TouchableOpacity style={styles.backBtn} onPress={...}>

// after
<TouchableOpacity style={styles.backBtn} onPress={...} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}>
```

## 8. Rollback plan

Pure additive, client-side-only diff across 16 files, 6 commits. `git revert` of any subset of the 6 commits is a complete, independent rollback — no data touched, no migration, no coordinated deploy, no feature flag needed (each commit is scoped to one app/batch, so a single bad commit can be reverted without touching the others).

## 9. Verification performed

- [x] `npx tsc --noEmit` — clean, all three apps, for every touched file.
- [x] `npx eslint` — 0 new errors in all three apps (driver-app has 8 pre-existing unescaped-entity errors in `profile.tsx`, confirmed present on `origin/main` before this change, at unrelated lines; admin-dashboard has 28 pre-existing `set-state-in-effect` warnings, also unrelated to the touched lines).
- [x] Full automated suites run, all green: admin-dashboard `vitest` (59 files / 562 tests), rider-app `jest` (139 suites / 1947 tests), driver-app `jest` (127 suites / 1437 tests). No dedicated unit tests existed for the specific touched screens, so this is regression coverage via the full suite, not new targeted tests.
- [x] Blast-radius check performed for every fix: `TableRow`'s implementation checked (`components/ui/table.tsx`, confirmed prop-passthrough, unmodified); every mobile touch-target citation traced to its actual `TouchableOpacity`/View wrapper *before* editing, which changed the fix list (see §3).
- [x] Reviewed against CLAUDE.md conventions: task decomposition (6 commits, ≤3 files each admin-dashboard batch), surgical/additive-only changes, no PII/logging/auth/money surface touched.
- [ ] Feature flag — not applicable; additive, non-behavior-changing (for mouse/visual users), low-risk, matches this repo's precedent of not flagging pure accessibility-attribute additions.

## 10. What was NOT verified

- No on-device or emulator testing for the mobile-app hitSlop changes, and no screen-reader (VoiceOver/TalkBack) testing for the admin-dashboard keyboard fixes — none available in this environment. The `spinr-accessibility-reviewer` subagent was not run against this diff before this log was written (recommended before merge, per the originating task's own instructions).
- No visual-regression tooling exists for rider-app/driver-app at all (CLAUDE.md gate #6) — these changes are visually invisible by design (hitSlop doesn't render), so this is a lower-risk gap than a visible-UI change would carry, but it's still undisclosed-by-default and is being disclosed here explicitly.
- admin-dashboard *does* have Playwright visual-regression coverage, but only for 5 seeded baseline pages (`login`, `dashboard-home`, `dashboard-drivers`, `dashboard-monitoring`, `dashboard-settings` per CLAUDE.md) — none of the 12 pages touched here are in that seeded set, so this diff has zero visual-regression coverage from that job either. The changes are non-visual (attribute-only) so this is a lower-risk gap, same reasoning as above.
- Did not exhaustively grep for every remaining sub-44pt touch target across both mobile apps — scoped to the audit's 4 original citations plus what a systematic trace of those citations' actual touchable boundaries surfaced. Further instances may exist elsewhere in either app.

## 11. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`, no data involved)
- [x] Blast radius is stated, not assumed (isolated per file, confirmed via prop-passthrough / touchable-boundary tracing)
- [x] No silent behavior change to an already-shipped flow — this section (§5) states plainly that the only observable difference is to keyboard/screen-reader users on admin-dashboard and to imprecise-tap users on the 4 mobile buttons; nothing else changes for anyone else
