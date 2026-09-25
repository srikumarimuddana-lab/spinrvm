# UX research: app motion, admin portal, and website (2026-09-25)

**Type:** research only. This document changes no code.
**Scope:** rider-app, driver-app, and `shared/` (motion, haptics, loading); admin-dashboard (motion and UX friction); the public website.
**Method:** code reading with `file:line` evidence, spot-checked by hand, plus primary library and platform documentation (sources at the end).

## What was and wasn't verified

- **Verified:** every finding below comes from reading the code on `main` as of 2026-09-25. A second pass re-checked these by hand:
  - `expo-haptics` has zero imports.
  - `shared/utils/motion.ts` `TIMING.fast/base/slow` are unused.
  - `RideOfferPanel.tsx:151` runs with `useNativeDriver: false`.
  - `TOAST_LIMIT = 1`.
  - `app/page.tsx` only redirects.
  - Monitoring markers jump via `setLngLat` (`monitoring-map.tsx:296`).
- **Not verified:**
  - Nothing was run on a device or in a browser.
  - Frame rates and jank were reasoned about, not profiled.
  - **www.spinr.ca could not be reviewed.** The site lives outside this repo (`docs/vendor-inventory.md:38` lists it on Vercel), and this session's egress proxy blocked the host. The website section is therefore limited to what is visible from here.
- **Counts are approximate.**
  - The native-`confirm` count is 13 by one grep and 16 by the sub-audit's broader match.
  - The spinner counts are grep totals.

---

## 1. Mobile apps (rider and driver): motion

### Current state

| Area | Finding | Evidence |
|---|---|---|
| Animation engine | Reanimated 4 is installed, but only 2 components use it. Most animation (~28 files) still uses legacy RN `Animated`. driver-app has 0 Reanimated imports. | `rider-app/components/AiWelcomeOrb.tsx`, `shared/components/AiAuroraBackground.tsx` |
| Motion tokens | `shared/utils/motion.ts` defines `TIMING.fast/base/slow`, but only the wrong-code shake uses the file. Every other animation hardcodes its duration or spring (e.g. 200 / 350 / 800 / 1500 / 1800 ms; tension 60–70, friction 8–12). There are no spring tokens. The design system doc has no motion section. | `shared/utils/motion.ts:31-47`, `docs/design/rider-driver-app-design-system.md` |
| Reduced motion | Only 5 places check `isReduceMotionEnabled()`, once each; only `AiAuroraBackground` listens for changes. | ungated: RideOfferPanel slide/countdown, DriverIdlePanel GO pulse, CarMarker ring pulse, SafetyOverlay pulse, SkeletonBox, Toast, ride-completed, payment-confirm stagger |
| Haptics | `expo-haptics` is a dependency of both apps and **imported nowhere**. All feedback uses `Vibration.vibrate` patterns. | `useRiderSocket.ts:116`, `useDriverDashboard.ts:1070`, `RideOfferPanel.tsx:125,173` |
| JS-thread animations | `useNativeDriver: false` on several visible loops, including the rider "searching" dots loop, the driver offer countdown bar (animates `width`), fare expand, HUD collapse, and hold-to-confirm. | `ride-status.tsx:209`, `RideOfferPanel.tsx:151`, `payment-confirm.tsx:517`, `DriverIdlePanel.tsx:73`, `useHoldToConfirm.ts:59` |
| Offer countdown | A 1s `setInterval` steps the countdown, so the bar moves in ~1s jumps. There is no urgency treatment near expiry. | `driver-app/app/driver/(tabs)/index.tsx:637` |
| Loading | Mostly spinners (~37 rider and ~38 driver files use `ActivityIndicator`). Skeletons exist only on rider ride-options and activity. driver-app has none. | `SkeletonBox.tsx` |
| Key moments | "Driver assigned" is an abrupt `router.replace` with no reveal. ETA and fare changes are plain text swaps. Rating stars are static. | `ride-status.tsx:600`, `ride-in-progress.tsx:390` |
| Map car | Strong: Kalman filter, playback buffer, route snap. Still open: shared CarMarker rotates in steps on Android (REC-C-03), and the route-rebase fix is missing from the shared fork (REC-C-04). | `shared/components/CarMarker.tsx:686`, `docs/audit/ride-experience/REPORT.md` |
| Hygiene | `ride-status.tsx:199-209` starts loops without stopping them on cleanup. `useState(new Animated.Value())` allocates a new value on every render (`:49-50`). | — |

