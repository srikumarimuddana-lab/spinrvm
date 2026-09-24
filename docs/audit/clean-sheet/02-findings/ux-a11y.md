# R17 — UX, Design & Accessibility Lead (W3)

Lane: R17 · Charter: rider/driver/admin UX quality, design-system adoption,
loading/empty/error/success states, WCAG 2.1 AA (+2.2 delta note), winter/
low-light/one-hand use, low-connectivity, navigation, search, animation/motion.

Status: **COMPLETE.**

<!-- SECTION: 0 -->
## 0. Method & tooling disclosure

**No browser, device, or screen reader was used in this audit.** Every claim
below is reasoned from source (component code, style objects, `accessibility*`/
`aria-*` props, hook implementations) or from a static grep sweep, never from a
rendered screenshot or a live VoiceOver/TalkBack/NVDA pass. Contrast ratios in
§6 are **computed** from the hex values in `shared/theme/index.ts` and
`admin-dashboard/src/app/globals.css` using the standard WCAG relative-luminance
formula (verified against a hand-written Python implementation, not a
contrast-checker tool) — they are correct arithmetic on the *token values*, but
say nothing about how those tokens actually render (anti-aliasing, overlays,
translucency, OS-level contrast boosts).

**Visual/snapshot regression tooling, per surface (CLAUDE.md gate 6):**
- **rider-app, driver-app: zero visual-regression tooling of any kind.**
  Confirmed by directory search — no Playwright/snapshot/Percy config anywhere
  in either app. Every rider-app/driver-app UI claim in this report is
  code-level reasoning, full stop.
- **admin-dashboard: two separate, both real, tools — not one.**
  1. `e2e/visual-regression.spec.ts` — pixel snapshot diffing on 6 seeded pages
     (`login`, `dashboard-home`, `dashboard-drivers`, `dashboard-monitoring`,
     `dashboard-settings`, `dashboard-rides`), merge-blocking per CLAUDE.md.
     None of the six were pixel-diffed in this pass (no rendering capability
     here) — any styling recommendation below for one of these six needs a
     human-recaptured baseline (`update-visual-baselines.yml`), not
     "no visible diff" reasoning.
  2. `e2e/crawl-audit.spec.ts` — a **functional accessibility** gate, distinct
     from the visual-regression job above: `@axe-core/playwright`'s
     `AxeBuilder().analyze()` against **42 routes** (`ROUTES` array,
     `crawl-audit.spec.ts:6-49`), with a per-route baseline-ratchet in
     `e2e/a11y-baseline.json` — a route may not regress past its own recorded
     violation count; a route with no entry defaults to 0 tolerance
     (`crawl-audit.spec.ts:229-233`). **This means admin-dashboard has real,
     CI-wired, merge-blocking automated accessibility coverage today** — a
     materially stronger position than "reasoned about, not screenshotted."
     Two caveats, both VERIFIED by direct read: (a) the ratchet permits
     *accumulated* debt to persist — it blocks new violations, it does not
     require zero; (b) `AxeBuilder().analyze()` is called with no
     `.withTags(...)` filter, so it runs axe-core's full default rule set
     (best-practice + WCAG2A/AA + WCAG21A/AA rules) — INFERRED default
     behavior from axe-core's own docs, not independently confirmed against
     the installed package version this session. See UXA11Y-006 for a
     material staleness finding in this file's own governing comment.
  This tooling picture is used throughout §§2–13 below; every finding says
  which bucket it falls in.

**Builds on, does not repeat:** `rapid-baseline-2026-09-24/A7-surfaces.md`
(A11Y-003 WAV spot-check, four-states table for 8 screens), R4's
`02-findings/rider-journey.md` (RIDERJ-004 vehicle-type card a11y gap,
RIDERJ-005 timezone display, the booking-flow a11y-label table in its §(g)),
R5's `02-findings/driver-journey.md` (driver journey map's four-states/a11y
columns), R7's `02-findings/admin-ops.md` §9 (admin UX-at-scale spot checks,
which explicitly deferred per-page loading/empty/error verification to this
lane). Where this file re-confirms a prior lane's finding it cites the ID
rather than re-deriving it; where it corrects or extends one, it says so.
De-duplicated against `ACTION_ITEMS.md` by keyword grep before filing (WCAG,
a11y, accessib*, contrast, screen-reader) — no exact duplicate of any finding
below was found; several findings below **extend** already-closed
`ACTION_ITEMS.md` fixes (cited inline) rather than contradicting them.

<!-- SECTION: 1 -->
## 1. Steelman

Read before attacking — these are real, load-bearing accessibility
investments, not defaults:

- **The Toast/error-announcement path was deliberately screen-reader-wired.**
  `ACTION_ITEMS.md:676-685` — the single shared `Toast`/`toastConfig`
  component used across both mobile apps (131 call sites per the
  rider-driver design-system skill doc) fires
  `AccessibilityInfo.announceForAccessibility(...)` plus
  `accessibilityRole="alert"` and `accessibilityLiveRegion` (`"assertive"`
  for error/danger, `"polite"` otherwise). This means the *majority* of
  form/API-failure copy in both apps is already screen-reader-announced by
  construction, not by per-screen discipline — a genuinely good, hard-to-copy
  architectural choice (one fix, ~all screens benefit).
- **Driver-app's real-time/safety-critical controls are the best-labelled
  surface in either mobile app.** The GO/STOP toggle
  (`driver-app/components/dashboard/DriverIdlePanel.tsx:274-294`,
  `accessibilityRole="button"`, dynamic label, `accessibilityState={{disabled}}`)
  and the entire in-trip action set in `ActiveRidePanel.tsx` (accept/decline,
  navigate, arrive, complete, cancel, no-show, PIN keypad — 15+ labelled
  controls, lines 592-934) are fully labelled. This is exactly the surface
  the design-system skill doc calls "the single highest-stakes tap in the
  app" and the safety-relevant carve-out — and it got the accessibility pass
  most other screens didn't.
- **Reduce-motion is respected where the app's own most elaborate,
  user-installed animations live**, not just token gestures: `ride-status.tsx`
  (search pulse, `AccessibilityInfo.isReduceMotionEnabled()` at line 171 —
  the `#4607` fix cited by R4), `driver-arriving.tsx`, `AiWelcomeOrb.tsx`,
  and both apps' `BrandSplash.tsx`. See UXA11Y-004 for where this adoption
  stops.
- **The destination-search debounce is a real, shared, well-built hook**
  (`shared/hooks/usePlacesAutocomplete.ts`) — 300ms debounce, request-sequence
  guarding against a stale late response overwriting a newer one
  (`requestSeqRef`), in-flight cancellation on input change, and a documented
  reason the admin-dashboard has its own parallel copy rather than importing
  this one (`@shared/api/client` is React-Native-specific). This is
  disciplined engineering; its one real gap (error swallowing) is narrow and
  named in UXA11Y-003, not evidence the whole hook is weak.
- **admin-dashboard did not skip accessibility as an afterthought.**
  `eslint-plugin-jsx-a11y` is wired as CI-time static analysis
  (`docs/ACCESSIBILITY.md:11-24`) *and* `crawl-audit.spec.ts` runs a real
  42-route, merge-blocking `axe-core` ratchet (§0) — two independent,
  automated layers most products at this stage don't have at all. The
  published `docs/legal/accessibility-statement.md` is also unusually
  careful: it explicitly refuses to claim WCAG 2.1 AA conformance as an
  achieved fact, names "not yet audited" for both mobile apps, and was held
  back from citing a not-yet-provisioned `accessibility@spinr.ca` inbox in
  favour of the real, monitored `support@spinr.ca` — the kind of restraint a
  plaintiff's-lawyer adversary pass specifically rewards.
- **The rider-app WAV toggle pairs its disabled state with text, not color
  alone** (`ride-options.tsx:1064-1077`, cited by A7's A11Y-003 and R4's
  RIDERJ scenario table) — "No WAV drivers nearby" accompanies the
  dimmed/disabled visual state, satisfying the "not color alone" rule for
  this specific control.

<!-- SECTION: 2 -->
## 2. Findings (§7.1 cards, CRITICAL/HIGH first)

