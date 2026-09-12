# Ride Experience — Prioritized, Phased Roadmap

**Module E — Governance, De-duplication & Synthesis**
**Date:** 2026-09-12
**Companion:** `REPORT.md` (scorecard, findings rollup, de-dup audit, verification boundaries)
**Source findings:** Modules A–D (`module-a-rider-app.md`, `module-b-driver-app.md`,
`module-c-shared.md`, `module-d-backend-cost.md`, `cost-inventory-table.md`) + four
synthesis-only findings (G-1..G-4, see `REPORT.md` §5).
**Report-only:** nothing below is implemented. Every item is a proposal for a separately
approved PR.

---

## How to read this

- **Priority** uses the `audit-framework/templates/run-audit.md` P0–P4 scale.
- **Phases** are the parent prompt's own buckets: Phase 1 = quick wins, Phase 2 = moderate
  effort, Phase 3 = strategic bets requiring an explicit decision, Phase 4 = preserve/no-action.
- **CIL required** means this item touches a live-tested surface (rides, dispatch, payments,
  auth, corporate, safety per CLAUDE.md) and **must** carry a Change Impact & Risk Log entry
  (`docs/templates/CHANGE_IMPACT_LOG.md`) in its implementation PR — not a recommendation, a gate.
- **Device pass required** means the change cannot be called done on CI alone: rider-app and
  driver-app have **zero** automated visual-regression tooling, so a visually-wrong-but-
  non-crashing regression is invisible to every check in the repo.

---

## P0 — none

**No finding in this audit is P0.** Stated explicitly rather than left blank, because "no P0" is
itself a result. Nothing found is an active outage, a rider or driver being mischarged, a safety
or dispatch failure, or a PII exposure. The worst finding (R1) is a rider-facing *visual*
correctness defect; the second (R2) is invisible *spend*, not incorrect money. Both are real and
should ship soon — neither justifies interrupting anything.

---

## Phase 1 — Quick wins

Small, well-understood changes with a known fix already written or a pattern already used
elsewhere in this codebase. Sequenced; R1 and R2 are independent and can run in parallel.

### R1 — Port the route-rebase fix to `shared/components/CarMarker.tsx` **[P1]**

**The single highest-priority item in this roadmap.** Not a research question: a live-tested-
surface defect with a known, already-proven fix sitting unapplied one file away.

- **Source:** REC-C-04 (`NEW`). Verified first-hand during synthesis (`REPORT.md` §3.1).
- **What:** `driver-app/components/CarMarker.tsx:328-357` re-bases `lastRouteSegmentIndexRef`
  onto the new polyline whenever `routeCoordinates`' identity changes, re-snapping with an
  unrestricted `snapToRoute` search. `shared/components/CarMarker.tsx:290-293` does nothing but
  store the new reference. Port the driver-app effect verbatim — it depends only on `snapToRoute`
  and `prevTargetRef`, both already present in the shared file.
- **Why it leads:** rider-app renders the shared copy on five ride screens
  (`ride-options`, `driver-arriving`, `driver-arrived`, `ride-in-progress`, `(tabs)/index`).
  Every live-route re-poll near a turn is a chance to reproduce the "car drives sideways" glitch
  that driver-app's own drivers reported on the 2026-09-11 test ride and had fixed for them the
  same day. Riders have been exposed to it ever since.
- **Effort:** **S** (~30 lines, verbatim-portable). **Spend delta:** $0 (client-side geometry).
- **Blast radius:** 5 rider-app screens + 6 rider-app test files consume the shared component;
  driver-app is unaffected (it already has the fix). Full consumer list in `module-c-shared.md`'s
  "Consumer / blast-radius reference" section — use it, do not re-derive.
- **Gates:** **CIL required** (`ride-in-progress.tsx` / `driver-arriving.tsx` are live-tested).
  **Device pass required** — and note the honest tension: this is the one Phase 1 item that is
  *not* zero-live-surface-risk. It leads anyway because the fix is already proven in production
  on the other app, which is the strongest evidence available short of a device.
