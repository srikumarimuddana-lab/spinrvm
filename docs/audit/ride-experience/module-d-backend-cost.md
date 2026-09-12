# Module D — Backend + Cost Governance

**Audit:** Ride Experience & Cost-Value Industry Benchmark, `docs/audit/RIDE_EXPERIENCE_INDUSTRY_BENCHMARK_AUDIT_PROMPT.md` §10.4
**Date:** 2026-09-12
**Scope:** `backend/routes/rides/{estimates,_shared,booking}.py`, `backend/services/fare_service.py`,
`backend/utils/{route_distance,maps_budget,maps_eta}.py`, `backend/routes/maps_proxy.py`,
`backend/routes/rides/receipts.py`, `backend/utils/{email_receipt,receipt_pdf}.py`,
`backend/features.py` (`send_push_notification`), `backend/routes/rides/{matching,lifecycle}.py`
(notification trigger points), `backend/ai/tools_booking.py` (Maps usage only).
**Companion file:** `docs/audit/ride-experience/cost-inventory-table.md` — the standalone
cost-site table, produced alongside this report per §10.4's OUTPUT instruction.
**Report-only:** no source file was modified by this audit. Nothing here has been implemented.

---

## Summary table (feature area → maturity 1-5 → gap)

| Feature area | Maturity | Gap | One-line verdict |
|---|---|---|---|
| Fare-estimate Directions cost governance (`_shared.py:100-134`) | 2 — Workaround (works, but no governance) | **RED** | Highest-volume Directions call site in the app; no budget, no cache — the flagship finding |
| Booking-confirm safety-net Directions re-derive (`booking.py:815`) | 2 | **RED** | Same root cause as above, second call site |
| Driver ETA Distance Matrix fallback (`maps_eta.py`) | 3 — Functional | **YELLOW** | Frequency well-controlled (B3); cost/SKU tracking structurally absent |
| Live in-trip route/ETA Directions fallback (`route_distance.py`) | 4-5 — At parity / differentiated | GREEN | Budgeted, cached, geohash-grid keyed — the pattern the flagship finding should learn from |
| Places Autocomplete/Details/Geocode proxy (`maps_proxy.py`) | 4-5 | GREEN | Budgeted, cached where cacheable, circuit-breakered |
| AI booking tool Maps usage (`tools_booking.py`) | 4 | GREEN (minor YELLOW note) | Budgeted throughout; one uncached-but-low-volume Directions loop |
| Fare-estimate 3.5s worst-case wait (`_PRICING_ROUTE_WAIT_S`) | N/A — decided | GREEN (not re-litigated) | Confirmed still implemented exactly as documented; permanent exception per CLAUDE.md |
| Receipts (format/delivery, industry-parity only) | 4-5 | GREEN | HTML + PDF attachment + in-app JSON, itemized GST/PST, transactional outbox |
| Push notification delivery-reliability design | 3 | YELLOW | Retry queue + priority tiers are sound; per-message-only sends (no FCM batching) is a real but low-severity gap |
| Push notification defect status (C97) | N/A — defect, not parity | Tracked separately | OPEN; 4 of 6 recommendations shipped — see below, not re-diagnosed here |
| Real Google Maps/Firebase spend visibility (org-level) | 1 — Missing | RED (already tracked) | No GCP Billing Budgets integration; EXTENDS-E13 |

---

## 0. C97 status check (performed before writing any notification finding, per instruction)

Read `docs/audit/2026-09-10-driver-app-notification-delivery-audit.md` in full and
`ACTION_ITEMS.md`'s current C97 entry before writing anything below. Current status,
as of this audit (2026-09-12), quoting the tracker directly:

- **Status: OPEN.**
- **Shipped:** recommendation #2 (loud Firebase Admin SDK init failure + Sentry tagging),
  #3 (`spinr_push_send_total{outcome=...}` delivery-outcome metric), #4 (client-side
  fallback toast for unhandled foreground message types, plus a background/killed-state
  log-only fallback), and #6 (RNFirebase-side tap-routing for non-data-only message types,
  shipped 2026-09-11, explicitly untested on a real device per the user's own choice).