### UXA11Y-001 — `allowFontScaling={false}` is used 76+ times across both mobile apps, with zero `maxFontSizeMultiplier` fallback anywhere, concentrated on money figures
- Hierarchy: L2 Shared Frontend Foundation › L3 Typography & dynamic type › L4 Text scaling support › L5 low-vision rider/driver using OS-level large text
- Severity: **HIGH**   Priority score: S×B×L = 3×3×3 = 27 (affects every screen that sets it, every session, for anyone using OS dynamic type — not a rare trigger)
- Status: VERIFIED   Existing item: new — grepped `ACTION_ITEMS.md` for "allowFontScaling"/"maxFontSizeMultiplier"/"dynamic type"/"font scal" — no hits
- Adversary: regulator/auditor (WCAG 1.4.4 Resize Text, AA); a low-vision rider or driver with 200% system text size set; plaintiff's lawyer (money figures specifically un-scalable)
- Evidence: grepped `allowFontScaling={false}` across `rider-app/**/*.tsx` and `driver-app/**/*.tsx` (both `app/` and `components/`): **76 hits**. Representative, money-relevant sites: `rider-app/app/payment-confirm.tsx:320` (`totalPrice`), `:526,551,563` (fare breakdown lines, promo discount), `rider-app/app/ride-in-progress.tsx:618` (`riderBill`), `rider-app/app/ride-completed.tsx:757,764,771` (trip stats), `driver-app/components/dashboard/DriverTopBar.tsx:96` (`earningsAmount`), `driver-app/components/dashboard/TripCompletedPanel.tsx:140` (`earningsHeroAmount`) plus every fare-breakdown line 182-213, `driver-app/components/dashboard/ActiveRidePanel.tsx:621,626` (driver's live per-trip earnings), `driver-app/lib/androidAuto/CarOfferPanel.tsx:132` (`moneyHero`). Also on entire panels of driver-facing state text: `DriverIdlePanel.tsx` online/offline pill, `CancelReasonSheet.tsx`'s whole dialog, `NavigationStepBanner.tsx`'s turn-by-turn instruction text. Separately grepped `maxFontSizeMultiplier` across both apps: **zero hits** — nowhere does either app reach for the WCAG-friendlier "cap scaling at Nx" middle ground instead of disabling it outright.
- What happens (plain language): a rider or driver who has turned up their phone's text size — a standard OS accessibility setting, not an app setting — sees these specific numbers and labels stay pinned at the developer's chosen pixel size while everything else on the same screen grows. On `payment-confirm.tsx` and `ride-in-progress.tsx` this is literally the fare total; on driver-app it's literally today's earnings. These are exactly the numbers CLAUDE.md's "every charge maps to a disclosed line item" and "driver earnings transparency" principles most need to be *readable*.
- Root cause: `allowFontScaling={false}` is almost always reached for defensively, to stop a large system font size from breaking a tightly laid-out row (a fare line, a pill badge, a two-column stat block) — a real layout risk — but the fix chosen disables scaling entirely rather than bounding it (`maxFontSizeMultiplier={1.3}` or similar), which is the standard middle ground precisely because it exists for this trade-off.
- Recommendation: replace `allowFontScaling={false}` with a bounded `maxFontSizeMultiplier` (start at 1.3–1.5, tune per layout) on money/status text specifically; for genuinely fixed-size iconography-adjacent labels (e.g. a single OTP digit box) a hard cap may be a legitimate exception, but that's a minority of the 76 sites found, not the default.   Alternative considered: leave as-is and rely on the `accessibility.tsx` settings screen's WAV toggle as the app's accessibility story — rejected, that screen has nothing to do with text scaling and doesn't mitigate this gap at all.
- Blast radius: two mobile apps' worth of `Text` components; no backend change; every hit is a local `style`/prop change with no logic dependency, but 76 sites means real UI QA effort, not a one-line fix — recommend prioritizing the money-figure sites (payment-confirm, ride-in-progress, ride-completed, DriverTopBar, TripCompletedPanel, ActiveRidePanel, CarOfferPanel) first.
- Rollout: additive, no flag needed (pure prop change, no behavior/logic change) — but per CLAUDE.md gate 6, rider-app/driver-app have no visual-regression tooling, so a human visual QA pass at 100% and 200% OS text size is the only verification available; state that explicitly in the PR, don't rely on "no visible diff in dev" reasoning.
- Verification to close: manual pass with OS text size set to the largest non-"bold text" setting on one representative screen per app (a payment screen, an earnings screen), confirming money figures grow without breaking layout; a snapshot test asserting `maxFontSizeMultiplier` (not `allowFontScaling={false}`) on the touched components would catch a future regression, but rider-app/driver-app have no such test infra today (see §16).

### UXA11Y-002 — Raw semantic/brand color tokens used directly as text/label color fail WCAG AA contrast in reasoned-about (not measured) pairings, despite an AA-safe token already existing
- Hierarchy: L2 Shared Frontend Foundation › L3 Design tokens › L4 Color contrast › L5 text-on-surface and button-label pairings
- Severity: **HIGH**   Priority score: S×B×L = 3×2×3 = 18
- Status: VERIFIED (arithmetic on token values, per §0's disclosure — not a live contrast-checker or rendered measurement)   Existing item: new — grepped `ACTION_ITEMS.md` for "contrast" (69 hits, all admin-dashboard-specific per-page fixes, e.g. `2026-08-27-demand-legend-swatch-contrast-fix.md`, `2026-07-29-admin-dashboard-status-priority-badge-contrast.md`); none address rider-app/driver-app's `lightColors.primary`/`success`/`warning`/`danger`/`info` used as foreground text color, so this is a new, different surface, not a duplicate
- Adversary: regulator/auditor (WCAG 1.4.3 Contrast Minimum, AA — 4.5:1 normal text / 3:1 large text & UI components); low-vision rider/driver; plaintiff's lawyer
- Evidence: computed WCAG contrast ratios from `shared/theme/index.ts`'s `lightColors` (the default, non-dark-mode palette every screenshot-free reasoning pass in this repo has to assume is the common case): `success` (#10B981) on white **2.54:1**; `warning` (#F59E0B) on white **2.15:1**; `info` (#3B82F6) on white **3.68:1**; `danger`/`error` (#EF4444) on white **3.76:1**; `primary` brand red (#FF3B30) on white **3.55:1** — all fail the 4.5:1 normal-text floor, and `success`/`warning` also fail the 3:1 large-text/UI-component floor. The badge-tint pairings (foreground token on its matching `*Bg` token, e.g. `error` on `dangerBg`) are worse, not better: `success` on `successBg` **2.41:1**, `warning` on `warningBg` **2.02:1**, `error` on `dangerBg` **3.44:1**, `info` on `infoBg` **3.38:1**. These are not hypothetical pairings — grepped for live call sites: `rider-app/app/ride-options.tsx:1802-1806` (`retryButtonText: {color:'#FFF', fontSize:14}` on `retryButton: {backgroundColor: colors.primary}` → white-on-#FF3B30 = the same 3.55:1 failure, on an error-state retry CTA); `rider-app/app/ai-assistant.tsx:727` (`rideBannerButtonText: {fontSize: FONT.bodySm, fontWeight:'600', color: colors.primary}`); `rider-app/app/driver-arriving.tsx:973` (`fontSize: sf(11), fontFamily:'...700Bold', color: colors.primary` — an 11px bold label, nowhere near WCAG's large-text bold threshold of ~18.7px). The design system already anticipated exactly this problem: `lightColors.primaryDark` (#D32F2F, computed **4.98:1** on white) is documented in `.claude/context/brand-spinr.md:21` as "use for text/buttons needing WCAG AA contrast on white" — but grepped usage: `colors.primary`-family tokens appear **644** times across rider-app/driver-app vs. **25** for `primaryDark` — the AA-safe variant is used in under 4% of call sites.
- What happens (plain language): any screen that puts brand-red, success-green, warning-amber, info-blue, or danger-red text/labels directly on a white or light background — which the grep above shows is common, not rare — renders text that, by the numbers, most users with low vision or in bright sunlight (a real driver-app use case) would struggle to read, and that a WCAG AA audit would fail on sight.
- Root cause: `lightColors.success`/`warning`/`danger`/`info` and the raw `primary` brand red were chosen for their brand/semantic fidelity (matching Tailwind's emerald/amber/red-500 family, per `docs/design/rider-driver-app-design-system.md`'s own 2026-09-07 color-token history) without a contrast pass against white/light surfaces at adoption time; `primaryDark` exists as the AA-safe sibling but nothing enforces its use over `primary` for text.
- Recommendation: (a) for text/icon/button-label usage specifically, swap `colors.primary` → `colors.primaryDark` at the ~600+ text-context call sites (start with money/CTA text, e.g. the `retryButtonText` case above); (b) for `success`/`warning`/`danger`/`info` used as *text* (not fill/icon-only), either darken the tokens for a text-safe variant (mirroring the `primary`/`primaryDark` split) or restrict the current shades to icon/fill-only contexts where the 3:1 UI-component floor — not the 4.5:1 text floor — applies. Not every one of the 644/50/86 hits is a text-contrast violation (many are icon fills, borders, or large-text contexts where 3:1 suffices) — this needs per-call-site triage, not a blind find-replace.   Alternative considered: leave as-is since dark mode's equivalents are all comfortably AA-compliant (dark-mode `success` on black computed at 10.92:1, `warning` 12.58:1, `primary` (#FF453A) 6.16:1) — rejected as a mitigation, because light mode is very likely the default/majority mode for most users and nothing in the code forces dark mode.
- Blast radius: `shared/theme/index.ts` token definitions are the single source (good — one place to reconsider values); the ~700 call sites across rider-app/driver-app are the real blast radius for any per-site remediation, spanning both apps' `app/` and `components/` trees.
- Rollout: additive at the token layer (adding a text-safe variant doesn't require removing the existing one); per-call-site swaps are additive too (color-only change). No flag needed for a color value change, but CLAUDE.md gate 6 applies — no visual-regression tooling exists for either mobile app, so this needs a manual visual pass, not "no diff" reasoning.
- Verification to close: a lint rule or CI check flagging `color: colors.primary` (and the other three raw semantic tokens) in a `Text` style as a candidate for review would catch regression; short of that, a manual contrast-tool pass (this report used computed arithmetic, not a live tool) against a sample of the sites cited above.

### UXA11Y-003 — Destination search silently converts a network/API failure into an indistinguishable "no results," with no distinct no-results state either
- Hierarchy: L2 Rider App › L3 Booking › L4 Destination search › L5 rider on a slow/flaky connection
- Severity: **MEDIUM**   Priority score: S×B×L = 2×2×3 = 12
- Status: VERIFIED   Existing item: new — grepped `ACTION_ITEMS.md` for "places autocomplete"/"search error"/"no results" — no hits
- Adversary: hostile network (a rider on slow 3G, per the mandated adversary list); first-time rider who has never used ride-share (no signal that "nothing happened" means "try again," not "that address doesn't exist")
- Evidence: `shared/hooks/usePlacesAutocomplete.ts:74-76` — `catch { if (mySeq !== requestSeqRef.current) return; setPredictions([]); }` — any thrown error (network timeout, 5xx, Maps proxy failure) is caught and silently resolves to an empty predictions array, with no `error` field anywhere in `UsePlacesAutocompleteResult` (lines 26-35) for a caller to distinguish "your search timed out" from "Google found nothing." `rider-app/app/search-destination.tsx:627-641` — when `predictions.length === 0 && !isSearching`, the UI falls back to the *same* `ListHeaderComponent` (quick-access "Use your GPS location"/"Choose a point on the map" + Recent + Saved) regardless of whether the rider has typed nothing yet, typed a query that genuinely has zero Google matches, or typed a query whose request failed outright — no "No results for '…'" or "Search failed — check your connection" copy was found anywhere in the file (grepped for "no results"/"couldn't find"/"failed" — zero hits in this file).
- What happens (plain language): a rider on a weak connection who types a destination and gets no response back sees exactly the same list of recent/saved places they'd see if they'd typed nothing at all — no indication their search failed, timed out, or genuinely found nothing. For a first-time user this reads as "the app isn't doing anything," not "try again" or "type more/check your spelling."
- Root cause: the shared hook was built to be UI-agnostic and minimal (per its own doc comment), and error handling was scoped to "don't crash, don't show stale results" rather than "tell the caller what happened" — a reasonable minimal contract that the one caller checked in this pass (`search-destination.tsx`) never extended with its own error/no-results branch.
- Recommendation: add an `error: boolean` (or `string | null`) field to `UsePlacesAutocompleteResult`, set on the catch path (still clearing predictions), and give `search-destination.tsx` a third branch — network/API error ("Search failed — check your connection" + implicit retry-by-retyping) distinct from genuine zero-results ("No matches for '…' — try a nearby landmark or street name") distinct from the current recents/saved fallback for an empty query.   Alternative considered: leave the fallback-to-recents behavior as a soft failure mode (arguably not the worst UX — the rider isn't stuck) — rejected as the primary fix because it doesn't help a first-time user with no recents/saved places yet, who has nothing to fall back to and no signal anything went wrong.
- Blast radius: `shared/hooks/usePlacesAutocomplete.ts` is also used by driver-app (per its own doc comment, "reusable ... for rider/driver apps") and has a separate, non-importing admin-dashboard parallel copy (`admin-dashboard/src/hooks/usePlacesAutocomplete.ts`, not read this pass) — a shape change to the shared hook's return type needs both mobile-app call sites checked, not just `search-destination.tsx`; the admin copy is a separate file and unaffected by this change.
- Rollout: additive (new optional field, existing behavior unchanged if callers ignore it); no flag needed.
- Verification to close: a test mocking the underlying `api.get` call to reject and asserting the hook surfaces a distinguishable error state; a rider-app UI test asserting distinct copy renders for the error vs. zero-results vs. empty-query cases.

### UXA11Y-004 — `CarMarker.tsx`'s live position/bearing animation — "the most elaborate motion in either app" per the design system's own description — has no reduce-motion check, unlike its sibling animated components
- Hierarchy: L2 Driver App / Rider App › L3 Live map tracking › L4 Vehicle marker animation › L5 motion-sensitive user (vestibular disorder)
- Severity: **MEDIUM**   Priority score: S×B×L = 2×2×2 = 8
- Status: VERIFIED   Existing item: new
- Adversary: a rider/driver with a vestibular disorder or motion sensitivity (WCAG 2.3.3 Animation from Interactions is AAA, not AA, but the *spirit* of "respect reduce-motion" is already an established pattern elsewhere in this exact codebase — see steelman §1)
- Evidence: grepped `driver-app/components/CarMarker.tsx` for `ReduceMotion`/`isReduceMotionEnabled` — zero hits, confirmed by direct file grep. By contrast, in the same two apps: `rider-app/app/ride-status.tsx:171`, `rider-app/app/driver-arriving.tsx:119-121`, `rider-app/components/AiWelcomeOrb.tsx:24-29`, and both apps' `components/BrandSplash.tsx` all call `AccessibilityInfo.isReduceMotionEnabled()` and branch their animation accordingly. `.claude/skills/spinr-rider-driver-design-system/SKILL.md` itself names `CarMarker.tsx`'s "live position/bearing animation" as "the most elaborate motion in either app, platform-branched for iOS/Android, continuously reflecting real vehicle movement" and explicitly carves it out from any *decluttering* pass — but that carve-out is about visual weight/signal, not about the separate, unaddressed question of whether the continuous motion itself should ease/skip when the OS-level reduce-motion setting is on.
- What happens (plain language): a rider or driver with reduce-motion enabled at the OS level gets the intended calmer experience everywhere else in the app (splash screen, search pulse, arrival animation, AI orb) except the one place with the most continuous, elaborate motion — the live car marker moving and rotating on the map for the entire trip.
- Root cause: `CarMarker.tsx` was very likely built and iterated on purely for visual/motion fidelity (platform-branched iOS/Android implementation, per the design-system doc) without a reduce-motion pass being part of that work, while the four other animated components got one — the same "later components get the accessibility pass, earlier ones don't" pattern seen in RIDERJ-004 (rider-app's option cards) and UXA11Y-002 above.
- Recommendation: read `AccessibilityInfo.isReduceMotionEnabled()` in `CarMarker.tsx` and, when true, either snap to position updates without the smoothing/rotation animation or shorten the animation duration substantially — mirroring the pattern already established in the four sibling components.   Alternative considered: leave as-is on the reasoning that the marker communicates real-time, safety-relevant vehicle position (a case for treating it as "essential" motion under WCAG's own exception) — worth a genuine product/accessibility judgment call rather than this lane silently deciding it, hence escalated in §17 rather than asserted as an unconditional fix.
- Blast radius: `driver-app/components/CarMarker.tsx`; design-system doc notes rider-app has an equivalent/related marker component too (not independently re-checked this pass for the same gap — see §16).
- Rollout: additive, no flag needed for a motion-duration branch; no logic/state change.
- Verification to close: a test or manual check that toggling `isReduceMotionEnabled` changes the marker's animation behavior; confirm the "essential motion" product decision (§17) before treating this as a hard requirement vs. a judgment call.

### UXA11Y-005 — `docs/ACCESSIBILITY.md` is stale on both its legal citation and its description of the actual a11y CI gate
- Hierarchy: L2 Governance & Docs › L3 Accessibility documentation › L4 Compliance status doc accuracy
- Severity: **LOW** (doc-only, doesn't change app behavior)   Priority score: S×B×L = 1×2×2 = 4
- Status: VERIFIED (the drift itself) / the AODA-vs-Saskatchewan point is ASSUMED (no primary Saskatchewan accessibility-statute source fetched this pass)   Existing item: adjacent to, not duplicating, `compliance.md`'s **X16**/**A4**/**COMP-011** (R12's finding is "WCAG 2.1 AA is ASSUMED to be the legal bar vs. the actual SK Human Rights Code accommodation duty" — a legal-basis question). This finding is narrower and different: the specific statute `docs/ACCESSIBILITY.md` cites by name is very likely the wrong province's, and separately, the doc's own description of the automated tooling is outdated.
- Adversary: regulator/auditor cross-checking the doc against the code; plaintiff's lawyer (a public-facing internal doc citing the wrong jurisdiction's law undermines credibility of every other claim in it)
- Evidence: `docs/ACCESSIBILITY.md:5-7` — "in accordance with **Ontario's Accessibility for Ontarians with Disabilities Act (AODA)**" — Spinr is a Saskatchewan-first product per CLAUDE.md's entire Regulatory section and `.claude/context/regulatory-sk.md`; Saskatchewan has its own accessibility statute (*The Accessible Saskatchewan Act*, per general knowledge — **ASSUMED, not fetched from a primary Government of Saskatchewan source this pass**), which is a different law than Ontario's AODA. Separately, VERIFIED by direct comparison: `docs/ACCESSIBILITY.md:26-30` says axe-core "scans `/login` and `/dashboard/drivers`" (2 routes) and "Critical violations fail the build" — but `admin-dashboard/e2e/crawl-audit.spec.ts` (the actual, current implementation) scans **42 routes** with a per-route baseline-ratchet that explicitly tolerates non-regressing pre-existing violations, not a flat 0-critical-violations gate. `docs/ACCESSIBILITY.md` is dated "as of 2026-04-09" and was never updated after the 42-route crawl-audit + ratchet shipped (per `ACTION_ITEMS.md` E11, itself citing a 2026-07-29 baseline capture).
- What happens (plain language): an internal or external reader of this doc — including counsel doing the review `docs/legal/accessibility-statement.md`'s own pre-publication notes call for — gets a materially smaller, and jurisdictionally wrong, picture of both the legal driver and the actual automated coverage than what the code does today.
- Root cause: the doc was written once (2026-04-09) at an earlier, genuinely-2-route stage of the a11y tooling and never revisited as the tooling grew; the AODA citation looks like a template/generic-Canadian-SaaS assumption that was never swapped for Spinr's actual Saskatchewan-first regulatory scope.
- Recommendation: update `docs/ACCESSIBILITY.md`'s route count and ratchet description to match `crawl-audit.spec.ts`'s current behavior; replace the AODA citation with the correct Saskatchewan statute (after a primary-source check — this is exactly the kind of claim CLAUDE.md's ground rules say must not be presented as settled without one) or, if WCAG 2.1 AA's actual legal basis for Spinr is the SK Human Rights Code's accommodation duty (per `compliance.md`'s X16), say that instead of citing an unrelated province's statute.   Alternative considered: leave it, since it's an internal doc not the published statement — rejected because `docs/legal/accessibility-statement.md`'s own "What we've built so far" section was explicitly filled in *from* this doc (`accessibility-statement.md:101-112`), so staleness here propagates into the public-facing page's factual basis.
- Blast radius: doc-only; no code change. Downstream reader: `docs/legal/accessibility-statement.md`'s maintenance notes explicitly say to re-check against this file before any future republish.
- Rollout: n/a (doc edit).
- Verification to close: doc updated with route count matching `crawl-audit.spec.ts` and a primary-sourced (or explicitly-hedged) statute citation.

### UXA11Y-006 — Positive correction: the crawl-audit ratchet's own governing comment (and `ACTION_ITEMS.md` E11 quoting it) understate real progress — 4 violations remain, not 64
- Hierarchy: L2 Admin Dashboard › L3 Accessibility CI gate › L4 Baseline-debt accuracy
- Severity: **RECOMMENDATION** (docs-hygiene, not a defect — the actual state is *better* than documented)   Priority score: n/a
- Status: VERIFIED   Existing item: extends `ACTION_ITEMS.md` E11 ("a11y checks in CI," closed 2026-09-04-ish per the checkbox, itself quoting the "64 pre-existing violations across 41 routes" figure) — not re-opening E11, correcting the number it and the code comment both still carry
- Adversary: n/a — self-check/hygiene finding
- Evidence: `admin-dashboard/e2e/crawl-audit.spec.ts:223-228`'s code comment states "64 pre-existing violations across 41 routes as of 2026-07-29 are tracked debt." Direct read of the actual `admin-dashboard/e2e/a11y-baseline.json` on disk (42 route entries, one per `ROUTES` array item) sums to **4 total tolerated violations across 3 routes** — `/dashboard/documents`: 2, `/dashboard/notifications`: 1, `/dashboard/support`: 1; all other 39 routes are at the 0-tolerance default. The comment (and `ACTION_ITEMS.md` E11's checkbox text, which quotes the same figure) was never updated after whatever remediation pass brought the real count down from 64 to 4.
- What happens (plain language): the admin-dashboard's accessibility debt is in materially better shape than the codebase's own comments claim — a genuinely positive finding, but one that means anyone reading the comment or E11 today underestimates how close admin-dashboard is to a true zero-tolerance a11y gate, and doesn't know which 3 routes (documents, notifications, support) still carry the residual 4.
- Root cause: a code comment (and the `ACTION_ITEMS.md` entry that quoted it) describing a point-in-time snapshot, not re-derived from the live baseline file when it changed — the same "docs are snapshots" pattern `00-history.md` names as a recurring issue fleet-wide.
- Recommendation: update the `crawl-audit.spec.ts:223-228` comment to reflect the current baseline (4 violations / 3 routes) and consider whether closing the remaining 4 (2 on `/dashboard/documents`) is now small enough to just fix outright rather than carry as tracked debt.   Alternative considered: none needed — this is a one-line comment correction.
- Blast radius: comment-only; the ratchet logic itself (`expect(...).toBeLessThanOrEqual(allowed)`) already reads the live JSON correctly regardless of what the comment says, so there is no functional bug here.
- Rollout: n/a.
- Verification to close: comment updated; optionally, the 4 remaining violations on `/dashboard/documents`(×2)/`/dashboard/notifications`/`/dashboard/support` triaged and fixed, dropping the baseline to 0 across the board.

<!-- SECTION: 3 -->
## 3. Four-states sweep

**Method, stated once:** grep-based signal sweep (loading/empty/error/retry
regexes) across all 48 rider-app screens (`rider-app/app/**/*.tsx`, excluding
tests), all 42 driver-app screens (`driver-app/app/**/*.tsx`), and all
**63** `admin-dashboard/src/app/dashboard/**/page.tsx` files found by direct
`find` — note this is the real, current count, not the "43 admin pages"
figure CLAUDE.md's own deploy/inventory language elsewhere in this repo
repeats; per W0's instruction to never trust a CLAUDE.md count without
re-reading the code, 63 is what a fresh `find` returns today (the 43 figure
likely predates several nested/dynamic routes like
`corporate-accounts/[id]/{members,policy,subscription}` or counts "pages" by
a different unit than `page.tsx` files — not reconciled further this pass).
Cells marked **INFERRED** are grep-signal-only (state logic likely exists
somewhere in the component tree but wasn't opened and read this pass); cells
marked **VERIFIED** were opened and read (by this lane or a cited sibling
lane) this pass or a prior one. A rendered/screenshotted check was performed
for **zero** rows (per §0).

### 3a. rider-app / driver-app (spot-checked screens beyond A7's 8; not exhaustive — 90 screens combined is out of this lane's time budget to open individually)

| Screen | Loading | Empty | Error+retry | Success | Evidence |
|---|---|---|---|---|---|
| `rider-app/app/search-destination.tsx` | VERIFIED — `isSearching` → `ActivityIndicator` + "Searching..." (line 620-624) | **Partial — see UXA11Y-003**: an empty-query state exists (recents/saved/quick-access) but is visually identical to a genuine zero-results state | **Gap — see UXA11Y-003**: hook swallows fetch errors into the same empty-predictions state, no distinct copy | VERIFIED — predictions render in a `FlatList` (line 627-634) | `search-destination.tsx:618-843` |
| `rider-app/app/payment-confirm.tsx` | Not independently verified this pass | Not independently verified this pass | Not independently verified this pass | Renders fare breakdown (money text, see UXA11Y-001) | not opened beyond the `allowFontScaling` grep hits |
| `driver-app/app/driver/(tabs)/index.tsx` (main driver map/dashboard) | VERIFIED — `wsErrorText` banner present (line 1352), speed chip renders | Not applicable (live dashboard, not a list) | VERIFIED — WS error text shown (line 1352) | VERIFIED — composes `DriverIdlePanel`/`ActiveRidePanel` (both read this pass, §1/§2) | index.tsx:1352,1896-1899; `DriverIdlePanel.tsx`, `ActiveRidePanel.tsx` |
| `driver-app/components/dashboard/DriverIdlePanel.tsx` | N/A (toggle, not async list) | N/A | N/A — `accessibilityState={{disabled}}` when ineligible (line 294) substitutes for an error state | VERIFIED — online/offline pill states both labelled (§1) | DriverIdlePanel.tsx:247-294 |
| `driver-app/components/dashboard/ActiveRidePanel.tsx` | VERIFIED — `isLoading` threaded through every action button (A7-cited, re-confirmed this pass via the accessibility-label grep at §4) | N/A (not a list screen) | VERIFIED — inline error + retry per A7's citation (line 477 area) | VERIFIED | ActiveRidePanel.tsx (full file read this pass for §1/§4) |
| Remaining 86 rider/driver screens | **INFERRED only** — not opened this pass; aggregate grep signal below | — | — | — | see 3c |

### 3b. admin-dashboard — 63 `page.tsx` files, aggregate grep sweep

Two regex passes were run, because the narrow one under-counted (many pages
compose a `lucide-react` `Loader2`/`animate-spin` icon rather than a
component named "loading," and several are thin composition shells that
delegate all loading/error logic to `_components/*-tab.tsx` children not
counted here — see the `documents/page.tsx`/`earnings/page.tsx` note below).

| Signal | Broad regex (`Loader2`\|`animate-spin`\|`Skeleton`\|`isPending`\|`isFetching`\|`Spinner`, case-insensitive) | Narrow regex (`isLoading`\|literal "loading"\|`ActivityIndicator`) |
|---|---|---|
| Pages with a loading-indicator signal | 44 / 63 | 15 / 63 |
| Pages with an explicit "No results/data/found" empty-state string | 25 / 63 | 1 / 63 |
| Pages with an error/retry/toast-on-failure signal | 51 / 63 (broad `catch`/`isError`/`errorMessage`) | 13 / 63 (`Try again`/`Retry`/`toast.error`) |

**9 pages showed zero hits on the broad loading-signal regex**: `ai-console`,
`data-transfer`, `documents`, `driver-offers`, `earnings`, `forecast`,
`monitoring`, `notifications`, `referrals`, `surge`. Spot-checked two: 
`documents/page.tsx` has **zero** matches for `loading`/`error`/`useState`/
`useEffect` at all — VERIFIED it is a thin shell with no own state;
`earnings/page.tsx` VERIFIED to compose three child tab components
(`RideEarningsTab`, `SpinrPassRevenueTab`, `PayoutsTab` under `_components/`)
— the loading/error logic, if any, lives in those children, not counted by
this page-level sweep. **This means the "9 pages with zero loading signal"
number is an artifact of the sweep's grain, not evidence those 9 pages
genuinely have no loading state** — flagged as a real gap in §16, not
asserted as a finding. `monitoring/page.tsx` itself has zero direct hits but
composes `alert-feed.tsx` (which VERIFIED handles reduce-motion, §1) and is
partially covered by R7/admin-ops's spot-check ("rides kept in place rather
than swallowed to `[]` on error" per A7's citation of that page).

Cross-reference: `02-findings/admin-ops.md` §9 explicitly deferred
per-page loading/empty/error verification to this lane ("would require
reading each `admin-dashboard/src/app/dashboard/*/page.tsx`, out of budget")
— the sweep above is this lane's answer to that deferral, at grep-aggregate
depth rather than per-page depth (63 files individually read was out of this
lane's budget too; the two-regex cross-check above is the compromise).

### 3c. Aggregate signal counts (all screens/pages scanned, INFERRED grep-only unless cited elsewhere as VERIFIED)

| Surface | Files scanned | `accessibilityLabel`/`aria-label` occurrences | `accessibilityRole`/`role` occurrences | Hex color literals | `fontFamily` occurrences | `hitSlop` occurrences |
|---|---|---|---|---|---|---|
| rider-app (`app/`) | 48 | 108 | 86 | 287 | 277 | 19 (in `app/`; +3 more in `components/`) |
| driver-app (`app/`) | 42 | 30 | 28 | 95 | 27 | 7 (in `app/`; +3 more in `components/`) |
| admin-dashboard (`page.tsx` only) | 63 | 108 | 7 | 67 | 0 (expected — Tailwind classes, not inline `fontFamily`) | 0 (N/A — web, not touch-target `hitSlop`) |

(These raw counts feed §5–7 below; they are not themselves four-states
evidence, included here once rather than re-scanned per section.)

<!-- SECTION: 4 -->
## 4. Screen-reader flows (rider booking, driver go-online→complete)

Reasoned from `accessibility*` props in source, per §0 — no VoiceOver/
TalkBack run performed.

### 4a. Rider booking flow: home → destination search → ride options → confirm → searching → driver assigned → in trip → completed → rate/tip → receipt

| Step | Screen | Primary control(s) | Label/Role/State | Live-region announcement | Evidence |
|---|---|---|---|---|---|
| Destination search | `search-destination.tsx` | Text input, prediction rows, "Add a stop," "Search Ride" CTA | **Yes** — `accessibilityLabel`/`Hint` on "Add a stop" (609-610), recent-search rows (826), Search Ride CTA has label+hint+`accessibilityState={{disabled}}` (852-855) | Not checked for the prediction list itself (`FlatList` — screen readers announce list items on focus by default, no custom live-region needed for this pattern) | `search-destination.tsx` (read this pass) |
| Vehicle-type selection | `ride-options.tsx` | Vehicle-type option cards | **No — see RIDERJ-004 (R4, cited not re-derived)**: the primary selector itself has zero `accessibilityLabel`/`Role`/`State`; every *other* control on the same screen (back, promo remove, WAV, quiet, fare-breakdown toggle, schedule clear, Confirm CTA) is labelled | N/A for the unlabelled cards | `ride-options.tsx` (R4's evidence, re-cited) |
| Confirm / book | `ride-options.tsx` | "Confirm"/"Schedule" CTA | Yes — dynamic label incl. vehicle type, `accessibilityState.busy` while booking (R4/A7-cited) | Not checked | cited from RIDERJ table |
| Searching / matching | `ride-status.tsx` | status text, cancel | **Yes** — live-region announcement per status transition VERIFIED by R4 (`ride-status.tsx:203-224`) | **Yes** — this is the one step in the whole rider flow with confirmed status-change announcements | `ride-status.tsx:203-224` (R4-cited) |
| Driver assigned/arriving/arrived | `driver-arriving.tsx`, `driver-arrived.tsx` | ETA text, plate/PIN display, ride-in-progress transition | Mixed: `allowFontScaling={false}` on ETA/plate/PIN text (UXA11Y-001); `accessibilityLabel` presence not independently re-verified this pass beyond the font-scaling grep | Not checked this pass | `driver-arriving.tsx:648,773,785` |
| In trip | `ride-in-progress.tsx` | SOS button, fare/ETA readouts, staleness banner | `DriverLocationStatus` staleness banner VERIFIED to carry `accessibilityLiveRegion="polite"` (A7/R4-cited); SOS button's own internal a11y props not independently re-checked this pass (R4 also flagged this as not-checked) | **Yes**, for the staleness banner specifically | `DriverLocationStatus.tsx` (R4-cited) |
| Complete / pay | `ride-completed.tsx` | Pay button, tip presets, stats | `isSubmitting` guard present (R4-cited); stat values use `allowFontScaling={false}` (UXA11Y-001); explicit a11y-label presence not independently re-checked | Not checked | `ride-completed.tsx:722,757,764,771` |
| Receipt | `ride-details.tsx` | fetch/email/PDF actions | Not checked this pass (R4 also flagged as not checked) | Not checked | — |
| Rate & tip | (part of `ride-completed.tsx`) | star rating, tip amount | Not checked this pass | Not checked | — |

**Net assessment of the rider flow:** the two steps with the most deliberate
screen-reader investment — the searching/matching wait screen and the
driver-location staleness banner — are genuinely well done (live-region
announcements, not just visual state). The weakest link is the single most
consequential tap of the entire flow (choosing a vehicle type,
`ride-options.tsx`), already filed as RIDERJ-004 by R4 — this lane's
independent read of the same file (for the four-states/label-grep sweep in
§3) reaches the same conclusion and does not duplicate the finding.

### 4b. Driver flow: go-online → offer → accept → arrive → start → complete

| Step | Screen/component | Primary control(s) | Label/Role/State | Live-region announcement | Evidence |
|---|---|---|---|---|---|
| Go-online | `DriverIdlePanel.tsx` | GO/STOP toggle | **Yes, fully** — `accessibilityRole="button"`, dynamic label, `accessibilityState={{disabled: !canGoOnline}}` (§1) | Not applicable (discrete toggle, not a status stream) | `DriverIdlePanel.tsx:274-294` |
| Offer received | `RideOfferPanel.tsx` | Accept/Decline | **Yes** — `accessibilityLabel="Decline ride"`/`"Accept ride"`, `accessibilityState={{disabled: isLoading \|\| fareMissing}}` (487-509) | Ephemeral push UI — a live-region announcement of the offer *arriving* was not checked (would matter for a screen-reader driver to know an offer appeared without visually scanning) | `RideOfferPanel.tsx:478-509` |
| En route / arrived / in trip | `ActiveRidePanel.tsx` | Navigate, Arrived, Start, Complete, Cancel, No-show, PIN keypad | **Yes, extensively** — 15+ distinct labelled controls (§1) | **No `accessibilityLiveRegion` found in this component** — status *changes* (e.g. transitioning from "en route" to "arrived") rely on the screen re-rendering with new labelled buttons, not an explicit announcement of the transition itself | `ActiveRidePanel.tsx:592-934` (full read) |
| Completion | `driver-app/app/driver/ride-detail.tsx` | — | Not checked this pass | Not checked | — |
| Appeal (if deactivated) | `appeal.tsx` | Submit CTA | VERIFIED present per driver-journey.md (R5): `accessibilityLabel`/`accessibilityRole` on the primary CTA | Not checked | driver-journey.md:65 (cited) |

**Net assessment of the driver flow:** materially better-labelled than the
rider flow at the control level — every actionable button in the highest-
stakes moments (going online, accepting an offer, completing a trip) has a
label, role, and disabled state. The one gap found: no live-region
announcement when the ride's *phase* changes (en route → arrived → in
progress) — a screen-reader driver would need to actively re-explore the
screen to notice the phase changed, rather than being told. Given a driver
is also expected to be watching the road/screen for navigation regardless,
this is a real but lower-severity gap than the rider-side ones — not filed
as a separate numbered finding given the time budget, but named here as a
NOT VERIFIED-adjacent gap worth a follow-up (§16/§17).

<!-- SECTION: 5 -->
## 5. Dynamic type & touch targets

**Dynamic type:** see UXA11Y-001 (§2) — the dominant finding here.
`maxFontSizeMultiplier` is used **zero** times in either app; `allowFontScaling={false}`
76+ times, heavily on money figures. No `Text.defaultProps` override or themed
`Text` wrapper enforcing a consistent scaling policy exists in either app —
this matches the rider-driver design-system skill doc's own "Known Gap UX1"
finding about `fontFamily` enforcement (a *different* but structurally
identical problem: no central `Text` wrapper, so every screen makes its own
choice) — cited, not re-derived, since UX1 is already tracked.

**Touch targets:** spot-checked (not exhaustive) button/control heights
across both apps:

| Control | Height/size | Meets 44×44pt (iOS) / 48×48dp (Android)? | Evidence |
|---|---|---|---|
| `ride-options.tsx` unnamed control (likely a chip/toggle) | `height: 44` | Meets iOS minimum exactly, at the floor | `ride-options.tsx:1695` |
| `ride-options.tsx` unnamed control | `height: 40` | **Below** both platform minimums | `ride-options.tsx:2191` |
| `ride-options.tsx` unnamed control | `minHeight: 48` | Meets both platforms | `ride-options.tsx:2279` |
| `ride-options.tsx` circular control | `width: 56, height: 56` | Comfortably exceeds both | `ride-options.tsx:2357` |
| GO/STOP toggle (`DriverIdlePanel.tsx`) | Not measured — described in the design-system skill doc as "a large circular gradient button," VERIFIED to be a `TouchableOpacity`-class control with full a11y wiring (§1), but exact px not read this pass | Likely exceeds minimums given the doc's "large" framing — **INFERRED, not measured** | `DriverIdlePanel.tsx` |

Given only a handful of controls were dimension-checked against 90+ mobile
screens, this is a **sample, not a sweep** — the one confirmed sub-minimum
control (`ride-options.tsx:2191`, 40pt) is worth a follow-up to identify what
it is and whether it's a primary or secondary action (a secondary/rarely-used
40pt control is a smaller concern than a primary one would be).

**`hitSlop` usage**: 19 rider-app `app/` sites + a further ~3 in
`rider-app/components/`; 7 driver-app `app/` sites + ~3 in
`driver-app/components/` (§3c counts). `hitSlop` is React Native's standard
mechanism for expanding a small visual control's *tappable* area without
changing its visual size — its presence at ~25-30 sites per app suggests the
touch-target problem is at least partially, deliberately mitigated in known
spots, but a visual 40pt control (the one found above) without an
accompanying `hitSlop` at that exact site was not cross-checked this pass.

**admin-dashboard touch targets**: not evaluated — web/mouse-primary surface,
touch-target minimums are a mobile-specific WCAG/platform concern; keyboard
operability (the web-equivalent access concern) is covered in §8 instead.

<!-- SECTION: 6 -->
## 6. Contrast table (reasoned, not measured)

**Disclosure, restated:** every ratio below is computed from the hex values
in `shared/theme/index.ts` / `admin-dashboard/src/app/globals.css` using the
standard WCAG relative-luminance formula, run as a one-off Python script this
session — not read from a live contrast-checker tool, and not verified
against how the color actually renders (anti-aliasing, translucency via the
`colors.primary + '15'` alpha-suffix idiom, OS color management). Treat every
number as "the token pairing computes to X:1," not "this text measures X:1
on a real device."

### rider-app / driver-app (`shared/theme/index.ts`)

| Pairing | Light mode | Dark mode | AA text (4.5:1) pass? | AA large-text/UI (3:1) pass? |
|---|---|---|---|---|
| `text` on `background` | 17.40:1 | 18.82:1 | Pass (both) | Pass (both) |
| `textSecondary` on `background` | 4.83:1 | 6.44:1 | Pass (both) | Pass (both) |
| `success` on `background` | **2.54:1** | 10.92:1 | **Fail (light)** / Pass (dark) | **Fail (light)** / Pass (dark) |
| `warning` on `background` | **2.15:1** | 12.58:1 | **Fail (light)** / Pass (dark) | **Fail (light)** / Pass (dark) |
| `danger`/`error` on `background` | **3.76:1** | 7.59:1 | **Fail (light)** / Pass (dark) | Pass (light, marginal) / Pass (dark) |
| `info` on `background` | **3.68:1** | 5.76:1 | **Fail (light)** / Pass (dark) | Pass (light, marginal) / Pass (dark) |
| `primary` (brand red) on `background` | **3.55:1** | 6.16:1 | **Fail (light)** / Pass (dark) | Pass (light, marginal) / Pass (dark) |
| `primaryDark` (AA-safe variant) on `background` | 4.98:1 | 4.98:1 (same value both modes) | Pass (both) | Pass (both) |
| White text on `primary` fill | **3.55:1** | n/a (dark-mode fill differs) | **Fail** | Pass (marginal) |
| White text on `success`/`warning` fill | **2.54:1** / **2.15:1** | n/a | **Fail, badly** | **Fail** (`warning`) / borderline (`success`) |
| `success`/`warning`/`danger`/`info` text on own `*Bg` tint | **2.41 / 2.02 / 3.44 / 3.38 : 1** | not computed (dark-mode tint values not read this pass) | **Fail, all four** | **Fail** (`success`,`warning`) / borderline-fail (`danger`,`info`) |
| `border` on `background` (UI-component 3:1 context) | 1.24:1 | 1.23:1 (`surface` on `background`) | N/A (not text) | **Fail, both modes** — a border this close in luminance to its background is barely visible as a UI-component boundary regardless of AA |

### admin-dashboard (`globals.css`, light mode `:root` and OLED dark)

| Pairing | Ratio | AA text (4.5:1) pass? |
|---|---|---|
| `foreground` on `background` | 16.98:1 | Pass |
| `primary` (#d32f2f, contrast-safe variant) on `background` | 4.76:1 | Pass (narrowly) |
| `destructive` on `background` | 4.62:1 | Pass (narrowly) |
| `success` on `background` | 4.80:1 | Pass (narrowly) |
| `warning` on `background` | 4.81:1 | Pass (narrowly) |
| `info` on `background` | 6.41:1 | Pass |
| White text on `primary` fill | 4.98:1 | Pass |
| `primary` on OLED dark `background` (#09090b) | 4.00:1 | **Fail** (4.5:1 floor) — passes only the 3:1 large-text/UI floor |

**Takeaways:** admin-dashboard's light-mode tokens were clearly chosen with
AA in mind — every one lands just above 4.5:1, consistent with
`spinr-admin-design-system`'s own documented rationale ("Contrast-safe brand
red variant... chosen for WCAG AA on white fills"). admin-dashboard's **OLED
dark-mode primary-on-background** pairing (4.00:1) is the one admin-side gap
found — it clears the 3:1 UI-component floor but not the 4.5:1 text floor,
worth flagging if `--primary` is ever used as small dark-mode body text
rather than icon/border/large-text contexts (not independently traced to a
specific dark-mode text call site this pass — flagged as reasoned-about-only,
see §16). rider-app/driver-app's **light mode** is the far larger gap: four
of five semantic/brand tokens fail the 4.5:1 text floor when used as
foreground text on white, and the badge-tint pairings fail even the more
forgiving UI-component floor — this is UXA11Y-002's evidence base, shown
here in full rather than re-derived.

<!-- SECTION: 7 -->
## 7. Design-system adoption

Per §3c's aggregate counts, cross-referenced against the two design-system
skill docs' own already-tracked adoption gaps (not re-filed as new):

- **Hardcoded hex colors**: 287 in rider-app, 95 in driver-app, 67 in
  admin-dashboard `page.tsx` files alone (undercounts admin's true total —
  `_components/`/`components/` subtrees not counted here). Per
  `docs/design/rider-driver-app-design-system.md`, this is *expected*
  residual, not raw debt — two 2026-09-07 sweeps already replaced the
  mechanically-fixable cases (value-identical-to-token literals, then the
  Tailwind-shade-vs-token split); what remains is disclosed as "genuine
  one-offs, module-level static color maps outside `useTheme()` scope, or
  fixed-contrast foreground on branded surfaces" — this lane did not
  re-audit which of the 287/95 remaining hits fall into which bucket
  (would require per-hit triage, out of budget) but takes the doc's framing
  at face value since it's evidenced with replacement counts, not asserted.
- **`fontFamily` enforcement — already-tracked gap (UX1), re-confirmed**:
  277 `fontFamily` occurrences in rider-app, 27 in driver-app — both far
  below full-file coverage (the design-system doc's own count: "21/64
  rider-app files, 8/61 driver-app files actually set `fontFamily`" — file
  counts, not occurrence counts, so not directly comparable to this lane's
  occurrence-count sweep, but directionally consistent: driver-app's
  adoption is visibly thinner than rider-app's in both counting methods).
  Not re-filing; UX1 already covers this with better evidence (file-level,
  not occurrence-level) than this lane gathered independently.
- **`shared/theme` token usage (`useTheme()`)** — not independently
  re-counted this pass; design-system doc's own count (52 rider-app files,
  53 driver-app files) is taken as current, per the ground rule against
  re-deriving what's already evidenced.
- **admin-dashboard `.theme-v2` / Quiet Console**: per
  `spinr-admin-design-system`, shipped in code but **off in production**
  (`admin_theme_v2_enabled` flag `false`) — meaning every admin-dashboard UX
  observation in this report, including the contrast table in §6 (which
  used the `:root` **default** tokens, not the `.theme-v2` overrides), 
  reflects what a real admin user sees today. Worth restating because it's
  easy to accidentally reason about the *intended* future admin UI instead
  of the shipped one.
- **`shared/components/Button.tsx` has zero driver-app consumers (UX3,
  already tracked)** — cross-referenced against this lane's own finding that
  driver-app's buttons (`DriverIdlePanel`, `ActiveRidePanel`,
  `CancelReasonSheet`) are all independently hand-styled `TouchableOpacity`s
  with per-component `allowFontScaling={false}` choices (UXA11Y-001) — UX3's
  "no shared button primitive" gap is very likely *why* the
  `allowFontScaling`/`maxFontSizeMultiplier` choice was never made once,
  centrally, and instead was decided ad hoc at 40+ separate call sites with
  no consistent policy. Worth naming as a causal link between an
  already-tracked design-system gap and this lane's own top finding, not
  just a coincidence.

<!-- SECTION: 8 -->
## 8. Navigation

- **rider-app/driver-app**: both use Expo Router's file-based routing
  (`app/` directory maps directly to routes) — this makes "reachable only by
  a hidden path" structurally harder to produce than a hand-rolled navigator,
  since every screen file is a route by construction. Whether every screen is
  *linked to* from somewhere reachable in the normal flow (vs. a route that
  exists but nothing ever navigates to) was not exhaustively traced this
  pass — the Cartographer's `traceability.csv` orphan-row flags are the
  better source for this question and weren't cross-referenced against
  screen files specifically in this pass (see §16).
- **Back behavior**: `ride-options.tsx`'s back button is explicitly labelled
  (`accessibilityLabel="Go back"`, R4-cited) — not independently checked
  whether back navigation mid-booking-flow ever leaves the rider in an
  inconsistent state (e.g. going back after a fare estimate has started
  re-pricing) — out of this lane's charter (state-machine correctness is
  R8's).
- **Deep links**: `support.tsx?topic=payment_failed` (cited by R4,
  `ride-completed.tsx`'s retry-to-manage-cards path) is a real, working
  deep-link pattern already in use — a positive precedent RIDERJ-002's
  recommendation (R4) explicitly proposes extending.
- **admin-dashboard**: Next.js App Router, standard nested-route structure
  (`dashboard/corporate-accounts/[id]/{members,policy,subscription}` etc.) —
  same "route exists ⇒ file-based, not hidden" structural property as the
  mobile apps. Sidebar-navigation completeness (does every `page.tsx` have a
  corresponding sidebar entry, or are some routes reachable only via a
  direct link from another page) was not checked this pass — flagged as
  NOT VERIFIED (§16).

<!-- SECTION: 9 -->
## 9. Search UX

- **rider-app destination search** — see UXA11Y-003 (§2) for the primary
  finding (error-swallowing, no distinct no-results state). Positives,
  restated from §1: 300ms debounce, request-sequence guarding, recent/saved
  places, GPS-location and pick-on-map quick actions, `keyboardShouldPersistTaps="handled"`
  on the results list (a real, easy-to-miss mobile keyboard-handling detail
  that's correctly set — VERIFIED, `search-destination.tsx:632,640`).
- **admin-dashboard search/filter (drivers page, spot-checked)** —
  `admin-dashboard/src/app/dashboard/drivers/page.tsx:56-285` — VERIFIED a
  real, explicit 300ms debounce pattern (`searchDebounced`, lines 277-279,
  same numeric value as the rider-app hook, though implemented separately —
  not shared code, just a coincidentally-matching convention) feeding a
  server-side `search` query param, with an explicit code comment at line
  732 acknowledging a click-before-debounce-settles race ("A click can
  arrive before the list's search debounce completes") — evidence this was
  a deliberately reasoned-through UX detail, not an accident. Behavior on a
  **slow query** specifically (does the list show a stale result set while
  waiting, a loading overlay, or a blank state) was not traced this pass.
  Behavior on **zero results** for this specific page was not independently
  confirmed (the broad "25/63 pages have explicit empty-state copy" sweep in
  §3b did not identify which 25, `drivers/page.tsx` not confirmed either way
  this pass).
- **Other admin filters (rides, riders)** — not independently checked this
  pass; `drivers/page.tsx` was the one spot-check performed given the time
  budget, chosen because R7/admin-ops already established it as a
  well-instrumented reference page for other sweeps.

<!-- SECTION: 10 -->
## 10. Motion / reduce-motion

Covered substantively in §1 (steelman) and UXA11Y-004 (§2). Summary table:

| Component | Respects `isReduceMotionEnabled`/`useReducedMotion`? | Evidence |
|---|---|---|
| `rider-app/app/ride-status.tsx` (search pulse) | **Yes** | line 171, the `#4607` fix cited by R4 |
| `rider-app/app/driver-arriving.tsx` | **Yes** | lines 119-121 |
| `rider-app/components/AiWelcomeOrb.tsx` | **Yes** | lines 24-29 |
| `rider-app/components/BrandSplash.tsx` | **Yes** | line 113 |
| `driver-app/components/BrandSplash.tsx` | **Yes** | line 133 |
| `admin-dashboard/.../monitoring/alert-feed.tsx` | **Yes** — `framer-motion`'s `useReducedMotion()`, with a code comment noting entry/exit animation is skipped entirely under reduce-motion | lines 36,95-96 |
| `driver-app/components/CarMarker.tsx` | **No — UXA11Y-004** | zero hits on a targeted grep |
| `driver-app/components/panels/RideOfferPanel.tsx` (countdown fill) | Not checked — plausible WCAG "essential motion" exception (offer timing is functionally meaningful, not decorative), a product judgment call, not asserted as a gap here | not traced this pass |
| rider-app's own live car-marker equivalent (if a separate component from driver-app's) | Not independently checked this pass | — |

<!-- SECTION: 11 -->
## 11. Winter / one-hand / low-connectivity

- **Winter/extreme-cold mode**: R4's rider-journey.md already searched for
  and found no evidence of a winter-operations mode (storm/extreme-cold
  pickup rules, stranded-rider priority) in rider-app or `backend/utils/`
  — cited, not re-derived; this lane's own read of driver-app didn't turn up
  anything additional (not an exhaustive search). Greenfield-extensions §12
  flags this as an open gap for R4/R5/R13 as well as R17 — from a pure UX
  angle, there is no dark-mode-adjacent "winter mode" visual treatment, no
  cold-weather copy, and no glove-mode/larger-target toggle found.
- **Dark mode**: both mobile apps have full `lightColors`/`darkColors`
  support via `ThemeContext` (§6, §7); admin-dashboard has true OLED-black
  dark mode as a deliberate battery-conscious constraint
  (`spinr-admin-design-system` skill doc). This is a genuine low-light
  accommodation already built, not a gap.
- **One-hand / glove-friendly (driver-app)**: the GO/STOP toggle is
  described in the design-system doc as "a large circular gradient button"
  (§1) — consistent with a glove-friendly, large-target design intent, though
  not pixel-measured this pass (§5). `ActiveRidePanel.tsx`'s action buttons
  are bottom-anchored per the component's structure (large action buttons at
  lines ~813-934, i.e. toward the end of the render, consistent with a
  bottom-sheet/panel pattern) — not independently confirmed via a rendered
  layout check that they sit within one-handed thumb reach on a large phone.
- **Low-connectivity / offline**: grepped both apps for `NetInfo`/
  `isConnected`/`isOffline`/`OfflineBanner` — **1 hit each** in rider-app and
  driver-app per the §3c-adjacent sweep (exact call sites not individually
  opened this pass — flagged NOT VERIFIED). A7's steelman already documents
  a strong retry/idempotency layer (`shared/api/client.ts` — bounded 503
  retry, refresh-dedup) and WS reconnect with exponential backoff
  (`useRiderSocket.ts`), which is the *reliability* half of low-connectivity
  handling — cited, not re-derived. What this lane did not confirm: whether
  a rider/driver sees an explicit **"You're offline" banner** distinct from
  a generic error toast when connectivity actually drops (the 1 grep hit per
  app suggests *something* exists, but its UX quality — does it persist
  while offline, does it explain what's degraded — was not read).

<!-- SECTION: 12 -->
## 12. WCAG statement audit

Covered in depth by UXA11Y-005 (§2). Summary: a real, careful, non-overclaiming
public accessibility statement exists (`docs/legal/accessibility-statement.md`,
published 2026-08-21) — a genuine positive (§1 steelman) — but its factual
basis (`docs/ACCESSIBILITY.md`) is stale on both the cited statute (AODA,
likely wrong province — ASSUMED, not primary-sourced this pass) and the
actual scope of the automated tooling (says 2 routes, code does 42 with a
ratchet). The statement itself correctly says neither mobile app has been
audited — this lane's own findings (UXA11Y-001 through -004, RIDERJ-004,
the driver-arriving.tsx / TripCompletedPanel.tsx labelling gaps) are
consistent with that self-assessment, not a contradiction of it — the
statement is accurate in aggregate even though its supporting doc has drifted.

<!-- SECTION: 13 -->
## 13. Feature completeness (UX-bearing L3 features)

Per greenfield-extensions §3's dimension set, scored at the row level (not
exhaustively per-dimension, consistent with sibling lanes' approach given
this lane's time budget) for accessibility/UX-specific features not already
scored by R4/R5/R7:

| L3 feature | UX | Tests (real) | Docs | Verdict |
|---|---|---|---|---|
| Dynamic type / font scaling | **N — UXA11Y-001** | Not found (no test asserting `maxFontSizeMultiplier` anywhere) | Not documented as a policy anywhere in `docs/design/` | **Incomplete — systemic gap, no policy, no test** |
| Color-contrast-safe token usage | Partial (`primaryDark` exists, adoption <4%) | N/A (admin has axe-core CI; mobile has none) | Documented intent in `brand-spinr.md`, not enforced | **Partial — token exists, not adopted** |
| Reduce-motion | Partial (5/6 checked components respect it) | Not found (no test asserting reduce-motion branch behavior) | Not documented as a cross-app policy | **Partial — inconsistent adoption** |
| Accessibility settings screen (rider-app `accessibility.tsx`) | Y (exists, single WAV-visibility toggle) | Y (per R4's citation) | N/A | **Present but narrow scope** — the screen's name ("Accessibility") promises more than a single toggle delivers; a dynamic-type or reduce-motion in-app override was not found there |
| admin-dashboard automated a11y gate (`crawl-audit.spec.ts`) | N/A (internal tooling, not user-facing) | **Y — real, CI-wired, 42 routes** | Stale (`docs/ACCESSIBILITY.md`, UXA11Y-005) | **Complete as tooling, docs need a refresh** |
| Search error/no-results states (rider destination search) | **N — UXA11Y-003** | Not found | N/A | **Incomplete** |

<!-- SECTION: 14 -->
## 14. Rebuild Delta card

## Epic: Shared Accessible UI Primitives (rider-app/driver-app)
- Verdict per inherited pattern: **BUILD** — no shared, enforced accessible
  primitive layer exists today; `shared/components/Button.tsx` exists but has
  zero driver-app consumers (UX3, already tracked) and, per this lane's
  reading, carries no opinion on font-scaling/contrast/touch-target policy
  even where it is used.
- Keep (already best-in-class): the shared `Toast` component's
  screen-reader wiring (§1) — proof that a *single, centrally-fixed*
  component can retroactively fix an accessibility gap across ~131 call
  sites in one PR. This is the model to replicate for text scaling and
  contrast, not a one-off.
- Uber/Lyft do: both ship a first-party design-system component library with
  built-in dynamic-type support and enforced contrast-safe token usage at the
  component level, not per-screen discretion (general product knowledge,
  **ASSUMED**, not independently fetched this pass).
  Spinr today: has the *tokens* (`shared/theme/`) but not the *enforcement*
  — 76+ independent `allowFontScaling={false}` decisions, 644 raw-vs-25
  AA-safe primary-color call sites, no shared `Text`/`Button` wrapper that
  bakes in a font-scaling or contrast policy.
- Clean-sheet Spinr would: build a themed `<Text>` wrapper (mirroring the
  already-established `useTheme()`/`createStyles(colors)` idiom both apps
  already use) that defaults to `maxFontSizeMultiplier={1.3}` (overridable,
  not silently disabled) and a `<Money>`/`<Fare>` variant specifically for
  currency figures that scales more conservatively but never locks to zero
  scaling; extend `shared/components/Button.tsx` (once driver-app actually
  adopts it, closing UX3) to only accept `primaryDark`-class tokens for its
  label color, making UXA11Y-002's failure mode structurally hard to
  reproduce rather than relying on per-screen discipline.
- Why (the edge it creates): converts ~76+2 scattered, screen-by-screen
  accessibility decisions into one enforced default, the same leverage the
  Toast fix already proved works in this exact codebase — cheaper to
  maintain and much harder to regress than a per-screen policy.
- How (architecture/pattern): a `shared/components/Text.tsx` and extended
  `Button.tsx`, adopted incrementally (screen-by-screen swap, not a
  big-bang rewrite) — consistent with CLAUDE.md's "incremental over
  rewrite" tie-breaker. Who: rider-app + driver-app frontend team (one
  shared component, both apps benefit — same leverage as `Toast`). When:
  **Now** for the wrapper components themselves (small, isolated, no ride/
  money-logic risk); **Next** for the screen-by-screen adoption sweep
  (mirrors the design-system doc's own two-pass token-adoption precedent).
- Incremental path from today (no big-bang): step 1 — build
  `shared/components/Text.tsx` with the bounded-scaling default (S effort,
  no consumers yet, zero risk) → step 2 — swap the money-figure sites found
  in UXA11Y-001 first (payment-confirm, ride-in-progress, ride-completed,
  DriverTopBar, TripCompletedPanel, ActiveRidePanel, CarOfferPanel — the
  highest-stakes subset) → step 3 — sweep the remaining ~60 sites
  opportunistically as those screens are touched for other reasons (matches
  the "surgical changes" principle — don't reformat unrelated code, but do
  fix accessibility props when already in a file for something else) →
  step 4 — same pattern for `primaryDark`-only `Button`/link-text color
  enforcement.
- Cost/effort: wrapper components = S. Money-figure sweep = S-M (< 15 call
  sites, mechanical). Full sweep = M (76+ sites, spread over time per step
  3). Risk: low (additive, no logic/state change, no money-math or
  ride-state involvement — CLAUDE.md's highest-caution domains are
  untouched by this epic entirely). Reversibility: high. Build vs. buy:
  build — this is a thin wrapper over React Native's own `Text`/
  `TouchableOpacity`, nothing to buy.
- Advantage type: trust/operational (accessibility conformance reduces
  legal exposure and support tickets from low-vision users; not a
  competitive differentiator most riders would notice day-to-day, but a
  real regulatory and reputational hedge given CLAUDE.md's explicit WCAG
  2.1 AA mandate).
- "Why not?": the likely reason this doesn't already exist is the same
  "surgical changes, don't touch unrelated code" discipline CLAUDE.md itself
  mandates working against the *discovery* of this pattern — no single PR
  had reason to look at all 76 `allowFontScaling` sites at once, so no one
  PR ever proposed the shared wrapper. A lightweight lint rule flagging
  `allowFontScaling={false}` without an accompanying `maxFontSizeMultiplier`
  (or a `// a11y-exempt: <reason>` comment) would catch new instances
  cheaply without waiting for the full wrapper to be adopted everywhere.

<!-- SECTION: 15 -->
## 15. Top 5

1. **UXA11Y-001 (HIGH)** — `allowFontScaling={false}` used 76+ times across
   rider-app/driver-app with zero `maxFontSizeMultiplier` anywhere,
   concentrated on money figures (fare totals, driver earnings) — a
   systemic WCAG 1.4.4 (Resize Text, AA) gap, not a scattered one.
2. **UXA11Y-002 (HIGH)** — raw semantic color tokens (`success`/`warning`/
   `danger`/`info`, and the raw brand-red `primary`) used directly as
   text/button-label color compute to 2.02–3.76:1 on white — below the
   4.5:1 AA text floor and, for two of them, below the 3:1 UI-component
   floor too — despite an AA-safe `primaryDark` token already existing and
   documented for exactly this purpose, used at <4% of `primary`-family
   call sites.
3. **RIDERJ-004 (R4's finding, independently re-confirmed this pass, MEDIUM-HIGH)**
   — `ride-options.tsx`'s vehicle-type option cards, the single highest-
   stakes tap in the booking flow, have no `accessibilityLabel`/`Role`/
   `State` at all, while every other control on the same screen does.
4. **UXA11Y-003 (MEDIUM)** — destination search silently converts a
   network/API failure into the same UI as "no results," with no distinct
   no-results state either — a rider on a slow connection or a genuinely
   unmatched address gets no signal of what actually happened.
5. **admin-dashboard's real, CI-wired axe-core gate (UXA11Y-006, tooling
   correction, POSITIVE)** — 42 routes, merge-blocking ratchet, currently
   down to 4 tolerated violations across 3 routes (not the 64/41 the
   governing comment and `ACTION_ITEMS.md` E11 still claim) — the strongest
   accessibility tooling position of any surface in this repo, materially
   undersold by its own documentation.

<!-- SECTION: 16 -->
## 16. NOT verified

- No screen reader (VoiceOver/TalkBack/NVDA), no browser, no device, no
  contrast-checker tool was used anywhere in this audit — every claim above
  is a source-code read or a computed-arithmetic contrast ratio (§0).
- Only 8 (A7) + ~6 (this pass) of ~90 combined rider-app/driver-app screens
  and ~5 of 63 admin-dashboard pages were individually opened and read for
  four-states logic; the rest rely on aggregate grep signal (§3c), which the
  `documents/page.tsx`/`earnings/page.tsx` spot-check shows can meaningfully
  undercount pages that delegate state to child components.
- Touch-target dimensions: only 4 controls dimension-checked across both
  mobile apps (§5) — one confirmed below the 44pt/48dp floor
  (`ride-options.tsx:2191`, unidentified control), not chased further.
- Whether rider-app has its own live car-marker/tracking-animation component
  parallel to driver-app's `CarMarker.tsx`, and whether it shares the same
  reduce-motion gap (UXA11Y-004) — not checked.
- `RideOfferPanel.tsx`'s countdown-fill animation and reduce-motion — flagged
  as a plausible "essential motion" exception, not resolved either way.
- Admin-dashboard sidebar-navigation completeness (every route reachable
  from the sidebar vs. link-only) — not checked.
- Admin-dashboard keyboard-only operability (tab order, focus traps outside
  the one already-fixed document-reviewer modal per `ACTION_ITEMS.md:742-744`)
  — not swept across the other 62 pages.
- Offline-banner UX quality (persistence, explanatory copy) in either mobile
  app — only confirmed a grep hit exists, not read.
- Whether the admin `.theme-v2` dark-mode `--primary` (4.00:1, §6) is ever
  actually used as small body text in a real component, or only as
  icon/border/large-text — not traced to a specific call site.
- The full 90-screen rider/driver `accessibilityLabel` coverage — §3c's
  occurrence counts (108/30) are a volume signal, not a per-screen pass/fail
  map; a screen could have one label and still be mostly unlabelled.
- Whether `docs/PRD.md` treats any of this lane's completeness gaps (§13) as
  deliberately out of scope — not cross-checked against the PRD this pass.

<!-- SECTION: 17 -->
## 17. Human-only questions

- **Is `CarMarker.tsx`'s continuous motion "essential" under WCAG's own
  animation-from-interactions exception** (real-time vehicle position is
  arguably safety/functionally relevant, not decorative), or should it
  respect reduce-motion like its four sibling animated components do? This
  is a genuine product/accessibility judgment call, not one this lane should
  resolve unilaterally (UXA11Y-004).
- **What is Spinr's actual accessibility legal basis** — WCAG 2.1 AA as a
  self-imposed standard (per CLAUDE.md), the Saskatchewan Human Rights
  Code's accommodation duty (per R12's X16/COMP-011), or a specific
  Saskatchewan accessibility statute (`docs/ACCESSIBILITY.md` currently
  cites Ontario's AODA, very likely wrong — needs a primary-source check by
  someone who can fetch/verify Government of Saskatchewan legislation).
  This determines whether UXA11Y-005's AODA citation is a "delete it, WCAG
  is a voluntary standard" fix or a "replace with the correct SK statute"
  fix.
- **Is a shared, enforced `<Text>`/`<Button>` accessibility wrapper (§14's
  Rebuild Delta) worth prioritizing now**, given it's additive/low-risk but
  touches 76+ call sites over time — or should the fix stay scattered,
  screen-by-screen, as CLAUDE.md's "surgical changes" principle would
  otherwise default to?
- **Does product want a distinct rider-facing "search failed" vs. "no
  matches" copy** (UXA11Y-003), and if so, what should the retry affordance
  be — automatic retry, a manual "Try again" button, or just re-typing?

<!-- SECTION: 18 -->
## 18. Escalations

- **UXA11Y-001 and UXA11Y-002** both touch money-adjacent surfaces (fare
  totals, driver earnings, payment-confirm) without touching money *logic*
  — per CLAUDE.md gate 9, flagging rather than assuming the fix scope is
  purely cosmetic: a low-vision user unable to read a fare total clearly is
  a real dispute/trust risk (greenfield-extensions §8's "payment mismatch"
  dispute category), even though the underlying Decimal math is untouched.
- **UXA11Y-005**'s AODA-vs-Saskatchewan-statute question should go to
  counsel/R12 before any doc edit ships, not be resolved by this lane
  guessing the correct citation — flagged as ASSUMED per the ground rules'
  explicit instruction that an unfetched legal/regulatory claim goes to the
  legal escalation list, not presented as settled.
- **§14's Rebuild Delta (shared accessible primitives)** is a real
  architectural recommendation but depends on a prioritization call this
  lane can't make — named as a human question in §17 rather than assumed.
