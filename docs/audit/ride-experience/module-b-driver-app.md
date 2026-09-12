# Module B — Driver App Ride Experience & Industry Benchmark Audit

**Date:** 2026-09-12
**Scope:** `driver-app/app/driver/(tabs)/index.tsx`, `driver-app/components/dashboard/ActiveRidePanel.tsx`,
`driver-app/components/CarMarker.tsx` (compared against `shared/components/CarMarker.tsx`),
`driver-app/hooks/liveRouteShared.ts`, `driver-app/store/navStore.ts`.
**Parent prompt:** `docs/audit/RIDE_EXPERIENCE_INDUSTRY_BENCHMARK_AUDIT_PROMPT.md` §10.2.
**Dimension applied:** 24 (Industry Benchmark & Cost-Value), spot-checks of 01/05/06/14/22.
**Report-only:** no source file was modified to produce this report. No `git add`/`commit`/`push` was run.

---

## Turn-by-turn: go/no-go recommendation

**Recommendation: NO-GO on starting Phase 1 build work right now. GO on the budget conversation the
proposal already asked for — do that first, this week, then re-decide.**

`docs/proposals/2026-09-01-driver-in-app-turn-by-turn-navigation.md` was read in full. It has not been
actioned: `ActiveRidePanel.tsx` (verified live, ~line 373–408) still deep-links out via
`Linking.openURL`/`Linking.canOpenURL` to Google Maps/Waze/Apple Maps exactly as the proposal describes,
and every backend routing call still requests `steps=false` (confirmed at `backend/utils/route_distance.py:211`
and `:536`, `backend/utils/maps_eta.py:107` — unchanged since the proposal cited the same three lines). The
only change on this surface since 2026-09-01 is cosmetic to the gap, not a step toward closing it: a new
`driver-app/store/navStore.ts` (added 2026-09-11) lets a driver persist which external app (`default` /
`google` / `waze`) the "Navigate" button opens — a nicer version of the same deep-link workaround, not
progress toward in-app guidance. **The proposal's own recommendation stands unchanged: Option A (build
turn-by-turn on the existing Google Directions data already flowing through the app) over Option B (Mapbox
Navigation SDK) or Option C (OSRM-primary).** This audit did not redo that architecture comparison — it
was sound on 2026-09-01 and nothing in the codebase has changed the tradeoffs it weighed.

**What this audit adds — the cost question the proposal explicitly could not answer:**

The proposal's §5 flagged one open risk: "This session has no live current Google Maps Platform pricing
data to compute a real cost delta." Live research (WebSearch, 2026-09-12; Google's own pricing pages are
not directly fetchable from this environment — see "What was not verified" below, so treat the dollar
figures as sourced from third-party pricing aggregators, not Google's canonical page) found:

- Spinr's Directions calls (`backend/utils/route_distance.py:679`, `backend/routes/rides/_shared.py:134`)
  hit `https://maps.googleapis.com/maps/api/directions/json` — the **Directions API (Legacy)**, not the
  newer Routes API. Legacy Directions is priced flat, reported around **$5.00 per 1,000 requests**, with
  the only documented trigger for the pricier "Directions Advanced" SKU being **traffic information, more
  than 10 waypoints, waypoint optimization, or location modifiers** — `steps` is not in that list. **This
  means flipping `steps=true` for Phase 1 does not, by itself, move Spinr's calls to a more expensive
  pricing tier under the API it currently calls** — a real answer to the proposal's open question, not
  available to the 2026-09-01 session. The proposal's other conclusion — that **re-route call frequency
  during active navigation is the actual cost driver**, not the `steps` parameter — is confirmed correct
  by this finding, not contradicted by it.
