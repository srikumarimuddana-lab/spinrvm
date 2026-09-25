# Spinr UX scorecard: world-class and minimal (2026-09-25)

**Type:** research only; changes no code.

**Scope:** an inventory of the UX components, principles and techniques in rider-app, driver-app, `shared/`, admin-dashboard and the public web pages, scored against ten principles drawn from primary sources.

**Companion to** `docs/audit/2026-09-25-ux-motion-admin-website-research.md`, which covers motion in depth.

## What was and wasn't verified

**Verified.** All findings come from reading the code on `main` at `4d934e5`. Counts are from grep and approximate. These findings were re-checked by hand:
- `allowFontScaling={false}` appears 84 times, and `maxFontSizeMultiplier` appears nowhere.
- `ErrorScreen`, `FormScreen` and `shared/validators` have no importers.
- admin-dashboard calls browser `confirm()` in 10 files.
- Dark mode is opt-in by design: `shared/theme/ThemeContext.tsx:80-87` says so.
- Rider marketing opt-ins are saved to the backend and default to off: `rider-app/app/privacy-settings.tsx:66-80`.

**Not verified.**
- Nothing was run on a device or in a browser.
- There is no Core Web Vitals or other performance data.
- www.spinr.ca lives outside this repo, and the egress proxy blocked it.
- WCAG references are from memory, because w3.org was blocked.
- The Apple HIG principles come from secondary summaries.

## The benchmark

| ID | Principle | What it means | Source |
|---|---|---|---|
| P1 | Start with user needs, do less | Every element earns its place; raise the signal-to-noise ratio | GOV.UK design principles; NN/g heuristic 8 |
| P2 | Clarity and deference | The UI recedes so content and the next action lead | Apple HIG |
| P3 | Consistency | One component system and one way to do each thing | NN/g heuristic 4; Jakob's law |
| P4 | Status, fast | 0.1 s feels instant, 1 s keeps flow, 10 s loses attention. Use skeletons for page loads and spinners for short blocking actions | NN/g response-time limits; NN/g skeleton screens |
| P5 | Prevent errors, recover plainly | Confirm destructive actions; explain errors and offer the fix | NN/g heuristics 5 and 9 |
| P6 | Accessibility as the floor | Text resizing, labels, contrast and reduced motion are baseline | WCAG 2.1 AA |
| P7 | Performance is UX | LCP ≤ 2.5 s, INP ≤ 200 ms, CLS ≤ 0.1 at the 75th percentile | web.dev Core Web Vitals |
| P8 | Purposeful motion | Short, eased, meaningful; no decorative loops | Material 3 motion |
| P9 | Efficiency for experts | Keyboard shortcuts, command palette, saved views, bulk actions | NN/g heuristic 7 |
| P10 | Trust and transparency | Live status, the price up front, safety one tap away | Spinr's "no hidden fees" principle (CLAUDE.md) |

## Scorecard

Key: ✅ strong · ◐ partial · ✗ gap · — not applicable, not in repo, or not measured

| Principle | Rider and driver apps | Admin portal | Public web |
|---|---|---|---|
| P1–P2 Do less, clarity | ◐ Minimalism is a written principle; screens not audited one by one | ◐ Quiet Console is built but `admin_theme_v2_enabled` is off | — Marketing site not in repo |
| P3 Consistency | ◐ Tokens in 117 files, but two toast systems; shared Button, Input and Card barely used; 3 unused components | ◐ Strong shadcn kit, but `confirm()` in 10 files and about 90 files with hardcoded colours | ◐ Tracking, driver sign-up and company pages are each styled differently |
| P4 Status, fast | ✅ Toasts, offline banner and smooth car marker. ◐ ETA and fare swap text without a transition; spinners are the loading standard | ◐ 58 skeleton pages, but about 107 in-page spinners, one toast at a time, and map markers that jump | ◐ "Updated" timestamp is shown, but the car jumps every 5 s |
| P5 Errors | ◐ Confirm sheets and hold-to-confirm; no automatic retry; error boundaries on some screens only | ◐ AlertDialog in 27 files vs `confirm()` in 10; unsaved-changes guard in 7 forms | ✗ The 404 page links to the admin dashboard |
| P6 Accessibility | ✗ 84 text elements ignore the OS text-size setting, with no size cap. Labels in 30 of 66 and 26 of 69 files. `docs/accessibility-plan.md` mobile checklist is all unchecked | ◐ axe runs on 2 pages; no `aria-sort`; 14 of 23 search inputs unlabelled | ◐ Untested |
| P7 Performance | ◐ Backend SLAs exist; some animations run on the JS thread | — Not measured | — Not measured |
| P8 Motion | ◐ Loops respect Reduce Motion since #5830; no haptics | ✗ No motion tokens; one Reduce Motion check | ✗ The tracking-page rotation tween ignores Reduce Motion |
| P9 Expert efficiency | — | ◐ CSV export and a remembered sidebar are strong. The command palette is behind a flag, there are no saved views, and bulk actions exist only on specialist pages | — |
| P10 Trust | ✅ Free-cancel timer, share trip, SOS with discreet mode. ◐ The iOS Live Activity is unverified at runtime | ◐ Stale-data banner on monitoring only | ◐ Driver name and plate shown; no clear end-of-trip state |
| Localization | ◐ Rider en/fr/es/zh, but i18n is used in 11 of 66 files and the non-English files are about half translated. Driver en/fr/es | — No i18n; acceptable for internal staff | — |

