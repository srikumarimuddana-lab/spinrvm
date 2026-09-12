# Ride Experience & Cost-Value Industry Benchmark — Consolidated Report

**Module E — Governance, De-duplication & Synthesis**
**Date:** 2026-09-12
**Parent prompt:** `docs/audit/RIDE_EXPERIENCE_INDUSTRY_BENCHMARK_AUDIT_PROMPT.md` §10.5
**Primary dimension:** `audit-framework/dimensions/24-industry-benchmark-cost-value.md` (first application)
**Inputs:** `module-a-rider-app.md`, `module-b-driver-app.md`, `module-c-shared.md`,
`module-d-backend-cost.md`, `cost-inventory-table.md`, `ACTION_ITEMS.md`,
`.claude/context/memory.md`
**Companion:** `ROADMAP.md` (phased, prioritized plan — the actionable half of this report)
**Report-only:** no source file outside `docs/audit/ride-experience/` was modified, except the
one required status line added to `audit-framework/dimensions/24-industry-benchmark-cost-value.md`.
No `git add`/`commit`/`push` was run by this module.

---

## 1. Executive summary

Spinr's ride experience is **closer to industry parity than the "fixes are going in circles"
framing that commissioned this audit implies** — but the circling is real, and this synthesis
found its actual mechanism. Six of eight feature areas are at or above what category-leading
ride-share apps ship. The marker/GPS-tracking pipeline, the OSRM-first live-route architecture,
the Places session-token billing discipline, the iOS Live Activity implementation, and the
receipt stack are all genuine strengths, several of them independently arriving at the same
technique Uber has published publicly.

The gaps that matter are not capability gaps. They are **three governance failures sitting on
top of otherwise-healthy features**:

1. **A known, already-fixed, rider-facing defect is sitting unapplied.** The 2026-09-11
   "car drives sideways" route-rebase fix landed in `driver-app/components/CarMarker.tsx` and
   was never ported to `shared/components/CarMarker.tsx`, which is what all five rider-app ride
   screens render. Verified directly during this synthesis (see §3.1). This is the single
   highest-priority item in the roadmap.
2. **Google Maps spend is partly invisible.** The highest-volume Directions call site in the
   app (`_shared.py`'s `_fetch_directions_route`, backing every fare estimate) has neither
   budget accounting nor caching, and six client-direct `MapViewDirections` mount sites bypass
   the backend's budget guard entirely using a bundled API key. These are two halves of one
   problem, not two problems.
3. **Nothing mechanically keeps the two `CarMarker.tsx` copies honest.** Five fixes have now
   needed manual one-way porting; three are tracked (C90), two were found only by this audit.
   That is the same shape as the `float()`-rediscovered-five-times pattern the parent prompt's
   §2.1 opens with — one bug class, many independent fix sites, no systemic guard.

**Overall current state: 3.8 / 5** (mean feature maturity across the eight in-scope areas —
six at parity, one workaround, one mixed). **Future-state target: 4.5 / 5.** The score reflects
feature maturity only; the three RED defect overlays above are tracked separately on the defect
scale, per Dimension 24's explicit instruction not to collapse the two scales into one number.

**Turn-by-turn: CONDITIONAL GO** — see §6.

---

## 2. Current state vs. future state scorecard

The eight feature areas named in the parent prompt's scope (§1 title line, and the §3.4 metric
that requires all eight rated). Maturity uses Dimension 24's 1–5 competitive-parity scale; Gap
uses GREEN/YELLOW/RED. Where a maturity-4 area carries a real defect, both are reported.