- A second finding the proposal did not have: Google has moved Directions API (Legacy) and Distance Matrix
  API (Legacy) to Legacy status as of March 1, 2025, positioning the **Routes API** (`computeRoutes`) as
  the forward path — Legacy APIs get no new features and Google's own docs steer new integrations to
  Routes API. This doesn't force an immediate migration (Legacy Directions keeps serving current
  integrations), but it means **building Phase 1's `steps=true` parsing on top of an API Google has
  already frozen is technical debt on day one** — the FieldMask-based Routes API
  (`routes.legs.steps.navigationInstruction`) is priced per-feature (Basic $5 / Advanced $10 / Preferred
  $15 CPM, by aggregator report) and would need its own SKU-tier check (traffic-aware routing is an
  Advanced-SKU trigger there) before a re-route-heavy navigation feature could safely estimate its own
  run-rate. Worth a line in the eventual Phase 1 spec: build against Routes API directly rather than
  extending calls to an API already in maintenance mode, even though the immediate `steps` cost-tier
  concern turns out to be a non-issue on the currently-used Legacy endpoint.
- Google's flat $200/month cross-SKU credit was retired March 2025, replaced by smaller per-SKU free
  monthly allowances (10,000 events/month for Essentials-tier SKUs). This narrows, not widens, Spinr's
  cost headroom for any new call volume compared to what the proposal's author would have assumed if they
  had priced it against the older credit model.

**Net effect on the go/no-go call:** the specific fear in the proposal's §5 — "steps=true might silently
bump us to a pricier SKU" — turns out to be unfounded for the API Spinr actually calls today. That removes
one reason to hesitate. It does **not** remove the other reason: re-route-call-frequency is still an
unbounded, undesigned cost surface (no rate-limit/debounce spec exists yet — see REC-B-01 below), and
Directions/Routes cost aside, `_shared.py`'s Directions call already has no budget/cache guard at all
(Module D's flagship finding, `_shared.py:100-134` — this module did not re-verify that file, only cites
it since Phase 1 would call the very same helper more often). **Recommendation: before writing Phase 1
code, (a) get Module D's budget/cache gap on `_shared.py` fixed first — building a higher-call-volume
feature on top of an already-unmetered call site compounds a known gap rather than just adding a new one
— and (b) design the re-route debounce (e.g. a minimum N-second floor between re-route calls, and a hard
per-ride cap) as part of the Phase 1 spec, not an afterthought, exactly as the proposal itself already
said.** With those two preconditions, Phase 1 (Option A, ~2–3 weeks per the existing estimate) is
reasonable to greenlight. Without them, shipping now risks a real, currently-invisible cost the way
`_shared.py` already is invisible for the calls it makes today.

---

## ACTION_ITEMS status confirmation (task-required check, not re-diagnosed)

| ID | What it tracks | Status as of this audit | Detail |
|---|---|---|---|
| **C70** | Android Auto hardware re-validation overdue — marker/heading/heatmap fixes since the 2026-08-16 device pass are JS-verified only | **Still open.** No closure entry found anywhere after the original C70 entry (checked C71–C100 for any reference to closing it; C71 is an unrelated numbering-collision item that briefly borrowed the "C70" label informally, not a status update on the real C70). `carSurface.tsx`'s "UNPROVEN ON HARDWARE" header comment is the tracked signal and this audit did not find evidence it changed. | Needs EAS dev build + Android Auto DHU/real head unit — no session in this repo's agent integration has that access. |
| **C90** | Phone-screen (rider-app + driver-app) vehicle-icon fixes unverified on a real device | **Still open**, unchanged. Root cause (no device/simulator access in any Claude session) is unchanged; the three ported fixes (`preferredFromIndex` continuity hint, GPS pre-smoothing/jump-rejection, ring-change re-arm) remain code-verified only. | Same class of gap as C70, different surface (phone screen, not AA head unit). |
| **C91** | admin-dashboard `dashboard-monitoring` visual-regression baseline never rendered a driver marker | **Partially closed.** Code fix (seeding a fixture driver via the REST-mock path, not WS) merged 2026-09-08 and locally verified against the real Playwright suite. **One step remains and needs a human**: re-capturing the committed baseline PNG via `update-visual-baselines.yml`, which needs Actions-dispatch access no Claude session here has. Until that runs, `visual-regression-test` will show an expected (not spurious) diff on `dashboard-monitoring` for any PR that touches it. | This item is admin-dashboard-scoped, not driver-app-scoped — flagged here only because the task asked this module to confirm its status; Module B does not own any remediation on it. |
| **C97** | Driver-app push notifications reported "not visible" — two independent root causes | **Mostly closed, two items open.** Of the 6 ranked recommendations in the audit doc (`docs/audit/2026-09-10-driver-app-notification-delivery-audit.md`): #2 (loud Firebase Admin SDK init failure + Sentry tag), #3 (delivery-outcome metric `_record_push_outcome`), #4 (client-side fallback toast for unhandled foreground message types), and #6 (RNFirebase-side tap-routing for FCM-originated notification taps, `pushNotificationRouting.ts`) have all shipped and were re-verified by this file's own maintainers by reading current code rather than trusting prior status lines. **Still open:** #1 (ops check — confirm `FIREBASE_SERVICE_ACCOUNT_JSON` is actually valid on Fly and/or Railway; blocked on C99, no ops/CLI access from any Claude session) and #5 (confirm the suspected iOS `UIBackgroundModes` gap against a real compiled iOS build; no build available here). The original "not visible" report is still not resolved to a single confirmed root cause — the backend/infra path (#1) and the client path (already fixed for #4/#6) are two independent explanations, and only a human who knows which symptom they actually saw (ride offers themselves missing vs. only secondary notification types missing) or ops access to check the Firebase credential can close the loop. | See `docs/audit/2026-09-10-driver-app-notification-delivery-audit.md` for the full finding tables — this audit does not re-diagnose it, per the task's own instruction. |

