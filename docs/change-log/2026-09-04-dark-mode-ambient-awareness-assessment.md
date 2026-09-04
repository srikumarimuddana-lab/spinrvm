# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-04 |
| Author | Claude Code (agent), on behalf of vikas@ngitservices.com |
| Surface(s) | driver-app |
| Domain (Sentry tag) | drivers |
| PR / commit link | see PR opened alongside this doc |
| Related issue or gap ID | `/design Spinr Apps` audit — "dark mode is opt-in only, not time-of-day/ambient-aware" finding |

**No code changed.** This is an investigation-and-decision deliverable, not a feature build — the audit finding explicitly framed the auto-switch as gated on a precondition ("not all screens are dark-mode-complete") and asked for that precondition to be checked before building anything.

## 1. Issue / gap identified

The design audit flagged driver-app's dark mode as opt-in-only (no time-of-day or OS-`system` auto-switching), and noted this was reasonable *only* while the app's dark-mode screen coverage is incomplete — auto-switching a half-themed app into dark mode at night would show a broken mix of themed and unthemed screens. The audit asked for a re-check of that precondition once it might be met.

## 2. Root cause / current state

`shared/theme/ThemeContext.tsx` implements a full theme system (`ThemeProvider`, `useTheme()`, persisted preference via AsyncStorage, `lightColors`/`darkColors` palettes) that already supports a `'system'` preference value in its type — but the code deliberately does not act on it:

```ts
// Dark mode is OPT-IN: the app stays light unless the user explicitly turns
// on Settings → Dark Mode (which persists pref='dark'). We intentionally do
// NOT follow the OS dark setting ('system' → light) because dark-mode theming
// is not yet complete across every screen/input — auto-following the device
// would throw users into half-themed screens (white-on-white inputs). Flip
// the 'system' branch back to `systemScheme === 'dark'` once the dark audit
// is done to restore OS-follow behaviour.
const isDark = useMemo(() => pref === 'dark', [pref]);
```

`app/driver/settings.tsx` exposes this as a single boolean "Dark Mode" toggle (`renderToggle(... isDarkOn, (v) => setTheme(v ? 'dark' : 'light') ...)`) — there is no "System" option surfaced in the UI at all, even though the context plumbing for it already exists. This is a stronger form of "opt-in only" than the audit language implied: it's not just "no auto-switch," the UI doesn't even offer OS-follow as a manual choice.

### Screen coverage audit (the actual precondition check)

Grepped `driver-app/app/**` (37 screen files, excluding `_layout.tsx` route files) for `useTheme`/`useColorScheme` usage, then separately grepped every screen for hardcoded hex/rgba color literals used in structural roles (`backgroundColor`, `borderColor`, `borderTopColor`, `borderBottomColor`, `borderLeftColor`, `borderRightColor`) — the properties most likely to produce a visibly broken (not just slightly-off-brand) result in dark mode.

**Top-line usage:** 36 of 37 screens call `useTheme()` either directly or through a themed shared component (e.g. `app/driver/help.tsx` → `@shared/components/SupportScreen`, which itself calls `useTheme()`). Only `app/index.tsx` (the auth-bootstrap/splash gate screen, rendered before routing resolves and again if session recovery fails) has zero theme awareness — hardcoded `backgroundColor: '#FFFFFF'` and light-only text colors (`#666`, `#888`, `#6C63FF`), including on its visible retry-button UI shown after 3 failed recovery attempts.

**But `useTheme()` usage does not mean complete theming**, and that's the more important number: **13 of 37 screens (35%) still contain at least one hardcoded structural background/border color that bypasses the theme system**, alongside their theme-driven styling. Examples found:

- `app/documents.tsx` (11 occurrences) — status badges mix theme-aware text (`color: colors.textDim`) with hardcoded pastel backgrounds that don't have dark-mode equivalents: `backgroundColor: '#F3F4F6'`, `'#ECFDF5'`, `'#FFFBEB'`, `'#FEF2F2'`, `'#FFF5F5'` (comment: "Light red tint"), plus `borderColor: '#FFE4E6'`, `'#FECACA'`. This is exactly the "half-themed screen" failure mode the audit warned about — light pastel badge chips on a dark background with theme-dark surrounding text.
- `app/driver/(tabs)/profile.tsx` (6 structural hits of 118 total color literals) — `borderBottomColor: '#F3F4F6'`, `borderTopColor: '#F3F4F6'`, `backgroundColor: '#D1D5DB'`, `backgroundColor: '#E5E7EB'` are static `StyleSheet.create` values, not theme-object references, so they render identically regardless of `isDark`.
- `app/driver/(tabs)/index.tsx` (the main dashboard, 5 hits), `app/vehicle-info.tsx`, `app/become-driver.tsx`, `app/profile-setup.tsx`, `app/driver/quests.tsx`, `app/driver/lost-and-found.tsx`, `app/driver/lost-and-found-chat.tsx`, `app/driver/referral.tsx`, `app/driver/emergency-contacts.tsx`, `app/driver/(tabs)/activity.tsx` each have 1-2 similar structural leftovers.
- `app/driver/ride-detail.tsx` and `app/driver/subscription.tsx` have many hardcoded hex literals (31 and 21 respectively) but on inspection these are accent/icon/status colors, not structural backgrounds/borders — lower dark-mode risk, not counted as gaps above.

