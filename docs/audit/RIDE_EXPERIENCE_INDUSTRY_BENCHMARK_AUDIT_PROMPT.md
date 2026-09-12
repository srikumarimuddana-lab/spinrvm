# Ride Experience & Cost-Value Industry Benchmark — Modular Audit Prompt

**Target:** Map navigation, car marker, turn-by-turn, route selection, pickup/dropoff
(start/end point) selection, fare calculation, receipts, and notifications — across
`rider-app/`, `driver-app/`, `shared/`, and the `backend/` routes/services that drive them.
**Sibling document:** `docs/audit/ADMIN_DASHBOARD_AUDIT_PROMPT.md` (same structure, admin
surface). This document follows the same 8-section shape deliberately, for consistency.
**Framework alignment:** `audit-framework/ground-rules.md`, `audit-framework/modules/rider-app.md`,
`audit-framework/modules/driver-app.md`, `audit-framework/dimensions/*` — **plus the new**
`audit-framework/dimensions/24-industry-benchmark-cost-value.md` created alongside this
prompt, because none of the existing 23 dimensions ask "are we at industry parity, and
is our third-party API spend buying the right thing" — they check internal correctness,
not competitive/economic positioning.
**Authoring context:** requested 2026-09-12 specifically because prior fix cycles on this
surface have been circling rather than resolving (see §2). Pre-audit research for this
prompt (three parallel repo-search passes) is baked into §6 below so the executing
session does not re-spend tokens re-discovering it.

---

## Table of Contents

