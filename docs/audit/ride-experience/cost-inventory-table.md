# Cost Inventory Table — Ride Experience Paid External Calls

**Produced by:** Module D — Backend + Cost Governance (Ride Experience Industry Benchmark
Audit, 2026-09-12). Standalone table for Module E to consume directly — see
`module-d-backend-cost.md` for prose findings and the full §7 recommendation entries.

**Method:** code-derived call-site analysis only. No Sentry/Stripe/GCP Billing MCP access
was available this session (both require OAuth not completed here; GCP Billing Budgets
integration doesn't exist yet per `ACTION_ITEMS.md` E13) — every "volume driver" below is
qualitative (what triggers the call), not a real production count. Do not read any number
in this table as a measured dollar figure; `maps_budget.py`'s `_PRICE_USD` constants are
self-declared engineering estimates, cross-checked against a 2026-09-12 WebSearch of
Google's public pricing page (see Sources in the module report) — they matched
($0.005/call for legacy Directions = $5.00/1,000 requests, confirmed).

## Every paid external call site touching the ride experience

| # | API / SKU | File:line | Volume driver | Metered (self-imposed budget)? | Cached? | Circuit-breaker? | Severity |
|---|---|---|---|---|---|---|---|
| 1 | Google Directions (legacy, `$0.005`/call) | `backend/routes/rides/_shared.py:100-134` (`_fetch_directions_route`) | Every `/rides/estimate` call (fires on every pin drag / re-quote in the rider-app booking flow) **plus** every booking confirm that lacks a valid estimate token (`booking.py:815`) — the highest-volume Directions call site in the app | **NO** — no `record_call()`/`check_budget()` anywhere in this function or its two call sites | **NO** — no Redis cache; identical pickup/dropoff pairs re-fetch every time | **NO** — bypasses `maps_budget.py`'s daily-spend breaker entirely | **HIGH** |
| 2 | Google Directions (legacy) | `backend/routes/rides/booking.py:815` (safety-net re-derive when no valid estimate token) | Every booking confirm where the estimate token is absent/expired (older client, direct API caller, or a token that expired between quote and confirm) | **NO** — same uncached/unbudgeted `_fetch_directions_route`, called a second time from here | **NO** | **NO** | **HIGH** (same root cause as #1 — one code fix closes both) |
| 3 | Google Distance Matrix (`~$0.005`/element, traffic-aware SKU) | `backend/utils/maps_eta.py` (`get_ride_eta_seconds` fallback path, `batch_get_etas`) | Driver→pickup ETA on every GPS ping during `driver_assigned`/`driver_accepted`/`driver_arrived`, only when OSRM is unavailable/misconfigured | **NO** — `"distance_matrix"` is not even a SKU in `maps_budget.py`'s `Sku` Literal or `_PRICE_USD` dict; this call type is structurally invisible to the daily-spend breaker | **PARTIAL** — Redis 15s TTL cache + a >100m movement-gate reuse (`_reusable_last_eta`, up to 120s) reduce call *frequency*, but do not gate call *cost visibility* | **NO** | **HIGH** for the missing budget/circuit-breaker (frequency is already well-mitigated by B3's 2026-07-28 fix — see finding REC-D-02) |
| 4 | Google Directions (legacy) | `backend/utils/route_distance.py:712-775` (`_compute_route_via_google`, live in-trip route/ETA fallback) | Rider/driver live-route line refresh when self-hosted OSRM `/route` is down | **YES** — `check_budget()` + `record_call("directions")` | **YES** — Redis, 110m-grid origin / ~1m-precision destination key, 30s TTL | Fails closed to "no update" (keeps client's last saved line) when budget exceeded | **PASS** — reference implementation for #1/#2 to copy, with one caveat (see REC-D-01) |
| 5 | Google Places API (New) — Autocomplete | `backend/routes/maps_proxy.py:98-164` | Rider destination-search keystrokes (`search-destination.tsx`) | **YES** — `check_budget()` + `record_call("autocomplete")` | N/A (live typeahead, not cacheable) | **YES** — `_ensure_budget()` raises 503 | **PASS** |
| 6 | Google Places API (New) — Details (Essentials) | `backend/routes/maps_proxy.py:167-201` | Rider selects an autocomplete prediction | **YES** — `record_call("details")` + session-token reconciliation (`close_autocomplete_session`) | N/A (one-shot) | **YES** | **PASS** |
| 7 | Google Geocoding (reverse) | `backend/routes/maps_proxy.py:204-258` | Rider drops a map pin (`confirm-pickup.tsx`) | **YES** — `record_call("geocode")` | **YES** — Redis, 24h TTL, ~11m grid | **YES** — `_ensure_budget()` | **PASS** |
| 8 | Google Directions (legacy) | `backend/ai/tools_booking.py:502-513` (`route()`, candidate scoring — "Directions calls run concurrently" per code comment, ~3 per lookup) | AI-assistant natural-language booking, `find_place`/`get_fare_quote` tool calls | **YES** — `record_call("directions")`, gated by `check_budget()` at `:563` | **NO** — no dedup cache for repeated candidate-scoring calls within/across AI sessions | **YES** (budget-gated) | **LOW** (budgeted; uncached, but this channel's volume is a small fraction of the rider-app estimate path — see REC-D-07) |
| 9 | Google Geocoding (forward) | `backend/ai/tools_booking.py:392-405` | AI-assistant place-name → coordinates resolution | **YES** — `record_call("geocode")` (both the city-scoped and fallback branches) | **NO** | **YES** (budget-gated) | LOW |
| 10 | Google Places API (New) — Text Search Pro | `backend/ai/tools_booking.py:421-436` | AI-assistant named-place lookup | **YES** — `record_call("text_search_new")` (added 2026-07-28, `ACTION_ITEMS.md` B5 — previously miscounted as an unrecognized string) | N/A | **YES** | PASS (fixed by B5) |
| 11 | Google Roads API — `snapToRoads` | `backend/utils/route_distance.py:270-317` (`_compute_via_google_roads`) | Post-trip road-snap for billed distance when self-hosted OSRM `/match` is unavailable | N/A — not in `maps_budget.py`'s SKU set at all (billing path, not the daily proxy breaker) | N/A (one-shot per ride completion) | **NO** explicit breaker, but call volume is inherently capped at 1/ride-completion when OSRM is down | LOW — see REC-D-02 note; same class of gap as #3 but far lower volume (once per ride vs. once per GPS ping) |
| 12 | Google Roads API — `nearestRoads` | `backend/utils/route_distance.py:778-826` (`snap_to_road`), used for pickup-pin correction | Rider drops a pickup pin inside a building/lot | Same as #11 — no SKU/budget tracking | N/A | NO | LOW |
| 13 | Twilio SMS | Not directly in this module's file scope (see `.claude/context/domain-payments.md`/OTP conventions) — out of this pass's read list | OTP verification | Not verified this pass | Not verified this pass | Not verified this pass | NOT AUDITED — outside Module D's named scope files; flag for Module E if a Twilio-specific pass is wanted |
| 14 | FCM (Firebase Cloud Messaging) | `backend/features.py:1324-1450` (`_deliver_push_now`) | Every dispatch offer (1 call per candidate driver per batch — no multicast), every ride lifecycle transition, cancellations, chat, safety | **N/A — free at Spinr's volume** (FCM has no per-call charge for this message volume class) | N/A | **Retry**: `push_retry_queue` + `push_retry_loop` (30s poll, exp. backoff, 5 attempts) — see REC-D-03 for the batching (not cost) gap | N/A dollar-cost, but flagged for throughput/architecture (REC-D-03) |
| 15 | Expo Push API | `backend/features.py:1255-1292` (`_send_expo_push`) | Same trigger set as #14, for Expo-managed tokens | **N/A — free** | N/A | Same retry queue as #14 | N/A |
| 16 | Stripe (PaymentIntent auth/capture/refund) | Referenced by `.claude/context/domain-payments.md`; not a file in Module D's named scope (fare_service.py touches settlement, not Stripe calls directly) | Every ride settlement | Metered by Stripe itself (2.9%+30¢ class fees), not by `maps_budget.py` | N/A | Stripe SDK idempotency keys (3-layer, see domain-payments.md) | NOT DEEPLY AUDITED this pass — out of named scope; Stripe's own dashboard is the real spend-visibility tool, not this table |

## Headline gaps (severity HIGH)

1. **#1/#2 — `_shared.py:100-134`'s `_fetch_directions_route`, backing every `/rides/estimate`
   call and every unlocked booking confirm, has zero budget accounting and zero caching.**
   This is the single highest-volume Directions call site in the codebase (every rider
   pin-drag re-quotes; every booking confirm without a valid token calls it again) and the
   only one of the app's five Directions call sites with neither guard. See
   `module-d-backend-cost.md` REC-D-01 for the fix recommendation and its anti-undercharge
   caveat.
2. **#3 — `maps_eta.py`'s Google Distance Matrix fallback has no SKU in `maps_budget.py` at
   all.** Frequency is already well-controlled (B3, 2026-07-28: 15s cache + movement gate),
   but if OSRM degrades fleet-wide, this fallback could run at full GPS-ping cadence with the
   circuit breaker structurally blind to it — `estimate_today_usd()` cannot total a SKU that
   doesn't exist in its own dict. See REC-D-02.

## Real spend visibility (org-level gap, already tracked)

`maps_budget.py`'s `_PRICE_USD` constants are a **self-imposed ceiling estimate**, not a
read of actual Google Cloud billing. `ACTION_ITEMS.md` E13 (`billing-usage-monitor.yml`,
2026-09-03) already names this exact gap: Stripe/Twilio balance monitoring is live, but
Google Maps/Firebase real-dollar spend has no equivalent because GCP Billing Budgets API
integration (service account + `billing.budgets` scope + a configured budget object) has
never been set up. This audit's cost table (above) is exactly the SKU inventory that
integration would need as its starting input — tag: **EXTENDS-E13**.

===MODULE-D-COST-TABLE-COMPLETE===