Also found, outside `app/**` but in shared driver-app UI: `components/BrandSplash.tsx` (hardcoded white splash, used from both `app/_layout.tsx` and `app/index.tsx` — arguably acceptable, splash screens are commonly brand-locked regardless of theme, but it is real evidence of incomplete coverage) and `components/toastConfig.tsx` (colorful toast backgrounds — likely intentional, toasts conventionally keep brand colors in both themes). `components/charts/EarningsBarChart.tsx` / `EarningsLineChart.tsx` have hardcoded chart colors but are **dead code** — not imported anywhere in `app/**` or elsewhere in the driver-app source, so they carry zero live-screen risk today.

## 3. Decision: **NO-GO** — precondition not met, no auto-switch implemented

The audit's own gating condition ("not all screens are dark-mode-complete") is **still true**, confirmed with numbers rather than assumption:

- 1 of 37 screens (`app/index.tsx`) has no theme awareness at all, and it's not a cosmetic-only screen — it carries a real retry-button UI on session-recovery failure.
- 13 of 37 screens (35%) that *do* call `useTheme()` still carry hardcoded structural background/border colors that will not adapt, several in high-traffic screens (documents upload status, driver profile, main dashboard).
- The engineering team already left an explicit, undated TODO in the theme provider itself (`ThemeContext.tsx`) describing this exact precondition and the exact flip to make once it's satisfied — this assessment did not have to infer the precondition, it's stated in code.

35% of screens carrying real, structural light-only styling is "more than a handful" per the task's own go/no-go bar. Auto-switching (by time-of-day or otherwise) today would mean a driver whose phone crosses into the auto-dark window mid-session sees a jarring mix of correctly-dark screens and screens with light pastel badges / light gray borders on a dark canvas — worse than today's opt-in-only state, which at least only shows dark mode to users who chose it and can toggle back immediately from the same Settings screen if something looks broken.

**No time-of-day auto-switch, no default-on system-follow, and no new Settings option were implemented.** The `ThemeContext.tsx` code and its comment are unchanged.

## 4. Risk & impact on existing functionality

None — this PR is documentation-only, no source files changed. Blast radius: **none** (no code diff).

## 5. User-experience effect

None. No rider/driver/corporate-admin/internal-admin facing change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `docs/change-log/2026-09-04-dark-mode-ambient-awareness-assessment.md` | New file (this doc) | Record the investigation and go/no-go decision per the audit follow-up task |

## 7. Before / after

Not applicable — no behavior-changing diff.

## 8. Rollback plan

Not applicable — no code shipped. Revert is a plain `git revert` of this doc-only commit if the assessment itself needs retraction.

## 9. Verification performed

- [x] Blast-radius / coverage grep performed: `driver-app/app/**` (37 non-`_layout` screen files) for `useTheme`/`useColorScheme` usage, cross-checked against a second grep for hardcoded structural (`backgroundColor`/`borderColor`/`border{Top,Bottom,Left,Right}Color`) hex/rgba literals per file. Also checked `driver-app/components/**` (20 files) the same way, and confirmed dead-code status of the two chart components via a repo-wide usage grep.
- [x] Reviewed against relevant `CLAUDE.md` convention: gate #5 ("No silent behavior change to a live-tested flow") and gate #9 ("Escalate, don't silently ship, when in doubt") — this doc *is* the escalation artifact; no default-on behavior change was made.
- [ ] Automated tests run — n/a, no code changed.
- [ ] Manual repro in staging — n/a, no code changed.
- [ ] Feature-flagged — n/a, nothing shipped to flag.

## 10. What was NOT verified

- This was a static grep-based audit, not a rendered visual check — no screenshots of any screen in dark mode were taken (driver-app has no visual-regression tooling per `CLAUDE.md`). "Hardcoded structural color" was used as a proxy for "will look broken in dark mode"; a small number of these may be intentional (e.g. an always-white icon inside an always-colored badge) rather than true gaps, and the 13-screen figure has not been manually screenshotted to distinguish real breakage from cosmetic-but-fine cases — treat it as an upper-bound signal, not a certified defect count.
- Did not audit `driver-app/components/**` subdirectories beyond top-level (`dashboard/`, `panels/`, `activity/` were covered by the earlier `useTheme` grep in step 2 of the investigation but not re-checked for the structural-hardcoded-color pattern the way `app/**` was).
- Did not check `rider-app` (out of scope — audit finding was driver-app-specific) or whether the same `ThemeContext.tsx` precondition comment/gap pattern exists there too.
- Did not estimate remediation effort/size for closing the 13-screen (+1 fully-unthemed) gap — that would be a natural next task for whoever picks up full dark-mode completion, but is out of scope for this go/no-go assessment.

## 11. Sign-off

- [x] Rollback plan is concrete and testable (trivial — doc-only revert)
- [x] Blast radius is stated, not assumed (none — no code change)
- [x] No silent behavior change to an already-shipped flow — nothing shipped; explicitly deferring the feature is the intended outcome of this task