1. [Scope](#1-scope)
2. [Rationale — why this audit, why now](#2-rationale)
3. [Benefits](#3-benefits)
4. [Methodology — modular, parallel by design](#4-methodology)
5. [Dimensions applied](#5-dimensions)
6. [Known seed findings from pre-audit research (2026-09-12)](#6-seed-findings)
7. [Recommendation template — the exact format every finding must use](#7-recommendation-template)
8. [Deliverables, severity/maturity rubric, reference commands](#8-deliverables)
9. [Model & execution plan (token optimization)](#9-model-plan)
10. [Ready-to-run prompts — one per module](#10-ready-to-run-prompts)
11. [Pre-flight checklist & verification boundaries](#11-pre-flight)
12. [Maintenance](#12-maintenance)

---

## 1. Scope

### 1.1 In-scope surfaces

| Module | Path | Why in scope |
|---|---|---|
| **A — Rider App** | `rider-app/app/{ride-options,confirm-pickup,search-destination,driver-arriving,driver-arrived,ride-in-progress}.tsx` and related hooks/stores | Every rider-facing map/fare/receipt/notification touchpoint |
| **B — Driver App** | `driver-app/app/driver/(tabs)/index.tsx`, `driver-app/components/dashboard/ActiveRidePanel.tsx`, `driver-app/components/CarMarker.tsx`, `driver-app/hooks/liveRouteShared.ts`, `driver-app/store/navStore.ts` | Navigation-adjacent driver UX; this app carries the forked marker code and the turn-by-turn gap |
| **C — Shared Library** | `shared/components/{AppMap,CarMarker,RouteLine,RoutePins}.tsx`, `shared/utils/{markerPlayback,gpsSmoothing,vehicleTracking}.ts`, `shared/hooks/usePlacesAutocomplete.ts` | The canonical implementation both apps are supposed to consume; divergence here is a maintenance and quality-drift risk in its own right |
| **D — Backend + Cost Governance** | `backend/routes/rides/{estimates,_shared,booking}.py`, `backend/services/fare_service.py`, `backend/utils/{route_distance,maps_budget,maps_eta}.py`, `backend/routes/maps_proxy.py`, `backend/routes/rides/receipts.py`, `backend/utils/{email_receipt,receipt_pdf}.py`, `backend/features.py` (notification dispatch), `backend/routes/rides/matching.py` / `lifecycle.py` (notification trigger points) | Where money, external API cost, and delivery-reliability decisions actually live |
| **E — Governance & Synthesis** | No new files — reads A–D's output plus `ACTION_ITEMS.md`, `docs/proposals/`, `.claude/context/memory.md` | Prevents this audit from becoming the next entry in the "going in circles" list |

### 1.2 Out of scope (explicitly)

- The 23 existing generic dimensions in full — this audit **spot-checks** dimensions 01
  (feature completeness), 05 (UI/UX), 06 (real-time), 14 (performance), and 22
  (third-party risk) only where they intersect this theme; it does not re-run a full
  rider-app/driver-app production-readiness pass (that already happened — see
  `audit-framework/modules/rider-app.md` v1, `driver-app.md` v4).
- Admin dashboard's own map/monitoring surfaces — covered by
  `docs/audit/ADMIN_DASHBOARD_AUDIT_PROMPT.md` §1.1 row F.
- Load/chaos testing, real-device QA (Android Auto/CarPlay), and any actual fix
  implementation — this is a **report-and-recommend** exercise; see `ground-rules.md`
  "Do not silently fix."
- Vendor posture (Google's, Stripe's, Twilio's own security) — only our integration
  boundary and spend.

---

## 2. Rationale

### 2.1 Why this specific theme, why now

The user's own framing: *"the fixes have been going in circles and nothing is getting
settled."* That is not a vague complaint — pre-audit research for this prompt found
direct evidence of it in `ACTION_ITEMS.md`:

- **C100** was filed twice, independently, by two different sessions on the same day
  (2026-09-10), both investigating the same `CarMarker.test.tsx` suite — duplicate audit
  effort on car-marker code specifically.
- The same float()-on-NUMERIC-column money bug was independently rediscovered and
  closed **piecemeal five times** across **B28 → B29 → B30 → B36** (closed) and **B35**
  (still open) over roughly 10 days, in five different files, with no systemic fix
  (lint rule / helper) ever applied — each fix addressed one instance, not the pattern.
- **B6** was closed by citing a decision made through an unrelated channel rather than
  completing its own stated re-tuning process.
- **C70, C90, C91** (marker/heading/animation fixes) and **C97** (driver-app push
  notifications "not visible") are all currently **open**, each already fixed once at
  the code level but never verified end-to-end on real hardware or against a real
  visual baseline — the exact shape of a fix that "goes in circles" because the
  verification loop was never actually closed.

None of Spinr's 21 `spinr-*` review agents, the `/full-audit` skill, or the existing
`audit-framework`'s 23 dimensions perform industry-technique research or cost-benefit
analysis (confirmed by reading their definitions as part of this prompt's own
preparation — see §6.4). That gap, not a missing internal-correctness check, is why
this specific angle has never been asked and answered before.

### 2.2 What "on par with ride-share giants" concretely means here

Not a subjective aesthetic judgment. For each feature area this audit names: (a) the
specific technique category leaders use (sourced via live research, not memory — see
§9), (b) whether Spinr's current implementation is a workaround, a functional-but-basic
version, or already at/above that bar, and (c) what closing any real gap would cost in
both engineering effort and ongoing third-party API spend. Two things already found
during pre-audit research cut in *opposite* directions, and both should stay in the
final report exactly as nuanced:

- **Car marker rendering is already sophisticated** — a Kalman-filtered GPS smoother,
  a 5-second playback-delay buffer, Catmull-Rom spline interpolation, dead-reckoning on
  gaps, and route-snapping are conceptually the same toolkit large ride-share apps use.
  This is *not* a gap to "fix" — it's a strength the audit should document as such, not
  bury under unrelated findings (the divergence between the shared and driver-app forks
  of this same component *is* a real finding — see §6.1).
- **Turn-by-turn navigation is a genuine, already-scoped gap** with a real cost driver
  (re-route call frequency) — and a proposal already exists
  (`docs/proposals/2026-09-01-driver-in-app-turn-by-turn-navigation.md`) recommending a
  specific approach (build on existing Google Directions data, not a new SDK, phased
  rollout). This audit's job on that topic is to **surface it for a decision**, not
  redo the analysis from scratch.

---

## 3. Benefits

### 3.1 Product & user value

- Rider/driver-facing gaps get named with a concrete "what riders/drivers would notice"
  statement, not just an engineering description — directly answers the user's ask for
  "how it would add value to users."
- Surfaces the one standing decision (turn-by-turn) that has been sitting unactioned
  since 2026-09-01, so it either ships or is explicitly deferred with a reason — either
  outcome is progress; silence is the failure mode being targeted.

### 3.2 Cost value

- Produces the first complete cost-site inventory for this feature set: every paid
  Google Maps/Twilio/FCM call, whether it's metered, cached, and circuit-breakered.
  Pre-audit research already found one concrete, fixable gap worth leading with: the
  Directions call backing **every** `/rides/estimate` and booking confirm
  (`backend/routes/rides/_shared.py:100-134`) has **no** budget accounting and **no**
  caching, unlike the other Maps call sites in `maps_proxy.py` and the AI booking tool,
  which are both budgeted and (for the route-distance fallback) cached. This is the
  highest-volume call site of all of them and the one place spend is currently
  invisible.
- Directly extends the already-documented gap that Google Maps/Firebase real spend
  tracking has no equivalent to the Stripe/Twilio balance monitor in
  `billing-usage-monitor.yml` (`ACTION_ITEMS.md`) — this audit's cost table is the input
  a future GCP Billing Budgets integration would need.

### 3.3 Governance value (the "stop going in circles" part)

- Every finding must be tagged `EXTENDS-<ACTION_ITEMS ID>` or `NEW` (Dimension 24's
  de-duplication checklist) — this is the mechanism that stops a sixth independent
  rediscovery of the same class of bug the float()-arithmetic case already showed.
- Produces one prioritized roadmap instead of another scattered set of findings that
  compete with each other for attention across separate PRs.

### 3.4 Concrete, measurable outcomes

| Metric | Baseline (to be filled in by audit) | Target |
|---|---|---|
| Paid API call sites with no budget/cache guard | ≥1 known (`_shared.py:100-134`) | 0 |
| Shared map/marker components with an untracked fork | ≥1 known (`CarMarker.tsx`) | 0 (either reconciled or the divergence is filed and owned) |
| Feature areas with an explicit maturity (1–5) + gap (GREEN/YELLOW/RED) rating | 0 today | 8 (all in scope §1.1) |
| Open ACTION_ITEMS items in this theme re-verified end-to-end (not just code-level) | 0 of C70/C90/C91/C97 | 4 of 4, each closed or explicitly re-scoped |
| Standing proposals (turn-by-turn) with a stated go/no-go | 0 (silent since 2026-09-01) | 1 |
| New findings that duplicate an existing open item | Unknown — this audit's own discipline is the test | 0 |

---

## 4. Methodology

This audit is **modular and parallel by design**, unlike the admin-dashboard audit's
strictly sequential 8-phase gate. Modules A–D are independent research-and-report
passes that touch **only their own output file** (no shared-file writes), so they can
run concurrently with zero merge conflict — this directly satisfies the user's own
requirement to pick up parallel work "without conflicts and overlaps avoiding any
merge issues." Module E is the one sequential dependency: it must run after A–D
produce their reports, because it synthesizes them.

```
   ┌─ Module A: rider-app   ─┐
   ├─ Module B: driver-app  ─┤
   ├─ Module C: shared/     ─┼──►  Module E: governance + synthesis + scoring
   └─ Module D: backend+cost─┘        (Opus 5 — reads summaries, not raw code)
        (all four: Sonnet 5, run in parallel)
```

Each module:
1. Reads its seed findings from §6 (do not re-derive what's already found).
2. Reads the relevant existing docs (proposal, ACTION_ITEMS entries, domain context)
   named in its ready-to-run prompt (§10) — extend, don't duplicate.
3. Does live industry research (WebSearch/WebFetch) for its feature areas — see §9 for
   why this needs external research, not internal memory.
4. Files every finding in the §7 template, tagged per Dimension 24's de-dup rule.
5. Writes its own report file under `docs/audit/ride-experience/<module>.md` — commit
   after each module, same discipline as the admin audit's phase-gate commits.

Module E then reads the four module reports (not the raw codebase) and produces the
single consolidated roadmap — current-state score, future-state target, prioritized
fix list, cost table, and an explicit go/no-go recommendation on the turn-by-turn
proposal.

---

## 5. Dimensions

| # | Dimension | Applied how |
|---|---|---|
| 24 | **Industry Benchmark & Cost-Value** (new — `audit-framework/dimensions/24-industry-benchmark-cost-value.md`) | Primary dimension for this entire audit — run in full for every module |
| 01 | Feature completeness | Spot-check only: does each in-scope screen/flow actually work end-to-end today (not a new full sweep) |
| 05 | UI/UX | Spot-check: map/marker/route rendering visual quality, load into `spinr-rider-driver-design-system` skill context before judging anything visual |
| 06 | Real-time | Spot-check: live-route/ETA polling cadence and WS event coverage for the flows in scope |
| 14 | Performance | Spot-check: marker/route rendering perf, `_PRICING_ROUTE_WAIT_S` tradeoff (already an accepted, documented exception — do not re-flag, see CLAUDE.md Performance SLAs) |
| 22 | Third-party risk | Spot-check: Google Maps/Twilio/Firebase dependency posture specifically for the call sites this audit inventories |

Every other dimension (02, 03, 04, 07–13, 15–21, 23) is **not** in scope for this
audit — those already have dedicated coverage via `rider-app.md`/`driver-app.md`'s full
audits or the relevant `spinr-*` reviewer agents, and re-running them here would
duplicate existing work rather than add anything.

---

## 6. Known Seed Findings From Pre-Audit Research (2026-09-12)

Bake these in — do not re-spend tokens rediscovering them. Verify with a quick
file:line check if a module wants extra confidence, but treat these as established.

### 6.1 Map, marker, route, pickup/dropoff (feeds Modules A/B/C)

- **Car marker**: `shared/components/CarMarker.tsx` is the canonical, shared
  implementation (Kalman-filtered smoothing via `shared/utils/gpsSmoothing.ts`,
  5-second playback-delay buffer + Catmull-Rom spline via
  `shared/utils/markerPlayback.ts`, route-snapping within 35m via
  `vehicleTracking.ts:snapToRoute`, shortest-arc bearing tween). **`driver-app/components/CarMarker.tsx`
  is a diverged fork** (1096 vs 937 lines) adding course-up-camera bearing/heading
  callbacks not present in the shared copy — rider-app's copy has comments noting some
  fixes were ported one-way only. This is a maintainability/drift finding, not a
  capability gap.
- **Turn-by-turn**: genuinely absent — `driver-app/components/dashboard/ActiveRidePanel.tsx`
  deep-links to Google Maps/Waze/Apple Maps via `Linking.openURL`; backend requests
  `steps=false` on every Directions/OSRM call (no maneuver data even fetched). A scoped
  proposal already exists: `docs/proposals/2026-09-01-driver-in-app-turn-by-turn-navigation.md`
  recommends Option A (build on existing Google Directions data, phased, ~2-3 weeks
  Phase 1) over a new SDK (Option B, Mapbox Navigation) or OSRM-primary (Option C) — with
  an explicit open cost question (re-route call frequency billing) never answered
  because no live Google Maps Platform pricing was available in that session either.
- **Route selection/drawing**: `shared/components/RouteLine.tsx` / `RoutePins.tsx` are
  the shared, undiverged renderers. Initial route preview prefers the backend-computed
  polyline (from the fare-estimate call); **`react-native-maps-directions`
  (`MapViewDirections`) is a client-side fallback that calls Google Directions directly
  from the device using `EXPO_PUBLIC_GOOGLE_MAPS_API_KEY`** — call sites:
  `rider-app/app/ride-options.tsx:781`, `driver-arriving.tsx:472,504`,
  `driver-arrived.tsx:193`, `driver-app/app/driver/(tabs)/index.tsx:1249`. This bypasses
  the backend's budget/rate-limit guard entirely and exposes a bundled API key
  client-side — both a cost and a security finding.
- **Live in-trip route/ETA**: proxied through self-hosted OSRM via
  `/rides/{id}/live-route` (driver-app polls every 6s, rider-app every 20s) — **not**
  metered Google Directions/Distance Matrix, which is the right cost decision already
  made; rider-app additionally re-derives ETA via haversine on every GPS update as a
  zero-cost interim value.
- **Pickup/dropoff selection**: `rider-app/app/confirm-pickup.tsx` (drag-map-under-pin,
  50m radius enforcement, curated venue pickup points via backend) and
  `rider-app/app/search-destination.tsx` (Places Autocomplete, session-token billing,
  saved Home/Work/Favourites). No map long-press point selection exists.

### 6.2 Fare, receipts, notifications, cost sites (feeds Module D)

- **The uncached, unbudgeted Directions call**: `backend/routes/rides/_shared.py:100-134`
  (`_fetch_directions_route`) backs **every** `/rides/estimate` call and every booking
  confirm (`booking.py:815`) — the highest-volume Maps call site in the whole app — and
  has **no** `maps_budget.py` accounting and **no** caching, unlike
  `routes/maps_proxy.py` (autocomplete/details/geocode — budgeted) and
  `utils/route_distance.py`'s Google fallback path (budgeted **and** Redis-cached on a
  ~110m grid, 30s TTL). This is the single clearest, most fixable cost-governance gap
  found — lead the Module D report with it.
- **Fare calc's 3.5s worst-case wait** (`_PRICING_ROUTE_WAIT_S`) is an **already-decided,
  documented, permanent exception** (CLAUDE.md Performance SLAs, decided 2026-08-21 per
  `docs/audit/2026-08-19-decision-writeups.md` §8) — do not re-litigate; only re-confirm
  it's still implemented as described.
- **Receipts**: already reasonably complete — in-app JSON, HTML email, and a PDF
  attachment, GST/PST as separate line items per the Saskatchewan tax rule, triggered
  via a transactional outbox on ride completion with a direct-send fallback. Module D's
  job here is industry-parity polish (format/delivery expectations), not re-checking
  line-item correctness (that's Dimension 08/12's job, already covered).
- **Notifications**: single dispatcher (`features.py::send_push_notification`) — Expo
  REST or FCM, one message per call, no batching; priority tiers bypass opt-out for
  dispatch/safety events with a `push_retry_queue` fallback. **`ACTION_ITEMS.md` C97
  (open)**: driver-app push notifications reported "not visible," two independent root
  causes found, neither fixed yet — Module D should check current status rather than
  re-diagnose from zero.
- **Cost circuit breaker**: `backend/utils/maps_budget.py` implements a Redis-backed
  daily budget ceiling (default $5/day) with fail-open on Redis outage — this exists
  and works for the call sites that use it; the gap is the call sites that don't (see
  above), not the mechanism itself.
- **Documented org-level gap**: `ACTION_ITEMS.md` already flags that
  `.github/workflows/billing-usage-monitor.yml` covers Stripe/Twilio balance but has no
  equivalent for actual Google Maps/Firebase dollar spend (GCP Billing Budgets API not
  yet integrated) — Module D's cost table is the input that integration would need.

### 6.3 Governance evidence (feeds Module E)

See §2.1 — C100 duplicate filing, B28→B36 five-times-rediscovered float bug, B6 closed
via side-channel, C70/C90/C91/C97 open-and-unverified. Module E must check every new
finding against these and the full open-item list in `ACTION_ITEMS.md` before it's
allowed to appear as `NEW` in the consolidated roadmap.

### 6.4 What already exists that this audit must not duplicate

- `/full-audit` fans out all 21 `spinr-*` agents for internal-rule conformance —
  none of them do industry-technique research or cost-benefit analysis.
- `/spinr-swarm ux-ideate <surface>` is the closest existing analog (three parallel
  agents: interaction-vocabulary/motion-craft, friction-as-raw-material,
  cross-surface precedent) but scores against an internal 10-dimension rubric, not
  external ride-share-leader benchmarking, and produces proposals in
  `docs/ux-ideas.md`, not a costed, prioritized roadmap.
- `docs/audit/2026-08-18-full-fleet-whole-app-audit.md` and
  `2026-08-15-full-fleet-launch-readiness.md` are broad, all-agent sweeps — neither
  targets map/nav/marker/fare/receipts/notifications specifically or benchmarks
  against competitors.
- `docs/audit/2026-09-10-driver-app-notification-delivery-audit.md` already covers the
  notification pipeline narrowly — Module D should read it before re-auditing
  notifications, and only add the industry-benchmark angle it doesn't cover.

---

## 7. Recommendation Template

Every finding in every module report **must** use this exact structure — it operationalizes
the user's own request verbatim (as-is decision → industry technique → why ours is/isn't
good → value → cost → priority):

```markdown
### REC-<module>-<NN>: <short title>

- **As-is decision:** What Spinr does today, with file:line evidence.
- **Industry technique:** What category-leading ride-share apps do for this, sourced
  from live research (cite what you found and when — pricing/technique claims decay).
- **Verdict:** GREEN / YELLOW / RED (Dimension 24 gap scale) + maturity 1–5, with one
  sentence on *why* — including "why existing is good" if it is; this template is not
  a bug list, a GREEN finding is a valid and expected outcome.
- **Recommendation:** The specific change, or "no change — already at parity."
- **Value to users:** What a rider/driver would concretely notice.
- **Value to Spinr:** Cost reduction, retention, competitive positioning, or
  regulatory/liability angle.
- **Cost:** Engineering effort (S/M/L) + ongoing third-party spend delta, if any —
  name the specific API/SKU, not just "Google Maps."
- **De-dup tag:** `EXTENDS-<ACTION_ITEMS ID>` or `NEW` (mandatory, per Dimension 24).
- **Priority:** P0–P4, using the same scale `audit-framework/templates/run-audit.md`
  already defines.
```

---

## 8. Deliverables

### 8.1 Directory layout

```
docs/audit/ride-experience/
├── module-a-rider-app.md
├── module-b-driver-app.md
├── module-c-shared.md
├── module-d-backend-cost.md
├── cost-inventory-table.md          # produced by Module D, referenced by Module E
├── REPORT.md                        # Module E — consolidated findings + scores
└── ROADMAP.md                       # Module E — prioritized, phased fix plan
```

### 8.2 Severity/maturity rubric

Reuse `audit-framework/templates/run-audit.md`'s CRITICAL/HIGH/MEDIUM/LOW/PASS/
RECOMMENDATION for defects, and Dimension 24's Maturity (1–5) + Gap (GREEN/YELLOW/RED)
for competitive positioning. Report both where both apply — see Dimension 24's note
that a maturity-4 feature can still carry a real defect finding.

### 8.3 Reference commands already run during pre-audit research (do not re-run blind)

```bash
# Already confirmed during this prompt's preparation — re-run only to double-check a
# specific line, not to re-discover the whole picture:
rg -n "MapViewDirections|react-native-maps-directions" rider-app/ driver-app/
rg -n "_fetch_directions_route|maps_budget" backend/routes/rides/_shared.py backend/utils/
rg -n "steps=false|steps=true" backend/utils/route_distance.py backend/utils/maps_eta.py
rg -n "CarMarker" shared/components/ driver-app/components/ rider-app/
```

```bash
# Not yet run — for the executing session:
rg -n "C70|C90|C91|C97|C100|B28|B29|B30|B35|B36|B6\b" ACTION_ITEMS.md
```

---

## 9. Model & Execution Plan (Token Optimization)

| Phase | Recommended model | Why |
|---|---|---|
| Modules A, B, C, D (parallel) | **Sonnet 5** | This session's own pre-audit research (3 parallel Sonnet-tier agents, ~125K tokens each, 28–49 tool calls) proved this tier finds exact file:line evidence in a codebase this size (CarMarker fork, the unbudgeted Directions call) without excessive cost. Each module also needs live external research (WebSearch/WebFetch) synthesized against code — a judgment task Sonnet handles well; Haiku risks shallow industry comparisons on a genuinely intricate codebase (`CarMarker.tsx` alone runs Kalman filtering + spline interpolation), and the accuracy cost of getting a money/cost finding wrong outweighs the savings. |
| Module E (synthesis + scoring + roadmap) | **Opus 5** | This is the one pass where reasoning quality matters most and context is smallest — it reads four already-compressed module reports (not raw code), so Opus's higher per-token cost is confined to a small input. Prioritizing fixes against cost, regulatory constraints ("What Spinr Is NOT" guardrails), and the turn-by-turn go/no-go call benefits from the strongest available reasoning; this is exactly the kind of cross-cutting judgment call not worth risking on a lighter model. |
| Narrow mechanical re-checks (optional) | **Haiku 4.5** | Only if a module wants a cheap confirmation grep before writing a finding — e.g. re-verifying an ACTION_ITEMS ID is still open. Not for any module's primary research. |
| Fable 5.1 | **Not recommended** | Tuned for creative/narrative work; no fit for technical/financial audit reasoning. If a plain-English stakeholder narrative of Module E's findings is wanted afterward, that's a legitimate (optional) use — but it is not part of this audit. |

**Estimated token budget:** ~150–250K tokens per module (A–D) × 4 = 600K–1M tokens,
plus ~50–150K for Module E's synthesis pass (small input, higher per-token cost). Total
ballpark 0.7–1.2M tokens for the full exercise — run A–D in parallel (wall-clock, not
token, savings) via the `Agent` tool; escalate to the `Workflow` tool's pipeline pattern
only if multi-agent orchestration is explicitly requested, since that tool requires
separate opt-in.

---

## 10. Ready-to-Run Prompts

Each block below is self-contained — paste into a separate `Agent` call (or a separate
Claude Code session) to run that module. **A, B, C, D can run simultaneously — they
write to different files and only read code.** Run E only after A–D's reports exist.

### 10.1 Module A — Rider App

```
===== BEGIN MODULE A PROMPT =====

ROLE
You are a senior mobile engineer + product analyst auditing Spinr's rider-app map,
fare, receipt, and notification experience against ride-share industry leaders
(Uber, Lyft, Bolt, Ola, Grab).

CONTEXT TO READ FIRST
1. docs/audit/RIDE_EXPERIENCE_INDUSTRY_BENCHMARK_AUDIT_PROMPT.md (this file) — full
   context, especially §6.1/§6.2 seed findings and §7 recommendation template
2. audit-framework/ground-rules.md
3. audit-framework/dimensions/24-industry-benchmark-cost-value.md
4. audit-framework/modules/rider-app.md (existing audit status — don't re-run its
   full 23-dimension sweep)
5. .claude/context/domain-payments.md (fare/receipt rules)
6. shared/rider-driver-design-system skill (load via Skill tool) before judging
   anything visual

SCOPE
rider-app/app/{ride-options,confirm-pickup,search-destination,driver-arriving,
driver-arrived,ride-in-progress}.tsx and the hooks/stores they use. You are reading
the rider-side consumption of shared/ components — do not re-audit shared/ itself
(Module C owns that); note anything you see there only if it changes your rider-app
finding.

TASK
For each of: map rendering, car marker, route preview/live tracking, pickup
selection, destination selection, fare display, receipt experience, and
notifications — as experienced from the rider side:
1. State the as-is decision with file:line evidence.
2. Research (WebSearch/WebFetch) what category-leading ride-share apps do for the
   equivalent rider-facing moment. Cite sources and note the research date.
3. Fill in the full §7 Recommendation Template for each area, including a GREEN
   verdict where warranted — this is not a bug hunt, a "no change" finding is a
   valid and expected outcome.
4. Before filing anything as NEW, grep ACTION_ITEMS.md for related open/closed
   items and tag EXTENDS-<ID> or NEW per Dimension 24.

RULES (non-negotiable, from CLAUDE.md)
- Report only — do not modify any source file. This is an audit, not a fix.
- No PII in your report (no raw GPS, no full names/phones/emails — see CLAUDE.md's
  PIPEDA logging rules, which apply to report artifacts too).
- Do not re-litigate the already-decided 3.5s fare-estimate wait
  (`_PRICING_ROUTE_WAIT_S`) — it's a permanent, documented exception.
- Respect "What Spinr Is NOT" (CLAUDE.md) — never recommend a commission-taking
  change, unbounded surge, or ad-SDK-style tracking as a "cost win."

OUTPUT
Write docs/audit/ride-experience/module-a-rider-app.md using the §7 template for
every finding, plus a short summary table (feature area → maturity 1-5 → gap
GREEN/YELLOW/RED) at the top. End with `===MODULE-A-COMPLETE===` on its own line.

===== END MODULE A PROMPT =====
```

### 10.2 Module B — Driver App

```
===== BEGIN MODULE B PROMPT =====

ROLE
Same role as Module A, applied to driver-app — this is where the turn-by-turn gap
and the CarMarker fork live, so treat this module as the highest-signal one.

CONTEXT TO READ FIRST
1. docs/audit/RIDE_EXPERIENCE_INDUSTRY_BENCHMARK_AUDIT_PROMPT.md — especially §6.1's
   turn-by-turn and CarMarker-fork seed findings
2. docs/proposals/2026-09-01-driver-in-app-turn-by-turn-navigation.md — READ THIS
   FULLY before writing anything about turn-by-turn. It already contains the
   industry-technique research, a 3-option architecture comparison, a phased
   rollout plan, and named cost risks. Your job is to check its status (has Phase 1
   started? has anyone decided?), re-confirm or update its cost section with fresh
   research if you can, and surface it for a go/no-go decision — NOT to redo this
   analysis from scratch.
3. audit-framework/ground-rules.md
4. audit-framework/dimensions/24-industry-benchmark-cost-value.md
5. audit-framework/modules/driver-app.md
6. .claude/context/domain-dispatch.md

SCOPE
driver-app/app/driver/(tabs)/index.tsx, driver-app/components/dashboard/ActiveRidePanel.tsx,
driver-app/components/CarMarker.tsx (the forked copy — compare explicitly against
shared/components/CarMarker.tsx, but let Module C own the reconciliation
recommendation; your job is to document what's different and why it matters from
the driver-app side), driver-app/hooks/liveRouteShared.ts, driver-app/store/navStore.ts.

TASK
1. Confirm current status of ACTION_ITEMS.md C70, C90, C91 (marker/heading fixes
   unverified on real hardware) and C97 (push notifications "not visible") — do not
   re-diagnose from zero, extend what's already known.
2. For turn-by-turn: read the existing proposal in full, state whether it's been
   actioned, and if not, produce the go/no-go decision brief Module E needs —
   including a fresh cost check via WebSearch on current Google Maps
   Platform/Routes API pricing if you can find it (the existing proposal explicitly
   could not).
3. For every other driver-facing area in scope (map rendering, marker, route/live
   tracking, notifications), follow the same process as Module A: as-is → industry
   technique → verdict → §7 template.

RULES
Same as Module A's RULES section.

OUTPUT
Write docs/audit/ride-experience/module-b-driver-app.md. Include an explicit
"Turn-by-turn: go/no-go recommendation" section near the top, separate from the
per-feature findings table. End with `===MODULE-B-COMPLETE===`.

===== END MODULE B PROMPT =====
```

### 10.3 Module C — Shared Library

```
===== BEGIN MODULE C PROMPT =====

ROLE
You are a staff mobile engineer auditing shared/ — the component layer both
rider-app and driver-app are supposed to consume without divergence.

CONTEXT TO READ FIRST
1. docs/audit/RIDE_EXPERIENCE_INDUSTRY_BENCHMARK_AUDIT_PROMPT.md — §6.1
2. audit-framework/dimensions/24-industry-benchmark-cost-value.md
3. docs/design/rider-driver-app-design-system.md

SCOPE
shared/components/{AppMap,CarMarker,RouteLine,RoutePins}.tsx,
shared/utils/{markerPlayback,gpsSmoothing,vehicleTracking}.ts,
shared/hooks/usePlacesAutocomplete.ts.

TASK
1. Diff shared/components/CarMarker.tsx against driver-app/components/CarMarker.tsx
   line-by-line at a feature level (not a literal diff dump) — list every capability
   the driver-app fork has that shared/ lacks (course-up-camera bearing/heading
   callbacks are already known; confirm and find any others).
2. For each divergent capability: is it driver-app-specific by nature (course-up
   camera only makes sense while driving), or should it be ported back to shared/
   so rider-app could use it too? Make a specific recommendation per item, not a
   blanket "reconcile everything."
3. Research (WebSearch) how the underlying techniques already in use here (Kalman
   filtering for GPS smoothing, playback-delay buffering, Catmull-Rom interpolation,
   route-snapping) compare to publicly known approaches used for real-time vehicle
   tracking in other rideshare/logistics apps — this is a case where the finding
   may legitimately be GREEN/maturity-4-or-5; say so plainly if the research
   supports it.
4. Fill in §7 template for every finding, including the fork itself as one finding
   (Dimension 24's MEDIUM-severity example).

RULES
Same non-negotiable rules as Module A. Additionally: do not propose deleting either
copy of CarMarker.tsx without explicitly naming the consumer-impact check (which
screens in each app import which copy) — this is exactly the kind of blast-radius
check CLAUDE.md's pre-merge gates require before any consolidation PR, even though
this audit itself only recommends, it doesn't implement.

OUTPUT
Write docs/audit/ride-experience/module-c-shared.md. End with
`===MODULE-C-COMPLETE===`.

===== END MODULE C PROMPT =====
```

### 10.4 Module D — Backend + Cost Governance

```
===== BEGIN MODULE D PROMPT =====

ROLE
You are a backend engineer + FinOps analyst auditing Spinr's fare-calculation,
receipt, notification, and third-party-API-cost surfaces.

CONTEXT TO READ FIRST
1. docs/audit/RIDE_EXPERIENCE_INDUSTRY_BENCHMARK_AUDIT_PROMPT.md — §6.2 (read this
   closely — it names the flagship finding you're extending, not rediscovering)
2. audit-framework/dimensions/24-industry-benchmark-cost-value.md
3. .claude/context/domain-payments.md
4. docs/audit/2026-09-10-driver-app-notification-delivery-audit.md (existing
   notification-pipeline audit — extend, don't duplicate)
5. CLAUDE.md's Performance SLAs section (the `_PRICING_ROUTE_WAIT_S` exception is
   already decided — do not re-litigate it)

SCOPE
backend/routes/rides/{estimates,_shared,booking}.py, backend/services/fare_service.py,
backend/utils/{route_distance,maps_budget,maps_eta}.py, backend/routes/maps_proxy.py,
backend/routes/rides/receipts.py, backend/utils/{email_receipt,receipt_pdf}.py,
backend/features.py (send_push_notification), backend/routes/rides/{matching,lifecycle}.py
(notification trigger points), backend/ai/tools_booking.py (Maps usage only).

TASK
1. Build the complete cost-site table from Dimension 24's "Cost Accounting &
   Governance" checklist: every paid external call (Google Maps Directions/Places/
   Geocoding/Distance Matrix/Roads, Twilio SMS, FCM/Expo push, Stripe) → file:line →
   volume driver → metered? → cached? → circuit-breaker present? Confirm the known
   gap at `_shared.py:100-134` with fresh evidence and quantify its volume (every
   /rides/estimate call + every booking confirm — pull a rough daily-ride-volume
   estimate from any metric/dashboard reference in the repo if one exists, otherwise
   state clearly that volume is unquantified without production telemetry access).
2. Research (WebSearch) how ride-share-scale apps typically cache/dedupe route-cost
   lookups (geohash-bucketed caching, pre-computed zone matrices, TTL strategies) —
   compare against what `route_distance.py`'s existing 110m-grid/30s-TTL cache
   already does for its own call site, and recommend whether the same pattern
   should extend to `_shared.py`'s uncached call.
3. Check ACTION_ITEMS.md C97 (driver-app push "not visible") current status before
   writing any notification finding — extend, don't re-diagnose.
4. For receipts: industry-parity check only (format/delivery expectations) — do not
   re-verify GST/PST line-item correctness, that's already covered elsewhere.
5. Fill in §7 template for every finding.

RULES
Same non-negotiable rules as Module A, plus: never propose Decimal-arithmetic
changes without confirming `_d()/_round()/_f()` usage is preserved; never propose
caching a Directions call in a way that could let a stale/undercharging distance
reach the fare math (the anti-undercharge design intent must survive any caching
recommendation).

OUTPUT
Write docs/audit/ride-experience/module-d-backend-cost.md AND
docs/audit/ride-experience/cost-inventory-table.md (the standalone cost table, for
Module E to consume directly without re-parsing prose). End with
`===MODULE-D-COMPLETE===`.

===== END MODULE D PROMPT =====
```

### 10.5 Module E — Governance, De-duplication & Synthesis (run after A–D)

```
===== BEGIN MODULE E PROMPT =====

ROLE
You are a principal engineer producing the final, consolidated ride-experience
industry-benchmark report and roadmap for Spinr's leadership. Your audience needs a
clear go-live-readiness picture, not a restatement of four separate reports.

CONTEXT TO READ FIRST
1. docs/audit/RIDE_EXPERIENCE_INDUSTRY_BENCHMARK_AUDIT_PROMPT.md — full document
2. docs/audit/ride-experience/module-{a-rider-app,b-driver-app,c-shared,d-backend-cost}.md
   and cost-inventory-table.md — the four module reports, already written
3. ACTION_ITEMS.md — full open-item list, to check every module's de-dup tags
4. .claude/context/memory.md — check for any standing decision this synthesis
   should respect rather than re-open

TASK
1. Verify every finding across all four modules is correctly tagged
   EXTENDS-<ID>/NEW per Dimension 24 — reject/merge any that duplicate each other
   across modules (e.g. both Module A and Module B may have touched the shared
   CarMarker fork; reconcile into one finding, don't double-count).
2. Produce ONE current-state-vs-future-state scorecard: every feature area from §1.1,
   its maturity (1-5), gap rating (GREEN/YELLOW/RED), and one line on what "future
   state" (on par with ride-share giants) looks like.
3. Produce ONE prioritized roadmap (P0-P4, reusing the existing severity scale),
   grouped into phases: Phase 1 = quick wins (low effort, no live-surface risk,
   e.g. adding budget/cache guard to the `_shared.py` Directions call), Phase 2 =
   moderate effort (e.g. CarMarker reconciliation), Phase 3 = strategic bets
   requiring an explicit decision (e.g. turn-by-turn Phase 1 go/no-go). Every item
   touching a live-tested surface (rides/dispatch/payments/safety per CLAUDE.md)
   must note that it needs a Change Impact & Risk Log entry before any
   implementation PR, not just a recommendation.
4. Give an explicit, single go/no-go recommendation on the turn-by-turn proposal
   using Module B's brief, with reasoning tied to cost + user value + effort.
5. State plainly what this audit did NOT verify (per CLAUDE.md's "What was NOT
   verified" discipline): no real-device testing, no live Google Maps Platform
   pricing data confirmed (WebSearch results only, timestamp them), no actual
   production spend figures (Sentry/Stripe/GCP Billing MCP access was not available
   during this audit — name that boundary explicitly rather than implying full
   cost coverage).

RULES
Same non-negotiable rules as the other modules. Do not implement anything — this
report is the input to a separate, explicit decision by the user on what to build
next.

OUTPUT
Write docs/audit/ride-experience/REPORT.md (scorecard + findings rollup) and
docs/audit/ride-experience/ROADMAP.md (phased, prioritized plan). Update
audit-framework/dimensions/24-industry-benchmark-cost-value.md's status line (add
one, "Last applied: YYYY-MM-DD, see docs/audit/ride-experience/REPORT.md") so
future audits know this dimension has been exercised at least once. End with
`===MODULE-E-COMPLETE===`.

===== END MODULE E PROMPT =====
```

---

## 11. Pre-Flight Checklist & Verification Boundaries

Before executing, confirm (or explicitly accept as a stated boundary in the final
report if not resolved):

- [ ] `WebSearch`/`WebFetch` tools are loaded (deferred tools in this environment —
      load via `ToolSearch` if not already available) — required for every module's
      industry-technique research; without them, "industry technique" sections
      degrade to unverifiable memory claims, which Dimension 24 treats as
      low-confidence and the report must say so.
- [ ] If real Google Maps Platform pricing is wanted (not just technique
      comparison), note that the `google-maps` MCP server failed to connect in this
      session (timeout) — either retry the connection or rely on WebSearch of
      Google's public pricing pages, timestamped, same caveat the existing
      turn-by-turn proposal already carries.
- [ ] If real production spend/error-rate data is wanted for the cost table, note
      that `sentry` and `stripe` MCP servers require user authorization (OAuth) not
      yet completed in this session — the cost inventory can and should proceed on
      code-derived call-site analysis regardless; just don't claim live dollar
      figures without them.
- [ ] This is a report-only exercise per `ground-rules.md` — no source file outside
      `docs/audit/ride-experience/` and the new dimension file should be modified by
      Modules A–E. Any fix implementation is a separate, explicitly approved
      follow-up.
- [ ] Branch: create `audit/ride-experience-industry-benchmark-2026-09-12` (or the
      execution date) before running any module, per the existing audit-framework
      convention in `run-audit.md` §1.

---

## 12. Maintenance

- Re-run when: the turn-by-turn proposal's status changes, a new Directions/Places/
  Roads call site is added anywhere in `rider-app/`, `driver-app/`, or `backend/`, or
  `CarMarker.tsx` changes in either `shared/` or `driver-app/`.
- If Dimension 24 proves useful beyond this one audit, fold its checklist into
  `audit-framework/modules/rider-app.md` and `driver-app.md`'s "Applicable Dimensions"
  tables (currently list 23; would become 24) so future full audits pick it up
  automatically.