## Already world-class

**Rider and driver apps**
- **Car-marker pipeline:** GPS smoothing, a playback buffer and road snapping.
- **Resilience:** an offline banner on every screen, a delayed reconnect warning, and a forced-update overlay.
- **Rating prompt:** asks only after a 4★+ rating, at least 3 rides, and 30 days since the last ask.
- **Safety:** SOS hold-to-confirm, plus discreet mode for drivers.
- **Consent:** CASL-compliant marketing opt-ins that default to off.

**Admin portal**
- Loading skeletons on 58 of 84 pages.
- CSV export guarded against formula injection.
- A sidebar that remembers each admin's open groups.
- Merge-blocking visual regression on 6 pages.

## Biggest gaps, ranked by impact against effort

1. **Let text follow the OS size setting** (apps, P6). Replace the 84 `allowFontScaling={false}` with a `maxFontSizeMultiplier` cap of about 1.3–1.5×. This conflicts today with the 200% target in `docs/accessibility-plan.md` and with WCAG 1.4.4.
2. **Switch Quiet Console on** (admin, P1–P3). It needs the hardcoded-colour cleanup first, and a person to re-capture the visual baselines.
3. **One feedback system per surface** (all, P3 P5):
   - unify the rider and driver toasts
   - replace the 10 admin `confirm()` calls with AlertDialog
   - raise `TOAST_LIMIT` so up to 3 admin toasts can show
4. **Skeletons for full-page loads in the apps** (P4). The rider/driver design skill makes spinners the standard, which runs against the NN/g guidance. This needs a product decision.
5. **Admin speed features** (P9):
   - enable the command palette
   - add saved filter views
   - make table headers sticky
   - add `aria-sort`
6. **Smooth markers on the web** (admin and tracking page, P4 P8). Reuse the app's smoothing logic.
7. **A small haptics vocabulary** (apps, P4). `expo-haptics` is installed but never imported.
8. **Finish or trim translations** (apps, P1). Half-translated screens are worse than English only.

## Housekeeping noticed, not changed

- **Unused components:** `shared/components/ErrorScreen.tsx`, `shared/components/FormScreen.tsx` and `shared/validators/index.ts` have no importers.
- **Stale claims in `.claude/skills/spinr-rider-driver-design-system/SKILL.md`:**
  - it says driver-app has zero shared Button consumers (it has 5)
  - it gives a `showToast` count of 131 (now about 148)

## Sources

- NN/g, 10 usability heuristics: https://www.nngroup.com/articles/ten-usability-heuristics/
- NN/g, response-time limits: https://www.nngroup.com/articles/response-times-3-important-limits/
- NN/g, skeleton screens: https://www.nngroup.com/articles/skeleton-screens/
- web.dev, Core Web Vitals: https://web.dev/articles/vitals
- GOV.UK, government design principles: https://www.gov.uk/guidance/government-design-principles
- Apple HIG principles (secondary summary): https://www.netguru.com/blog/ios-human-interface-guidelines
- Doherty threshold: https://www.uxtoast.com/ux-laws/doherty-threshold
- Material 3, easing and duration: https://m3.material.io/styles/motion/easing-and-duration