### Recommendations (ranked by user impact vs. effort)

**M1. A shared motion foundation in `shared/theme/motion.ts`.** This is the prerequisite for everything else.
- **Durations:** `instant 100`, `fast 150`, `base 250`, `slow 400`, `emphasis 600`.
- **Easings:** standard, decelerate for entering elements, accelerate for exiting elements. Material 3's emphasized-decelerate curve is a proven default for entrances.
- **Springs:** `snappy` (sheets/panels, ≈ damping 18 / stiffness 220), `gentle` (success moments), `bouncy` (celebration only).
- **Reduced-motion default:** one `useReducedMotion()` source of truth.
- Add a "Motion" section to `docs/design/rider-driver-app-design-system.md`.
- *Alternative considered:* per-app tokens. Rejected: the design system already mandates shared tokens with no per-app overrides.

**M2. Reduced motion everywhere, done properly (WCAG 2.1 AA is a stated regulatory floor).**
- Reanimated animations default to `ReduceMotion.System`, so migrating to Reanimated gets this for free.
- Legacy `Animated` loops must be gated by hand. Priority order: infinite loops first (pulses, dots, SOS/Safety pulse), then slide-ins.
- Rule: under reduced motion, pulses become static and slides become fades or instant. Information conveyed by motion (e.g. countdown urgency) must also be conveyed by text or color.

**M3. Driver offer card: highest-stakes motion in the product.** Dispatch has a P95 target under 2 s, and the offer window is about 15 s.
- Drive the countdown from a single `withTiming(0, {duration: remainingMs})` on the UI thread, animating `scaleX` (not `width`). This removes both the JS-thread work and the 1s stepping.
- Add urgency in the last 5 s: the bar changes color and a light haptic ticks at 3-2-1. Every urgency cue also has a text equivalent ("5 s left").
- Accept: `Haptics.notificationAsync(Success)`, then a checkmark morph. Expiry: a short collapse animation rather than the card vanishing.
- **Constraint:** the backend offer timeout remains the source of truth, and the animation must not imply time the server won't honour. Anchor it to the server `expires_at` if the payload carries one, otherwise to the receive timestamp. **Unverified here** whether the payload carries `expires_at`, so check this before implementing.

**M4. Haptics vocabulary via `expo-haptics`.** The dependency is already installed, so no new supply-chain risk.

| Event | Haptic |
|---|---|
| Driver assigned (rider) | `notification(Success)` |
| Driver arrived (rider) | `notification(Warning)` (replaces the raw vibrate pattern; respect user preferences) |
| Trip complete / payment success | `notification(Success)` |
| Payment fail / PIN error | `notification(Error)` |
| Go online / offline (driver) | `impact(Medium)` |
| Accept / decline tap | `impact(Light)` |
| SOS hold-to-confirm progress | `selection()` ticks during the hold, `notification(Warning)` on trigger |

Keep the long `Vibration` pattern only for the incoming ride offer, where it acts as an alert rather than feedback. Gate everything behind the existing driver `useAlertPrefsStore.vibration`, and add a rider equivalent.

**M5. "Driver found" reveal (rider).**
- Replace the abrupt `router.replace` with a shared-element-style transition. The search pulse contracts into the driver card, and the car, plate and name stagger in at about 60 ms each.
- This is the single most emotionally loaded moment in the rider flow; a clear reveal reduces "did it work?" anxiety.