| # | Feature area | Maturity | Gap | Defect overlay | Future state — "on par with the giants" |
|---|---|---|---|---|---|
| 1 | **Map navigation & rendering** (booking backdrop, service-area overlay, follow-camera) | **4** | GREEN | — | Already there. Full-bleed map + bottom sheet, camera anchored on the rendered marker position rather than the raw GPS fix. Future state = this, held. |
| 2 | **Car marker & vehicle tracking** (Kalman -> playback buffer -> spline -> route-snap) | **4** | **RED** | HIGH: one rider-facing correctness defect unported (REC-C-04); MEDIUM: untracked fork | Technique is already category-standard. Future state = the same pipeline with *one* governed source of truth, so a fix lands on both apps at once instead of by hand. |
| 3 | **Turn-by-turn navigation** | **2** | **RED** | LOW (gap is scoped and owned — escalate the decision, not the finding) | In-app guided navigation with voice, re-route-on-deviation, and arrival detection — driver never leaves Spinr. Phase 1 of the existing proposal gets to a credible first version. |
| 4 | **Route selection & live tracking** | **4** (primary path) | **RED** | HIGH: 6 client-direct metered calls on a bundled key, bypassing the budget guard | Primary path (backend polyline + self-hosted OSRM + traveled-line erasure) is already ahead of a naive baseline. Future state = every metered call proxied and budgeted, no exceptions. |
| 5 | **Pickup / dropoff selection** | **4** | GREEN | — | Drag-pin + 50 m radius + curated venue pickup points already match Uber's/Lyft's own venue-PIN pattern. Future state = this, held. Explicitly *not* a gap: "map long-press pin drop" (see `ROADMAP.md` Phase 4 non-goals). |
| 6 | **Fare calculation** | **4** (rider-facing) / **2** (cost governance) | **RED** | HIGH: highest-volume Directions call site unbudgeted + uncached | Rider-facing fare UX (itemized breakdown, surge shown and actively acknowledged before booking, "100% to driver" framing) is at or ahead of parity. Future state = the same UX with the call site behind it metered, cached and circuit-breakered like every sibling call site already is. |
| 7 | **Receipts** | **4** | GREEN | MEDIUM: two independent PDF renderers computing the same money math | Three-surface coverage (in-app, HTML email, PDF) with GST/PST as separate lines already meets or exceeds the category. Future state = one source-of-truth renderer, or a parity test that forces the two to agree. |
| 8 | **Notifications** | **4–5** (rider) / **3** (driver) | YELLOW | Tracked as C97 — verification gap, not a design gap | Rider side (server-driven iOS Live Activity + Android ongoing notification) already matches Uber's own published architecture. Future state = driver side verified end-to-end on real hardware, and batch fan-out sent as one FCM call rather than N. |

**Governance areas** (not among the eight, but the scorecard is dishonest without them):

| Area | Maturity | Gap | Future state |
|---|---|---|---|
| **Maps cost accounting & real spend visibility** | **1** | **RED** | A self-imposed Redis ceiling is not spend visibility. Future state = GCP Billing Budgets integration reading real dollars, with this audit's cost table as its SKU inventory (`EXTENDS-E13`). |
| **Shared-component governance** (`shared/` vs per-app forks) | **2** | **RED** | `RoutePins.tsx` already shows what good looks like in this repo — one spec, one place, a comment that says so. `CarMarker.tsx` is the opposite. Future state = a mechanical parity guard, not a comment. |
| **Device / ops verification access** | **1** | **RED** | Five standing open items share one root cause: no session has a device, emulator, EAS build, or ops CLI. Future state = a named owner and a recurring device pass, or the surface stays permanently "code-verified only." |

### Score derivation (stated so it can be argued with)

Mean of the eight areas' primary maturity: (4 + 4 + 2 + 4 + 4 + 4 + 4 + 4) / 8 = **3.75 -> 3.8**.
Future-state target assumes areas 3 and 6-governance reach 4, everything else holds:
(4 + 4 + 4 + 4 + 4 + 4 + 5 + 5) / 8 = **4.25–4.5** depending on whether receipts and
notifications are counted at their differentiated level. The score is deliberately *not*
adjusted downward for defects — Dimension 24 requires the two scales be reported separately,
and collapsing them would hide that Spinr's problem here is governance, not capability.

---

## 3. Cross-module reconciliation

34 findings were filed across Modules A–D (A: 11, B: 6, C: 10, D: 7). Four pairs/groups touched
the same underlying subject from different angles. They are reconciled below into single owned
items so the roadmap does not double-count them.

### 3.1 CONS-1 — CarMarker: one component, four findings, one owner

| Source | Angle | Disposition |
|---|---|---|
| REC-C-01 | The fork itself — governance | **Authoritative.** Module C owns reconciliation. |
| REC-B-02 | The fork from the driver-app side (`onBearingChange` is load-bearing for a shipped feature) | **Merged into CONS-1** as the constraint: any reconciliation must not regress the course-up camera, and must preserve the deliberate 5 s-delayed-icon vs. immediate-camera-bearing split. |
| REC-C-02 + REC-C-05 | The course-up capability itself | **Merged.** Both modules independently reached the same conclusion — course-up is legitimately driver-only; do *not* port as default-on. Optional opt-in props only, and only if a rider consumer ever exists. No disagreement to resolve. |
| REC-A-02 | Rider-side consumption of the shared copy | **Merged** as the consumer-impact evidence (5 screens + 6 test files), and as the pointer to C90. |

**Not merged, deliberately:** REC-C-03 and REC-C-04 are *unported capabilities*, not fork
governance. They are independently actionable today and should ship before, not with, any
reconciliation decision.