- **Rollback:** revert the single effect; no data, migration, or state-machine involvement, so a
  revert is a true rollback here (unlike anything touching applied live data).
- **Also:** file this as its own `ACTION_ITEMS.md` entry. It is **not** covered by C90 — C90
  enumerates exactly three ported fixes and this is a fourth.

### R2 — Budget accounting + `check_budget()` on `_fetch_directions_route` **[P1]**

- **Source:** REC-D-01 part 1 (`NEW`), cost-table rows #1 and #2. Part of the consolidated
  CONS-2 Maps-cost-governance program (`REPORT.md` §3.2).
- **What:** add `await record_call("directions")` after every attempted call in
  `backend/routes/rides/_shared.py:100-134`, and gate the call behind `check_budget()` the way
  `maps_proxy.py`'s `_ensure_budget()` already does. On budget exhaustion, fall through to the
  haversine path that **already exists** — make the existing fallback reachable from a
  budget-exceeded state, do not write a new one.
- **Why:** this is the highest-volume Directions call site in the app (every `/rides/estimate`,
  which fires on every pin drag, plus every booking confirm lacking a valid estimate token) and
  the only one of the app's Directions call sites with neither budget nor cache. A spend spike
  here would not trip the daily-budget breaker at all while every sibling call site correctly
  stops. One code fix closes both cost-table rows #1 and #2.
- **Effort:** **S** (mirrors a pattern used four times in this codebase). **Spend delta:**
  negative — stops runaway spend that currently continues silently past the ceiling.
