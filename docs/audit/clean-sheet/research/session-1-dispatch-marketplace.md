# Research Session 1 — Dispatch & marketplace

*Clean-sheet rebuild audit, research phase. Written 2026-09-25. Report only: no code,
config or data was changed. No PII. No raw coordinates. Fix-oriented wording only.*

## §0 Method, inputs, deviations and limits

**Brief.** `docs/audit/clean-sheet-prompt/research-sessions.md` §1 (shared method, card
format, rules) and §2 (Session 1: scope, research questions, guardrails). Guardrails
applied to every card: drivers are contractors, so nothing here proposes a penalty for
being offline; any algorithm change needs a replay test on Spinr's own past trips before
rollout, stated per card.

**Deviations from §1 (by coordinator instruction, matching Session 3's precedent):**
- **One file, not four.** §1 asks for `current-state.md`, `techniques-radar.md`,
  `recommendations.md` and `DEFERRED.md` in `research/dispatch-marketplace/`. The
  coordinator asked for exactly one file at this path, following the pattern already set
  by `research/session-3-trust-safety-fraud.md`. The four sections map as: §1 = current
  state, §2 = techniques radar, §3 = ranked build order (recommendations), §6 = deferred.
- **No lane agents spawned.** Lane C (current state) was run directly by this session,
  applying `spinr-dispatch-reviewer`'s charter, then re-reading with
  `spinr-realtime-reliability-reviewer`'s rules per the session brief. Lane A (adversary)
  is folded into each technique card's "how it fails or gets gamed" field rather than run
  as a separate pass, again following Session 3's structure. Lane R (external research)
  was run directly with WebSearch.
- **No commit, no push, no PR** (coordinator instruction; §1's minute 50–60 step is
  skipped). "REPORT-ONLY" per the task: no code, config or data changes.

**Inputs read, cited, not re-audited:** `02-findings/dispatch.md` (DISPATCH-001..004,
full scenario tables §3.1–3.3, write-site table §4, Rebuild Delta §6), `driver-journey.md`
(DRIVER-004 full card, DRIVER-005/006), `reliability.md` (loop registry, REL-001/-003),
`03-benchmark.md` §6 (fairness benchmark, the Pandey/Caliskan Chicago study), `04-blueprint.md`
(Epic: Ride Booking & Matching, Epic: Ride Fulfillment, Epic: Maps & Routing, H1/H7
hypothesis tests), `08-hostile-review.md` (corrections to DISPATCH-001/DRIVER-004's cards,
the DISPATCH-001 blast-radius correction, the "why now" ranking of the event-log delta),
`ROADMAP.md` (N11, N13, N17, N22, X3, X13, X15, X21, the DISP lane sequencing note), `matrices/edge-case-matrix.md`
Flow 2 (dispatch/offer) and Flow 7 (driver go-online), `10-live-checks.md` (live flag
values, 2026-09-25 ~03:30 UTC).

**Live facts used (VERIFIED-LIVE, `10-live-checks.md`, will drift):**
`driver_availability_v2_enabled` = **false** in production — so DISPATCH-003's atomic
`is_available` release fix is built but dark, matching the task brief. Not separately
re-checked live in this session: `dispatch_direct_pool_enabled`, `max_simultaneous_offers`,
`minimal_fcm_offer_payload_enabled`, any per-area surge or heatmap config override, or
whether `admin_efficiency_metrics`' `eta_sample` is large enough in any service area to
trust the ETA-error percentiles cited in §1.

**Evidence labels:** VERIFIED (read in code this session), VERIFIED-LIVE (from
`10-live-checks.md`), INFERRED, ASSUMED, PROPOSED, UNKNOWN. Every external claim carries
a URL and the date read (2026-09-25); an external claim with no URL is ASSUMED.

**Web access.** WebSearch worked for every query run this session (about 14 searches);
no WebFetch was attempted (Session 3's log shows the egress proxy blocking most primary
source domains on 2026-09-25, so this session went straight to WebSearch and treats every
external claim as resting on a search-result snippet unless stated otherwise). External
claims are therefore INFERRED from snippets, not VERIFIED against a full primary-source
read, except where a claim is Spinr's own published benchmark work already cited
VERIFIED in `03-benchmark.md` §6 (the Pandey/Caliskan Chicago study — that citation is
carried forward, not re-fetched here).

**Absence claims.** Per the task's warning that four absence claims in this audit were
refuted, every "not found" statement below was searched with at least two spellings
(e.g. "acceptance rate" and "cancellation rate" and "reliability score"; "batch matching"
and "batched assignment" and "bipartite matching"; "airport mode" and "airport queue" and
"geofence dispatch") and a targeted grep before being reported as absent, and is labelled
VERIFIED (absence) rather than silently assumed.

**Scale screen.** Spinr dispatches in a handful of Saskatchewan service areas, not a
global network. Every technique below is screened against that scale explicitly: an
idea that only pays off with thousands of concurrent open rides per city (global
bipartite optimization, ML ETA models trained on tens of millions of trips, GPU-backed
real-time reassignment) is marked HOLD and the reasoning states the volume gap, not just
asserted.

---

## §1 Current state (Lane C) — what this session found beyond `dispatch.md`/`driver-journey.md`

Only what is **new or changed** relative to `dispatch.md` (DISPATCH-001..004) and
`driver-journey.md` (DRIVER-004..006) is listed below; their findings are cited, not
repeated. `dispatch.md` §4's write-site table and `08-hostile-review.md`'s corrections to
DISPATCH-001/DRIVER-004 are treated as settled and carried forward without re-derivation.

| # | Surface | What the code does today | Label | Why it matters for this session |
|---|---|---|---|---|
| CS-1 | Matching shape | Dispatch is **per-ride, non-exclusive broadcast to the top N candidates**, not global bipartite assignment across concurrent open rides. `match_driver_to_ride(ride_id, ...)` (`routes/rides/matching.py:251`) is called once per ride; `rank_by_eta_with_acceptance()` (`services/dispatch_service.py:83-98`) sorts the candidate pool by `effective_eta = eta_seconds / acceptance_rate`, then the claim loop offers to the first `max_offers` (default 3, area/`app_settings`-configurable, hard-capped at 10 — `dispatch_service.py:66,461-465`) simultaneously; whichever driver accepts first wins (CAS-guarded, `dispatch.md` §1/§3). No cross-ride batching window exists anywhere in the codebase (grepped `batch_window`/`dispatch_batch`/`batching_window` across `backend/`: the only hits are the unrelated Sunday-payout batch window in `utils/auto_payout.py`). | VERIFIED | This is the direct answer to research question 1's "what does Spinr do today" — it is closer to the industry term **non-exclusive dispatch (NED)** than to either pure single-driver exclusive dispatch or a periodic Hungarian-algorithm batch match. §2.1 below evaluates whether a batching window on top of this would pay off at Spinr's scale. |
| CS-2 | ETA error is already a measured, stored metric | `admin_efficiency_metrics` (Postgres RPC, `migrations/352_efficiency_and_financial_fns.sql`) computes `eta_error = (ride_started_at − responded_at) − eta_seconds` (the **accepted offer's** promised `eta_seconds`), exposed as `eta_error_p50_secs`/`eta_error_p95_secs`/`eta_on_time_pct`/`eta_sample` at `GET /api/admin/analytics/efficiency` (`routes/admin/analytics.py:1362-1424`). The migration's own comment is explicit that this spans **acceptance-to-trip-start**, not a pure drive-time window, because `rides` has no arrival timestamp column — it is named "assignment to trip start" in the API specifically to avoid overclaiming precision. | VERIFIED | Directly answers research question 2's "how big is the error today, from code and stored metrics" — Spinr does not need to build ETA-error measurement from scratch, only to read what already exists (§2.2). Whether the live `eta_sample` is large enough per service area to trust the percentile was **not checked this session** (no production DB read) — see §6. |
| CS-3 | ETA computation chain, and a scope boundary inside it | `utils/maps_eta.py`: OSRM first (self-hosted/public, free) → Google Distance Matrix (traffic-aware, metered) → haversine at a fixed 30 km/h (`_FALLBACK_SPEED_KMH`). 15 s Redis cache; a "movement gate" (`_ETA_MOVE_THRESHOLD_M = 100`) reuses the last computed ETA if the driver has moved under 100 m, capped at 120 s reuse even for a stationary driver. **Scope note, not previously flagged**: the module's own docstring states this chain is called **only** for the pre-pickup phases (`driver_assigned`, `driver_accepted`, `driver_arrived`); during `in_progress` "the rider app computes ETA client-side via haversine so we do not re-call Maps on every GPS ping for the full trip duration." | VERIFIED | The `eta_error` metric in CS-2 measures the pre-pickup window this chain serves, so the two are aligned — but it means Spinr has **no measured accuracy figure at all for the in-trip (to-dropoff) ETA the rider sees**, since that number is a client-side haversine estimate the backend never records or compares to actual arrival. This is a real, previously-unflagged gap for a rider-experience "ETA quality" question, distinct from the well-measured pickup ETA. |
| CS-4 | Supply-positioning infrastructure is already substantially built | Three cooperating pieces, none previously catalogued together by an audit lane: (1) `services/h3_heatmap.py` — aggregates ride pickups into H3 cells at a fixed resolution, drops any cell under a k-anonymity floor (`k_floor`, default enforced ≥3) before it ever reaches a client — a real PIPEDA control, not a bolt-on; (2) `services/demand_tiles.py` — rasterises the same suppressed aggregate into transparent XYZ tiles with a Gaussian kernel-density spread (not per-tile-normalised alpha compositing, which the module's docstring explains would make the same colour mean different things on adjacent tiles); (3) `GET /demand-heatmap` (`routes/drivers/profile.py:471-530`) — driver-facing endpoint, rate-limited 20/min with a 30 s poll floor, serving a v1 `points:[[lat,lng,weight]]` 7-day decayed aggregate always, and a v2 (`driver_heatmap_v2_enabled`, default **false**, `schemas.py:731-750`) that adds `cells`/`surge`/`forecast` layers for a "next-6h demand timeline." | VERIFIED | This is a large fraction of research question 3 ("supply positioning: heatmaps and forecasts... without control-of-work pressure") **already shipped**, not a gap. It is informational-only by construction (a driver reads it or ignores it; nothing in the endpoint or its callers ties heatmap data to availability, offer ranking, or any quota) — a genuine example of the guardrail already being respected. §2.3 evaluates only the v2 rollout and the forecast layer's data quality, not whether to build the base capability, which exists. |
| CS-5 | Destination-mode filtering is a second, independent positioning primitive | `is_destination_mode_active()` / `_ride_brings_driver_closer_to_destination()` (`dispatch_service.py:104-165`) — a driver who has opted into "heading home" is only offered rides whose dropoff is at least 5% closer to their stated destination; the gate fails open (no filtering) on any missing/expired/unparseable state so a driver never goes invisible from a bug. Auto-expires after 2 hours (`DESTINATION_MODE_TTL`). | VERIFIED | A working example of "let a driver shape their own supply position without penalty" — the correct opposite of a control-of-work mechanic. Cited here because it belongs in the same "what already works" bucket as CS-4, and because its expiry/fail-open pattern is the template §2.3's first step should copy for any new positioning feature. |
| CS-6 | Airport/event/winter-storm modes: absence confirmed, not a pre-existing gap statement copied from another lane | Grepped `airport`, `event_mode`, `winter_mode`, `storm`, `weather_mode`, `surge_event` across `backend/**/*.py` (excluding tests). The only "airport" hits are `is_airport`/`airport_fee` — a flat **pricing surcharge** on a ride (`services/fare_service.py:184-350`, `schemas.py:818-819`), not a dispatch mechanism. There is no virtual queue, no widened search radius, no candidate-pool change, and no surge-behaviour change tied to an airport, a declared event, or a weather condition anywhere in the dispatch or surge code. `utils/surge_engine.py`'s only per-area override is the existing `surge_disabled`/`surge_source` toggle (`tests/test_surge_engine.py`, `tests/test_routes_fares_coverage.py`) — a manual admin flag, not an automatic emergency response. | VERIFIED (absence, two spellings each) | Directly answers research question 5: today "airport mode" is a fee, not an operational mode. §2.5 below designs the smallest additions for Regina/Saskatoon's two airports and for a declared winter storm, scoped to Spinr's actual city count rather than an Uber-scale queue system. |
| CS-7 | Surge granularity as a fairness-relevant fact, re-confirmed by direct read | `surge_engine.py`'s docstring and structure confirm `03-benchmark.md` §6's claim first-hand: surge is computed and written **per service area** (`service_areas.surge_multiplier`), on a 2-minute tick, not per micro-zone/geohash. | VERIFIED (re-confirms `03-benchmark.md` §6, not independently re-derived there) | Directly feeds §2.6's fairness-audit design — coarser granularity is easier to audit (fewer, larger units) but also structurally cannot price-discriminate at the block level the Chicago study measured, which is worth stating as a design property when the audit is published, not just a limitation. |
| CS-8 | Acceptance-rate ranking penalty, re-confirmed with the exact floor value | `rank_by_eta_with_acceptance()` floors `acceptance_rate` at `0.1` before dividing (`dispatch_service.py:94`), so the **worst-case penalty a driver's own history can apply to themselves is a 10× effective-ETA multiplier** — re-confirming DRIVER-004's mechanism with the concrete bound, which no prior finding card stated numerically. | VERIFIED | Used directly in §2.4's fairness card — a 10× ceiling is a specific, checkable number a disclosure or an appeal policy can reference, rather than "a large penalty." |

---

## §2 Techniques radar

Each card follows `research-sessions.md` §1's format. Every card is screened against
Spinr's scale (Saskatchewan service areas, not a metro with thousands of concurrent open
rides) and against the guardrail: **no penalty for being offline**, and any change to the
matching/ranking/offer algorithm needs a **replay test against Spinr's own past trips**
before rollout — stated explicitly per card, not assumed.

### 2.1 Matching: nearest-driver vs batched/bipartite

### Non-exclusive broadcast to the top-N ranked candidates (current design) — ADOPT (keep)
- What it is (plain language): instead of offering a ride to one driver at a time and
  waiting for a no before trying the next, offer it to several good candidates at once;
  whoever accepts first gets it, and the CAS write plus the DB-level `UNIQUE(ride_id,
  driver_id)` constraint (`dispatch.md` DISPATCH-002) stop two drivers from both winning.
- Who uses it / source: industry term is **non-exclusive dispatch (NED)**; a 2026 Lyft
  research paper frames it as the current industry direction because exclusive
  (one-at-a-time) dispatch's sequential retries add rider wait time as decline rates rise
  ([Lyft: Non-Exclusive Notifications for Ride-Hailing I](https://arxiv.org/html/2603.21533v1), read 2026-09-25, INFERRED from an arXiv paper with Lyft-affiliated authors — not Lyft's own engineering blog, so treated as independent research **about** the practice Lyft studies, not a confirmed statement that Lyft runs exactly this). A separate Lyft engineering-blog post confirms dispatch is treated as an assignment/optimization problem at Lyft generally ([Lyft Eng: Solving Dispatch in a Ridesharing Problem Space](https://eng.lyft.com/solving-dispatch-in-a-ridesharing-problem-space-821d9606c3ff), snippet, read 2026-09-25, INFERRED).
- Spinr today: exactly this shape (CS-1, VERIFIED); `effective_eta = eta / acceptance_rate`
  ranks the candidate pool before the top-N are chosen.
- Benefit for Spinr at our scale: broadcasting to 3–10 drivers per ride is cheap at
  Saskatchewan volumes and already gets most of NED's latency benefit over one-at-a-time
  offers without needing a batching window's added rider wait. Cost/effort: **none**
  (already built). Risk: none from keeping it.
- How it fails or gets gamed: contention (two drivers accepting near-simultaneously) is
  already handled (DISPATCH-002/`dispatch.md` #5); the live default (legacy) path's
  worse failure mode under a stale offer row — whole-batch abort instead of a
  single-driver skip — is `dispatch.md`'s DISPATCH-002, not re-derived here.
- First step (n/a — already shipped): none needed for this card; DISPATCH-001/-002 remain
  the open items and are already on `ROADMAP.md`'s X13.

### Periodic global bipartite matching across all open rides in a service area — HOLD
- What it is: instead of matching one ride against a driver pool the instant it's
  requested, collect every currently-open ride and every currently-available driver in a
  short window (industry examples cluster around 2–5 seconds for real-time dispatch, up
  to a few minutes for the batching studied in the operations-research literature), then
  solve one assignment problem (e.g. the Hungarian algorithm) that minimises total wait
  or maximises match count across the whole batch at once, rather than one ride at a time.
- Who uses it / source: batching **can** beat greedy/instant matching when it produces a
  provably better global assignment, but recent operations-research work is explicit that
  the advantage is conditional, not universal: batching is asymptotically optimal only
  when rider impatience (how long they'll wait before abandoning) is not too high, and
  under some rider-patience distributions (exponential sojourn times) batching "can perform
  arbitrarily poorly," with greedy/instant matching offering the more reliable
  constant-factor guarantee instead ([Eom & Toriello, *Batching and Greedy Policies: How
  Good Are They in Dynamic Matching?*, MSOM 2024/2025](https://bpb-us-e1.wpmucdn.com/sites.gatech.edu/dist/7/1474/files/2025/09/Dynamic_nbip_matching_v4.pdf), abstract read via search snippet, 2026-09-25, INFERRED — the PDF itself was not opened, only the abstract summary returned by the search). Uber and Lyft's own **production** dispatch is reported (independent summaries, not their own primary engineering posts, so INFERRED) to already batch requests over short windows before matching — but at a scale (many concurrent open rides per few square kilometres) Spinr does not have.
- Spinr today: no batching window exists (CS-1, VERIFIED absence).
- Benefit for Spinr at our scale: **low, and possibly negative.** Batching's entire
  argument is "the global optimum from N rides × M drivers beats N separate local
  optimums" — that gap only opens up when N and M are both large enough that a single
  ride's best-driver choice can conflict with another ride's best-driver choice at the
  same moment. In a Saskatchewan service area with a handful of concurrently searching
  riders, the odds of a real conflict in any given few-second window are low, so a
  batching window mostly just **adds rider wait time for no matching-quality gain** — the
  exact failure mode the cited paper's high-impatience case describes. Cost/effort: **L**
  (a new scheduler, a solver, a window-tuning problem, and a genuinely different failure
  mode to test). Risk: medium-high — this changes the state machine's dispatch timing on
  a live, KPI'd surface (P95 dispatch latency < 2s is a CLAUDE.md SLA a batching window
  directly works against).
- How it fails or gets gamed: over-tuned batch windows increase rider wait without
  improving match quality when supply/demand density is low (exactly Spinr's regime); a
  batch that spans a Redis or DB hiccup risks the same DISPATCH-002-class re-offer bug at
  a larger blast radius (a whole batch's decisions instead of one ride's).
- False-positive cost / appeal path: n/a — this is an algorithm change, not a
  fraud/enforcement signal; the relevant control is the replay-test gate below.
- Verdict: **HOLD.** Do not build. If Spinr's per-city concurrent-open-ride count ever
  grows enough that this session's own reasoning (low conflict odds at low density) no
  longer holds, revisit with real production concurrency numbers, not before. If revisited:
  **mandatory replay test** — run the candidate batching window against a full quarter of
  Spinr's own historical `ride_status_events`-equivalent trail (today: `rides` + `ride_offers`
  timestamps) and compare P95 time-to-match and match-rate against the current
  non-exclusive-broadcast baseline before any production exposure, per the session brief's
  guardrail.

### Reinforcement-learning dispatch (Grab DispatchGym, DiDi-style RL matching) — HOLD
- What it is: train a policy that decides not just *who* to match but *when* to hold a
  driver or a ride back from an immediate match, learned from simulated or historical
  outcomes rather than hand-written rules.
- Who uses it / source: Grab publishes **DispatchGym**, an open research framework for RL
  dispatch experiments ([Grab Engineering: DispatchGym](https://engineering.grab.com/techblog_-dispatchgym), snippet, read 2026-09-25, INFERRED); DiDi and academic work report RL dispatch deployed at large-city scale ([arXiv: RL dispatching deployed in ridehailing marketplace](https://arxiv.org/pdf/2202.05118), snippet, INFERRED).
- Spinr today: nothing of this kind exists or is implied by any code read this session.
- Why HOLD: this needs a large volume of historical matching outcomes to train against,
  an ML-ops owner Spinr does not have (the same "no ML owner" reasoning Session 3 applied
  to graph-ML fraud models), and produces a dispatch decision that is materially harder to
  explain to a driver who disputes why they weren't offered a ride than the current
  `effective_eta` formula already is — worsening, not helping, the DRIVER-004
  transparency gap §2.4 covers. Revisit only after (a) the event-log/ride-status-events
  table `04-blueprint.md` H1 recommends exists and has enough history, and (b) a much
  larger driver/ride volume than Saskatchewan-scale.

### 2.2 ETA quality

### Keep the OSRM → Google → haversine fallback chain; publish the existing `eta_error` metric where it can inform this decision — ADOPT (keep + surface)
- What it is: no change to the chain itself — OSRM first (free, no live traffic), Google
  Distance Matrix second (traffic-aware, metered, the current source of truth when
  available), haversine-at-30km/h last resort. The addition is process, not code: treat
  the already-built `eta_error_p50/p95`/`eta_on_time_pct` figures (CS-2) as the metric
  that answers "is this chain accurate enough," reviewed periodically, rather than an
  unmeasured assumption.
- Who uses it / source: independent comparisons describe OSRM as fast and free but
  without live traffic, with ETAs that "drift" as speed-limit/road data ages, versus
  Google's traffic-aware, generally more accurate ETAs at a metered cost ([Big Iron: OpenRouteService vs OSRM vs Valhalla](https://www.bigiron.cc/guides/openrouteservice-vs-osrm-vs-valhalla-self-hosted-routing), [StackShare: Google Maps vs OSRM](https://stackshare.io/stackups/google-maps-vs-osrm), snippets, read 2026-09-25, INFERRED — vendor/community comparison pages, not a controlled study).
- Spinr today: exactly this chain, with Google as the traffic-aware primary and OSRM as
  the free first attempt in front of it (CS-3, VERIFIED) — i.e. Spinr already orders the
  two providers the *opposite* way a pure cost-minimising deployment would (OSRM-only)
  and the way a pure accuracy-maximising deployment would (Google-only, more expensive);
  it tries the free one first and pays for Google only when needed, which is a reasonable
  middle ground at Spinr's scale. `eta_error` (CS-2) is the number that would prove or
  disprove whether that trade-off is actually working, i.e. whether OSRM's non-traffic-aware
  answer is winning the race often enough to matter, or whether Google essentially always
  serves it.
- Benefit for Spinr at our scale: near-zero-cost — read a metric that already exists.
  Cost/effort: **S** (a recurring look at `/api/admin/analytics/efficiency`, or a small
  admin-dashboard chart if one does not already exist — not independently checked whether
  the admin dashboard renders this endpoint's data today). Risk: none.
- How it fails or gets gamed: n/a (a measurement practice, not an enforcement action);
  the risk is entirely on the **input** side — `eta_sample` may be small per service area
  (not checked live, §6), which the API already surfaces so a reviewer cannot mistake a
  thin sample for a fleet-wide figure (the migration's own stated design goal).
- First step behind a flag: none needed — no flag, just a review habit; if the admin
  dashboard does not already chart `pickup_eta_error`, add that chart (additive,
  read-only). Measure success by: `eta_on_time_pct` trend over time and `eta_sample` size
  per service area before trusting the percentile.

### Learned ETA-residual correction (DeepETA-style post-processing) — HOLD
- What it is: instead of replacing the road-network ETA, train a small model on top of it
  that predicts the *gap* between the routing engine's answer and what actually happened,
  using historical outcomes plus real-time signals (time of day, request type, recent
  traffic).
- Who uses it / source: Uber's DeepETA is exactly this — a post-processing model that
  predicts the residual between the routing engine's ETA and real-world outcomes, reported
  as Uber's highest-QPS ML model ([Uber Engineering Blog: DeepETA](https://www.uber.com/us/en/blog/deepeta-how-uber-predicts-arrival-times/), snippet, read 2026-09-25, INFERRED).
- Spinr today: nothing of this kind; the fallback chain (CS-3) has no learned correction
  step.
- Benefit for Spinr at our scale: **low relative to cost.** DeepETA-class models are
  justified by Uber's request volume (a model retrained continuously against a global
  trip stream); Spinr's `eta_sample` at Saskatchewan volume is very unlikely to be enough
  to train and validate a model that beats "read `eta_error` and, if it's consistently
  biased in one direction, apply a single constant correction factor" — a much cheaper
  version of the same idea. Cost/effort: **L** for a real model; **S** for a constant/
  seasonal correction factor derived from the existing `eta_error_p50_secs`. Risk: a
  poorly-fit correction (learned or constant) makes ETAs *worse* in a way that is harder
  to notice than the current, honestly-labelled "assignment to trip start... upper bound"
  framing.
- How it fails or gets gamed: n/a (an accuracy tool, not adversarial); the failure mode is
  silent drift if the correction is set once and never re-measured.
- Verdict: **HOLD** the ML model; the constant-correction-factor version is folded into the
  ADOPT card above rather than scored separately, since it is the same "read `eta_error`
  and act on it" first step, just with one more line of arithmetic if a persistent bias
  shows up. **Mandatory replay test** if ever built: any correction (constant or learned)
  must be validated against a held-out slice of Spinr's own historical rides before it
  changes what a rider or driver sees, per the session guardrail.

### 2.3 Supply positioning

### Turn on `driver_heatmap_v2_enabled` (layered heatmap + forecast) after a data-quality check — TRIAL
- What it is: the v2 driver heatmap (`cells`, `surge`, and a "next-6h demand timeline"
  forecast layer) that already exists behind a dark flag (CS-4).
- Who uses it / source: Uber's own guidance heatmap is described as using probabilistic
  deep models that output a distribution of forecasted earnings outcomes, explicitly
  informational (drivers choose whether to act on it), not a mandate
  ([Uber Blog: Enhancing Uber's Guidance Heatmap with Deep Probabilistic Models](https://www.uber.com/us/en/blog/enhancing-ubers-guidance-heatmap-with-deep-probabilistic-models/), snippet, read 2026-09-25, INFERRED); Grab describes "supply shaping" — weighting a driver's visibility toward nearby demand — as a positioning-guidance layer distinct from dispatch itself ([Grab Engineering: Understanding Supply & Demand in Ride-hailing](https://engineering.grab.com/understanding-supply-demand-ride-hailing-data), snippet, INFERRED).
- Spinr today: v1 (7-day decayed point aggregate) is live by default; v2 is built, dark
  (CS-4, VERIFIED).
- Benefit for Spinr at our scale: real — a forecast layer helps a driver decide where to
  be before a shift, and Spinr's version is already built on the k-anonymity-safe
  aggregation pipeline (CS-4), so there is no new PIPEDA exposure to review. Cost/effort:
  **S** (flip the flag; the harder work — the forecast model's own accuracy — is a
  separate question this session did not evaluate, since it was not asked to and no
  forecast-accuracy metric was found in this pass). Risk: low, provided the forecast
  layer is clearly labelled as guidance (Uber's own framing above), never as a quota or an
  expectation.
- How it fails or gets gamed: a forecast that is wrong often enough erodes trust in the
  tool, not in dispatch itself, since nothing downstream depends on it; the only real risk
  is product/copy risk — if the forecast layer is ever framed as "you should be online
  now" rather than "riders are asking for rides here," it becomes exactly the
  control-of-work pressure CLAUDE.md's "What Spinr Is NOT" forbids.
- False-positive cost: a driver who repositions based on a wrong forecast loses idle time,
  not earnings already made — no enforcement action is attached.
- Appeal path: n/a (informational feature, no enforcement).
- First step behind a flag: `driver_heatmap_v2_enabled` in one service area first;
  confirm the copy around the forecast layer reads as guidance, not instruction, before
  wider rollout. Measure success by: driver engagement with the layer (opens/interactions)
  and, if measurable without new tracking, whether drivers who use it self-report shorter
  idle time — not a mandated metric, an optional survey question.

### 2.4 Offer design

### Disclose the acceptance-rate ranking penalty (DRIVER-004) — ADOPT (Now, already scheduled)
- What it is: in-app and FAQ copy stating that declining an offer lowers `acceptance_rate`,
  which lowers how soon future offers reach that driver (up to the 10× `effective_eta`
  ceiling this session confirmed, CS-8) — disclosure, not removal, matching
  `driver-journey.md`'s own recommendation.
- Who uses it / source: driver algorithmic-transparency pressure is a live, contested area
  — Colorado's SB 24-75 requires Uber/Lyft to disclose driver wages and rider charges after
  drop-off starting Feb 2025 ([search-summarised, snippet, read 2026-09-25, INFERRED — the
  statute text itself was not read]); drivers have sued over deactivation with "no real
  appeal process" and undisclosed grounds ([CalMatters: Uber drivers sue over
  deactivations](https://calmatters.org/economy/2026/04/uber-proposition22-lawsuit/), snippet, INFERRED); a cited 2026 report found 30% of deactivated drivers received no explanation at all ([search-summarised, snippet, INFERRED]). None of these sources are about an *acceptance-rate ranking* penalty specifically — Spinr's mechanism is narrower (ranking, not deactivation) — but they establish the direction regulators and courts are moving on undisclosed algorithmic effects on driver earnings opportunity generally.
- Spinr today: built, undisclosed (`driver-journey.md` DRIVER-004, re-confirmed CS-8).
  Already on `ROADMAP.md` X3 ("disclose that declining can affect offer priority (FAQ row
  + one in-app line)") and `04-blueprint.md`'s Epic: Ride Booking & Matching incremental
  path step 1b.
- Benefit for Spinr at our scale: this session adds nothing new to the mechanism finding
  itself — it is fully scoped elsewhere — but confirms the concrete 10× ceiling (CS-8) as
  a fact worth including in the disclosure copy itself, since "declining lowers your
  future offer priority" is vaguer and less checkable than "a very low acceptance rate can
  push your offers back to about a tenth of your distance-based turn," if product/legal
  decide the exact number should be public. Cost/effort: **S** (copy-only, no code).
  Risk: none from disclosure; the risk was in the silence.
- Verdict: **ADOPT**, already scheduled (X3) — this session's contribution is confirming
  the numeric floor for the copy-writer, not a new recommendation.

### A lower, reviewed cap on the acceptance-rate ranking penalty — ASSESS
- What it is: the floor of `0.1` (CS-8) was not derived from any stated fairness analysis
  in the code (no comment cites a target match-rate impact or a driver-earnings-impact
  study) — it reads as an engineering safety floor ("avoid division explosion," the
  code's own comment) rather than a chosen fairness policy. Worth asking, once disclosed
  (the card above), whether 10× is the right ceiling or an accidental one.
- Who uses it / source: no external source states what ceiling Uber/Lyft use for an
  acceptance-rate-linked ranking effect — this is Spinr's own number to own, and no
  primary source describing a comparable numeric cap at another platform was found in this
  session's searches (searched "acceptance rate" and "reliability score" and "cancellation
  rate" ranking penalty magnitude; results were about deactivation thresholds, not ranking
  multipliers).
- Spinr today: `max(rate, 0.1)`, unexplained beyond the divide-by-zero guard
  (`dispatch_service.py:94`, VERIFIED).
- Benefit for Spinr at our scale: a deliberately-chosen, disclosed, and periodically
  reviewed ceiling is a stronger answer to a classification/fairness challenge than an
  undocumented constant that happens to also prevent a crash. Cost/effort: **S** (a
  product/legal decision plus a one-line constant change, not a rebuild). Risk: low —
  changing the floor changes ranking outcomes for the lowest-acceptance-rate drivers only,
  and any change needs the mandatory replay test below.
- How it fails or gets gamed: a floor set too generously (e.g. 0.5, only 2× penalty) could
  let a driver who reflexively declines high-value or long trips still get offered rides
  ahead of a more responsive driver — the fairness argument `driver-journey.md` cites for
  *keeping* the mechanism at all (riders shouldn't wait behind a driver who reflexively
  declines everyone).
- False-positive cost: a driver with a temporarily low acceptance rate (e.g. after a run
  of legitimate declines for a WAV/accessibility mismatch or a genuinely bad-fare batch of
  offers) sits at the ceiling until their EWMA recovers — the existing miss-streak/decline
  separation (`driver-journey.md`'s Steelman) already protects against conflating this
  with the auto-offline mechanism, but the ranking effect itself has no separate reset or
  appeal.
- Appeal path: none today beyond the general driver-support contact; worth an explicit one
  once disclosed (e.g. "why was I offered this ride later than expected?" support macro).
- First step behind a flag: a product/legal decision on the target ceiling (10× vs. a
  smaller number), landed as a named constant (not a magic `0.1`) with a code comment
  citing the decision, **shipped together with** the disclosure copy above so drivers are
  never told about a mechanism whose exact strength changes without notice. Measure
  success by: match-rate and rider-wait-time impact of the new ceiling on a replay of
  Spinr's own historical dispatch data, compared against the current `0.1` floor, **before**
  changing the live constant — the session's mandatory replay-test guardrail applies
  directly here since this is an algorithm change.

### Keep pre-accept fare/destination transparency; keep the 30 s server-authoritative offer timeout — ADOPT (keep)
- What it is: no change — the offer card already shows `driver_earnings` (not gross
  fare), pickup distance, trip distance/duration, surge multiplier, and both pickup and
  dropoff before the driver decides (`driver-journey.md` §5 Steelman, re-cited not
  re-derived), and the 30 s timeout (15 s client countdown + 15 s server grace) is
  independent of client state so a dead phone doesn't strand the ride (`dispatch.md` #6).
- Who uses it / source: one arXiv implementation example cites a 15 s per-driver response
  window as typical ([search-summarised dispatch config example, snippet, read 2026-09-25,
  INFERRED — not a named platform's own documentation]) — consistent with, not longer or
  shorter than, Spinr's client-visible half of the window.
- Spinr today: exactly this (VERIFIED via `dispatch.md`, not re-read this session).
- Benefit / risk: n/a — this is a "don't touch it" card. Both mechanisms are already
  ahead of or in line with what the research surfaced, and `04-blueprint.md` already
  marks both **KEEP**.
- Verdict: **ADOPT (keep)**. No first step.

### 2.5 Airport, event and winter-storm modes

### Airport virtual queue (FIFO by wait time, at Regina and Saskatoon's airports) — ASSESS
- What it is: when a driver enters a defined airport geofence, they join a queue instead
  of being ranked by `effective_eta`; the driver who has waited longest in the zone gets
  the next airport pickup, and only trips accepted from inside the zone count.
- Who uses it / source: Uber's queue is exactly this — first-in-first-out by wait time in
  the geofenced area, with prepositioning guidance ahead of predicted peak hours ([Uber
  Blog: Airport Queue](https://www.uber.com/hk/en/blog/airport_carpark/), [Uber Blog: Forecasting Models to Improve Driver Availability at Airports](https://www.uber.com/us/en/blog/forecasting-models-to-improve-availability-at-airports/), snippets, read 2026-09-25, INFERRED); airport surge is scoped to drivers physically inside the queue zone, so a driver who accepts a trip from outside it doesn't collect the airport rate ([Uber Help, snippet, INFERRED]).
- Spinr today: `is_airport`/`airport_fee` is a flat surcharge on the fare only
  (`fare_service.py:184-350`); there is no queue, no geofence-scoped ranking change, and
  no prepositioning guidance (CS-6, VERIFIED absence).
- Benefit for Spinr at our scale: **genuinely worth building, at a much smaller scope than
  Uber's.** Spinr operates two or three airports total, not hundreds — a FIFO queue keyed
  to a fixed geofence per airport is a small, well-understood data structure (a Redis
  sorted set by entry time, mirroring the presence/offer-skip patterns `dispatch.md`
  already documents this codebase using), not a forecasting-model investment. It also
  directly serves fairness: without a queue, `effective_eta` ranking means the driver who
  happens to be nearest the terminal at the right instant always wins the long, predictable
  wait most drivers accept specifically *because* it's predictable — a queue converts that
  into "first come, first served," which is the fairer and more driver-legible rule for a
  location drivers deliberately choose to wait at (unlike ordinary street-hail dispatch,
  where nearest-driver is the fairer default because nobody is choosing to queue).
  Cost/effort: **M** (a geofence config per airport, a queue-entry/exit event, a ranking
  override scoped to that geofence, a driver-facing "you are #N in the airport queue"
  screen). Risk: medium — this changes ranking for a real subset of rides and needs the
  replay-test guardrail before shipping.
- How it fails or gets gamed: a driver "phantom-queues" (drives in and out of the geofence
  repeatedly to reset position without actually waiting) unless queue position is
  time-in-zone-based with a cooldown on re-entry, not purely entry-timestamp-based; a
  driver who is offered an airport trip and declines it should lose their queue position
  (mirroring the existing `acceptance_rate` logic's *intent*, though this needs its own
  explicit rule, not a silent reuse of the ranking-wide `acceptance_rate`, since a
  driver's general acceptance behaviour and their behaviour inside a queue they chose to
  join are different signals).
- False-positive cost: a driver who enters the geofence briefly (e.g. dropping off a
  passenger there) and is mistakenly queued; scope the geofence tightly to the actual
  waiting lot, not the whole airport perimeter.
- Appeal path: a visible queue position is itself the appeal path (a driver can see they
  are "#3", unlike an opaque ranking) — a real transparency improvement over the general
  ranking formula, worth noting as a template for §2.4's broader disclosure question.
- First step behind a flag: `airport_queue_enabled`, one airport (the higher-volume of
  Regina/Saskatoon, product to decide which) first, geofence config in `service_areas`
  rules (the same JSON `04-blueprint.md`'s Maps & Routing card already proposes for
  per-area rules). **Mandatory replay test**: simulate the queue against a slice of
  Spinr's own historical airport-pickup rides (filterable by `is_airport`) and compare
  wait-time fairness (variance across drivers) against the current `effective_eta`
  ranking for the same rides before enabling live. Measure success by: driver-reported
  fairness (a simple in-app "was the queue fair?" prompt after an airport pickup) and
  wait-time variance across drivers who used the queue.

### Event mode (temporary widened search radius / notice for a known large gathering) — ASSESS
- What it is: for a pre-known event (a stadium concert, a fair, a provincial event) with a
  predictable end-time demand spike, temporarily widen the dispatch search radius and/or
  pre-notify nearby off-duty drivers with informational (not mandatory) demand guidance for
  that window, using the heatmap/forecast layer already built (CS-4/§2.3) rather than a
  new mechanism.
- Who uses it / source: Uber and Lyft both run event-based demand forecasting and
  positioning guidance; specific mechanics (radius widening, driver notification timing)
  were not confirmed in any primary source this session could reach — the airport-mode
  sources above are the closest documented analogue and are cited there, not repeated.
- Spinr today: nothing (CS-6, VERIFIED absence) beyond the general, always-on
  `search_radius_km` default and per-area override (`dispatch_service.py`
  `DEFAULT_SEARCH_RADIUS_KM`).
- Benefit for Spinr at our scale: real but **smaller than it looks** — a manually
  admin-configured, time-boxed radius widening for a named event (not an automated
  event-detection system) is a small, additive `service_areas`-rules change, and the
  demand-side half of "event mode" (telling drivers where a spike is coming) is *already
  covered* by turning on the v2 heatmap forecast layer (§2.3) for that window — so this
  card's actual net-new scope is narrow: an admin-settable temporary radius override, not
  a whole new subsystem. Cost/effort: **S–M**. Risk: low if scoped to search radius only
  (never to ranking or the acceptance-rate mechanism) and time-boxed with an automatic
  revert.
- How it fails or gets gamed: a widened radius that stays on past the event window quietly
  changes matching behaviour with no one noticing — mitigate with a mandatory end time on
  the override, not an indefinite toggle.
- False-positive cost: a wider radius can mean a longer average pickup ETA for rides
  matched during the window if used carelessly — worth measuring, not assuming away.
- Appeal path: n/a (an operational config, not an enforcement action).
- First step behind a flag: an admin-settable, time-boxed `search_radius_km` override per
  service area (reuses the existing per-area override mechanism `dispatch_service.py`
  already reads — `area_settings.get(...)`), defaulting to no change; pilot on one known,
  scheduled event. **Replay test**: compare match rate and pickup-ETA distribution for a
  past comparable event (if one exists in Spinr's history) with and without a simulated
  radius widening. Measure success by: match rate during the event window vs. a
  non-widened control comparison (a similar-sized past event, if available).

### Winter-storm mode: driver safety messaging and an explicit "no automatic surge suppression during an emergency" statement — ASSESS
- What it is: two separate, smaller things, not one mechanism: (1) informational,
  non-mandatory in-app messaging to online drivers during an Environment Canada
  weather-warning-triggered window (road conditions, no pressure to stay online); (2) a
  documented, explicit policy on what happens to the 2.5× surge cap during a declared
  provincial state of emergency, since CLAUDE.md's surge cap is otherwise silent on
  emergency conditions specifically.
- Who uses it / source: rideshare driver availability is reported to drop sharply (a
  reported 67–78% fewer available drivers) during significant winter weather events
  ([search-summarised, snippet, read 2026-09-25, INFERRED — original study not
  identified]); Uber is reported to suspend surge pricing during declared states of
  emergency/natural disasters in some jurisdictions ([RideGuru: does Uber turn off surge
  during emergencies](https://ride.guru/lounge/p/is-it-true-that-uber-turns-off-surge-pricing-during-emergencies-disasters-and-storms), snippet, INFERRED) — often because local price-gouging law requires it, a legal question this session did not verify for Saskatchewan (see §5).
- Spinr today: nothing (CS-6, VERIFIED absence); CLAUDE.md's surge rules cap auto-mode at
  2.5× and require documented justification for any manual override above it, but say
  nothing about an emergency-specific behaviour.
- Benefit for Spinr at our scale: **the safety-messaging half is cheap and squarely fits
  a Saskatchewan-first product's brand; the surge half is a legal question, not an
  engineering one.** Winter driving is core to Spinr's stated market (CLAUDE.md's
  Saskatchewan Regulatory section), so a "conditions are rough right now, no pressure to
  keep driving" push during a declared warning is a low-cost, on-brand trust signal, not a
  dispatch-algorithm change. The surge-during-emergency question should not be answered by
  this session (no primary Saskatchewan/Canadian price-gouging statute was read) —
  see §5.
- Cost/effort: **S** (messaging: a scheduled/admin-triggered push to online drivers in an
  affected service area, reusing the existing notification pipeline — no new dispatch
  logic). Risk: low for messaging; the surge question carries real legal risk if answered
  wrong in either direction (keeping surge on during a declared emergency risks a
  price-gouging complaint; auto-suspending it needs a stated trigger so it isn't arbitrary).
- How it fails or gets gamed: n/a for messaging (informational only, never gates
  going online/offline — explicitly must never look like a control-of-work signal, i.e.
  never phrased as "you should stay online despite conditions").
- False-positive cost: none (informational).
- Appeal path: n/a.
- First step: (1) messaging — an admin-triggerable push template tied to a service area,
  no new dispatch code; (2) surge-during-emergency — a human legal question, not a build
  item (§5), before any surge-cap-adjacent code is written.

### 2.6 Fairness audit of dispatch outcomes by neighbourhood

### Self-run dispatch fairness audit using the existing H3 aggregation pipeline — TRIAL
- What it is: reuse the k-anonymity-safe H3 aggregation already built for the heatmap
  (CS-4) to compute, per H3 cell (or per service area, given CS-7's coarser surge
  granularity), three dispatch-specific fairness measures over a rolling window: match
  rate (rides requested vs. matched), P95 time-to-match, and driver cancellation rate —
  the same figures `admin_efficiency_metrics` (CS-2) already computes fleet-wide, sliced
  by geography instead of only by service area — then cross-reference cell-level results
  against publicly available Statistics Canada census-tract data (income, a
  demographic composition proxy) the way the cited Chicago study did with U.S. Census
  data, and publish the *methodology*, not necessarily the raw per-cell data.
- Who uses it / source: this extends `03-benchmark.md` §6's own recommendation (already
  VERIFIED-cited there, not re-derived) — a 2020-2021 study (Pandey & Caliskan, George
  Washington University, 100M+ Chicago rides) found Uber/Lyft per-mile fares were
  systematically higher in neighbourhoods with a higher non-white population share, lower
  median house price, or lower education level, even controlling for demand/speed
  ([CACM coverage](https://cacm.acm.org/news/245817-uber-lyft-pricing-algorithms-charge-more-in-non-white-areas/fulltext?mobile=false), VERIFIED there per `03-benchmark.md`'s own citation, not re-fetched this session). This session's own search adds two directly relevant companion papers not previously cited in the audit: a fairness evaluation specifically of ride-hailing **dispatch/matching** black-box markets using the same Chicago dataset ([arXiv: Evaluating Fairness in Black-box Algorithmic Markets: A Case Study of Ride Sharing in Chicago](https://arxiv.org/pdf/2407.20522), snippet, read 2026-09-25, INFERRED), and a demand-**forecasting**-fairness paper noting that naive demand prediction systematically underestimates travel demand in disadvantaged neighbourhoods ([arXiv/MIT: Fairness-Enhancing Deep Learning for Ride-Hailing Demand Prediction](https://arxiv.org/pdf/2303.05698), snippet, INFERRED) — directly relevant to §2.3's forecast layer, since an unaudited demand forecast could under-serve exactly the neighbourhoods a fairness audit should be checking.
- Spinr today: no fairness-by-neighbourhood metric exists (`03-benchmark.md` §6, VERIFIED
  absence there); the raw ingredients (H3 aggregation with k-anonymity suppression, CS-4;
  match-rate/time-to-match/cancellation-rate SQL already computed fleet-wide, CS-2) now
  both exist independently and were not previously connected to each other by any audit
  lane.
- Benefit for Spinr at our scale: **high relative to cost, and a genuine trust
  differentiator** — `03-benchmark.md` §6 already scores this as "Maturity 1 (missing)"
  for Spinr and effectively "Maturity 2" for the whole industry (a documented external
  critique with no confirmed internal remediation anywhere), so doing this analysis at
  all, even a first pass, is ahead of the stated industry baseline, not merely catching
  up. Reusing the existing k-anonymity pipeline means no new PIPEDA review is needed for
  the aggregation step itself. Cost/effort: **M** (mostly a SQL/analysis exercise plus a
  census-data join, not new product code — the closest new code is generalising
  `admin_efficiency_metrics`'s per-service-area grouping to per-H3-cell, an additive
  query change). Risk: low technically; the real risk is reputational if the audit finds a
  real disparity and it is not acted on — which is an argument for doing the audit
  proactively and transparently rather than a reason to avoid it.
- How it fails or gets gamed: n/a (a measurement exercise, not an adversarial system); the
  methodological risk is the same one the source studies had to manage — correlation
  between neighbourhood demographics and legitimate cost drivers (distance, road speed,
  time-of-day demand) must be controlled for, or the audit will over- or under-state bias.
  Spinr's coarser (service-area, not micro-zone) surge granularity (CS-7) means the
  *pricing* half of a Chicago-style audit has less to find by construction — the *dispatch*
  half (match rate, wait time, driver cancellation by geography) is where this session's
  extension actually adds new ground the Chicago study didn't directly measure.
- False-positive cost: a published finding that turns out to be a data artifact (e.g. one
  under-covered service area skewing a cell's numbers) — mitigate with the same
  `eta_sample`-style sample-size disclosure `admin_efficiency_metrics` already practices,
  applied per cell.
- Appeal path: n/a (a self-audit, not an enforcement mechanism against a person).
- First step behind a flag: no flag needed — this is an internal analysis exercise, not a
  shipped feature. Step 1: extend the `admin_efficiency_metrics` query pattern to group by
  H3 cell (reusing `h3_heatmap.py`'s existing cell/k-anonymity logic) instead of only
  service area, for match rate, P95 time-to-match, and driver cancellation rate. Step 2:
  join against a public census data source at the same geography. Step 3: publish the
  methodology (per `03-benchmark.md` §6's own recommendation) before or alongside any
  results. Measure success by: whether the methodology, once run, either finds nothing
  (a genuine, publishable good-news result at Spinr's scale and surge-granularity) or
  finds something actionable — either outcome is a win over today's total absence of the
  measurement.

---

## §2.7 Verdict summary

| # | Technique | Verdict |
|---|---|---|
| 1 | Non-exclusive broadcast to top-N ranked candidates (current design) | ADOPT (keep) |
| 2 | Periodic global bipartite matching across all open rides | HOLD |
| 3 | Reinforcement-learning dispatch (Grab DispatchGym / DiDi-style) | HOLD |
| 4 | OSRM → Google → haversine ETA chain + surface the existing `eta_error` metric | ADOPT (keep + surface) |
| 5 | Learned ETA-residual correction (DeepETA-style model) | HOLD (constant-correction variant folded into #4) |
| 6 | Turn on `driver_heatmap_v2_enabled` (layered + forecast) after a data-quality check | TRIAL |
| 7 | Disclose the acceptance-rate ranking penalty (DRIVER-004) | ADOPT (already scheduled, X3) |
| 8 | A lower, reviewed, documented cap on the acceptance-rate ranking penalty | ASSESS |
| 9 | Pre-accept fare/destination transparency + 30 s server-authoritative offer timeout | ADOPT (keep) |
| 10 | Airport virtual FIFO queue at Regina/Saskatoon | ASSESS |
| 11 | Event mode (admin-settable, time-boxed radius widening + forecast-layer reuse) | ASSESS |
| 12 | Winter-storm driver safety messaging | ASSESS |
| 13 | Winter-storm / declared-emergency surge behaviour | Human legal question (§5), not scored |
| 14 | Self-run dispatch fairness audit by neighbourhood (H3 + census join) | TRIAL |

---

## §3 Ranked build order

Ranking: cheapest, already-scoped fixes first (disclosure, measurement-surfacing); then
small additive builds with a clear fairness/trust payoff (airport queue, fairness audit);
then items that need a human decision before any code. Nothing here is a rewrite of the
state machine — `dispatch.md` §6 and `04-blueprint.md`'s H1 already own that question and
are cited, not re-litigated. Every item that changes matching/ranking/offer behaviour
states its replay-test plan and rollback per the session guardrail.

### Now (days; mostly config, copy, docs, or reading a metric that already exists)

| Rank | Item | Effort | Closes | Replay-test plan | Rollback |
|---|---|---|---|---|---|
| 1 | **Disclose the acceptance-rate ranking penalty** (FAQ + one in-app line) | S | DRIVER-004, `ROADMAP.md` X3, COMP-009's open half | None needed — copy-only, no algorithm change | Revert the copy |
| 2 | **Surface the existing `eta_error_p50/p95`/`eta_on_time_pct` metric** in a recurring review (admin-dashboard chart if not already rendered) | S | Research question 2 ("how big is the error today") | None needed — read-only | n/a |
| 3 | **Name the numeric acceptance-rate ranking ceiling** (`0.1` → a documented, decided constant) and ship it with the disclosure (item 1) | S | CS-8's "unexplained constant" gap | **Yes** — replay the new ceiling against historical dispatch data before it goes live; compare match rate and rider wait vs. the current `0.1` floor | Revert to `0.1`; no data migration |
| 4 | **Winter-storm driver safety messaging** (informational push during a declared weather warning, no gating effect) | S | Research question 5 (winter mode, safety half) | None needed — messaging only, no dispatch-logic change | Stop sending the push |

### Next (weeks; new code, flagged, additive)

| Rank | Item | Effort | Closes | Replay-test plan | Rollback |
|---|---|---|---|---|---|
| 5 | **Self-run dispatch fairness audit by neighbourhood** (extend `admin_efficiency_metrics`'s query pattern to group by H3 cell; join census data; publish methodology) | M | `03-benchmark.md` §6's own recommendation, research question 6 | n/a — this *is* the analysis; no live behaviour changes | n/a (read-only analysis) |
| 6 | **Turn on `driver_heatmap_v2_enabled`** in one service area, after confirming the forecast layer's copy reads as guidance not instruction | S | Research question 3 (already largely built) | Optional: compare driver-reported idle time before/after in the pilot area if a survey mechanism exists; not a dispatch-algorithm change so not mandatory under the guardrail | Flag off |
| 7 | **Admin-settable, time-boxed search-radius override for a named event** | S–M | Research question 5 (event mode) | **Yes** — compare match rate / pickup-ETA distribution for a comparable past event, with and without a simulated widening, before the first live use | Flag off; override auto-expires at its set end time regardless |
| 8 | **Airport virtual FIFO queue** at the higher-volume of Regina/Saskatoon first | M | Research question 5 (airport mode) | **Yes** — mandatory: simulate the queue against historical airport-pickup rides (`is_airport=true`) and compare wait-time fairness (variance across drivers) to the current `effective_eta` ranking for the same rides | Flag off; ranking reverts to fleet-wide `effective_eta` for that geofence |

### Later (needs a human legal/product decision, or depends on another session's item)

| Rank | Item | Effort | Closes | Depends on |
|---|---|---|---|---|
| 9 | **Surge-cap behaviour during a declared provincial state of emergency** — document a policy (freeze at current value, suspend to 1.0×, or no automatic change) with a stated trigger | S once decided | Research question 5 (winter mode, pricing half) | §5 human question 1 — a Saskatchewan/Canadian price-gouging or emergency-management legal read this session could not perform |
| 10 | **A driver-facing appeal path for "why was I offered this ride later than I expected"**, once the ranking ceiling is disclosed (rank 3) | S | The appeal-path gap the disclosure creates once drivers can ask the question | Rank 1 and 3 shipping first |
| 11 | **Periodic bipartite/batch matching, or RL dispatch** | L | Would only be revisited if Spinr's per-city concurrent-open-ride volume grows by an order of magnitude | Real production concurrency numbers this session did not have; explicitly HOLD, not scheduled |

**Sequencing note.** Ranks 1 and 3 should ship together (never disclose a mechanism whose
strength is about to change without saying so, and never silently change the strength of
an undisclosed mechanism either). Rank 5 (the fairness audit) has no code dependency on
anything else in this list and can start immediately in parallel with the Now items.

---

## §4 What not to build

- **A periodic global bipartite/Hungarian-algorithm batching window.** The operations-
  research literature this session found is explicit that batching's advantage is
  conditional on enough simultaneous conflicting demand to exist — a condition Spinr's
  current scale does not meet, and pursuing it risks adding rider wait for no matching-
  quality gain (§2.1).
- **Reinforcement-learning dispatch.** No labelled volume, no ML-ops owner, and it makes
  the already-open driver-transparency question (DRIVER-004) harder to answer, not
  easier (§2.1).
- **A full DeepETA-class learned ETA model.** Spinr's own `eta_error` sample size is very
  unlikely to support training and validating a model that beats a much cheaper constant-
  correction check against the metric that already exists (§2.2).
- **An automated event-detection or airport-forecasting system.** The event and airport
  cards in this session are deliberately scoped to *admin-configured, time-boxed*
  mechanisms, not the automated demand-forecasting infrastructure Uber's own airport
  posts describe — that infrastructure is justified by an airport count and volume Spinr
  does not have (§2.5).
- **Any acceptance-rate-linked mechanism that gates going online, availability, or pay** —
  only ranking order may be affected, matching what already exists; CLAUDE.md's
  "not a driver-control platform" and the guardrail's "no penalty for being offline" both
  forbid extending this into an offline penalty of any kind.
- **A demand forecast or heatmap framed as an expectation or a quota.** The existing v1/v2
  heatmap (CS-4) and destination mode (CS-5) are correct today specifically because
  nothing downstream ties them to availability or offer ranking — any future positioning
  feature must keep that property.
- **Answering the surge-during-emergency question in code before it is answered in
  policy.** This is a legal question (§5), not an engineering one, and CLAUDE.md's surge
  cap (2.5×, "never suggest raising it without explicit business + legal review") applies
  with equal force to any emergency-specific carve-out.

---

## §5 Open human questions

1. **Surge behaviour during a declared provincial/municipal state of emergency**: does
   Saskatchewan or federal price-gouging/emergency-management law require Spinr to
   suspend or cap surge during a declared emergency (independent of the existing 2.5×
   auto-mode cap), the way some U.S. jurisdictions require of Uber? This session found no
   primary Saskatchewan or Canadian statute on point (`ride.guru`'s claim about Uber is a
   secondary source about a different jurisdiction) — needs a legal read, not an
   engineering guess (§2.5, §4).
2. **The acceptance-rate ranking ceiling**: is `0.1` (a 10× penalty) the intended, decided
   fairness ceiling, or an unreviewed engineering safety floor that happens to also work?
   Product/legal should decide the number before it is disclosed to drivers (§2.4).
3. **Is a driver appeal path needed for the ranking penalty once disclosed**, or does the
   existing general support contact suffice? (§3, rank 10)
4. **Which airport should pilot the FIFO queue first** — Regina or Saskatoon — and does
   either airport authority have its own ground-transportation queue rules Spinr would
   need to coordinate with (a question this session, with no local-authority access,
   could not check)? (§2.5)
5. **Should the fairness-audit methodology be published even if it finds nothing
   actionable**, as `03-benchmark.md` §6 already recommends, or only once Spinr has a
   result worth publishing? A "we checked and found no disparity at our scale" result is
   itself a defensible trust claim, but the founder should decide the publishing bar.
6. **Does the admin dashboard already chart `pickup_eta_error`**, or does rank 2 in §3
   need a small chart added, not just a review habit? Not checked this session (no
   `admin-dashboard/` read was in scope).

---

## §6 Deferred and not verified

- **External sources**: every claim in this file except the Pandey/Caliskan Chicago study
  (VERIFIED via `03-benchmark.md`'s own prior citation) rests on a WebSearch result
  snippet of the named URL, not a full page read (WebFetch was not attempted this
  session, following Session 3's precedent that the egress proxy blocks most primary-
  source domains) — every Uber/Lyft/Grab/DoorDash engineering-blog claim, the two
  additional fairness papers (arXiv 2407.20522, arXiv 2303.05698), the Eom & Toriello
  batching paper, the Lyft non-exclusive-dispatch paper, the OSRM-vs-Google comparison
  pages, and the SB 24-75/deactivation-lawsuit claims should all be re-read from a
  machine that can reach them before being quoted as settled fact in a decision memo.
- **Live state not checked**: `dispatch_direct_pool_enabled`'s live value;
  `max_simultaneous_offers`' live per-area values; whether `driver_heatmap_v2_enabled` or
  any per-area surge/heatmap override is on anywhere; the live `eta_sample` size per
  service area (needed before trusting the `eta_error` percentiles cited in §2.2); whether
  the admin dashboard already renders `pickup_eta_error` (§5 question 6).
- **Code not read this session, cited from `dispatch.md`/`driver-journey.md` instead**:
  the full `_offer_timeout_handler`/`resolve_driver_offer` RPC internals, the v2/v3
  batch-claim RPC (migrations 402/403/448/460+), `ride_complete.py`'s fare-finalization
  logic, `location.py`'s staleness handling, and every scenario `dispatch.md` §3.2/§3.3
  already marked UNKNOWN (driver GPS freeze, wrong-way-driver fee dispute, mid-trip stop
  re-pricing) — this session did not re-derive any of them and has nothing new to add.
- **Not designed this session**: the exact Redis data structure and cooldown rule for the
  airport queue's anti-phantom-queuing protection (§2.5) — flagged as a design detail for
  whoever builds rank 8, not resolved here.
- **No replay test was run** for any of this session's proposed algorithm changes
  (the ranking-ceiling change, the airport queue, the event-radius widening) — every
  number in §2 and §3 is a design target, not a validated result, per the session's own
  mandatory-replay-test guardrail.
- **Admin-dashboard rendering of any new fairness-audit or ETA-error chart** was not
  designed or screenshotted — `04-blueprint.md`/CLAUDE.md's visual-regression rules for
  `admin-dashboard` apply to any future artifact, not evaluated in this report-only
  session.