- **Still open:** #1 (ops check — confirm `FIREBASE_SERVICE_ACCOUNT_JSON` is actually valid
  on Fly/Railway; blocked on C99, no ops access from any Claude session) and #5 (iOS
  `UIBackgroundModes` confirmation; needs a real compiled iOS build not available here).

This module does not re-diagnose any of the above — it only adds the one angle Dimension 24
asks for that the 2026-09-10 audit did not: whether the *notification-sending architecture*
(not a specific bug) is at industry parity. See REC-D-03.

---

## 1. REC-D-01: Uncached, unbudgeted Directions call backing every fare estimate

- **As-is decision:** `backend/routes/rides/_shared.py:100-134` (`_fetch_directions_route`)
  calls the legacy Google Directions API directly via `httpx`, with **no** call to
  `maps_budget.record_call()` or `maps_budget.check_budget()`, and **no** Redis cache —
  confirmed by direct reading of the full function body (no import of `maps_budget` or
  `redis_client` anywhere in `_shared.py`). It is called from two sites:
  1. `estimates.py:300-307` inside `compute_ride_estimates` — the engine behind every
     `POST /rides/estimate` call, which fires on essentially every pin adjustment in the
     rider-app booking flow (`ride-options.tsx`), making this the single highest-volume
     Directions call site in the entire backend.
  2. `booking.py:815-822` — a safety-net re-derive when a booking arrives without a valid
     signed estimate token (expired token, non-app client, or a token that never carried a
     road distance because its own estimate call fell back to haversine).
  By contrast, every other Directions/Places/Geocode/Roads call site with real cost weight
  in this codebase (`maps_proxy.py`'s three endpoints, `route_distance.py`'s live-route
  fallback, `tools_booking.py`'s AI Maps calls) does call `record_call()`, and the live-route
  fallback additionally caches. This one does neither, on the app's busiest Directions path.
- **Industry technique:** Ride-share-scale routing/ETA systems commonly cache
  origin-destination route costs at a geohash/H3-cell granularity with Redis, sized so a
  cache hit returns in-memory rather than re-querying a routing graph or a paid API — one
  documented pattern uses H3 cell-pair keys (`route_cost:{h3_A}:{h3_B}`) with TTLs from
  minutes (traffic-sensitive ETA) up to 30 days (pure road-network distance, which rarely
  changes), reporting cache-hit ratios above 95% when paired with a free self-hosted router
  as the origin data source. Google's own published pricing for the exact SKU this call site
  uses confirms the unit cost this gap is exposed to: the legacy Directions API is billed at
  **$5.00 per 1,000 requests ($0.005/call)** — which independently cross-checks
  `maps_budget.py`'s own `_PRICE_USD["directions"] = 0.005` constant as accurate. (Sourced
  2026-09-12 — see Sources section at the end of this document; pricing figures decay, verify
  again before relying on this for a real budget decision.)
- **Verdict:** **RED**, maturity 2 (workaround — the call works and produces a correct
  price, but has none of the governance every comparable call site in this same codebase
  already has). This is a defect-scale HIGH finding independent of the maturity rating —
  Dimension 24's own severity guide names exactly this shape ("a high-volume paid API call
  site with no budget/circuit-breaker and no cache, on a path every user request touches") as
  HIGH.
