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
| CS-1 | Matching shape | Dispatch is **per-ride, non-exclusive broadcast to the top N candidates**, not global bipartite assignment across concurrent open rides. `match_driver_to_ride(ride_id, ...)` (`routes/rides/matching.py:251`) is called once per ride; `rank_by_eta_with_acceptance()` (`services/dispatch_service.py:63-78`) sorts the candidate pool by `effective_eta = eta_seconds / acceptance_rate`, then the claim loop offers to the first `max_offers` (default 3, area/`app_settings`-configurable, hard-capped at 10 — `dispatch_service.py:66,461-465`) simultaneously; whichever driver accepts first wins (CAS-guarded, `dispatch.md` §1/§3). No cross-ride batching window exists anywhere in the codebase (grepped `batch_window`/`dispatch_batch`/`batching_window` across `backend/`: the only hits are the unrelated Sunday-payout batch window in `utils/auto_payout.py`). | VERIFIED | This is the direct answer to research question 1's "what does Spinr do today" — it is closer to the industry term **non-exclusive dispatch (NED)** than to either pure single-driver exclusive dispatch or a periodic Hungarian-algorithm batch match. §2.1 below evaluates whether a batching window on top of this would pay off at Spinr's scale. |
| CS-2 | ETA error is already a measured, stored metric | `admin_efficiency_metrics` (Postgres RPC, `migrations/352_efficiency_and_financial_fns.sql`) computes `eta_error = (ride_started_at − responded_at) − eta_seconds` (the **accepted offer's** promised `eta_seconds`), exposed as `eta_error_p50_secs`/`eta_error_p95_secs`/`eta_on_time_pct`/`eta_sample` at `GET /api/admin/analytics/efficiency` (`routes/admin/analytics.py:1362-1424`). The migration's own comment is explicit that this spans **acceptance-to-trip-start**, not a pure drive-time window, because `rides` has no arrival timestamp column — it is named "assignment to trip start" in the API specifically to avoid overclaiming precision. | VERIFIED | Directly answers research question 2's "how big is the error today, from code and stored metrics" — Spinr does not need to build ETA-error measurement from scratch, only to read what already exists (§2.2). Whether the live `eta_sample` is large enough per service area to trust the percentile was **not checked this session** (no production DB read) — see §6. |
| CS-3 | ETA computation chain, and a scope boundary inside it | `utils/maps_eta.py`: OSRM first (self-hosted/public, free) → Google Distance Matrix (traffic-aware, metered) → haversine at a fixed 30 km/h (`_FALLBACK_SPEED_KMH`). 15 s Redis cache; a "movement gate" (`_ETA_MOVE_THRESHOLD_M = 100`) reuses the last computed ETA if the driver has moved under 100 m, capped at 120 s reuse even for a stationary driver. **Scope note, not previously flagged**: the module's own docstring states this chain is called **only** for the pre-pickup phases (`driver_assigned`, `driver_accepted`, `driver_arrived`); during `in_progress` "the rider app computes ETA client-side via haversine so we do not re-call Maps on every GPS ping for the full trip duration." | VERIFIED | The `eta_error` metric in CS-2 measures the pre-pickup window this chain serves, so the two are aligned — but it means Spinr has **no measured accuracy figure at all for the in-trip (to-dropoff) ETA the rider sees**, since that number is a client-side haversine estimate the backend never records or compares to actual arrival. This is a real, previously-unflagged gap for a rider-experience "ETA quality" question, distinct from the well-measured pickup ETA. |
| CS-4 | Supply-positioning infrastructure is already substantially built | Three cooperating pieces, none previously catalogued together by an audit lane: (1) `services/h3_heatmap.py` — aggregates ride pickups into H3 cells at a fixed resolution, drops any cell under a k-anonymity floor (`k_floor`, default enforced ≥3) before it ever reaches a client — a real PIPEDA control, not a bolt-on; (2) `services/demand_tiles.py` — rasterises the same suppressed aggregate into transparent XYZ tiles with a Gaussian kernel-density spread (not per-tile-normalised alpha compositing, which the module's docstring explains would make the same colour mean different things on adjacent tiles); (3) `GET /demand-heatmap` (`routes/drivers/profile.py:471-530`) — driver-facing endpoint, rate-limited 20/min with a 30 s poll floor, serving a v1 `points:[[lat,lng,weight]]` 7-day decayed aggregate always, and a v2 (`driver_heatmap_v2_enabled`, default **false**, `schemas.py:731-750`) that adds `cells`/`surge`/`forecast` layers for a "next-6h demand timeline." | VERIFIED | This is a large fraction of research question 3 ("supply positioning: heatmaps and forecasts... without control-of-work pressure") **already shipped**, not a gap. It is informational-only by construction (a driver reads it or ignores it; nothing in the endpoint or its callers ties heatmap data to availability, offer ranking, or any quota) — a genuine example of the guardrail already being respected. §2.3 evaluates only the v2 rollout and the forecast layer's data quality, not whether to build the base capability, which exists. |
| CS-5 | Destination-mode filtering is a second, independent positioning primitive | `is_destination_mode_active()` / `_ride_brings_driver_closer_to_destination()` (`dispatch_service.py:104-165`) — a driver who has opted into "heading home" is only offered rides whose dropoff is at least 5% closer to their stated destination; the gate fails open (no filtering) on any missing/expired/unparseable state so a driver never goes invisible from a bug. Auto-expires after 2 hours (`DESTINATION_MODE_TTL`). | VERIFIED | A working example of "let a driver shape their own supply position without penalty" — the correct opposite of a control-of-work mechanic. Cited here because it belongs in the same "what already works" bucket as CS-4, and because its expiry/fail-open pattern is the template §2.3's first step should copy for any new positioning feature. |
| CS-6 | Airport/event/winter-storm modes: absence confirmed, not a pre-existing gap statement copied from another lane | Grepped `airport`, `event_mode`, `winter_mode`, `storm`, `weather_mode`, `surge_event` across `backend/**/*.py` (excluding tests). The only "airport" hits are `is_airport`/`airport_fee` — a flat **pricing surcharge** on a ride (`services/fare_service.py:184-350`, `schemas.py:818-819`), not a dispatch mechanism. There is no virtual queue, no widened search radius, no candidate-pool change, and no surge-behaviour change tied to an airport, a declared event, or a weather condition anywhere in the dispatch or surge code. `utils/surge_engine.py`'s only per-area override is the existing `surge_disabled`/`surge_source` toggle (`tests/test_surge_engine.py`, `tests/test_routes_fares_coverage.py`) — a manual admin flag, not an automatic emergency response. | VERIFIED (absence, two spellings each) | Directly answers research question 5: today "airport mode" is a fee, not an operational mode. §2.5 below designs the smallest additions for Regina/Saskatoon's two airports and for a declared winter storm, scoped to Spinr's actual city count rather than an Uber-scale queue system. |
| CS-7 | Surge granularity as a fairness-relevant fact, re-confirmed by direct read | `surge_engine.py`'s docstring and structure confirm `03-benchmark.md` §6's claim first-hand: surge is computed and written **per service area** (`service_areas.surge_multiplier`), on a 2-minute tick, not per micro-zone/geohash. | VERIFIED (re-confirms `03-benchmark.md` §6, not independently re-derived there) | Directly feeds §2.6's fairness-audit design — coarser granularity is easier to audit (fewer, larger units) but also structurally cannot price-discriminate at the block level the Chicago study measured, which is worth stating as a design property when the audit is published, not just a limitation. |
| CS-8 | Acceptance-rate ranking penalty, re-confirmed with the exact floor value | `rank_by_eta_with_acceptance()` floors `acceptance_rate` at `0.1` before dividing (`dispatch_service.py:74`), so the **worst-case penalty a driver's own history can apply to themselves is a 10× effective-ETA multiplier** — re-confirming DRIVER-004's mechanism with the concrete bound, which no prior finding card stated numerically. | VERIFIED | Used directly in §2.4's fairness card — a 10× ceiling is a specific, checkable number a disclosure or an appeal policy can reference, rather than "a large penalty." |