**Governance read for Module E:** three of these four items (C70, C90, C91's remaining step, C97's #1/#5)
share the identical blocker — no device, simulator, EAS build, or ops/CLI access exists in any Claude
Code session in this repo's current integration. This is not a code gap; it is a **tooling-access gap**
that will keep re-appearing as "still open, needs a human" on every future audit of this surface until
someone with the missing access actually runs the checks. Module E should consider flagging the access
gap itself as a standing item, not just re-listing these four individually again next cycle.

---

## Feature-area summary (maturity 1–5, gap GREEN/YELLOW/RED)

| Feature area | Maturity | Gap | One-line why |
|---|---|---|---|
| Vehicle marker rendering (driver's own icon, driver-app consumption) | 5 | GREEN | Kalman-filtered smoothing + spline interpolation + route-snapping match published industry technique (Uber's own "Rethinking GPS" engineering post); course-up camera is a driver-app-only enhancement on top of that shared base. |
| CarMarker shared/driver-app fork | — (defect scale, not maturity) | MEDIUM (defect) | Confirmed: `onBearingChange`/course-up callback exists only in `driver-app/components/CarMarker.tsx`, absent from `shared/components/CarMarker.tsx` — an untracked-in-code (comment-only) capability drift Module C owns reconciling. |
| Turn-by-turn navigation | 2 — Workaround | RED (by Dimension 24's rule: any capability gap is RED regardless of how well-scoped the deferred decision is) | External deep-link only; a scoped, costed proposal exists and is unactioned — see go/no-go section above. |
| Live in-trip route & ETA (driver-side) | 4 — At parity | GREEN | Self-hosted OSRM, 4s/30s adaptive GPS cadence essentially matches Uber's published 4–8s (en route) / 30–60s (idle) cadence; zero incremental metered cost per poll. |
| Off-route detection | 3 — Functional | YELLOW | Detects and toasts; does not auto re-route. Correctly scoped by the codebase's own comment as "detection only" — this is the hook a re-route feature would use, not a finished feature itself. |
| Driver-facing push notifications | 3 — Functional (ride offers: 4/GREEN: confirmed correct end-to-end; secondary types: 3/YELLOW pending #1/#5) | YELLOW, pending C97 closure | See ACTION_ITEMS confirmation above — do not re-file as a new finding. |
| Driver's preferred external nav app (`navStore.ts`) | 2 — Workaround (of the underlying gap) | n/a — not a gap in itself | A real, small UX improvement to the deep-link workaround; does not change the workaround's category. |

---

## Findings (§7 template)

### REC-B-01: In-app turn-by-turn navigation — still absent, decision overdue

- **As-is decision:** `driver-app/components/dashboard/ActiveRidePanel.tsx:373-408` deep-links to Google
  Maps, Waze, or Apple Maps via `Linking.openURL`/`Linking.canOpenURL`, honoring a driver's saved
  preference from the new `driver-app/store/navStore.ts` (`default`/`google`/`waze`, AsyncStorage-persisted,
  added 2026-09-11). The driver's own Spinr screen (earnings, ride state, SOS) is not visible while
  navigating. Backend routing calls (`route_distance.py:211,536`, `maps_eta.py:107`) still request
  `steps=false`, so no maneuver data is even fetched today.
- **Industry technique:** Uber built a proprietary routing/nav engine (cost control + proprietary GPS-trace
  signal at their scale); Lyft and most mid-scale competitors integrate a third-party navigation SDK
  (historically Mapbox Navigation) rather than building from scratch — general industry framing already
  correctly captured in the existing proposal (§2.3), re-confirmed and not contradicted by this pass's
  research (2026-09-12; no rideshare-specific engineering post was found that overturns this framing —
  see "What was not verified" below).
- **Verdict:** RED (maturity 2 — Workaround) per Dimension 24's rule that a capability gap is RED
  regardless of whether it already has a scoped, costed, owned decision pending — but per the same
  dimension's severity guide, the *defect* severity for "a gap with an existing scoped proposal awaiting
  go/no-go" is **LOW**, not HIGH: the gap is known and owned, so the actionable item is the decision, not
  a fresh finding.
- **Recommendation:** See go/no-go section above — get the budget conversation and the re-route debounce
  design done, and land Module D's `_shared.py` budget/cache fix first, before starting Phase 1 (Option A)
  code.
- **Value to users:** Driver stays inside Spinr's app (sees earnings/ride state/SOS) instead of context-
  switching to a separate app for the whole drive; Spinr gains visibility into route deviation and
  automatic arrival detection tied to nav completion, neither of which is possible while the driver is in
  a different app.
- **Value to Spinr:** Competitive parity with Lyft/Bolt's driver experience; retention lever (driver UX
  friction reduction) rather than a cost-reduction lever — this is a spend-to-compete item, not a
  spend-to-save one.
- **Cost:** Engineering effort **M** (2–3 weeks Phase 1, per the existing proposal, unchanged by this
  audit). Ongoing spend: **not newly increased by `steps=true` on the currently-used Legacy Directions
  API** (confirmed above); the real ongoing spend delta is re-route call volume during active
  navigation on the same Directions SKU (~$5/1,000 calls, third-party-reported), sized by however tight
  the re-route debounce is designed — unquantifiable without a debounce spec, which does not exist yet.
- **De-dup tag:** `EXTENDS-docs/proposals/2026-09-01-driver-in-app-turn-by-turn-navigation.md` (no
  ACTION_ITEMS ID exists for this — confirmed via `rg -n "turn-by-turn" ACTION_ITEMS.md`, zero hits).
- **Priority:** P2 (strategic bet requiring an explicit decision, not a quick win — matches the parent
  prompt's Phase 3 bucket).

### REC-B-02: `driver-app/components/CarMarker.tsx` fork carries a real, driver-app-only capability (course-up camera bearing)

- **As-is decision:** `driver-app/components/CarMarker.tsx` exposes `onBearingChange`/`onAnchorChange`-style
  callbacks (confirmed at lines 153, 261, 314-317, 711) that `shared/components/CarMarker.tsx` does not
  have (confirmed via grep — zero matches for `onBearingChange`/`course-up` in the shared copy). Driver-app's
  own dashboard (`app/driver/(tabs)/index.tsx:763-899`) consumes this callback to drive its course-up follow
  camera (`camBearingRef`), and comments there (line 748-751) explicitly note the design tension: the camera
  wants a raw two-fix GPS bearing (immediate), while `CarMarker`'s own icon bearing is deliberately delayed
  (5s playback buffer) — the fork exists because the two consumers of "bearing" inside this one component
  need different latency characteristics, not because of casual drift.
- **Industry technique:** course-up (heading-up) camera orientation while driving is standard in
  Uber/Lyft/Google Maps driver-facing navigation UIs — an expected, not novel, pattern.
- **Verdict:** The camera capability itself is GREEN/maturity 4 (matches expectation). The **fork** is a
  MEDIUM defect per Dimension 24's own severity table ("a shared UI component forked between two apps with
  no tracking of the divergence") — though here the divergence *is* commented in-code (this module's own
  read found explanatory comments at both the driver-app consumer and the CarMarker file itself), so it is
  better characterized as "explained, not silent" drift rather than the worst case of that category. It is
  still untracked in any ACTION_ITEMS entry.
- **Recommendation:** Module C owns the reconciliation call (whether `onBearingChange` should be ported to
  `shared/CarMarker.tsx` so rider-app could use a course-up camera too, or whether it's legitimately
  driver-app-only since only a driver actively navigates). From the driver-app side: the callback is load-
  bearing for a real, shipped feature (course-up camera) — any reconciliation must not regress driver-app's
  camera behavior, and should preserve the 5s-delayed-icon vs. immediate-camera-bearing split the current
  comments describe, since that split is deliberate, not an oversight.
- **Value to users:** Drivers get a heading-up map orientation while driving (already shipped) — no new
  user-facing value from fixing the fork itself; the value is entirely maintainability.
- **Value to Spinr:** Reduces the risk of the two copies silently diverging further (per the seed finding,
  rider-app's copy already has comments noting some fixes were ported one-way only) — a maintenance-cost
  avoidance, not a feature win.
- **Cost:** Engineering effort **S–M** depending on Module C's chosen reconciliation approach (parameterize
  one shared component vs. keep two intentionally-different ones with a documented contract). No
  third-party spend impact.
- **De-dup tag:** `EXTENDS` — the fork itself is already named in this parent prompt's own §6.1 seed
  findings and §1.2/§3.4 metrics table; this entry adds the specific confirmed capability
  (`onBearingChange`) and the driver-app-side reasoning for why it exists, not a new discovery of the fork.
- **Priority:** P2 (moderate effort, no live-surface risk if done as an additive port rather than a
  destructive merge — matches the parent prompt's Phase 2 bucket).

### REC-B-03: Vehicle marker rendering & GPS smoothing (driver-app side) — already at industry parity

- **As-is decision:** Driver-app renders its own vehicle via the forked `CarMarker.tsx`, which shares the
  same underlying toolkit as the canonical `shared/` copy per the seed findings (Kalman-filtered GPS
  smoothing, 5s playback-delay buffer + Catmull-Rom spline, route-snapping within 35m). Location update
  cadence (`driver-app/utils/backgroundLocation.ts:349-378`) is **4,000ms/10m during a trip
  (`TRIP_CADENCE`)** and **30,000ms/50m while idle (`IDLE_CADENCE`)**.
- **Industry technique:** Uber's own published engineering post ("Rethinking GPS: Engineering Next-Gen
  Location at Uber," researched 2026-09-12) states GPS updates roughly every 4–8 seconds while driving to
  pickup and every 30–60 seconds while idle/waiting, with adaptive sampling by speed/battery/trip-state —
  the same adaptive-cadence philosophy Spinr already implements. Kalman filtering for GPS smoothing and
  dead-reckoning fusion is confirmed (2026-09-12 research) as the standard industry/ADAS technique for
  exactly this problem, not a novel or below-baseline approach.
- **Verdict:** GREEN, maturity 5. Spinr's trip-cadence number (4,000ms) sits at the tight end of Uber's own
  published 4–8s range, and its idle cadence (30,000ms) sits at the tight end of Uber's 30–60s range — this
  is a positive finding to state plainly, not bury: the marker/location toolkit is not a workaround or a
  "good enough" implementation, it matches a publicly-documented industry leader's own numbers.
- **Recommendation:** No change — already at parity. (This is exactly the "GREEN is a valid and expected
  outcome" case the template calls for.)
- **Value to users:** Smooth, accurate on-map vehicle position with no visible lag or battery-draining
  over-polling.
- **Value to Spinr:** Battery-life and bandwidth cost avoidance already achieved via adaptive cadence,
  competitive parity already achieved — no further spend needed here.
- **Cost:** None — no change recommended.
- **De-dup tag:** `EXTENDS` — the marker toolkit's sophistication is already named in the parent prompt's
  §2.2 seed findings ("Car marker rendering is already sophisticated... a strength... not a gap to fix");
  this entry adds the specific GPS-cadence-vs-Uber's-published-numbers comparison, which was not in the
  seed findings.
- **Priority:** P4 (no action needed).

### REC-B-04: Live in-trip route & ETA (driver-side OSRM polling) — at parity, zero incremental metered cost

- **As-is decision:** `driver-app/app/driver/(tabs)/index.tsx` polls `/rides/{id}/live-route` (self-hosted
  OSRM) — confirmed shortened from 20s to 6s on 2026-09-09 per an in-code comment (line ~666-670: "live-
  testing report: the route line/ETA visibly lagged the car through turns"). While OSRM is healthy, Google
  Directions (`MapViewDirections`, metered) is suppressed entirely; on OSRM failure it resumes as fallback.
  **Minor doc-drift finding (new, not in this prompt's seed findings):** `driver-app/hooks/liveRouteShared.ts`'s
  own header comment (lines 12-13) still says the phone "polls `/rides/{id}/live-route`... every 20s" — this
  is stale by the same 2026-09-09 change the consuming file's own comment documents accurately. Low-severity
  documentation-only drift; flagging rather than silently ignoring since it could mislead a future reader of
  that file specifically.
- **Industry technique:** Self-hosting a routing engine (OSRM) for high-frequency in-trip polling, reserving
  metered Directions calls for lower-frequency/fallback use, is a standard cost-control pattern once volume
  justifies the infra — this is the same tradeoff Uber cites for building its own routing stack at scale,
  applied here at a smaller, self-hosted-OSS scale appropriate to Spinr's size.
- **Verdict:** GREEN, maturity 4. This is already the "right cost decision" per this prompt's own §6.1 seed
  finding — re-confirmed, not re-litigated.
- **Recommendation:** No change to the polling architecture. Fix the one-line stale comment in
  `liveRouteShared.ts` (20s → 6s) next time that file is touched for any other reason — not worth a
  standalone PR by itself, but worth not leaving silently wrong in a file whose whole purpose is being the
  single documented source of truth for this cadence.
- **Value to users:** Route line/ETA tracks the car's actual turns in near-real-time (the exact complaint
  that motivated the 6s change) with no additional driver-facing cost.
- **Value to Spinr:** Zero incremental Google Maps spend for this call site regardless of polling frequency,
  since it's self-hosted OSRM — the 20s→6s tightening was "free" from a third-party-spend perspective by
  design.
- **Cost:** Engineering effort for the comment fix: trivial (**S**, <5 minutes). No spend impact either way.
- **De-dup tag:** `NEW` (the stale comment specifically) layered on an otherwise `EXTENDS` (the polling
  architecture itself, already covered in §6.1).
- **Priority:** P4 for the architecture (no action needed); P3 for the comment fix (low effort, no risk,
  just not urgent).

### REC-B-05: Off-route detection exists but does not auto re-route — correctly scoped, not a gap to close independently

- **As-is decision:** `driver-app/app/driver/(tabs)/index.tsx:925-949` — `OFF_ROUTE_M = 60`, a 3-consecutive-
  fix streak counter, and a rate-limited (60s) toast ("You have left the planned route"). The code's own
  comment states this is "Detection only: no auto-reroute" and is explicitly named as "client-side
  groundwork for rider-facing safety alerts" as well as the turn-by-turn re-route trigger point.
- **Industry technique:** Automatic re-route on deviation (not just a toast) is standard in every
  navigation-capable rideshare driver app once in-app turn-by-turn exists — but is meaningless without
  turn-by-turn to re-route *within*, which Spinr does not have yet (REC-B-01).
- **Verdict:** YELLOW, maturity 3 — functional as designed for its current, narrower purpose (a heads-up,
  not navigation), correctly scoped rather than a bug. Do not conflate with REC-B-01: this component is not
  itself "broken," it is deliberately partial pending the turn-by-turn decision.
- **Recommendation:** No change needed independent of the turn-by-turn decision. If/when Phase 1 (REC-B-01)
  proceeds, this is confirmed as the reusable hook the proposal already claimed it would be (§3, item 4) —
  this audit independently verified that claim against current code rather than taking the proposal's word
  for it.
- **Value to users:** Already delivers a real, if modest, safety-adjacent value today (a heads-up when
  visibly off-route) independent of any navigation feature.
- **Value to Spinr:** Zero-cost reuse path for Phase 1 — no separate off-route-detection engineering effort
  needed when that phase starts.
- **Cost:** None now. Confirms REC-B-01's cost estimate doesn't need to add a line item for building this
  detection from scratch.
- **De-dup tag:** `EXTENDS` (the proposal's own §2.2/§3 claim about this hook being reusable — re-verified,
  not re-discovered).
- **Priority:** P4 (no independent action).

### REC-B-06: Driver push notifications — see ACTION_ITEMS confirmation, not re-filed as new

- **As-is decision / Verdict / Recommendation:** See the ACTION_ITEMS status confirmation table above for
  C97's current, detailed state. Per Dimension 24's own checklist ("Any open, unresolved 'notification not
  received' defect is treated as a Dimension 10/13 finding, not re-filed here — this dimension only asks
  whether the design, not a specific bug, is at parity"): the **design** (Expo/FCM dual-path delivery,
  priority-tier bypass for dispatch/safety events, `push_retry_queue` fallback, retry/backoff) is a
  recognized industry pattern (confirmed via 2026-09-12 research: lean payloads, high-priority-only-when-
  necessary, stale-token cleanup, and monitoring delivery-vs-impression gap are the standard FCM best-
  practice checklist, and Spinr's design touches all of them at the architecture level) — GREEN on design.
  The **open defect** (C97) is a separate, already-tracked concern this module does not re-diagnose.
- **Value to users / Spinr:** Unchanged from C97's own entry — not re-stated here to avoid duplicating it.
- **Cost:** None — no new recommendation from this module; C97's existing action items already cover it.
- **De-dup tag:** `EXTENDS-C97`.
- **Priority:** Matches C97's own priority (its two open items are blocked on access, not on a design
  decision this audit could accelerate).

---

## What was NOT verified (per CLAUDE.md's discipline)

- **No live Google Maps Platform pricing was confirmed directly against Google's own documentation.**
  `developers.google.com` (and every third-party pricing-aggregator page this module attempted to fetch —
  woosmap.com, mapatlas.eu — plus a plain `example.com` control fetch) returned `EGRESS_BLOCKED` from this
  environment's network egress proxy for every `WebFetch` attempt. All pricing figures in this report
  ($5/1,000 for Directions Legacy, $5/$10/$15 CPM Basic/Advanced/Preferred for Routes API, the March 2025
  $200-credit retirement, the Directions-Advanced-SKU trigger list) come from `WebSearch`'s synthesized
  summaries of third-party aggregator sites (woosmap, storerocket, mapatlas, lazige.agency, and others),
  cross-checked against each other for consistency where more than one source was returned, but **not**
  confirmed against Google's canonical pricing page directly. Timestamp: all research in this report was
  run 2026-09-12; Google Maps Platform pricing has changed at least once already in the period covered
  (March 2025 credit-model change) and should be re-checked against `developers.google.com/maps/billing-and-pricing/pricing`
  directly before any budget commitment is finalized.
- **No real-device or emulator verification was performed by this module** — this is a report-and-research
  pass with no device/EAS/simulator access, consistent with C70/C90's own stated blockers.
- **No production spend, error-rate, or Sentry/Firebase telemetry was consulted** — `sentry` and `firebase`
  MCP servers were unavailable/unauthenticated in this session; all findings are code-derived.
- **The Uber/Lyft/Bolt "build vs. buy" framing in REC-B-01 is not independently re-verified against each
  company's own current engineering documentation** — the 2026-09-12 WebSearch pass did not surface a
  source contradicting the existing proposal's framing, but also did not surface fresh confirmation beyond
  what the proposal already stated; treat that framing as "unchanged from 2026-09-01," not "freshly
  re-confirmed."
- **This module did not open or diff `shared/components/CarMarker.tsx` and `driver-app/components/CarMarker.tsx`
  line-by-line in full** — the specific capability cited (`onBearingChange`) was confirmed via targeted
  grep, but a complete feature inventory of every divergence is Module C's job per the parent prompt's own
  scope division; this module's claim is limited to the one capability it explicitly checked.

===MODULE-B-COMPLETE===