- **Recommendation:** Two independent fixes, doable separately or together:
  1. **Budget accounting (do this first, it's nearly free):** add `await
     record_call("directions")` after every successful (and, per the existing
     `route_distance.py` pattern, every attempted) call in `_fetch_directions_route`, and gate
     the call behind `check_budget()` the same way `maps_proxy.py`'s `_ensure_budget()` does —
     on budget exhaustion, fall back to haversine (the code already has this fallback path;
     it just needs to be reachable from a budget-exceeded state too, not only a Directions
     failure). This alone closes the "invisible spend" half of the gap with no caching
     complexity and no risk to the anti-undercharge design intent, since a budget-exceeded
     fallback to haversine is the *existing*, already-accepted-risk behavior
     (`select_fare_distance`'s `"haversine_fallback"` basis), not a new one.
  2. **Caching, with a caveat specific to this call site (do not skip this caveat):**
     `route_distance.py`'s existing live-route cache pattern rounds the *origin* to a 3-decimal
     (~110m) grid but the *destination* to 5 decimals (~1m) — a deliberate asymmetry, because
     that cache serves a **moving driver's current position** (imprecise by nature, and
     repeated polls from nearby points should share a cache entry) routing to a **fixed
     destination** (which must stay precise). The fare-estimate call site is different: both
     endpoints are **fixed, rider-chosen pickup/dropoff points that directly determine the
     bill**. Rounding the pickup to a 110m grid here would mean two riders whose pins are
     ~100m apart could be billed on the same cached road distance — a small but real new
     source of billing imprecision that the live-route cache's asymmetry was specifically
     designed to avoid on its own destination side. **Recommendation: if this cache is added,
     key it at fine precision on BOTH endpoints (5-6 decimals, ~1-10m) rather than copying the
     110m grid verbatim, and keep the TTL short (30s, matching `_LIVE_ROUTE_CACHE_TTL_S` — road
     distance between two fixed points is stable for far longer than 30s, but a short TTL costs
     nothing given how repeat-quote bursts actually happen: a rider dragging one pin while the
     other stays fixed, within the same booking session).** This is orthogonal to, and does
     not weaken, the estimate-token fare-lock mechanism (`sign_estimate_token`/
     `resolve_booking_distance`) that already pins the *exact* quoted distance for the
     token-present booking path — caching only affects consistency across *repeated estimate
     calls before a token is issued*, never the quote-to-charge consistency, which the token
     already guarantees byte-for-byte regardless of any cache.
  3. Apply the same two fixes to `booking.py:815`'s safety-net call — it is the same
     underlying function, so fix #1 (budget) and the presence of fix #2's cache (if added)
     apply there automatically once `_fetch_directions_route` itself is patched.
- **Value to users:** None directly visible — riders see the same price either way. The
  value is entirely to Spinr (below); a caching fix could shave a few hundred ms off repeat
  quotes during a booking session, a minor UX polish on top of the cost fix.
- **Value to Spinr:** Closes the one gap in an otherwise-consistent cost-governance system —
  today a Directions outage/spend spike specifically on this call site would not trip the
  Redis daily-budget breaker at all, silently continuing to spend past the configured
  ceiling while every *other* Maps call site correctly stops. Caching (if added) reduces
  redundant spend on repeat quotes within one booking session at no accuracy cost, given the
  precision caveat above.
- **Cost:** Engineering effort **S** for the budget-accounting fix (a few lines, mirrors an
  existing pattern used four times elsewhere in this codebase); **S-M** for the caching fix
  if the precision caveat is respected (needs a fresh cache-key scheme, not a copy-paste of
  `route_distance.py`'s). Ongoing third-party spend delta: **negative** (reduces spend) for
  the budget fix (correctly stops runaway spend instead of silently exceeding the ceiling)
  and for the cache fix (fewer redundant Directions calls); the specific API/SKU is Google
  Directions API (legacy), $0.005/call.
- **De-dup tag:** `NEW`. Checked against `ACTION_ITEMS.md` B5 (2026-07-28): B5 explicitly
  verified `record_call("directions")` was **already present** at `route_distance.py:734`
  and closed that specific check as "not a gap" — but B5's check never examined
  `_shared.py:100-134`'s separate `_fetch_directions_route`, a different function with a
  different (and larger) call volume. This is not a re-discovery of what B5 already checked;
  it is the gap B5's own scope did not reach.
- **Priority:** **P1** (high-volume, no-cost-control gap on a live money path; not P0 only
  because the current failure mode is "spend keeps flowing," not an active outage or
  incorrect fare — no rider is being mischarged today, the exposure is purely on the cost
  side).

---

## 2. REC-D-02: Google Distance Matrix fallback has no budget/SKU tracking at all

- **As-is decision:** `backend/utils/maps_eta.py`'s `get_ride_eta_seconds` (driver→pickup
  ETA, called on the hot GPS-ping path during `driver_assigned`/`driver_accepted`/
  `driver_arrived`) and `batch_get_etas` both fall back to Google's Distance Matrix API when
  self-hosted OSRM is unavailable — confirmed via direct reading: neither function imports
  `maps_budget`, and grepping the whole file for `maps_budget`/`record_call`/`check_budget`
  returns zero matches. More significantly, `"distance_matrix"` **is not a member of
  `maps_budget.py`'s own `Sku` Literal type or `_PRICE_USD` dict at all** — so even if a call
  site tried to call `record_call("distance_matrix")` today, it would fail a type check (or,
  if the type check were bypassed, silently miscount against no bucket, exactly the same
  failure mode `ACTION_ITEMS.md` B5 already found and fixed once for `"places_text_search"`).
  Call frequency is already well-mitigated by a prior fix: `ACTION_ITEMS.md` B3
  (2026-07-28) added a 15s Redis cache plus a >100m movement-gate reuse (up to 120s) so a
  stationary or slow-moving driver does not re-trigger routing on every GPS ping — but that
  fix addressed call *frequency*, not call *cost governance*. If OSRM ever degrades
  fleet-wide (the exact kind of infra failure the daily-budget breaker exists to catch),
  every active ride's ETA refresh would fall through to this ungoverned, unmetered path with
  the circuit breaker structurally blind to it.
- **Industry technique:** The same caching discipline this codebase already applies
  elsewhere (self-hosted routing as the free-tier default, metered API strictly as fallback)
  is the standard shape; the gap here isn't the caching pattern itself — B3 already got that
  right — it's that a fallback provider was wired in without also being registered in the
  cost-governance layer that every *other* fallback-to-Google path in this codebase already
  uses.
- **Verdict:** **YELLOW** leaning RED on the governance axis specifically (frequency
  mitigation is GREEN/maturity 4 thanks to B3; SKU/budget visibility is maturity 1 — missing
  entirely). Reported as one finding per Dimension 24's "report both scales" guidance: a
  maturity-4 frequency control sitting on top of a maturity-1 cost-visibility gap.
- **Recommendation:** Add `"distance_matrix"` as a real SKU to `maps_budget.py`'s `Sku`
  Literal and `_PRICE_USD` dict (current published Distance Matrix pricing should be
  re-verified at implementation time — pricing decays), then call `record_call(
  "distance_matrix")` after each live call in `get_ride_eta_seconds` and `batch_get_etas`,
  gated by the same `check_budget()` pattern used elsewhere. On budget exhaustion, fall back
  to the haversine estimate the function already computes as its own last-resort path — no
  new fallback logic needed, just an earlier exit into the one that exists.
- **Value to users:** None directly — this is a pure cost/observability fix, no user-facing
  behavior changes.
- **Value to Spinr:** Closes the last remaining blind spot in the Maps cost-governance
  system for a call site that, unlike the fare-estimate one above, would fire at very high
  frequency (per-GPS-ping, per-active-ride) during exactly the kind of OSRM outage that would
  also be a stressful ops moment — the two failures (OSRM down + spend blind) would compound.
- **Cost:** Engineering effort **S** (one new SKU entry + two `record_call`/`check_budget`
  call sites, same pattern already used four times in this codebase). Ongoing spend delta:
  none by itself (doesn't change call frequency, which B3 already optimized) — it only makes
  existing, already-happening spend visible to the breaker and, on a real budget breach,
  correctly forces the haversine fallback instead of silently continuing to spend.
- **De-dup tag:** `EXTENDS-B3` (same call site B3 already touched — B3 solved frequency/
  caching; this closes a different, adjacent gap B3's own scope did not address: cost
  visibility and circuit-breaker coverage for the same fallback path).
- **Priority:** **P2** (real gap, but lower urgency than REC-D-01 — this only manifests
  during an OSRM outage, which is already a rarer condition than "every fare estimate,"
  and B3's frequency mitigation already bounds the worst case to a few calls per minute per
  active ride rather than per-ping).

---

## 3. REC-D-03: Push notification sends are per-message only — no FCM batching

- **As-is decision:** `backend/features.py:1324-1450` (`_deliver_push_now`) calls
  `messaging.send(message)` (Firebase Admin SDK's single-message API) once per recipient —
  confirmed no use of `messaging.send_each`/`send_multicast`/`send_each_for_multicast`
  anywhere in `features.py`. The clearest manifestation is the batch-offer dispatch loop
  (`backend/routes/rides/matching.py:1467-1476`): for each candidate driver in a batch offer,
  the code spawns one independent `send_push_notification()` call
  (`_deps.spawn(_deps.send_push_notification(...))`) inside the per-driver loop — for N
  candidate drivers, that's N separate FCM API calls (mitigated from serializing the
  per-driver offer loop by `_deps.spawn`, but still N HTTP round-trips to Firebase rather
  than one). FCM and Expo push are **free at Spinr's message volume** (no per-call charge
  applies to either service in this class of usage), so this is not a dollar-cost finding —
  it's a throughput/architecture one, included here because Dimension 24's checklist
  explicitly asks for "delivery-reliability technique named and compared against a known
  industry pattern" for notifications, independent of whether the SKU is billed.
- **Industry technique:** Firebase Admin SDK's `send_each`/`send_each_for_multicast`
  (up to 500 messages per call) exists specifically so a fleet-wide fan-out (e.g., a batch
  ride offer to N nearby drivers) can be sent as one API call with per-message results
  returned individually, rather than N independent round-trips. This is the documented
  replacement for the now-deprecated `send_multicast`, and is the pattern typically used by
  any service dispatching the same class of message to multiple recipients at once.
- **Verdict:** **YELLOW**, maturity 3 (functional — every offer is correctly delivered
  today, per-driver, with a working retry queue behind it — but not the batched-send shape
  a category-leading dispatch system would use for a fan-out of this kind). This does not
  affect correctness or the C97 defect findings above (which are about display/routing on
  the client, not about how many server-side HTTP calls are made) — it is a separate,
  narrower architectural observation.
- **Recommendation:** Batch the per-offer dispatch push calls in `matching.py`'s batch-offer
  loop via `send_each` (grouping the driver tokens gathered in that loop into chunks of up to
  500), instead of one `_deps.spawn(...)` per driver. Scope this narrowly to the
  batch-dispatch call site specifically — the other ~13 `send_push_notification` call sites
  across `chat.py`, `safety.py`, `lifecycle.py`, `cancellation.py`, `lost_found.py`,
  `booking.py` each send to a single recipient per event and have no fan-out to batch.
- **Value to users:** Marginal — at typical Saskatchewan-market batch-offer sizes (a handful
  of candidate drivers, not hundreds), the latency difference between N sequential
  fire-and-forget calls and one batched call is unlikely to be perceptible to any individual
  driver. The value grows only if/when the candidate pool per offer grows materially.
  Documenting the gap now, rather than after a scale-driven latency complaint, is the
  proactive value.
- **Value to Spinr:** Fewer outbound HTTP round-trips under load; a small resilience
  improvement (Firebase's own batched-send API has documented per-message granular results,
  which line up more naturally with `_record_push_outcome`'s existing per-outcome metric
  than N independent `try/except` blocks do).
- **Cost:** Engineering effort **S-M** (rewrite one call site's loop into a batch call plus
  its own error-per-message handling; the ~13 other single-recipient call sites are
  unaffected). No third-party spend delta — FCM/Expo are free regardless of batching at this
  message volume.
- **De-dup tag:** `NEW`. Not a duplicate of C97 (which is entirely about client-side
  display/routing correctness for already-delivered messages, and backend delivery-outcome
  observability) — this finding is specifically about the *shape* of the server-side send
  call, a question C97's two research passes did not examine.
- **Priority:** **P3** (real, but low-urgency architectural observation at current scale;
  revisit if/when a service area's typical candidate-pool-per-offer size grows).

---

## 4. REC-D-04: Fare-estimate 3.5s worst-case wait — re-confirmed, not re-litigated

- **As-is decision:** `backend/routes/rides/estimates.py:42-62` defines
  `_PRICING_ROUTE_WAIT_S = DIRECTIONS_TIMEOUT_S + 0.5` (currently `3.0 + 0.5 = 3.5s`), and
  the pricing loop at `estimates.py:453-461` calls `asyncio.wait({route_task},
  timeout=_PRICING_ROUTE_WAIT_S)` before falling back to haversine. `DIRECTIONS_TIMEOUT_S =
  3.0` is defined in `_shared.py:97`. Both constants and the invariant linking them
  (`_PRICING_ROUTE_WAIT_S` must exceed `DIRECTIONS_TIMEOUT_S`, never the reverse) are present
  and match CLAUDE.md's Performance SLAs section description exactly, including the
  documented 2026-07-29 change from 1.5s→3.0s and the incident it was raised to prevent
  ($30.92→$39.44 re-price between quote and confirm). **This is a re-confirmation only** —
  per this module's explicit instructions and CLAUDE.md's own "already decided, permanent
  exception" framing, no change is recommended and none was investigated further.
- **Industry technique:** N/A — not re-researched, per instruction.
- **Verdict:** **GREEN** (confirmed implemented as documented; not re-scored on the
  maturity/gap scale since this is a deliberate trade-off already decided, not an
  open competitive question).
- **Recommendation:** No change — already correctly implemented and already decided
  (2026-08-21, per `docs/audit/2026-08-19-decision-writeups.md` §8, cited in CLAUDE.md).
- **Value to users / Value to Spinr:** N/A — not re-argued here.
- **Cost:** N/A.
- **De-dup tag:** `EXTENDS-<CLAUDE.md's own documented decision>` (not an ACTION_ITEMS ID —
  this is a CLAUDE.md-level permanent exception, cited verbatim rather than re-opened).