- **Gates:** **CIL required** (fare-estimation path — money-adjacent, live-tested). Per CLAUDE.md
  pre-merge gate 4, exercise against `mock_supabase_client` fixtures and state a concrete
  before/after scenario (e.g. "budget exhausted mid-day: before = Directions keeps being called
  and billed; after = haversine basis, `select_fare_distance` records
  `haversine_fallback`, already an accepted-risk behavior").
- **Rollback:** feature-flaggable via the existing `app_settings`-in-DB pattern if wanted, though
  the change is small enough that a revert is adequate — no live data is mutated.
- **Precondition for:** R12 (turn-by-turn). See `REPORT.md` §6.

### R3 — Port interpolated Android rotation to `shared/components/CarMarker.tsx` **[P2]**

- **Source:** REC-C-03 (`NEW`, explicitly not covered by C90).
- **What:** replace the shared file's direct `setAndroidRotation(...)` (line 556) with
  driver-app's `stepAndroidRotation` / `animateAndroidRotationTo` `requestAnimationFrame` loop
  (`driver-app/components/CarMarker.tsx:451-486,713`), which interpolates along the shortest arc
  over the same `TICK_MS` window position already animates over.
- **Why:** Android's `Marker.rotation` is a plain native prop, not an `Animated.Value`, so the
  shared copy steps hard to each new bearing every 500 ms. A turn's angular rate can exceed
  30–90° inside one tick, so the icon visibly snaps through the corner even though position stays
  smooth — the "no smooth animation" live-testing report driver-app fixed on 2026-09-09.
- **Effort:** **S** (~50 lines of already-proven logic + two call sites). **Spend delta:** $0.
- **Gates:** **CIL required.** **Device pass required — Android specifically** (this path is
  Android-only by construction; iOS uses the `Animated` path and is unaffected).
- **Sequencing note:** ship after or alongside R1, ideally in the same device-testing window so
  one device pass covers both ports.

### R4 — Register `distance_matrix` as a SKU and meter `maps_eta.py` **[P2]**

- **Source:** REC-D-02 (`EXTENDS-B3`), cost-table row #3. Third leg of CONS-2.
- **What:** add `"distance_matrix"` to `backend/utils/maps_budget.py`'s `Sku` Literal and
  `_PRICE_USD` dict (re-verify the current published rate at implementation time — pricing
  decays), then `record_call("distance_matrix")` + `check_budget()` in `get_ride_eta_seconds`
  and `batch_get_etas`. On exhaustion, exit early into the haversine estimate the function
  already computes.
- **Why:** `"distance_matrix"` is not a member of the SKU type at all today, so
  `estimate_today_usd()` structurally *cannot* total it — the breaker is blind, not merely
  unwired. B3 (2026-07-28) already fixed call *frequency* (15 s cache + >100 m movement gate);
  this closes the adjacent *cost-visibility* gap B3's scope did not reach. If OSRM degrades
  fleet-wide, every active ride's ETA refresh falls through this ungoverned path during exactly
  the ops moment the breaker exists for.
- **Effort:** **S.** **Spend delta:** none by itself — makes existing spend visible and correctly
  forces the haversine fallback on a real breach.
- **Gates:** **CIL required** (driver ETA feeds dispatch and the rider-facing arrival estimate).

### R5 — Two one-line hygiene fixes (bundle only, never standalone) **[P3/P4]**

- **Sources:** REC-B-04 (stale comment, `NEW`) and REC-C-07 (`cachePolicy`, `NEW`).
- **What:** (a) `driver-app/hooks/liveRouteShared.ts:12-13` still documents a 20 s live-route
  poll; the actual cadence has been 6 s since 2026-09-09. Fix the number. (b) Add
  `cachePolicy="disk"` to `driver-app/components/CarMarker.tsx`'s `<ExpoImage>` to match the
  shared copy and make the behavior explicit rather than dependent on an `expo-image` default.
- **Why bundle-only:** per CLAUDE.md's surgical-changes principle, neither justifies its own PR.
  Fold (a) into the next PR touching `liveRouteShared.ts` and (b) into R3 or R11, which already
  open that file. The same applies to **REC-C-06** (the vestigial `isOnline` prop): drop it and
  its one call site (`driver-app/app/driver/(tabs)/index.tsx:1196`) only when that file is already
  open for another reason — never as a standalone zero-behavior-change edit.
- **Effort:** **XS.** **Gates:** none (comment + explicit-default only; no CIL needed).

### R6 — Add tracked-fork header comments to both `CarMarker.tsx` copies **[P2]**

- **Source:** REC-C-01 option (b) (`EXTENDS-C90`).
- **What:** a header comment in each file naming the other as a tracked fork and pointing at
  `docs/audit/ride-experience/module-c-shared.md` for the capability diff.
- **Why:** C100 was filed twice independently on the same day by two sessions both investigating
  `CarMarker.test.tsx` — this file pair already demonstrably costs duplicate effort. A comment
  stops the *next* engineer having to rediscover this audit's own diff from scratch.
- **Important:** this is a **stopgap, not a closure.** A comment is not a guard. G-2 (R11) is what
  actually closes the drift risk; R6 buys time until R11 is decided.
- **Effort:** **S.** **Gates:** none (comments only).

---

## Phase 2 — Moderate effort

### R7 — Close the client-direct Directions exposure **[P2]**

- **Sources:** REC-A-04 (`NEW`) + G-1 (`NEW`). Phase 2 half of CONS-2.
- **What:** six `MapViewDirections` mount sites call Google Directions **directly from the
  device** with a bundled `EXPO_PUBLIC_GOOGLE_MAPS_API_KEY`, bypassing `maps_budget.py` entirely:
  `rider-app/app/ride-options.tsx:781`, `driver-arriving.tsx:472` and `:504`,
  `driver-arrived.tsx:193`, `ride-in-progress.tsx:705-742`, and
  `driver-app/app/driver/(tabs)/index.tsx:1249`. Two options, pick one:
  - **(a) Proxy it** — route the fallback through a backend Directions endpoint the way
    `maps_proxy.py` already does for autocomplete/details/geocode. Effort **M**. Preferred.
  - **(b) Restrict and instrument** — if client-direct must stay for latency, add Android/iOS
    application restrictions (bundle ID / package name) to the key in Google Cloud Console, and
    add a Sentry breadcrumb or analytics event so Spinr has *any* visibility into how often this
    path fires. Effort **S**. A partial mitigation, not a close.
- **Do not** remove the fallback outright — it is a legitimate resilience path when the backend
  polyline is missing.
- **Also required (G-1):** add these six call sites to `cost-inventory-table.md` as rows 17–22.
  The table is backend-only today, so anyone treating it as the complete inventory (its stated
  purpose as input to E13's GCP Billing Budgets work) would under-scope by six sites. **The
  inventory deliverable is not closed until this lands.**
- **Gates:** **CIL required** (four rider ride screens + the driver dashboard). Option (b)'s key
  restriction is a Google Cloud Console change, not a code change — it needs a human with console
  access and should be scheduled accordingly.
- **Rollback:** for (a), keep the client fallback behind a flag during rollout so a proxy outage
  degrades to today's behavior rather than to no route line at all.

### R8 — Fare-estimate Directions cache, fine-precision keys **[P3]**

- **Source:** REC-D-01 part 2 (`NEW`). Ship only after R2.
- **What:** add a Redis cache to `_fetch_directions_route`. **Do not copy
  `route_distance.py`'s cache key scheme verbatim.** That cache rounds *origin* to a ~110 m grid
  deliberately, because it serves a moving driver's imprecise position. The fare-estimate call
  site has two *fixed, rider-chosen* endpoints that directly determine the bill — a 110 m origin
  grid would let two riders with pins ~100 m apart be billed on the same cached road distance.
  Key at **5–6 decimals (~1–10 m) on both endpoints**, TTL **30 s**.
- **Why it does not weaken the fare lock:** the estimate-token mechanism
  (`sign_estimate_token` / `resolve_booking_distance`) already pins the exact quoted distance for
  the token-present booking path. Caching affects only consistency across repeated estimate calls
  *before* a token is issued — never quote-to-charge consistency.
- **Effort:** **S–M.** **Spend delta:** negative (fewer redundant calls during a booking session).
- **Gates:** **CIL required** (money path). Dry-run against `mock_supabase_client` per pre-merge
  gate 4, with an explicit before/after scenario for the pin-drag repeat-quote case.

### R9 — Reconcile the two receipt-PDF renderers **[P3]**

- **Source:** REC-A-09 (`NEW`).
- **What:** `rider-app/app/ride-details.tsx:40-129`'s `buildReceiptHtml()` re-derives fare lines,
  tax lines and the grand total in plain JS (`parseFloat`/`toFixed`), entirely independently of
  `backend/utils/receipt_pdf.py::generate_receipt_pdf`, which builds the emailed PDF using the
  backend's Decimal helpers. Two languages, two rounding paths, one document with a 7-year
  CRA/SK retention requirement. Two options:
  - **(a)** Have the "Download Invoice" action fetch the server-rendered receipt instead of
    re-deriving it client-side. Effort **M**; removes the `expo-print` dependency.
  - **(b)** Keep client-side generation but add a regression test rendering both paths from one
    fixture `ride` and asserting line-items and total match. Effort **S–M**.
- **Why now, not later:** both renderers currently agree because both read the same settled
  `ride.grand_total`/`fare_breakdown`/`tax_breakdown` fields, so there is no user-visible symptom
  today. The risk is the *next* time either is touched in isolation — `receipt_pdf.py`'s tax/surge
  line items have already been fixed once with no mechanism to reach the client copy. Fix it
  before the next receipt-format change, so that change isn't made twice.
- **Gates:** **CIL required** (payments-adjacent, regulatory document).

### R10 — Batch FCM sends in the batch-offer dispatch loop **[P3]**

- **Source:** REC-D-03 (`NEW`; explicitly not a duplicate of C97, which is about client-side
  display/routing of already-delivered messages).
- **What:** `backend/routes/rides/matching.py:1467-1476` spawns one independent
  `send_push_notification()` per candidate driver — N separate FCM round-trips per batch offer.
  Group the tokens gathered in that loop and use Firebase Admin SDK's `send_each` (up to 500 per
  call), which returns per-message results that line up naturally with `_record_push_outcome`'s
  existing per-outcome metric.
- **Scope it narrowly:** only the batch-dispatch call site. The other ~13
  `send_push_notification` call sites (`chat.py`, `safety.py`, `lifecycle.py`, `cancellation.py`,
  `lost_found.py`, `booking.py`) each send to one recipient and have nothing to batch.
- **Why P3 and not higher:** FCM and Expo are free at Spinr's message volume, so this is a
  throughput/architecture item, not a cost one, and at Saskatchewan-market candidate-pool sizes
  the latency difference is unlikely to be perceptible. The value is proactive — documenting and
  fixing it before a scale-driven complaint, not after.
- **Effort:** **S–M.** **Gates:** **CIL required** (dispatch is live-tested; a batching bug here
  means an offer not delivered, which is a match-rate KPI event).

### R11 — Decide and implement CarMarker fork reconciliation **[P2, with a P1 governance rider]**

- **Sources:** REC-C-01 + REC-B-02 + REC-C-02 + REC-C-05 (CONS-1), and **G-2** (`NEW`, the
  governance rider).
- **Decide between:**
  - **(a) Tracked fork with a contract** — two files stay, each documenting the other, plus a
    **mechanical parity guard**: a test that diffs the two components' capability surface and
    fails on undeclared divergence. Effort **S–M**. **Recommended now.**
  - **(b) Single parameterized component** with an optional `courseUp` capability object.
    Effort **L**. The real fix, but a larger refactor than this audit recommends attempting while
    five rider-app screens depend on the shared copy simultaneously.
- **Constraints either way (from REC-B-02 and REC-C-02, which agree):**
  - Do **not** port `onBearingChange`/`mapHeadingRef` into shared as default-on behavior —
    rider-app has no course-up camera and north-up is the correct rider-side convention (Uber and
    Lyft rider apps do not rotate the map to the driver's heading). Optional opt-in props only,
    mirroring how `ring` is already optional. The math primitive
    (`visualRotationDegrees`, `shared/utils/vehicleTracking.ts:323-325`) already lives in shared.
  - Preserve the deliberate 5 s-delayed-icon vs. immediate-camera-bearing split the driver-app
    comments describe. That split is a fix, not an oversight.
  - REC-C-05 (parked-tick iOS retarget) ports with REC-C-02's props or not at all.
- **The G-2 rider is the point:** whichever option is chosen, **a header comment does not close
  this item.** Five fixes have now needed manual one-way porting (three tracked by C90, two found
  by this audit and still unported). Without a mechanical guard, there will be a sixth. This is
  the same shape as the `float()`-on-`NUMERIC` bug closed piecemeal five times across B28->B36
  with no systemic fix — the pattern this entire audit was commissioned to break.
- **Blast radius:** use `module-c-shared.md`'s consumer list. 5 rider-app screens + 6 test files
  on the shared side; 1 driver-app screen + the Android Auto surface + 1 test file on the fork
  side. `admin-dashboard` has no consumer of either.
- **Gates:** **CIL required** for any behavior-affecting variant. **Device pass required.**

---

## Phase 3 — Strategic bets (explicit decision required)

### R12 — Turn-by-turn navigation Phase 1: **CONDITIONAL GO** **[P2]**

- **Source:** REC-B-01 (`EXTENDS-PROPOSAL-2026-09-01`), REC-B-05 (reusable off-route hook).
- **Full reasoning:** `REPORT.md` §6. Summary:
  - **GO** on Phase 1 / Option A of
    `docs/proposals/2026-09-01-driver-in-app-turn-by-turn-navigation.md`.
  - **Architecture change to the proposal:** build against the **Routes API** (`computeRoutes`,
    `routes.legs.steps.navigationInstruction` FieldMask), **not** by flipping `steps=true` on the
    Legacy Directions endpoint. Legacy went to maintenance status 2025-03-01 and gets no new
    features; building new maneuver parsing on it is day-one technical debt. Add ~3–5 days to the
    proposal's 2–3 week Phase 1 estimate, and verify Routes API's own SKU tier (traffic-aware
    routing is an Advanced-SKU trigger there, unlike on Legacy).
  - **Precondition 1:** R2 lands first. Do not build a higher-call-volume feature on a call path
    that is invisible to the budget breaker.
  - **Precondition 2:** the re-route debounce (minimum interval floor + hard per-ride cap) is
    written into the Phase 1 spec before code starts. It is currently unbounded and undesigned,
    and it — not `steps` — is the real cost driver.
  - **What flips it to NO-GO:** Routes API tier check lands Phase 1 on Advanced/Preferred
    ($10–15 CPM) *and* a realistic debounce still yields a run-rate the business won't carry.
    Defer explicitly in that case; do not go quiet again.
  - **Resolved:** the proposal's own open cost question. `steps` is not a Directions-Advanced SKU
    trigger on the Legacy API Spinr calls today (~$5.00/1,000 flat). That specific fear was
    unfounded.
  - **Guardrail check:** an optional in-app nav aid is not control-of-work, adds no per-trip cut
    and no behavioral tracking — no "What Spinr Is NOT" conflict, no reclassification risk.
- **Gates:** **CIL required** for every implementation PR (dispatch + driver live surface).
  Re-verify all pricing against `developers.google.com/maps/billing-and-pricing/pricing` before
  committing a budget — this audit could not reach it (`EGRESS_BLOCKED`).

### R13 — GCP Billing Budgets integration for real Maps/Firebase spend **[P2]**

- **Source:** REC-D-07 (`EXTENDS-E13`).
- **What:** `maps_budget.py`'s ceiling is a **self-imposed Redis estimate**, not a read of actual
  Google Cloud billing — its own module docstring says so. `.github/workflows/billing-usage-monitor.yml`
  covers Stripe and Twilio balances and deliberately does not cover Google Maps or Firebase,
  because that needs a GCP service account with the `billing.budgets` scope plus a configured
  budget object. E13 left this as a named open gap rather than silently assuming coverage.
- **Strategic because** it is gated on a human creating cloud credentials, not on engineering time.
- **This audit's contribution:** `cost-inventory-table.md` is the SKU-level inventory that
  integration needs as its starting input — **once G-1's six client-direct rows are added (R7)**.
  Do not start R13 from the table in its current, backend-only state.
- **Priority note:** inherits E13's own; not re-scored here.

### R14 — Close the device / ops verification access gap **[P1 governance]**

- **Source:** G-3 (`EXTENDS-C99`), consolidating REC-A-02, REC-A-10's Voltra caveat, Module B's
  C70/C90/C91/C97 status table, and REC-C-03/C-04's own port-verification needs.
- **What:** one access gap produces five standing open items — C70 (Android Auto DHU), C90
  (physical Android device/emulator), C91's final baseline re-capture
  (`update-visual-baselines.yml` Actions-dispatch access), C97 #1 (Fly/Railway ops CLI, itself
  blocked on C99) and C97 #5 (a real compiled iOS build). No Claude session in this repo's
  integration has any of them.
- **Why it is P1 despite not being a code change:** it is causally why R1 exists. driver-app got
  the "car drives sideways" fix because a human was on a real test ride; rider-app has nobody
  doing the equivalent and no visual-regression tooling to substitute. Leave this open and the
  same class of defect recurs, and every future audit of this surface re-lists the same five
  items as "still open, needs a human."
- **Recommended action:** file it as **one** standing `ACTION_ITEMS.md` entry with a named owner
  and a recurring device-pass cadence, rather than re-listing five symptoms. Also add a tracking
  entry for REC-A-10's iOS Live Activity, which has never been watched render on a device and
  currently has no entry at all.
- **Effort:** not an engineering estimate — a resourcing decision.

---

## Phase 4 — Preserve, and explicit non-goals

### Preserve (GREEN — do not let a future cost-cutting or "simplification" pass regress these)

| What | Why it is on this list |
|---|---|
| **OSRM-first live route/ETA** (driver 6 s, rider 20 s polling; Google Directions only as fallback) | Zero incremental metered cost by design. Module A named this as exactly the thing a naive cost-cutting pass would "simplify" toward a metered Directions call. REC-A-03, REC-B-04, cost-table row #4. |
| **Places session-token lifecycle** (`usePlacesAutocomplete`, rotated after each Details call) | Matches Google's own billing requirement precisely. Reusing or omitting tokens bills every keystroke separately. REC-A-06, REC-C-10. This is the "what good looks like" reference for R2/R4. |
| **iOS Live Activity + Android ongoing notification, server-driven** | Independently arrived at the same architecture Uber published, including the reasoning for why Android gets no native equivalent, and a deliberate privacy choice (`dropoffArea: null` — exact address never on the lock screen). REC-A-10. |
| **`_PRICING_ROUTE_WAIT_S` 3.5 s worst case** | A permanent, documented SLA exception decided 2026-08-21. Confirmed still implemented as described. **Do not re-litigate.** REC-D-04. |
| **`RoutePins.tsx` / `RouteLine.tsx` / `AppMap.tsx` sharing discipline** | Genuinely undiverged, with an explicit in-code instruction to change the spec in one place. This is the internal model R11 should aim at. REC-C-09. |
| **Marker technique stack** (physics rejection -> Kalman -> playback buffer -> Catmull-Rom spline -> route-snap with continuity hinting) | Category-standard toolkit, with a refinement (continuity hinting near intersections) most public writeups don't describe. REC-B-03, REC-C-08. |
| **Curated venue pickup points** (`GET /maps/pickup-points`) | Functionally the same as Uber's/Lyft's airport/venue PIN systems, solving the same driver-reachability problem. REC-A-05. |
| **Surge transparency** (badge on every card + active acknowledgment sheet before booking) and the **"100% to driver / 0% commission"** framing | Meets the disclosure requirement and reinforces Spinr's stated differentiation at the exact moment a rider compares prices. REC-A-07. |
| **Three-surface receipts** with GST/PST as separate line items | At or above category baseline, and satisfies the Saskatchewan tax-disclosure rule. REC-A-08, REC-D-05. |

### Explicit non-goals — do not add these

- **Map long-press pin drop.** The parent prompt's §6.1 seed finding notes its absence. Module A
  established this is not a gap: the fixed-center-pin-drag pattern in `confirm-pickup.tsx` and
  `pick-on-map.tsx` is the same interaction Uber's own "set location on map" uses. Adding
  long-press would duplicate an already-equivalent interaction.
- **Course-up camera in rider-app as default behavior.** North-up is correct for a rider watching
  an overview map; the rider is not navigating. See R11's constraints.
- **Removing the client-side `MapViewDirections` fallback outright.** It is a legitimate
  resilience path. R7 governs it; it does not delete it.
- **Raising `SURGE_CAP` above 2.5x**, adding a "service fee," or adding any per-trip cut.
  Confirmed: no finding in this audit recommends anything touching commission structure, the
  surge cap, or ad-style tracking. CLAUDE.md "What Spinr Is NOT" guardrails hold throughout.
- **Server-side map-matching against the full OSRM road graph** (the theoretical maturity-5 step
  beyond current route-snapping). Module C named it as a speculative future enhancement and
  explicitly out of scope. Do not treat it as a gap to close.

---

## Dependency summary

```
R2 (budget guard)  ──────────────► R8 (cache)
        └────────────────────────► R12 (turn-by-turn, precondition 1)

R1 (route rebase) ──┐
R3 (Android rot.)  ─┼─► one device-pass window (R14 unblocks this)
                    │
R6 (fork comments) ─┴─► R11 (fork reconciliation + G-2 parity guard)

R7 (client-direct) ─────► adds rows 17-22 to cost-inventory-table.md (G-1)
                              └────► R13 (GCP Billing Budgets, needs complete inventory)

R14 (access gap) ───► unblocks C70, C90, C91-final, C97 #1/#5, and verification of R1/R3
```

**If only three things get done:** R1 (a live defect with the fix already written), R2 (the
cheapest real cost-governance win available), and R14 (the access gap that is causally upstream
of the defect class R1 belongs to).

---

===MODULE-E-ROADMAP-COMPLETE===