**Verified during this synthesis** (the one exception to Module E reading reports rather than
code, taken because this is the roadmap's #1 item and it deserved first-hand confirmation):

- `driver-app/components/CarMarker.tsx:328-357` contains the `routeRef` rebase effect, with an
  in-code comment naming the 2026-09-11 test ride and the "car drives sideways" symptom.
- `shared/components/CarMarker.tsx:290-293` contains only `routeRef.current = routeCoordinates;`
  — no rebase, no early return, no `snapToRoute` re-anchor.
- `shared/components/CarMarker.tsx:556` calls `setAndroidRotation(...)` directly (step function);
  `driver-app/components/CarMarker.tsx:451-486,713` has the `stepAndroidRotation` /
  `animateAndroidRotationTo` rAF interpolation.

Both divergences are confirmed real. REC-C-04 is a live rider-facing defect with a known,
already-proven, verbatim-portable fix.

### 3.2 CONS-2 — Google Maps cost governance: one program, three findings

Module A explicitly asked for this consolidation ("should be scoped as one remediation project,
not two separate line items competing for priority"), and it is correct to do so — the two
findings are the client-side and server-side halves of the same exposure.

| Source | Call sites | Disposition |
|---|---|---|
| REC-D-01 | `_shared.py:100-134` -> `estimates.py:300`, `booking.py:815` (cost-table rows #1, #2) | **Program lead.** Budget-accounting half is the cheapest, highest-value fix in the entire audit. |
| REC-A-04 | 6 client-direct `MapViewDirections` mounts (`ride-options.tsx:781`, `driver-arriving.tsx:472,504`, `driver-arrived.tsx:193`, `ride-in-progress.tsx:705-742`, `driver-app/.../index.tsx:1249`) | **Merged into CONS-2 as its Phase 2 half.** Module A itself deferred authority on this to Module D; Module D never saw the client sites (out of its file scope). |
| REC-D-02 | `maps_eta.py` Distance Matrix fallback (cost-table row #3) | **Merged as the third leg** — same fix shape (register the SKU, `record_call`, `check_budget`, fall back to the existing haversine path), same mechanism, different call site. |

Sequencing matters here: R2 (budget guard on `_shared.py`) is a few lines mirroring a pattern
used four times elsewhere in the same codebase. R7 (the client-direct half) requires either a
new backend proxy endpoint or a Google Cloud Console key restriction — a different shape of
work with a different owner. Ship R2 first; it is not blocked on R7.

### 3.3 CONS-3 — Device / ops verification access: one blocker, five items

Module A asked for its two device caveats to be folded into one line; Module B went further and
asked for the *access gap itself* to be flagged as a standing item. Both are right.

| Item | Blocked on | Status |
|---|---|---|
| C70 | Android Auto DHU / real head unit | Open |
| C90 | Physical Android device or emulator | Open |
| C91 (final step) | `update-visual-baselines.yml` Actions-dispatch access | Code fix merged 2026-09-08; baseline re-capture outstanding |
| C97 #1 | Fly/Railway ops CLI (blocked on C99) | Open |
| C97 #5 | A real compiled iOS build | Open |

Plus this audit's own additions: REC-A-10's Voltra Live Activity has never been watched render
on a device and has no tracking entry at all; REC-C-03 and REC-C-04's ports will need the same
device confirmation once written.

This is not five problems. It is one access gap producing five symptoms, and it will produce a
sixth next cycle. It is also, causally, why REC-C-04 is a rider-facing defect at all: driver-app
got the fix because a human was on a real test ride on 2026-09-11; rider-app has nobody doing
the equivalent, and has zero automated visual-regression tooling to substitute.

### 3.4 CONS-4 — Notifications: two pointers, one item

REC-B-06 and REC-D-06 both resolve to `EXTENDS-C97` and both correctly decline to re-diagnose.
Counted once. REC-D-03 (FCM batching) is genuinely separate — it is about the *shape* of the
server-side send call, which C97's two research passes never examined — and stays as its own
roadmap item.

### 3.5 Non-duplicates worth stating explicitly

Two GREEN confirmations look like duplicates and are not: REC-B-03 (driver-app's GPS cadence vs.
Uber's published 4–8 s / 30–60 s numbers) and REC-C-08 (the shared utils' technique stack vs.
published Kalman/map-matching literature) rate the same pipeline from different altitudes. They
occupy **one** scorecard row (area 2) but both citations are retained, because Module B's cadence
comparison is evidence Module C did not have and vice versa.

Likewise REC-A-03 (rider), REC-B-04 (driver) and cost-table row #4 (backend) are three views of
the single OSRM-first live-route architecture -> one scorecard row (area 4, primary path).

---

## 4. De-duplication tag audit (Dimension 24's mandatory check)

Every one of the 34 findings was checked against `ACTION_ITEMS.md` and `docs/proposals/`.

**Tags verified correct:**

- **REC-C-03, REC-C-04 -> `NEW`.** Confirmed against C90's actual text, which enumerates exactly
  three ported fixes (`preferredFromIndex` continuity hint, GPS pre-smoothing/implausible-jump
  rejection, ring-change re-arm). Neither the Android rotation interpolation nor the route rebase
  is in that list. These are a fourth and fifth capability, never ported at all.
- **REC-A-04 -> `NEW`.** Independently re-grepped: every `MapViewDirections` /
  `react-native-maps-directions` / `EXPO_PUBLIC_GOOGLE_MAPS_API_KEY` hit in `ACTION_ITEMS.md`
  (lines 9683, 9755–9760, 10035–10059, 10526, 10936–10939, 11312, 11456, 15081, 15203–15204) is
  a test-coverage entry. None frames the client-direct call as a cost or security governance gap.
- **REC-D-01 -> `NEW`.** B5 verified `record_call("directions")` at `route_distance.py:734` and
  closed that check; B3 addressed call *frequency* on the location hot path. Neither reached
  `_shared.py`'s separate `_fetch_directions_route`.
- **REC-D-02 -> `EXTENDS-B3`**, **REC-D-07 -> `EXTENDS-E13`**, **REC-D-06/REC-B-06 -> `EXTENDS-C97`**,
  **REC-A-02/REC-C-01 -> `EXTENDS-C90`** — all confirmed against the live entries.

**Tag defects found and corrected here** (Dimension 24 requires an ID, not a bare marker):

| Finding | Filed as | Corrected to | Why |
|---|---|---|---|
| REC-B-02 | `EXTENDS` (no ID) | **`EXTENDS-C90`** | Aligns with REC-C-01; C90 is the nearest tracked item covering CarMarker porting. |
| REC-B-03 | `EXTENDS` (no ID) | **`EXTENDS-AUDIT-PROMPT §2.2`** | Extends a seed finding, not an ACTION_ITEMS entry — legitimate, but must say so. |
| REC-B-05 | `EXTENDS` (no ID) | **`EXTENDS-PROPOSAL-2026-09-01 §2.2/§3`** | Same. |
| REC-D-04 | `EXTENDS-<CLAUDE.md's own documented decision>` | **`EXTENDS-DECISION-2026-08-21`** (`docs/audit/2026-08-19-decision-writeups.md` §8) | Prose reference resolved to the dated decision it means. |

**Result:** 0 findings duplicate an already-open `ACTION_ITEMS` entry without a label. The §3.4
target for this metric is met.

**Standing decisions checked and respected** — `.claude/context/memory.md` (three entries: D5
in-app VoIP, A28 total-rides definitions, N14 rider email verification) contains **nothing**
bearing on map, marker, navigation, route, fare, receipt, or notification behavior. No finding in
this audit re-opens a settled question. The two settled items this audit *does* touch —
`_PRICING_ROUTE_WAIT_S`'s permanent SLA exception and the B18 attributable-retention model — were
re-confirmed as implemented, not re-litigated (REC-D-04).

---

## 5. New findings from synthesis

Four findings exist only at the synthesis layer — no single module could have produced them.

### G-1: The cost-site inventory is incomplete as shipped — `NEW`, MEDIUM

`cost-inventory-table.md` has 16 rows, **all backend**. The six client-direct `MapViewDirections`
mount sites Module A found are absent from it entirely, because Module D's file scope is
backend-only and Module A had no mandate to write into Module D's table. The parent prompt's §3.2
promises "the first complete cost-site inventory for this feature set" — as shipped, it is the
first complete *backend* cost-site inventory.

**Impact:** anyone using this table as the starting input for the GCP Billing Budgets integration
(its stated purpose, per E13) would under-scope by six call sites on a key Spinr's own Google
Cloud project bears the abuse cost for.

**Fix:** add rows 17–22 when R7 is scoped. Do not consider the inventory deliverable closed until
then. **Priority: P2**, bundled into R7.

### G-2: Nothing mechanically enforces CarMarker parity — `NEW`, HIGH (governance)

This is the root cause of the "going in circles" complaint on this specific surface, and the most
important finding in the audit that is not itself a defect.

Five fixes have now needed manual, one-way, human-noticed porting between the two `CarMarker.tsx`
copies: three tracked by C90 (ported), and two found by this audit (**not** ported — REC-C-03,
REC-C-04). Both of the unported ones came from the *same* 2026-09-11 live test-ride session whose
other outputs did get ported. The porting step is entirely dependent on a human remembering, and
on that human having looked at both files.

This is structurally identical to the `float()`-on-`NUMERIC` bug the parent prompt's §2.1 cites as
rediscovered and closed piecemeal five times across B28->B36 with no systemic fix ever applied.
Same shape: one bug class, many independent fix sites, no guard. `ACTION_ITEMS.md` C100 — filed
twice independently on the same day by two sessions both investigating `CarMarker.test.tsx` — is
further evidence this file pair already costs duplicate effort.

**Fix:** whichever reconciliation option R11 selects, it **must** include a mechanical guard — a
test that diffs the two components' capability surface and fails on undeclared divergence, or a
single parameterized component. A cross-reference header comment (REC-C-01's option (b)) is worth
doing immediately as a stopgap, but a comment is not a guard and should not close this item.
**Priority: P1 (governance).** Tag: `NEW`, related to `C90` and `C100`.

### G-3: The device/ops access gap is a standing item, not five recurring ones — `EXTENDS-C99`

See §3.3. C99 already names the ops half of the blocker (no Fly/Railway CLI, Firebase MCP cannot
authenticate). This finding extends it to the device/emulator/EAS-build half and to the pattern:
five open items, one cause, guaranteed to regenerate.

The concrete cost of leaving it open is now demonstrable rather than theoretical — REC-C-04 is a
rider-facing defect that exists *because* driver-app has live human testing and rider-app does
not, and rider-app has no visual-regression tooling to substitute. **Priority: P1 (governance).**
Not a code item: this is a resourcing/access decision for a human, and no amount of agent work
closes it.

### G-4: Tag-discipline slippage in one module — `NEW`, LOW

Three of Module B's six findings carried a bare `EXTENDS` with no ID, and one of Module D's used a
prose reference. Corrected in §4. Noted not to criticize the module — the substance was right in
every case — but because Dimension 24's whole de-duplication mechanism is the ID. A bare `EXTENDS`
is unsearchable, which means the next audit cannot check against it, which is exactly how a
finding gets rediscovered a sixth time. **Priority: P4** (already fixed here).

---

## 6. Turn-by-turn navigation — go/no-go

### Recommendation: **CONDITIONAL GO**

Greenlight Phase 1 of `docs/proposals/2026-09-01-driver-in-app-turn-by-turn-navigation.md`
(Option A), with **one architecture change to the proposal** and **two preconditions that must
land first**. Record the decision now; start the build once the preconditions are met (realistically
~2 weeks out, gated on roadmap item R2, which is a days-not-weeks fix).

This is a *go*, not a *go now*. Module B recommended NO-GO on starting build work today and GO on
the budget conversation — that sequencing is correct and is preserved here; what this synthesis
adds is the commitment that the answer at the end of that conversation should be yes, so the
decision stops sitting open. Silence since 2026-09-01 is the failure mode the audit was
commissioned to end; a conditional yes with named gates ends it, a fourth "let's think about it"
does not.

### Why go

**The cost question that blocked this decision is answered.** Module B's research (2026-09-12)
resolved the proposal's own §5 open risk: Spinr calls the **Directions API (Legacy)** at a flat
~$5.00/1,000 requests, and `steps` is **not** among the documented triggers for the pricier
Directions Advanced SKU (traffic information, >10 waypoints, waypoint optimization, and location
modifiers are). Flipping `steps=true` does not, by itself, move Spinr to a more expensive tier.
The specific fear that stalled this proposal was unfounded.

**The value is real and is not available any other way.** While a driver is in Google Maps or
Waze, Spinr cannot see route deviation, cannot do automatic arrival detection, and cannot show the
driver their earnings, ride state, or SOS control. Those are not nice-to-haves bolted onto a
navigation feature; they are capabilities that only exist if the driver stays in the app.
REC-B-05 independently verified that the existing off-route detection (`OFF_ROUTE_M = 60`, 3-fix
streak, 60 s-rate-limited toast) is genuinely the reusable hook the proposal claimed — so Phase 1
does not need to build deviation detection from scratch.

**It does not violate any "What Spinr Is NOT" guardrail.** Checked explicitly: an optional in-app
navigation aid is not control-of-work (it does not dictate routes, shifts, or penalize declining),
so it carries no driver-reclassification risk; it adds no per-trip cut, no hidden fee, no
behavioral tracking. This is a spend-to-compete lever, not a monetization change.

### Why conditional — the architecture change

**Build Phase 1 against the Routes API, not by flipping `steps=true` on Legacy Directions.**

Module B surfaced a wrinkle the 2026-09-01 proposal did not have: Google moved Directions API
(Legacy) and Distance Matrix API (Legacy) to **Legacy status as of 2025-03-01**. Legacy keeps
serving existing integrations and gets no new features; Google steers new integrations to the
**Routes API** (`computeRoutes`, with a `routes.legs.steps.navigationInstruction` FieldMask).
Building a brand-new maneuver-parsing feature on an API already in maintenance mode is technical
debt on day one — the worst possible moment to incur it.

This changes the proposal's estimate. Routes API is priced per-feature by tier (reported
Basic $5 / Advanced $10 / Preferred $15 CPM) and **traffic-aware routing *is* an Advanced-SKU
trigger there**, unlike on Legacy. So the SKU-tier question the proposal worried about is not
eliminated — it is *relocated* to the API Phase 1 should actually be built on, and must be
answered against Routes API's own tier table before a run-rate can be estimated. Budget roughly
3–5 days on top of the proposal's 2–3 week Phase 1 estimate for the Routes API integration and
its tier verification.

A third factor tightens the frame: Google retired the flat $200/month cross-SKU credit in
March 2025, replacing it with smaller per-SKU monthly allowances (~10,000 events for
Essentials-tier SKUs). Headroom for new call volume is narrower than the proposal's author would
have assumed under the old model.

### The two preconditions

1. **Land R2 (budget accounting + `check_budget()` on `_fetch_directions_route`) first.**
   Turn-by-turn would call routing helpers at materially higher frequency. Building a
   higher-call-volume feature on top of a call path that is already invisible to the daily-spend
   circuit breaker compounds a known gap rather than merely adding a new one. R2 is an S-effort
   fix mirroring a pattern already used four times in this codebase — this precondition costs
   days, not weeks.
2. **Write the re-route debounce spec as part of the Phase 1 spec, not as an afterthought.**
   Re-route call frequency during active navigation is the real cost driver — the proposal said
   so, and Module B's pricing research confirmed rather than contradicted it. It is currently
   **unbounded and undesigned**: no minimum-interval floor, no per-ride cap, no spec. Without
   those two numbers written down, Phase 1's ongoing spend is genuinely unquantifiable, and this
   audit will not endorse a build whose run-rate nobody can state.

### What would flip this to NO-GO

If the Routes API tier check finds Phase 1's required feature set lands on the Advanced or
Preferred tier ($10–15 CPM) *and* a realistic re-route debounce still produces a run-rate the
business is not willing to carry — then the honest answer is to defer, keep the deep-link
workaround (which `navStore.ts`'s per-driver app preference, added 2026-09-11, already makes
nicer), and revisit when volume or margin justifies it. That is a legitimate outcome and should be
recorded as a decision, not as continued silence.

### Verification boundary on this recommendation

Every dollar figure above comes from `WebSearch` synthesis of third-party pricing aggregators
(woosmap, storerocket, mapatlas, radar.com and others), cross-checked against each other,
**timestamped 2026-09-12**, and **not confirmed against Google's canonical pricing page** —
`developers.google.com` returned `EGRESS_BLOCKED` from this environment for every `WebFetch`
attempt, as did every aggregator page and a plain control fetch. Google Maps Platform pricing has
already changed once in the period these figures cover. **Re-verify against
`developers.google.com/maps/billing-and-pricing/pricing` before any budget is committed.** The
one internal cross-check available did pass: the reported $5.00/1,000 legacy Directions rate
matches `maps_budget.py`'s own `_PRICE_USD["directions"] = 0.005` constant exactly.

---

## 7. Findings rollup — all 34, with final tags

Priorities below are Module E's reconciled values; where they differ from the filing module's, the
reason is noted. See `ROADMAP.md` for sequencing.

| ID | Title | Maturity / Gap | Defect | Final tag | Priority | Roadmap |
|---|---|---|---|---|---|---|
| REC-C-04 | Route re-anchor missing in shared CarMarker ("car drives sideways") | — / RED | HIGH | `NEW` | **P1** | R1 |
| REC-D-01 | `_shared.py` Directions call: no budget, no cache | 2 / RED | HIGH | `NEW` | **P1** | R2, R8 |
| REC-A-04 | Client-direct `MapViewDirections` on a bundled key (6 sites) | 2 / RED | HIGH | `NEW` | **P2** | R7 |
| REC-C-03 | Android rotation is a step function in shared, interpolated in driver-app | — / RED (shared) | MEDIUM | `NEW` | **P2** | R3 |
| REC-D-02 | Distance Matrix fallback has no SKU in `maps_budget.py` | 3->1 / YELLOW-RED | HIGH (governance) | `EXTENDS-B3` | **P2** | R4 |
| REC-C-01 | CarMarker fork untracked | — / — | MEDIUM | `EXTENDS-C90` | **P2** | R6, R11 |
| REC-B-02 | Fork carries driver-only course-up capability | 4 / GREEN (capability) | MEDIUM (fork) | `EXTENDS-C90` *(corrected)* | **P2** | R11 |
| REC-B-01 | Turn-by-turn absent | 2 / RED | LOW | `EXTENDS-PROPOSAL-2026-09-01` | **P2** | R12 |
| REC-A-02 | Rider-side marker consumption unverified on device | 4 / GREEN* | — | `EXTENDS-C90` | **P2** | R14 |
| REC-D-07 | No real GCP spend visibility | 1 / RED | — | `EXTENDS-E13` | **P2** | R13 |
| REC-A-09 | Two independent receipt-PDF renderers | 3 / YELLOW | MEDIUM | `NEW` | **P3** | R9 |
| REC-D-03 | No FCM batching on batch-offer fan-out | 3 / YELLOW | LOW | `NEW` | **P3** | R10 |
| REC-B-04 | Stale 20 s cadence comment in `liveRouteShared.ts` | 4 / GREEN (arch) | LOW (doc) | `NEW` (comment) / `EXTENDS-AUDIT-PROMPT §6.1` (arch) | **P3** | R5 |
| REC-C-07 | `cachePolicy="disk"` missing on driver-app ExpoImage | — / — | LOW | `NEW` | **P4** | R5 |
| REC-C-02 | Course-up props: leave door open, don't port default-on | 4 / GREEN (driver) | — | `NEW` | **P4** | R11 |
| REC-C-05 | Parked-tick iOS retarget (corollary of C-02) | 4 / GREEN (driver) | — | `NEW` | **P4** | R11 |
| REC-C-06 | Vestigial `isOnline` prop | — / — | — | `NEW` | **P4** | bundle-only |
| REC-B-05 | Off-route detection is detection-only | 3 / YELLOW | — | `EXTENDS-PROPOSAL-2026-09-01 §2.2/§3` *(corrected)* | **P4** | R12 (input) |
| REC-B-06 | Driver push notifications | — | — | `EXTENDS-C97` | inherits C97 | CONS-4 |
| REC-D-06 | Notification delivery design at parity | 3 / YELLOW | — | `EXTENDS-C97` | inherits C97 | CONS-4 |
| REC-A-01 | Booking-screen map layout | 4 / GREEN | — | `NEW` | P4 | preserve |
| REC-A-03 | Backend-computed route polyline + traveled-erasure | 5 / GREEN | — | `NEW` | P4 | preserve |
| REC-A-05 | Pickup selection + curated venue points | 4 / GREEN | — | `NEW` | P4 | preserve |
| REC-A-06 | Destination search, session tokens, 3-stop cap | 4 / GREEN | — | `NEW` | P4 | preserve |
| REC-A-07 | Fare display + surge acknowledgment + 0% commission framing | 4 / GREEN | — | `NEW` | P4 | preserve |
| REC-A-08 | Receipt content & three delivery channels | 4 / GREEN | — | `NEW` | P4 | preserve |
| REC-A-10 | iOS Live Activity + Android ongoing notification | 5 / GREEN | — | `NEW` | P4 | preserve (+ R14 device check) |
| REC-A-11 | In-app notification center | 4 / GREEN | — | `NEW` | P4 | preserve |
| REC-B-03 | Driver GPS cadence vs. Uber's published numbers | 5 / GREEN | — | `EXTENDS-AUDIT-PROMPT §2.2` *(corrected)* | P4 | preserve |
| REC-C-08 | Marker technique stack (Kalman -> buffer -> spline -> snap) | 4 / GREEN | — | `NEW` | P4 | preserve |
| REC-C-09 | `AppMap`/`RouteLine`/`RoutePins` genuinely shared | 4 / GREEN | — | `NEW` | P4 | preserve (model for R11) |
| REC-C-10 | `usePlacesAutocomplete` shared + documented divergence | 4 / GREEN | — | `NEW` | P4 | preserve |
| REC-D-04 | `_PRICING_ROUTE_WAIT_S` 3.5 s exception re-confirmed | — / GREEN | — | `EXTENDS-DECISION-2026-08-21` *(corrected)* | n/a | preserve |
| REC-D-05 | Receipt format/delivery at parity | 4–5 / GREEN | — | `NEW` | P4 | preserve |
| **G-1** | Cost inventory missing 6 client-direct sites | — | MEDIUM | `NEW` | **P2** | R7 |
| **G-2** | No mechanical CarMarker parity guard | — | HIGH (gov) | `NEW` | **P1** | R11 |
| **G-3** | Device/ops access gap is one standing blocker | 1 / RED | — | `EXTENDS-C99` | **P1** | R14 |
| **G-4** | Tag-discipline slippage | — | LOW | `NEW` | P4 | fixed in §4 |

**19 of 38 findings (including G-1..G-4) are GREEN "no change — already at parity."** That is a
result, not filler. A "go find gaps" framing would have missed the iOS Live Activity architecture,
the session-token billing discipline, the OSRM-first live-route decision, and the marker technique
stack — four things that are working, that a future cost-cutting or simplification pass could
plausibly break, and that are now on the record as deliberate.

---

## 8. Parent prompt §3.4 measurable outcomes — actuals

| Metric | Baseline | Target | **Actual after this audit** |
|---|---|---|---|
| Paid API call sites with no budget/cache guard | >=1 known | 0 | **11 identified, 0 remediated.** 5 backend (cost-table rows #1, #2, #3, #11, #12) + 6 client-direct sites not in the table. Report-only audit; remediation is R2/R4/R7/R8. |
| Shared map/marker components with an untracked fork | >=1 known | 0 | **1 confirmed, divergence now catalogued (4 capabilities: `onBearingChange`/`mapHeadingRef`, Android rotation interpolation, route rebase, parked-tick retarget), not yet reconciled.** Tracking exists as of this report; the fork does not. |
| Feature areas with maturity + gap rating | 0 | 8 | **8 (met)** — see §2, plus 3 governance areas. |
| Open ACTION_ITEMS in this theme re-verified **end-to-end** | 0 of 4 | 4 of 4 | **0 of 4 end-to-end; 4 of 4 code-level status-confirmed.** Target **not met** — blocked by G-3's access gap. C97 is ~67% closed (4 of 6 recs shipped); C91's code fix is merged with one human step outstanding; C70 and C90 are unchanged. |
| Standing proposals with a stated go/no-go | 0 | 1 | **1 (met)** — §6, CONDITIONAL GO. |
| New findings duplicating an existing open item | unknown | 0 | **0 unlabeled duplicates.** 4 cross-module overlaps reconciled (§3); 4 tag defects corrected (§4). |

---

## 9. What this audit did NOT verify

Stated plainly, per CLAUDE.md's "What was NOT verified" discipline. Silence here would imply
coverage this audit does not have.

**No real-device or emulator testing of any kind.** No session in Modules A–E had a physical
device, simulator, EAS build, Android Auto DHU, or compiled iOS build. Every rendering, animation,
camera, notification-display, and Live Activity claim in all five reports is a **code-level read**.
This is not a footnote — it is the same blocker that lets REC-C-04 exist, and it means the two
ports this roadmap recommends (R1, R3) will themselves need device confirmation before being
called done. rider-app and driver-app have **zero** automated visual-regression tooling, so there
is no substitute.

**No live Google Maps Platform pricing was confirmed against Google's own documentation.**
`developers.google.com` and every third-party pricing aggregator returned `EGRESS_BLOCKED` from
this environment for every `WebFetch` attempt (a plain `example.com` control fetch also failed, so
this is an environment limitation, not a site-specific block). All pricing figures — $5.00/1,000
legacy Directions, $5/$10/$15 CPM Routes API tiers, the March 2025 $200-credit retirement, the
Directions Advanced SKU trigger list, the 10,000-events Essentials allowance — come from
`WebSearch`-synthesized summaries of third-party aggregators, cross-checked against each other
where multiple sources returned. **Timestamped 2026-09-12.** Google Maps Platform pricing changed
at least once within the window these figures describe. The `google-maps` MCP server was
unavailable this session (connection timeout).

**No actual production spend figures were available.** The `sentry` and `stripe` MCP servers
require OAuth authorization not completed in this session; GCP Billing Budgets integration does
not exist at all (that is E13's own open gap, and this audit's cost table is the input it needs,
not a substitute for it). Every figure in `cost-inventory-table.md` is either a code-derived
qualitative volume driver or `maps_budget.py`'s **self-declared estimate constant** — a
self-imposed ceiling, explicitly not a read of real Google Cloud billing. **No dollar amount in
this audit is a measured production spend.** The `firebase`, `redis`, `twilio` and `context7` MCP
servers also failed to connect this session.

**No production telemetry.** No ride volume, crash rate, notification delivery rate,
marker-render error rate, or real-world frequency of the REC-C-04 "car drives sideways" defect.
Its severity is argued from code and from a dated live-testing report, not measured.

**Module E read the four module reports, not the raw codebase** — by design (parent prompt §4).
Two deliberate exceptions were made and are disclosed: the REC-C-04 route-rebase divergence and
the REC-C-03 Android-rotation divergence were verified first-hand against
`shared/components/CarMarker.tsx` and `driver-app/components/CarMarker.tsx`, because REC-C-04 is
the roadmap's #1 item and warranted direct confirmation. Both were confirmed exactly as Module C
described. **Every other finding in this report rests on its filing module's evidence, not on
Module E's independent re-verification.**

**Scope boundaries inherited from the modules, restated:** GST/PST line-item correctness was not
re-checked (Dimension 08/12's job). Twilio SMS and Stripe transaction-level cost structures were
not audited (outside Module D's file scope; cost-table rows #13 and #16 are marked NOT AUDITED
rather than silently omitted). `shared/utils/fixFeed.ts` was not evaluated in depth despite both
CarMarker copies depending on it. The 23 pre-existing audit-framework dimensions were spot-checked
only where they intersect this theme (01, 05, 06, 14, 22), not re-run.

**Industry-technique claims are sourced from public writing, not from competitors' code.** Uber's
and Lyft's engineering blogs, Google's own API documentation, and general GIS/telematics
literature are what "industry technique" means throughout this audit. Where no citable source was
found — the `markerPlayback.ts` doc comment's attribution of the 5-second delay buffer to Lyft
specifically, and the Uber/Lyft/Bolt build-vs-buy framing for navigation — that is flagged in the
source modules and should be treated as informal team lore or as "unchanged since 2026-09-01,"
not as freshly confirmed.

**Nothing was implemented, tested, or fixed.** Every recommendation is a proposal for a separately
approved implementation PR. Per CLAUDE.md's pre-merge gates, each one touching a live-tested
surface (rides, dispatch, payments, safety) needs its own Change Impact & Risk Log entry before
merge — see `ROADMAP.md`, where that requirement is marked per item.

---

===MODULE-E-COMPLETE===