- **Priority:** N/A — no action.

---

## 5. REC-D-05: Receipts — industry-parity check (format/delivery only)

- **As-is decision:** Confirmed via direct reading of `routes/rides/receipts.py`,
  `utils/email_receipt.py`, and `utils/receipt_pdf.py`: a rider receipt is available as (a)
  in-app JSON via `GET /{ride_id}/receipt`, (b) an HTML branded email
  (`utils/email_receipt.py`, AWS SES primary / Resend guardrail), and (c) a PDF attachment
  (`utils/receipt_pdf.py`, fpdf2, Decimal-only money math, matches the branded layout). Both
  the email and PDF renderers itemize GST/PST as separate lines and split the surge
  multiplier into a real dollar line (not just a text footnote) — the surge-disclosure fix
  cited in `receipt_pdf.py`'s own header comment (ranked #26 / audit N14, 2026-08-19).
  Delivery is triggered via a transactional outbox on ride completion with a direct-send
  fallback (per the seed findings this module was instructed not to re-derive). Per this
  module's scope, line-item correctness (GST/PST accuracy) was **not** re-verified here —
  that is Dimension 08/12's job, already covered elsewhere.
- **Industry technique:** Category-leading ride-share apps typically offer an itemized
  in-app receipt plus an emailed copy; a downloadable PDF attachment is common but not
  universal (some apps link to a web receipt instead of attaching a PDF). Spinr's three-format
  coverage (in-app + HTML email + PDF attachment) matches or slightly exceeds this common
  baseline.