**M6. Skeletons replace spinners on content screens.**
- Priority: driver activity/earnings, rider trip history, receipts, wallet.
- Keep spinners for sub-second button actions.
- Use a subtle static-shimmer skeleton that becomes a flat placeholder under reduced motion.

**M7. Animated numbers.**
- ETA and fare get a crossfade or number roll when the value changes, so riders notice it without it jumping.
- Earnings use a count-up only on first reveal; never animate money on every re-render.
- **Constraint:** the fare shown must stay exactly the Decimal-formatted server value. Only the transition animates.

**M8. Migrate hot paths to Reanimated.** Start with the offer panel, searching screen, sheets and hold-to-confirm, then clean up the leaked loops. Don't do a big-bang rewrite: migrate each component when it is touched (M1 tokens make this incremental).

**M9. Close the existing CarMarker items REC-C-03 and REC-C-04.**
- `CarMarker` is a registered fork (`docs/known-forks.md`), so any change must check both copies. The 2026-09-12 audit's lesson was exactly this: a fix reached one copy and not the other.

## 2. Admin dashboard

### Current state

| Area | Finding | Evidence |
|---|---|---|
| Design direction | "Quiet Console" is built but switched off (`admin_theme_v2_enabled` = false), so staff still see the old styling. There are about 90 files with hardcoded colors (#2816), Geist instead of Plus Jakarta Sans, and a placeholder "S" logo on login. | `.claude/skills/spinr-admin-design-system/SKILL.md`, `app/dashboard/layout.tsx:16,23`, `app/login/page.tsx:127-135` |
| Motion | The `motion` library is used in one file (alert feed). There are no motion tokens and no global `prefers-reduced-motion` rule. There are about 194 `animate-spin` and 82 `animate-pulse` uses. `AnimatePresence` has no `exit`, so rows vanish instantly. | `app/dashboard/monitoring/alert-feed.tsx:96-98` |
| Live maps | Driver markers teleport on every update on both the monitoring and live-ride maps, so ops staff lose track of which car moved. | `monitoring-map.tsx:296`, `rides/live/[id]/live-map.tsx:187` |
| Confirmations | About 13–16 native `window.confirm`/`alert` calls on destructive and PII actions, even though `AlertDialog` exists. | `drivers/page.tsx:636,665,705`, `users/page.tsx:276`, `topbar.tsx:54,65` |
| Toasts | `TOAST_LIMIT = 1`, so a success toast can overwrite an unread error. | `components/ui/use-toast.ts:5` |
| Tables | No sticky headers. `SortableHead` has no `aria-sort`. No bulk actions on the main Drivers/Users/Rides lists. Only about 9 of 23 search inputs have an `aria-label` (F-51 still open). | `components/ui/sortable-table.tsx:83-97` |
| Productivity | A Cmd/Ctrl+K palette exists but is flag-off, and its route list (43) is separate from the sidebar's (48), so the two will drift. | `components/command-palette.tsx:96`, `lib/command-palette-routes.ts` |
| Background cost | 8 `setInterval` pollers keep running in hidden tabs. | — |
| Already fixed since audit 06 | 58 `loading.tsx` skeletons, a sanitized error boundary, server-paginated rides, and text labels on status dots. | `docs/audit/admin-dashboard/06-perf-ux-a11y.md` |

### Recommendations

**A1. Finish and flip Quiet Console. This is the biggest perceived-quality gain per hour.**
- Close the hardcoded-color tail (#2816) on the 6 visually baselined pages first.
- Turn the flag on for internal staff, then everyone.
- The flag already exists, so rollback is a DB setting.
- **Note:** the 6 seeded Playwright visual baselines will fail on the flip by design. A human must re-run `update-visual-baselines.yml`.

**A2. Admin motion system: restrained, per Quiet Console.** Motion should confirm, not decorate.
- Add CSS tokens in `globals.css`: `--duration-fast 120ms`, `--duration-base 200ms`, `--ease-out cubic-bezier(0.2,0,0,1)`.
- Wrap the app in `<MotionConfig reducedMotion="user">`, plus one global `@media (prefers-reduced-motion: reduce)` rule that neutralises `animate-*` loops.
- Allowed motion:
  - new live-feed rows briefly highlight (≤ 1 s background fade, never a flash loop)
  - the drawer and sheet slide in (right-hand detail drawer, per the canvas inventory)
  - the toast stack
  - an optimistic row-state change
- Not allowed: page-transition flourishes, parallax, bouncy springs.

**A3. Interpolate live-map markers.**
- Tween between positions over the poll interval with rAF lerp and bearing interpolation. Snap if the jump exceeds a threshold, matching the mobile CarMarker's instant-snap rule.
- Add a 1-frame "moved" ring on the selected driver.
- Skip the tween when reduced motion is on.
- This is highest-value for ops staff watching `/dashboard/monitoring`, one of the 6 visually baselined pages. Its map tiles are stubbed in the test, but marker behaviour still needs the baseline check.

**A4. Replace native `confirm`/`alert` with `AlertDialog`.**
- Use destructive styling and require typed confirmation for irreversible PII actions such as delete user.
- This also fixes keyboard and screen-reader behaviour, and the native dialog's unbranded appearance.

**A5. Toast policy.**
- Raise `TOAST_LIMIT` to 3.
- Make error toasts persist until dismissed, and success toasts auto-dismiss after about 4 s.
- Use `role="status"` for success and `role="alert"` for errors.

**A6. Table ergonomics.**
- Sticky `thead`.
- `aria-sort` on `SortableHead`.
- `aria-label` on the remaining search inputs (F-51).
- Bulk select and actions on Drivers, then Users.

**A7. Command palette.**
- Derive its routes from the sidebar config, one source of truth.
- Add a `?` shortcut sheet.
- Enable it for staff.

**A8. Pause pollers on `document.hidden`** and refresh on focus. This reduces backend load and avoids stale-then-jump updates.

**A9. Shared `EmptyState` component** (icon + one sentence + primary action). Replaces the ad-hoc "No rides found" strings.

## 3. Website and public surfaces

### Current state

- **www.spinr.ca**
  - There is no marketing site in this repo. www.spinr.ca exists (its `/legal/*` pages are cited in `docs/audit/2026-09-14-spinr-app-phantom-domain-audit.md:23`) but is built elsewhere and could not be fetched from this session.
  - **Open question for the owner:** where is its source?
- **Public pages served by admin-dashboard**
  - `app/page.tsx` only redirects to `/dashboard` (`noindex`).
  - `/company-login` and `/company-signup` use an emerald icon badge, which is off-brand compared with the admin red.
  - `/register/driver` is a 6-step wizard.
  - `/track/[rideId]` is the most polished consumer-facing web page. Its status pill, ETA sheet and driver card are good, but it uses a raw gray palette. The car rotates smoothly but its position jumps on each 5 s poll, and the 600 ms rotation tween ignores reduced motion (`:147`, `:281`).
  - `not-found.tsx` sends public visitors to "Go to Dashboard", a dead end for riders and share-link recipients.

### Recommendations

**W1. Share-a-trip tracking page (`/track`) is the website moment riders' families actually see.**
- Apply marker interpolation (same as A3).
- Honour reduced motion.
- Use brand tokens.
- Add an "arrived" state with a clear end-of-trip message.
- Add a public-appropriate 404: "This trip link has expired." Never link to the dashboard.
- **PIPEDA check:** this page already shows the driver name and plate. Any addition must not expose more (no rider name, no exact addresses beyond what the rider chose to share).

**W2. Consistent public brand shell.** One logo/header/footer component for `/login`, `/company-*`, `/register/driver` and `/track`. Replace the "S" placeholder and the emerald badge with the real Spinr mark (`.claude/context/brand-spinr.md`).

**W3. Marketing site: guidance for whoever owns it** (not implementable from this repo).
- **Performance:** hero content visible without JavaScript. Only animate `transform` and `opacity`. Scroll-reveal at most once per section and never on body copy. Respect `prefers-reduced-motion`. Target LCP under 2.5 s on mobile.
- **Messaging:** lead with the product's differentiators, which the site should state plainly and verifiably:
  - drivers keep 100% of the fare
  - surge is capped at 2.5× and always shown before booking
  - Saskatchewan-first
  - every receipt line is disclosed
  - **Do not claim** "no surge" or "cheapest". Legal copy review applies; driver-recruitment copy must avoid control-of-work language (contractor classification).
- **Conversion paths:** Ride (app store badges), Drive (links to `/register/driver`), Business (links to `/company-signup`). The corporate B2B spec (`docs/superpowers/specs/2026-04-15-corporate-accounts-b2b-design.md:381`) already calls for a `/business` page that was never built.
- **Accessibility:** WCAG 2.1 AA; a skip link; visible focus; no auto-playing video without a pause control.

## 4. Suggested sequencing

Each step is independent unless noted. The workstreams touch disjoint files, so **mobile (M*)**, **admin (A*)** and **public web (W*)** can run in parallel without merge conflict.

| # | Item | Surface | Effort | Risk | Gate |
|---|---|---|---|---|---|
| 1 | M1 motion tokens + M2 reduced-motion gating | shared | S | Low (additive) | none; tokens unused until adopted |
| 2 | A4 AlertDialog + A5 toast policy | admin | S | Low | behaviour change, needs a UX-effect note |
| 3 | M3 offer-card countdown on the UI thread + haptics | driver | M | **Medium: dispatch surface** | feature flag; verify timeout parity with the backend |
| 4 | M4 haptics vocabulary | both apps | S | Low | respect user preferences |
| 5 | A3 + W1 marker interpolation (web) | admin | M | Low | visual baseline on monitoring |
| 6 | A1 Quiet Console flip | admin | M | Medium (all 6 baselines) | flag exists; human re-baseline |
| 7 | M5 driver-found reveal, M6 skeletons, M7 number transitions | rider/driver | M | Low | no visual tooling on mobile, so screen-record on device |
| 8 | A6–A9, W2 | admin/web | M | Low | — |
| 9 | M8 incremental Reanimated migration, M9 CarMarker fork fixes | all | L | Medium (fork) | check both CarMarker copies |

**Guardrails that apply to all of the above:**
- **Flags:** feature-flag user-visible changes through the existing `app_settings` pattern.
- **Mobile verification:** rider-app and driver-app have no visual regression tooling, so each PR must say "screen-recorded on device" or "reasoned about, not recorded".
- **Motion must never:**
  - carry information on its own (colour or text must also convey it)
  - delay a money, dispatch or safety action
  - loop indefinitely without a reduced-motion exit

## Sources

- Reanimated `useReducedMotion`: https://docs.swmansion.com/react-native-reanimated/docs/device/useReducedMotion/
- Reanimated `ReducedMotionConfig`: https://docs.swmansion.com/react-native-reanimated/docs/device/ReducedMotionConfig/
- Reanimated accessibility guide: https://docs.swmansion.com/react-native-reanimated/docs/guides/accessibility/
- Motion for React `MotionConfig`: https://motion.dev/docs/react-motion-config
- Motion for React accessibility guide: https://motion.dev/docs/react-accessibility
- Material 3 easing and duration: https://m3.material.io/styles/motion/easing-and-duration
- WCAG 2.1 SC 2.2.2 (Pause, Stop, Hide) and SC 2.3.3 (Animation from Interactions): cited from knowledge. The w3.org fetch was blocked by the egress proxy, so re-confirm before quoting in compliance material.