- **Verdict:** **GREEN**, maturity 4-5. This is a case where the existing implementation is
  already at or above parity — reported plainly, per the §7 template's own instruction that
  a GREEN finding is a valid, expected outcome and not something to bury.
- **Recommendation:** No change — already at parity on format/delivery. (Not audited here:
  GST/PST correctness, already covered by other dimensions per this module's scope note.)
- **Value to users:** Riders already get a complete, itemized, multi-format receipt
  matching category-leader expectations — no gap to close.
- **Value to Spinr:** Transparency-as-differentiator (per CLAUDE.md's "What Spinr Is NOT" —
  "not a hidden-fee operator") is already reflected in the receipt implementation, not just
  the policy language.
- **Cost:** N/A — no change recommended.
- **De-dup tag:** `NEW` (first time this specific format/delivery angle has been checked
  against an industry baseline — the GST/PST correctness angle it deliberately does not
  duplicate is separately covered).
- **Priority:** N/A — no action (P4/informational at most, if a priority must be assigned).

---

## 6. REC-D-06: Notification delivery-reliability design vs. industry pattern

- **As-is decision:** Centralized send function (`features.py::send_push_notification`,
  ~15+ call sites funnel through it), priority tiers (`normal`/`dispatch`/`safety`/
  `account`) that determine retry eligibility, and a dedicated `push_retry_queue` table
  drained every 30s with exponential backoff capped at 5 attempts
  (`backend/utils/push_retry.py`) — all confirmed present by the 2026-09-10 notification
  audit and not re-verified line-by-line here (per this module's instruction to extend, not
  duplicate, that audit). As of this audit (2026-09-12), per the C97 status check in §0
  above, the observability gap that audit named (no delivery-outcome metric) has since been
  closed (`spinr_push_send_total{outcome=...}`, shipped 2026-09-11) and the client-side
  silent-drop gap for non-ride-offer types has a fallback toast (shipped 2026-09-09/11).
- **Industry technique:** Retry-with-backoff plus priority-tiered delivery is a standard
  reliability pattern; delivery *receipts* (server-confirmed "the device acknowledged this
  push") are less universal and typically reserved for the highest-stakes message type (here,
  that would be `new_ride_assignment`, which already gets bespoke, heavily-tested treatment
  per the 2026-09-10 audit's own assessment).
- **Verdict:** **YELLOW**, maturity 3. The retry/priority/metric architecture is sound and,
  as of this audit, the two biggest gaps the prior audit found are shipped fixes — but two
  items remain genuinely open per the tracker (§0): the ops check on the Firebase credential
  (Fly/Railway) and the iOS background-mode confirmation. Neither is a *design* gap (Dimension
  24's own scoping note: "any open, unresolved 'notification not received' defect is treated
  as a Dimension 10/13 finding, not re-filed here — this dimension only asks whether the
  design, not a specific bug, is at parity") — the design itself is reasonably close to
  parity; what's open is verification, not architecture.
- **Recommendation:** No new recommendation from this module — the 2026-09-10 audit's own
  recommendation list (rec #1: ops check; rec #5: iOS build confirmation) already covers the
  two open items, and this module's job per its own scope note is only to add the
  industry-benchmark angle, not re-propose fixes for a defect already tracked. One addition
  this module does contribute: REC-D-03 above (FCM batching) is a genuinely new angle the
  2026-09-10 audit did not examine, since that audit was scoped to delivery/display
  correctness, not send-architecture efficiency.
- **Value to users:** N/A beyond what C97's existing recommendations already state.
- **Value to Spinr:** N/A beyond what C97's existing recommendations already state.
- **Cost:** N/A — no new fix proposed here.
- **De-dup tag:** `EXTENDS-C97`.
- **Priority:** Inherits C97's own priority; not re-scored here.

---

## 7. REC-D-07: Cost-accounting table + real spend visibility gap

- **As-is decision:** See `cost-inventory-table.md` for the full 16-row table. Summary: 8
  Google Maps Platform call sites are correctly budgeted via `maps_budget.py`'s Redis
  circuit breaker; 2 (the flagship REC-D-01 finding) are not; 1 additional call
  (`maps_eta.py`'s Distance Matrix fallback, REC-D-02) uses a provider that isn't even
  registered as a trackable SKU; 2 Roads API call sites (`route_distance.py`'s
  `snapToRoads`/`nearestRoads`) have no SKU tracking either, but at much lower, inherently
  bounded volume (once per ride completion / once per pickup-pin-in-a-building correction).
  Every one of these budgets is a **self-imposed Redis-tracked estimate**, not a read of
  actual Google Cloud billing — `maps_budget.py`'s own module docstring says as much
  ("Estimates daily USD spend by multiplying counts by per-call pricing constants"). No real
  dollar figure from GCP Billing was available to this audit (no MCP access this session,
  per the pre-flight checklist's stated boundary).
- **Industry technique:** N/A for this specific finding — it's an internal governance
  observation, not a competitive-technique question.
- **Verdict:** **RED** at the org level (maturity 1 — a real-spend visibility gap, not
  merely a self-imposed-ceiling one), but this is not a new discovery — `ACTION_ITEMS.md`
  E13 (2026-09-03, `billing-usage-monitor.yml`) already names exactly this gap explicitly:
  Stripe/Twilio balance monitoring is live, Google Maps/Firebase real-dollar spend has no
  equivalent because GCP Billing Budgets API integration doesn't exist, and E13's own text
  states this was deliberately left as an open, named gap rather than silently assumed
  covered.
- **Recommendation:** No new recommendation beyond what E13 already states — this audit's
  contribution is the cost-inventory table itself (`cost-inventory-table.md`), which is the
  SKU-level input a future GCP Billing Budgets integration would need, exactly as this
  audit's own rationale (§3.2 of the parent prompt) anticipated.
- **Value to users:** None directly.
- **Value to Spinr:** Gives whoever eventually builds the GCP Billing Budgets integration
  (E13's stated next step) a ready-made starting inventory instead of having to re-derive
  the call-site list from scratch.
- **Cost:** Not estimated here — GCP Billing Budgets API integration itself is E13's scope,
  not this audit's.
- **De-dup tag:** `EXTENDS-E13`.
- **Priority:** Inherits E13's own priority; not re-scored here.

---

## What was NOT verified (this module's own boundary)

- **No real production call-volume data.** Every "volume driver" in the cost table is a
  qualitative description of what triggers a call, not a measured count. No Sentry/Stripe/
  GCP Billing MCP access was available this session (Sentry and Stripe both require OAuth
  not completed here; GCP Billing Budgets integration doesn't exist per E13) — a rough
  daily-ride-volume figure was searched for in-repo (no dashboard/metrics reference with a
  concrete number was found) and is stated here as genuinely unquantified, per the parent
  prompt's own instruction for this exact case, rather than estimated.
- **No live Google Maps Platform pricing confirmed beyond a single WebSearch pass**
  (2026-09-12) cross-checking the legacy Directions API's $5.00/1,000-request rate against
  `maps_budget.py`'s own constant. Distance Matrix's current exact per-element rate was not
  independently re-verified against a fresh search — the REC-D-02 recommendation says to
  re-verify current pricing at implementation time rather than trusting this audit's figure.
- **Twilio SMS cost sites** were not audited — out of this module's named file scope
  (`.claude/context/domain-payments.md`/OTP conventions live elsewhere) — table row #13 is
  marked NOT AUDITED rather than silently omitted.
- **Stripe transaction-level cost/fee structure** was not deeply audited — out of this
  module's named scope; Stripe's own dashboard remains the authoritative spend-visibility
  tool for that surface, not this table.
- **No code was changed, and no fix was applied or tested.** Every recommendation above is
  a proposal for a future, separately-approved implementation PR, which per CLAUDE.md's
  pre-merge gates would need its own Change Impact & Risk Log entry (this touches a
  live-tested money-adjacent surface — fare estimation) before merging, not just this audit's
  say-so.
- **GST/PST line-item correctness on receipts** was deliberately not re-verified — explicitly
  out of this module's scope per the parent prompt's own instruction (Dimension 08/12's job).

---

## Sources (live research, dated)

- [Distance and ETA Calculation Caching: Optimizing Real-Time Performance in Ride-Hailing Apps](https://medium.com/@helal.hamed/distance-and-eta-calculation-caching-optimizing-real-time-performance-in-ride-hailing-apps-779da17529fc) — H3 geohash-cell route-cost caching pattern, TTL strategy, cache-hit-ratio claims. Retrieved 2026-09-12.
- [Google Maps API Pricing: 2026 Cost Breakdown - StoreRocket](https://storerocket.io/learn/google-maps-api-pricing) — legacy Directions API pricing ($5.00/1,000 requests). Retrieved 2026-09-12.
- [The true cost of the Google Maps API and how Radar compares in 2026](https://radar.com/blog/google-maps-api-cost) — general 2026 Google Maps Platform pricing context. Retrieved 2026-09-12.

Pricing and technique claims decay — re-verify before making a real budget or architecture
decision on their basis, per this document's own repeated caveat.

===MODULE-D-COMPLETE===
