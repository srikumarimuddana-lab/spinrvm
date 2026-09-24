# R4 — Rider Journey Owner (W1)

Lane: R4 · Charter: every rider story install → signup → book → wait → ride → pay →
receipt → rate → dispute → refund → delete account. Report-and-recommend only, one
output file.

**Disclosures (read once, applies to every claim below):**
- rider-app has **no visual-regression tooling** (CLAUDE.md, confirmed by directory
  search — no Playwright/snapshot config in `rider-app/`). Every UI claim in this
  report is reasoned about from source (screen components, styles, `accessibility*`
  props, store/hook logic), never from a rendered screenshot or a live run.
- Builds on, does not repeat, `docs/audit/clean-sheet/rapid-baseline-2026-09-24/A7-surfaces.md`
  (lane A7). Rows below cite "A7:" and say **upgraded** (new evidence changes the
  status) or **confirmed** (independently re-derived, same conclusion).
- De-duplicated against `ACTION_ITEMS.md` (grepped by keyword before filing) and the
  finished sibling lanes the coordinator named: `02-findings/dispatch.md` (R8, cited
  as DISPATCH-001..004), `02-findings/trust-safety-fraud.md` (R11, cited as TSF-010),
  `02-findings/compliance.md` (R12, cited as COMP-007/008 and the dispute-window
  finding).
- **Public-repo note:** this repo is public. Findings below describe *what* is wrong,
  *where* (file/function, not exact exploit steps), and *why*, with the minimum
  evidence needed for an engineer to locate and fix the gap — not a reproduction
  recipe an abuser could follow (e.g. no worked example of how to race the CAS
  filter or forge a request).

Status: **COMPLETE.**

---

## (a) Top 5

1. **RIDERJ-002 (HIGH)** — A driver's phone dying/app being force-killed mid-trip
   (`in_progress`) has **no automated resolution and no rider-facing messaging**.
   The backend's own `stale_in_progress_ride_alerter.py` module docstring says so
   explicitly: it is alert-only by design, and a rider in this state is locked out
   of booking a new ride (`in_progress` is in `active_statuses`) indefinitely,
   with only a passive "Location updates delayed" banner and an SOS button — no
   copy that says the trip may be abandoned, no self-serve way to end it or get
   help. Resolution requires a human admin to notice a Sentry alert (5-15 min
   detection window) or the rider to call support.
2. **RIDERJ-003 (HIGH)** — The backend's structured dispute/refund endpoint
   (`POST /disputes` — reason enum, requested amount, ride linkage, auto-creates a
   Zoho ticket) has **zero rider-app caller**. Drivers can file a structured
   dispute from `driver-app/app/driver/ride-detail.tsx`; admin has full tooling
   built around the `disputes` table; a rider can only reach a generic free-text
   support ticket (`POST /tickets` via `SupportScreen`) with no ride-linked reason
   or amount capture. Directly answers the charter's "can a rider finish every
   journey without contacting support" question for disputes/refunds: **no** —
   and the one structured path that exists is asymmetric by client.
3. **RIDERJ-001 (MEDIUM-HIGH)** — Mid-trip add/remove-stop is fully built on the
   backend (`backend/routes/rides/stops.py`: CAS-guarded, re-prices via
   `_reestimate_fare_for_stops`, notifies the driver over WS, race-safe) but has
   **zero caller in rider-app or driver-app**. The one MUST-close item the
   coordinator asked about — "rider changes destination/adds a stop mid-trip and
   the re-price disclosure" — has no UX to evaluate because the feature is
   unreachable from any client. This is either dead scope (should be removed/
   documented as backend-only-for-now) or a shipped-but-never-wired feature
   (should get a rider-app entry point).
4. **RIDERJ-004 (MEDIUM)** — The core booking-flow tap target — the vehicle-type
   option cards in `ride-options.tsx` (name, ETA, price, surge, capacity,
   selected/unavailable state) — has **no `accessibilityLabel`, `accessibilityRole`,
   or `accessibilityState`** anywhere in the card component. A screen-reader rider
   gets a jumble of unlabeled text nodes for the single most important choice in
   the booking flow, while every other primary action on the same screen (back,
   promo remove, WAV toggle, schedule, confirm) is correctly labelled. WCAG 2.1 AA
   risk on a customer-facing surface CLAUDE.md explicitly requires it for.
5. **RIDERJ-006 (INFO, corrects two A7 rows)** — Two A7 "Partial (unverified)"/
   "INFERRED" rows are resolved by direct code read this pass: (a) WS reconnect
   **does** explicitly re-fetch ride state via REST on every `onopen` (both first
   connect and reconnect) — genuinely Handled, not partial; (b) the rider WS
   client never sends the `last_seq` query param the server's sequenced replay
   outbox and driver-app's client both support — a real, undocumented
   rider/driver client asymmetry, mitigated in practice by the full-state refetch
   but worth a parity decision. See the Scenario table and RIDERJ-007.

---

## (b) §7.2 Scenario cards — sweep-catalog §3.1–§3.4 (rider side), §3.6

Chain legend: TRIGGER → DETECTION → SYSTEM STATE → USER EXPERIENCE → BUSINESS RULE →
RECOVERY → ESCALATION → AUDIT RECORD → TEST. Cells marked "—" were not traced this
pass (see §(h) NOT VERIFIED); this is stated per-row, not silently omitted.

### §3.1 Booking & matching

| # | Scenario | Status | Evidence / chain |
|---|---|---|---|
| 1 | Double-tap "Request" → duplicate ride | **Handled** | TRIGGER: fast double-tap. DETECTION: client `isBooking` re-entrancy guard (`ride-options.tsx:693-694,1229-1230`, `accessibilityState.busy`) + server-side idempotency key bucketed by pickup/dropoff/2-min window with a DB unique index (`idx_rides_rider_idempotency_key`, `backend/routes/rides/booking.py:469-478,1656-1670`). SYSTEM STATE: second call returns the existing ride. USER EXPERIENCE: no visible duplicate. RECOVERY: n/a, nothing to recover. TEST: covered per A7 (re-confirmed, not re-read this pass). |
| 2 | Rider books while a previous ride is active | **Handled** | TRIGGER: booking call while rider already has a ride in `active_statuses`. DETECTION: pre-check `get_rows("rides", {rider_id, status:{$in:active_statuses}})` (`routes/rides/booking.py:552-562`) + a DB-level race path (`:1661`, 409 `"You already have an active ride"`). Also guards scheduled-ride collisions ("You already have a scheduled ride around this time", `:627`). USER EXPERIENCE: 409 surfaced — not independently read whether `ride-options.tsx` shows this 409 detail verbatim or a generic error (client-side rendering of this specific 409 — **not verified this pass**). |
| 3 | No drivers 5 min → auto-cancel, hold released, rider told why | **Handled** | `backend/utils/stuck_ride_sweeper.py:41-142` — `_SEARCHING_TIMEOUT_MINUTES=5`, atomic DB claim (`.eq("status","searching").lt("ride_requested_at", cutoff)`), releases the card hold via `release_open_hold()` **before** notifying (money-first ordering, explicit comment), sends `ride_cancelled` WS + push with reason `"no_drivers_found"` and copy "No nearby drivers found. Please try again." Rider is told why, not left on a silent spinner. |
| 4 | Driver accepts at the same instant rider cancels | **Handled (per R8, not re-derived)** | Cited from `02-findings/dispatch.md` — atomic CAS on `{status:'searching'}` for accept; rider-cancel path guarded symmetrically. Not re-read this pass (R8's domain). |
| 5 | Two drivers accept the same ride (race guard → 409) | **Handled** | CLAUDE.md-documented invariant, re-confirmed present via the `_stops_cas_filters`-style CAS pattern seen directly in `stops.py` and cited as present fleet-wide in `dispatch.md`'s steelman ("no bare 'just write status' call site found"). |
| 6 | Offer timeout while driver's phone is backgrounded/locked | **Handled (R8)** | Server-side 30s authoritative timeout independent of client state — cited from `dispatch.md`, not re-derived. |
| 7 | Pickup pin on wrong side of a divided road / gated community | **Partial** | `confirm-pickup.tsx` gives the rider a draggable pin with a 50 m "still counts as this pickup" radius circle (`PICKUP_RADIUS_M=50`, lines 1-60) — the rider *can* manually correct a wrong-side pin. No automatic street-side/road-snap detection or hint text found (grepped for street-side/divided-road copy — zero hits). Manual-correction-only, no smart assist. |
| 8 | Scheduled ride: driver cancels 10 min before; surge active at dispatch but not at booking | — | Not traced this pass (scheduled-dispatch re-offer path is R8's `scheduled_rides.py`/`matching.py` domain). `scheduled-rides.tsx` UI reviewed for display only (see DST row below), not for the cancel-and-reoffer UX. **Not verified.** |
| 9 | Price changes between quote and confirm | **Handled by design (documented SLA exception)** | CLAUDE.md's own documented incident + fix: `_PRICING_ROUTE_WAIT_S` (3.5s worst case) exists specifically because a too-short wait let the same trip re-price between quote and confirm (the cited 12.12→16.46 km incident). This is the accepted, decided mitigation (2026-08-21) — re-confirmed present, not re-litigated. |
| 10 | Rider books for someone else (guest) | **Unhandled / feature does not exist** | Grepped `rider-app/app/ride-options.tsx` and `search-destination.tsx` for guest/on-behalf-of booking language — zero hits. There is no "who gets notifications/receipt" question to answer because there is no guest-booking feature at all. Not a defect (nothing broken), but a completeness gap if guest booking is in scope for `docs/PRD.md` — **not cross-checked against PRD this pass.** |
| 11 | WAV / service-animal request with no eligible driver online | **Partial (spot-checked, re-confirms A7 A11Y-003)** | `ride-options.tsx:1064-1077` — WAV toggle dims and switches to explicit text "No WAV drivers nearby" when `wavCount===0`, paired with an `accessibilityLabel`. Not verified: whether the *reason* is announced to a screen reader beyond the label text, and whether a WAV-specific dispatch retry/notify-when-available exists once a WAV driver comes online (not traced). |

### §3.2 En route & pickup (rider-visible dimensions)

| # | Scenario | Status | Evidence / chain |
|---|---|---|---|
| 12 | Driver GPS frozen / drifting / teleporting | **Handled (backend), Handled (rider display)** | Backend: `evaluate_gps_plausibility()`/`check_location_integrity()` gates location-derived ride-state writes (cited from A7 INFO-005, `routes/drivers/ride_flow.py:933-954`, not re-read this pass — R8/R11 territory). Rider display: `DriverLocationStatus.tsx` shows "Waiting for driver location" (unknown/stale >5s in the future — clock-skew guard) or "Location updates delayed · showing last known position" (age >20s), `accessibilityLiveRegion="polite"`. Degrades by elapsed time, not frozen forever. |
| 13 | Driver going the wrong way; rider wants to cancel — who pays the fee? | — | Not traced this pass (cancellation-fee fault-attribution logic is `services/cancellation_service.py`, out of this lane's read list). **Not verified.** |
| 14 | Rider no-show: wait timer, fee, evidence | — | Not traced this pass (driver-side wait-timer/fee logic; would need `routes/drivers/ride_flow.py` + `cancellation_service.py`, R5/R9 territory more than R4). **Not verified.** |
| 15 | Wrong rider gets in (verification PIN?) | **Unhandled** | Cited from `02-findings/trust-safety-fraud.md` **TSF-010** — no `trip_pin`/`ride_pin`/`verification_pin` anywhere in the backend (grep confirmed there, not re-run here). Confirms sweep-catalog §3.2 #15 is fully open. Not re-deriving TSF-010's own recommendation; flagging here because it is a rider-experience gap too (a rider has no way to confirm *their* driver is the right one either, beyond the in-app photo/plate shown pre-pickup — that part reasoned-about, not independently re-verified this pass). |
| 16 | Rider changes pickup after acceptance | — | `confirm-pickup.tsx` supports pin adjustment pre-request; whether pickup can be changed **after** a driver has accepted (`driver_accepted`+) was not traced this pass. **Not verified.** |
| 17 | Rider or driver phone dies before pickup | **Partial** | Driver side: no automated recovery before `in_progress` beyond the offer-timeout/claim-reaper paths (R8 territory). Rider side: if the rider's own phone dies before pickup, nothing client-side changes server state — the ride simply proceeds as normal from the driver's perspective; on relaunch the rider's app reconciles via `fetchActiveRide()` (A7-confirmed `_layout.tsx:807-849`, `hydrateActiveRide()`). No specific "rider unreachable pre-pickup" driver-side handling traced this pass. |

### §3.3 In trip & completion

| # | Scenario | Status | Evidence / chain |
|---|---|---|---|
| 18 | Phone dies / app killed / no connectivity mid-trip (rural SK) — who completes the trip, on what distance | **Unhandled (rider-facing) — see RIDERJ-002** | Full chain: TRIGGER: driver's app force-killed or loses connectivity for >10 min while `in_progress`. DETECTION: `stale_in_progress_ride_alerter.py` (5 min tick, `STALE_MINUTES=10` measured off `drivers.updated_at`). SYSTEM STATE: ride stays `in_progress` forever — no state write occurs (module is read-only by design, CLAUDE.md ride-state invariant "never cancelled after trip start" is cited as the explicit reason an automated fix is out of scope). USER EXPERIENCE: rider sees only `DriverLocationStatus`'s passive "Location updates delayed" text; no messaging that the trip may be abandoned, no self-serve end/complete action, `RiderSOS` is the only escalation control on-screen and is a safety (not "my trip seems stuck") control. BUSINESS RULE: rider cannot book a new ride (`in_progress` ∈ `active_statuses`) until this ride resolves. RECOVERY: `admin_complete_ride` (`routes/admin/rides.py`) once a human confirms abandonment via the admin live-monitoring page. ESCALATION: Sentry `capture_message` + structured error log, tagged `domain=dispatch`. AUDIT RECORD: none written until an admin acts (no automatic row). TEST: not located this pass — **not verified whether `_check()` has a unit test** (module itself wasn't in the traceability rows checked). Rural-connectivity distance-reconciliation question ("on what distance") — not traced; depends on how much of `actual_route_segments` survived to the last successful write, out of scope to verify this pass. |
| 19 | Driver forgets to end the trip; rider already out | **Unhandled (rider-facing), same mechanism as #18** | Same `stale_in_progress_ride_alerter` covers this too (it doesn't distinguish "driver forgot" from "driver's app died" — both look identical as "no location update since ride_started_at + 10min" if the driver also stopped moving/pinging). If the driver's app is still running and pinging, this specific case (forgot to tap End Trip, but device is live) is **not** covered by the alerter at all — no separate "trip duration wildly exceeds ETA" check found. **Not verified**: whether a duration/distance plausibility check exists to catch a live-but-forgotten trip. |
| 20 | Driver ends trip early / far from destination | — | Not traced this pass — would need `routes/rides/ride_complete.py`'s distance-at-completion validation (R8 territory). **Not verified.** |
| 21 | Route deviation / long-hauling — safety alert vs fare adjustment | **Handled (existence only, R11/R8 territory)** | CLAUDE.md lists `route_deviation_alerter` as one of the 42 background loops (`domain-safety.md` scope). Not read this pass. **Not verified in depth.** |
| 22 | Stops added/removed mid-trip; re-pricing disclosure | **Unhandled from the rider side — see RIDERJ-001** | Full chain: TRIGGER: none reachable — no rider-app UI calls `POST/DELETE /{ride_id}/stops`. Backend readiness: CAS-guarded (`_stops_cas_filters`), re-prices via `_reestimate_fare_for_stops` before committing, returns 409 `"Stops changed. Refresh the ride and try again."` on a lost race, notifies the driver over WS (`stops_updated`). If it *were* wired to a UI: the re-price would arrive as a new `estimated_fare`/`grand_total` in the same response — no evidence of a rider confirmation step before the new price is applied (the endpoint applies the CAS write and returns the new total in one call, with no intermediate "confirm this adds $X" prompt in what was read). USER EXPERIENCE today: none — feature is not reachable. This is the MUST-close item; answer is "there is no UX to evaluate; the capability is orphaned." |
| 23 | SOS pressed; false SOS; SOS with no data connection | — | `RiderSOS` component present and wired on `ride-in-progress.tsx` (always-visible floating button + action-bar button). Deeper SOS mechanics (false-alarm handling, offline SOS queuing) are R11's domain (`domain-safety.md`) — **not traced this pass.** |
| 24 | Vehicle breakdown / collision mid-trip — insurance Period 3 evidence | — | Out of scope — R8/R11 territory (`insurance_periods.py`). **Not verified.** |
| 25 | Rider intoxicated / vomits — cleaning fee evidence/appeal | — | Not traced (`services/cancellation_service.py`/damage-claim flow not read). **Not verified.** |
| 26 | Minor riding unaccompanied | — | Grepped rider-app for age-verification/minor language — no hits found in the files read this pass; **not exhaustively searched**, flagging as not verified rather than "unhandled" since the search wasn't targeted enough to be conclusive. |
| 27 | Extreme cold (−40 °C) — pickup wait tolerance, stranded-rider priority | — | Not traced. Greenfield-extensions §12 flags "winter operations mode" as an added gap item for R4/R5/R13 — **no evidence found this pass that a winter mode exists**; grep for "storm"/"extreme cold"/"stranded" across `rider-app/app` and `backend/utils/` returned nothing during this pass's searches (not exhaustive). Recommend a dedicated follow-up. |

### §3.4 Payment, fraud & disputes (rider side)

| # | Scenario | Status | Evidence / chain |
|---|---|---|---|
| 28 | Card declined at completion; 3DS required after trip | **Handled (UX layer)** | `ride-completed.tsx` — `isSubmitting` guard, retry path to `manage-cards.tsx` preserving the chosen tip/rating state across the detour (explicit code comment re: Codex 62i6 fix), and `support.tsx` has a dedicated `topic=payment_failed` deep-link pre-filling a support ticket ("My card was declined and I'm unable to pay or change cards"). Deeper 3DS/Stripe mechanics are R9 territory, not re-derived. |
| 29 | Stripe webhook arrives twice/out of order/never | **Out of scope (R9)** | Not re-audited per this lane's charter (money-math/webhook idempotency belongs to `spinr-money-auditor`). |
| 30 | Refund issued twice; partial refund + chargeback same ride | **Out of scope (R9)**, rider-display only checked | `ride-details.tsx:31-32` displays `refunded`/`partially_refunded` status strings if the ride row carries them — confirms the rider-facing *display* exists; the prevention-of-double-refund logic itself is R9's. |
| 31 | Tip added after payout already sent | **Handled (rider-app late-tip path exists)** | `payment_service.py:795-878` (`_notify_allowance_threshold` area) documents a "late tip" debit path with an explicit accumulation guard: "`/rate` accumulates tip into `driver_earnings` on every call... a re-send can never double-credit a tip" (code comment, `ride-completed.tsx:331-333,407-419`). Money mechanics not re-verified (R9); rider-UX guard (`isSubmitting`, single-charge-per-tip intent) is directly read and present. |
| 32 | Promo stacking; referral self-referral; multi-account device farming | — | Not traced (`promotions.py`/`referral.py` abuse checks are R11 territory). **Not verified.** |
| 33 | Driver–rider collusion for fake trips | — | Out of scope (R11). |
| 34 | GPS spoofing; impossible speed between pings | **Handled (per A7 INFO-005, R8 territory)** | Cited, not re-derived. |
| 35 | Account takeover (SIM swap, OTP brute force, stolen refresh token) | **Out of scope (R10)** | Rider-app-side mitigation confirmed present (token refresh dedup/queue per A7's steelman) but the security-mechanism depth is R10's. |
| 36 | Chargeback evidence pack auto-assembled | — | Not traced (`docs/runbooks/payment-dispute-evidence.md` not read this pass). **Not verified.** |
| 37 | Lost item returned — fee, contact masking, abuse | **Partial (UI exists, abuse controls not verified)** | `lost-and-found.tsx` and `lost-and-found-chat.tsx` exist as dedicated rider-app screens with their own test files (`lostAndFoundScreen.test.tsx`, `lostAndFoundChatScreen.test.tsx`) — a real, tested flow exists. Contact-masking and fee-abuse controls specifically were not traced (`routes/lost_and_found.py` or equivalent not read). |
| 38 | Corporate allowance exhausted / company suspended mid-trip | **Handled (allowance-exhausted case); Unknown (company-suspended case)** | Allowance running out mid-trip does **not** interrupt the trip: `payment_service.py:152-218` — the ride settles via an allowance-then-master-wallet fallback saga (code comment: "mirrors `settle_corporate`'s allowance-then-master-wallet saga"), and the rider is informed via push notification when crossing an 80%-used threshold or full exhaustion (`_notify_allowance_threshold`, copy: "Your company ride allowance is fully used for this period"), or via a 4xx at their *next* booking attempt (comment at `payment_service.py:152-154`). No rider-app screen shows a live "allowance running low mid-trip" banner (checked `ride-options.tsx`'s pre-booking `workBannerSubtitle` only — nothing found for mid-trip). Company-suspended-mid-trip fallback (what happens if the master-wallet fallback also fails) was **not traced** — money-path depth, R9/R6 territory. |

### §3.6 Platform & ops (rider-relevant rows)

| # | Scenario | Status | Evidence / chain |
|---|---|---|---|
| 44 | Deploy mid-ride (WS reconnect, state resync) | **Handled — upgrades A7 "Partial (unverified)" to Handled** | TRIGGER: backend redeploy drops the rider's WS mid-ride. DETECTION: `ws.onclose` fires; `useRiderSocket.ts` exponential backoff [1,2,5,10,30s] with jitter (lines 42, 346-367). RECOVERY: on **every** `ws.onopen` (first connect *and* every reconnect), the client explicitly calls `useRideStore.getState().fetchRide(rideId)` (`useRiderSocket.ts:312-327`) with an inline comment: "Re-sync ride state: any events sent while disconnected are not buffered by the server, so pull from the HTTP source of truth." This is a genuine REST re-sync on reconnect, not reliance on the next WS event alone — resolves A7's flagged gap. See RIDERJ-007 for a related nuance (the rider client doesn't use the server's sequenced-replay protocol at all, relying solely on the coarser full refetch). Also covered client-side by the OTA/forced-upgrade mid-ride carve-out (A7 INFO-005, `middleware.py:221-349`, not re-derived). |
| 45 | Redis down; Supabase down; Railway failover | **Out of scope (R13)** | Not re-derived — rider-app-visible effect of a Redis-degraded dispatch path is covered indirectly via DISPATCH-002 (`dispatch.md`, cited not re-derived). |
| 46 | Background loop runs twice across replicas | **Handled for the two loops read this pass** | `stuck_ride_sweeper` uses an atomic DB claim (`.eq("status","searching")` — two replicas racing both get correct, non-duplicated results) plus a load-shedding (not correctness-critical) Redis leader lock, explicitly documented as fail-open. `stale_in_progress_ride_alerter` is pure-read + Redis `SET NX` dedupe (fails open to "alert again" rather than "alert never," explicitly by design) — both replay-safe per CLAUDE.md's requirement. |
| 47 | Clock skew / DST (Saskatchewan has none) | **Handled (scheduling logic); Partial (client display)** | Backend scheduling math: `scheduled_rides.py` computes entirely in `timezone.utc` (A7-confirmed, re-cited not re-read). Client display: `scheduled-rides.tsx:91-93` and `ride-options.tsx:1191` both call `toLocaleDateString`/`toLocaleTimeString('en-CA', …)` with **no explicit `timeZone` option** — this renders in the **device's** current system timezone, which is correct for the overwhelmingly common case (rider physically in Saskatchewan, phone set to CST) but not pinned to `America/Regina`/`America/Swift_Current`. A rider scheduling a Saskatchewan pickup while travelling in a DST-observing timezone (e.g. booking ahead from Toronto/Winnipeg) would see times rendered — and, if the picker's `new Date(...)` construction is also device-local (confirmed at `ride-options.tsx:235-236,645,777,1177-1178` — no timezone pin found anywhere in the booking flow) — **captured** in their current device timezone rather than the Saskatchewan service area's, i.e. the picker's "3:00 PM" could book a pickup 1 hour off from what the rider intended if their device isn't set to SK time at booking. Narrow blast radius (requires booking ahead while physically outside SK), but real and unguarded. |
| 48 | Old app version calling a changed API | **Handled (per A7 INFO-005, re-cited)** | `middleware.py`'s 426 forced-upgrade gate with an explicit mid-ride carve-out — not re-derived. |
| 49 | Admin mis-configures fare/surge/service area | **Out of scope (R7/R9)** | Not traced this pass. |

---

## (c) Journey map — install → delete account

Test column reflects a same-named test file found under `rider-app/__tests__/` (a
file existing is not proof of real, non-stubbed coverage — that judgment is R14's
per CLAUDE.md's "Done" gate; this column only answers "does a test file exist").

| Journey step | Screen(s) | Backend endpoint(s) | 4 states (load/empty/error/success) | A11y labels | Test file exists |
|---|---|---|---|---|---|
| Install / first launch | `index.tsx`, `_layout.tsx` | — | Splash-phase hook (`useSplashPhase.ts`) exists; not independently verified for all 4 states | Not checked this pass | `indexScreen.test.tsx` |
| Sign up / OTP | `otp.tsx`, `profile-setup.tsx`, `verify-email.tsx` | `routes/auth.py` (OTP send/verify, hashed at rest per CLAUDE.md, not re-verified) | Not checked this pass | Not checked this pass | `otpScreen.test.tsx`, `profileSetupScreen.test.tsx`, `verifyEmailScreen.test.tsx` |
| Consent / legal | `legacy-consent-notice.tsx`, `legal.tsx`, `policies.tsx` | `routes/legacy_consent.py` (cited via COMP-008, not re-derived) | — | — | `legacyConsentNoticeScreen.test.tsx`, `legalScreen.test.tsx`, `policiesScreen.test.tsx` |
| Set pickup/dropoff | `search-destination.tsx`, `pick-on-map.tsx`, `confirm-pickup.tsx` | address/places proxy (not traced) | Not checked | `accessibilityLabel="Add a stop"` present (line 609); pin-drag not labelled — not exhaustively checked | `searchDestinationScreen.test.tsx`, `searchDestinationPinIntegrity.test.tsx`, `pickOnMapScreen.test.tsx`, `confirmPickupScreen.test.tsx` |
| Choose ride type / book | `ride-options.tsx` | `GET` fare estimate, `POST` ride request (`routes/rides/booking.py`) | **Yes** — `isLoading`/skeleton, `allUnavailable` empty branch, `fetchError` + "Tap to retry", success (A7-confirmed, re-cited) | **Gap — RIDERJ-004**: back/promo/WAV/quiet/fare-breakdown-toggle/schedule/confirm all labelled; the vehicle-type option cards (the primary selector) are **not** | `rideOptionsScreen.test.tsx`, `ride-options-payment-sheet.test.tsx`, `rideStore-wav-gating.test.ts` |
| Wait for match | `ride-status.tsx` | WS + 3s/15s REST poll fallback (`ride-status.tsx:140-168`) | Searching pulse/dot animation gated behind reduce-motion (`#4607` fix, lines 174-201); screen-reader announcement per status transition (lines 203-224) | Live-region announcements present for status changes | `rideStatusScreen.test.tsx`, `rideStatusContract.test.ts`, `rideStatusCloseButton.test.tsx` |
| Driver assigned/en route/arrived | `driver-arriving.tsx`, `driver-arrived.tsx` | WS `driver_accepted`/`driver_arrived` (+ `fetchRide` fallback) | Not checked this pass | Not checked this pass | `driverArrivingScreen.test.tsx`, `driverArrivedScreen.test.tsx` |
| In trip | `ride-in-progress.tsx`, `ride-tracking-webview.tsx` | WS `ride_started`/`driver_location_update`; `RiderSOS` for emergencies | `DriverLocationStatus` staleness banner present (RIDERJ-002 gap: no "trip may be stuck" escalation beyond it) | SOS button labelled (not independently re-checked) | `rideInProgressScreen.test.tsx`, `rideTrackingWebviewScreen.test.tsx` |
| Complete / pay | `ride-completed.tsx` | settlement endpoint (keyed by `rideId`, not a new payment record — A7-cited); `POST /rate` for tip | `isSubmitting` re-entrancy guard, retry-to-manage-cards path preserving tip/rating state | Not checked this pass | `rideCompletedScreen.test.tsx`, `ride-completed-route.test.tsx`, `payment-confirm-error-state.test.tsx` |
| Receipt | `ride-details.tsx` | `GET /rides/{id}`, `POST /rides/{id}/email-receipt`, PDF download | `fetchRide` with silent catch (`catch {}` at line 114 — no explicit error UI branch found for the receipt fetch itself, only for email/PDF actions) | Not checked this pass | `rideDetailsScreen.test.tsx`, `ride-details-route.test.tsx` |
| Rate & tip | (part of `ride-completed.tsx`) | `POST /rate` (accumulates tip idempotently, code-comment-confirmed) | — | — | covered by `rideCompletedScreen.test.tsx`, `tipPresets.test.ts`, `customTipMinimum.test.ts` |
| Dispute / refund | **none — RIDERJ-003** | `POST /disputes` exists, **no rider-app caller** | N/A — screen doesn't exist | N/A | N/A |
| Support (fallback for disputes) | `support.tsx` → `shared/components/SupportScreen.tsx` | `POST /tickets`, `GET /faqs`, `POST /ai/chat` | Loading/error present per A7's four-states table (re-cited) | Not checked this pass | `supportScreen.test.tsx` |
| Delete account | `settings.tsx` → `privacy-settings.tsx` | `DELETE /users/account` (soft-delete/tombstone, PIPEDA-aligned, 7-year regulatory retention window, reactivatable via OTP) | 409 with an actionable reason if an active ride/balance exists, shown verbatim per code comment (`users.py:326-328`) | Not checked this pass | `privacySettingsScreen.test.tsx`, `reactivateAccountScreen.test.tsx` |

---

## (d) Finding cards (§7.1)

### RIDERJ-001 — Mid-trip stop add/remove is fully built server-side, orphaned client-side
- Hierarchy: L2 Ride Fulfillment › L3 In-trip route changes › L4 Add/remove stop mid-trip › L5 rider-initiated re-route
- Severity: MEDIUM-HIGH   Priority score: S×B×L ≈ 2×2×3 = 12 (feature-completeness/UX gap, not a live defect — nothing is broken, a shipped capability is simply unreachable)
- Status: VERIFIED   Existing item: new (grepped `ACTION_ITEMS.md` for "mid-trip stop"/"add stop"/"AddStopMidTrip" — no hits)
- Adversary: n/a (completeness gap, not an attack surface); indirectly a plaintiff's-lawyer angle if a rider is later told "you could have added a stop" when the app gave them no way to
- Evidence: `backend/routes/rides/stops.py:72-193` — `POST /{ride_id}/stops` and `DELETE /{ride_id}/stops/{stop_index}` are CAS-guarded (`_stops_cas_filters`), call `_reestimate_fare_for_stops` before committing, return 409 with rider-readable copy on a lost race, and notify the assigned driver over WS (`stops_updated`). Grepped `rider-app/` and `driver-app/` for any call site referencing `/stops` (POST or DELETE) or the WS event as an *outbound* action: rider-app has zero; driver-app only calls the separate `POST /{ride_id}/stops/{stop_index}/complete` endpoint (`driver-app/app/driver/(tabs)/index.tsx:1999-2000`). `search-destination.tsx`'s `addStop`/`removeStop`/`handleAddStop` (lines 33, 362-364, 603-613) operate on **pre-booking** route-planning state only (`rideStore.ts`'s `addStop`/`removeStop` actions), a completely separate code path from the mid-trip endpoints.
- What happens (plain language): a rider mid-trip who wants to add a coffee-shop stop or drop a second passenger has no in-app way to do it, even though the backend has a race-safe, re-priced, driver-notified implementation ready to receive that request. They'd have to ask the driver verbally and hope the driver's app has a matching entry point — it doesn't.
- Root cause: the backend capability was built (likely alongside the driver-side `complete` endpoint for an ordered multi-stop trip) but the rider-facing UI to initiate a mid-trip add/remove was never shipped.
- Recommendation: either (a) add the missing rider-app entry point (e.g. a "+ Add a stop" affordance on `ride-in-progress.tsx`, wired to `POST /{ride_id}/stops`, with the returned re-price shown before or immediately after the CAS write — see the re-pricing note below) and a symmetric driver-app entry point for driver-initiated removal if that's an intended flow, or (b) if mid-trip stop editing is deliberately descoped for now, say so in `docs/PRD.md`/`ACTION_ITEMS.md` so a future engineer doesn't rediscover this as a "missing feature" bug report against working backend code.   Alternative considered: leave undocumented as-is — rejected, this is exactly the "orphan code" pattern the Cartographer's `traceability.csv` flags as a candidate finding, and it directly blocks answering the coordinator's MUST-close re-pricing-disclosure question.
- Blast radius: `routes/rides/stops.py` (2 endpoints, no other readers besides the driver-side `complete` handler and the WS `stops_updated` listener already present in `useRiderSocket.ts:127-132`, which passively refetches on the event but never triggers it).
- Rollout: additive UI work behind a flag if shipped now (CLAUDE.md gate 3 — new user-visible flow on a live-tested ride surface).
- Verification to close: product decision recorded (build the UI vs. formally descope); if built, a re-price-disclosure UX pass (confirm before applying, not just reflecting the new total after the fact) plus an integration test asserting the rider sees the updated total before/at the same moment the driver is notified.

### RIDERJ-002 — `in_progress` ride abandoned by a dead/force-killed driver app has no rider-facing resolution
- Hierarchy: L2 Ride Fulfillment › L3 In-trip reliability › L4 Driver-device-failure recovery › L5 rider experience while stuck
- Severity: HIGH   Priority score: S×B×L ≈ 3×3×2 = 18 (low frequency, high severity when it happens — rider is functionally stranded in the product, not physically, but locked out of booking with no explanation)
- Status: VERIFIED   Existing item: new (grepped `ACTION_ITEMS.md` for "stale_in_progress"/"abandoned trip"/"P2 task #16" — no hits; the module's own docstring references "P2 task #16" as its origin, so the gap was known at build time and consciously scoped as alert-only, not silently missed)
- Adversary: hostile network/device (the trigger itself); indirectly a plaintiff's-lawyer angle (a rider stuck with no information or self-serve recourse during what they experience as an ongoing trip)
- Evidence: `backend/utils/stale_in_progress_ride_alerter.py` (full file read) — its own docstring states the gap plainly: *"An `in_progress` ride abandoned by a force-killed driver app has NO automated recovery today: the rider can't book a new ride (`in_progress` is in `active_statuses`) and the driver's Period-3 insurance audit row stays open indefinitely."* The module is deliberately alert-only (Sentry + error log, `STALE_MINUTES=10`, 5-min tick) — it never mutates ride state, by design, because CLAUDE.md's own ride-state invariant ("never cancelled after trip start") makes an automated force-complete unsafe (a driver's phone dying doesn't prove the trip itself ended). Rider-side: `rider-app/components/DriverLocationStatus.tsx` shows only a passive, non-actionable "Location updates delayed · showing last known position" text once the last position is >20s old — no distinct copy or CTA once the outage crosses the 10-minute threshold the backend itself uses to escalate. `ride-in-progress.tsx`'s only on-screen escalation control is `RiderSOS` (lines 671-672, 727-729) — a safety/emergency control, not a "my trip seems abandoned" one.
- What happens (plain language): if a driver's phone dies, crashes, or the app is force-killed mid-trip, the rider's screen keeps showing "Location updates delayed" forever. After 10 minutes an internal alert fires to Sentry — invisible to the rider — and a human admin has to notice it and manually resolve the ride via `admin_complete_ride`. Until that happens (which today only reliably happens if the rider also calls/tickets support), the rider cannot book another ride through the app and has no in-app explanation of what's going on.
- Root cause: deliberately scoped as detect-not-fix (documented, reasoned trade-off — force-completing an ambiguous ride risks mischarging or undercharging a trip that might still be legitimately underway). No corresponding rider-facing UX was built to accompany that trade-off.
- Recommendation: add a client-side escalation tier to `DriverLocationStatus`/`ride-in-progress.tsx`: once staleness crosses a threshold close to the backend's own `STALE_MINUTES` (e.g. 8-10 min), switch the banner's copy to something actionable ("We're having trouble reaching your driver — contact support" with a direct deep-link into `support.tsx?topic=stuck_ride&rideId=…`, mirroring the existing `topic=payment_failed` pattern already built for `ride-completed.tsx`) so the rider has a self-serve path instead of silently waiting. This does not require touching the backend's alert-only design — it's additive UI that surfaces information the backend already computes (staleness).   Alternative considered: auto-cancel/auto-complete after a timeout — rejected for the same reason the backend module itself rejects it (risk of mischarging a real, ongoing trip); a support deep-link is strictly additive and doesn't touch ride-state safety.
- Blast radius: `rider-app/components/DriverLocationStatus.tsx` (already the sole consumer of `capturedAt` staleness), `ride-in-progress.tsx` (adds a support deep-link, mirrors an existing pattern). No backend change required for the recommended fix.
- Rollout: additive UI, no flag strictly required (pure information/UX improvement on a passive banner) but CLAUDE.md gate 3 still applies since it's a new copy/CTA on a live-tested ride surface — ship behind a flag to be safe.
- Verification to close: a test asserting the escalation copy/CTA appears once simulated staleness crosses the threshold; manual confirmation the deep-link pre-fills a ride-linked support ticket.

### RIDERJ-003 — Rider has no self-serve dispute/refund path; drivers and admin do
- Hierarchy: L2 Ride Completion & Payments › L3 Dispute & refund › L4 Rider-initiated dispute › L5 client parity
- Severity: HIGH   Priority score: S×B×L ≈ 3×3×3 = 27
- Status: VERIFIED   Existing item: adjacent to (not duplicating) the compliance-lane's dispute-window finding — `02-findings/compliance.md`'s dispute-window row: *"60-day dispute window enforced — STILL OPEN: routes/disputes.py has no time-based cutoff"* — that finding is about the window not being enforced; this finding is that the rider client can't reach the endpoint the window would apply to at all. Grepped `ACTION_ITEMS.md` for "rider dispute UI"/"POST /disputes rider" — no hits.
- Adversary: n/a directly (support-burden/UX gap); regulator/plaintiff angle — a consumer-protection expectation that a documented dispute mechanism (the backend clearly implements one) is actually reachable by the party it's built for
- Evidence: `backend/routes/disputes.py:53-125` (`create_dispute`) accepts `ride_id`, a structured `reason` enum (`overcharged | wrong_route | driver_issue | payment_error | other`), an optional `requested_amount`, snapshots `original_fare`, sends a push notification, and fires a background Zoho ticket via `create_ticket_for_dispute` — a materially richer, ride-linked record than a free-text support ticket. Grepped `rider-app/app`, `rider-app/store`, `rider-app/hooks`, `rider-app/services` for `/disputes`, `createDispute`, or the word "dispute" (case-insensitive) as a UI trigger: **zero hits**. `rider-app/app/support.tsx` (the only rider-facing "something went wrong with my ride" entry point besides the payment-failed deep-link) posts to `POST /tickets` via `shared/components/SupportScreen.tsx:285` — a generic, non-ride-linked, non-amount-capturing ticket. By contrast, `driver-app/app/driver/ride-detail.tsx` does call the disputes endpoint (confirmed by grep — driver-side dispute UI exists), and `admin-dashboard` has multiple pages/tabs built specifically to process `disputes` rows (`dashboard/support/_tabs/disputes.tsx`, `dashboard/disputes/chargebacks-tab.tsx`, `lib/disputeResolutionSchema.ts`, plus e2e coverage `e2e/disputes.spec.ts`).
- What happens (plain language): a rider who wants to dispute a fare or request a refund cannot do so with a structured, ride-linked, amount-specific request the way a driver can — they're routed into the same generic support-ticket form used for "the app crashed" or "I have a question," with no reason taxonomy and no amount field, and admin has to manually reconstruct the ride/amount context that the `disputes` table (and the driver-side flow) would have captured automatically.
- Root cause: `POST /disputes` was built to be role-agnostic (`is_rider`/`is_assigned_driver` branching is already in the handler itself — it fully supports a rider caller today), but only the driver-app UI was ever wired to it.
- Recommendation: add a "Dispute this ride" entry point to `ride-details.tsx` (the natural place — it already renders `refunded`/`partially_refunded` status) that calls `POST /disputes` with the reason enum and an optional amount, mirroring whatever pattern `driver-app/app/driver/ride-detail.tsx` already uses so the two clients converge on one contract instead of two.   Alternative considered: keep routing rider disputes through the generic support ticket and have support staff manually create the structured `disputes` row after triage — rejected as the status quo, because it adds a manual step for every rider dispute that the driver side and the backend contract already avoid, and it's the reason the compliance lane's 60-day-window finding matters less in practice than it should (a window that's rarely reached because riders rarely reach the endpoint that starts the clock is not evidence the window is safe to leave unenforced).
- Blast radius: new UI only; `POST /disputes` already accepts a rider caller with no backend change needed. Existing consumers (driver-app, admin-dashboard) unaffected by adding a third caller.
- Rollout: additive, flag if desired (new user-visible action on a payments-adjacent surface — CLAUDE.md gate 3).
- Verification to close: rider-app test asserting a rider can submit a dispute from `ride-details.tsx` and it appears in `GET /disputes` for that user; confirm the compliance lane's dispute-window fix (if implemented) is exercised by the same test once both land.

### RIDERJ-004 — Vehicle-type option cards (primary booking selector) have no accessibility label, role, or state
- Hierarchy: L2 Ride Booking & Matching › L3 Vehicle-type selection › L4 Option card component › L5 screen-reader path
- Severity: MEDIUM-HIGH   Priority score: S×B×L ≈ 2×3×3 = 18
- Status: VERIFIED   Existing item: new — narrower and more specific than A7's `A11Y-003` (which scoped only the WAV/quiet-ride toggles, not the option cards themselves; not a duplicate)
- Adversary: regulator/auditor (WCAG 2.1 AA — CLAUDE.md mandates it for customer-facing surfaces); plaintiff's lawyer (inaccessible core booking flow)
- Evidence: `rider-app/app/ride-options.tsx` — the `TouchableOpacity` rendering each vehicle-type option (name, ETA, price, surge badge, capacity, availability, selected checkmark; component body roughly lines 1566-1657) has no `accessibilityLabel`, `accessibilityRole`, or `accessibilityState` anywhere in its JSX. Confirmed by grep: `accessibilityLabel`/`accessibilityRole`/`accessibilityState` on this file collectively hit 12 times, and every hit maps to a *different* control (back button, promo-remove, WAV toggle, quiet-ride toggle, fare-breakdown expand/collapse, schedule-time clear, the final "Confirm"/"Schedule" button at line 1232) — none land inside the option-card component itself. The "selected" state is conveyed only by a visual checkmark icon (`styles.selectedCheck`, line ~1652) and a shadow/elevation change, both invisible to a screen reader.
- What happens (plain language): a screen-reader rider navigating the option list hears an unlabelled, unordered jumble of text nodes (vehicle name, then ETA, then a surge badge, then capacity, then price) per card with no indication which one is currently selected, whether a card is available, or that tapping it selects a ride type — for the single highest-stakes choice in the booking flow, while every other button on the same screen is correctly labelled.
- Root cause: the option-card component was likely built and iterated on visually (it has detailed selection/shadow/scale animation styling) without an accessibility pass; every other, simpler control on the screen got one.
- Recommendation: add `accessibilityRole="radio"` (or `"button"` with `accessibilityState={{ selected: isSelected, disabled: !isAvailable }}`) plus a composed `accessibilityLabel` (e.g. `` `${estimate.vehicle_type.name}, $${cardGrandTotal.toFixed(2)}, ${estimate.eta_minutes} minutes away${isAvailable ? '' : ', unavailable'}` ``) to the card's `TouchableOpacity`.   Alternative considered: wrap the whole scroll list in a single `accessibilityRole="radiogroup"` with per-card labels only — the per-card `accessibilityState.selected` approach is preferred because VoiceOver/TalkBack's radio-group semantics announce position ("2 of 4") for free, which a plain button list doesn't.
- Blast radius: isolated to this one component in `ride-options.tsx`; no other screen reuses this exact card (driver-app has no equivalent selector).
- Rollout: additive prop changes only, no logic change — safe to ship without a flag; still worth a manual VoiceOver/TalkBack pass before merge since rider-app has no automated a11y or visual tooling to catch a regression here.
- Verification to close: manual screen-reader pass confirming each card announces name/price/ETA/availability/selected state as one coherent unit.

### RIDERJ-005 — Scheduled-ride time picker/display has no explicit service-area timezone pin
- Hierarchy: L2 Ride Booking & Matching › L3 Scheduled rides › L4 Date/time picker & display › L5 timezone correctness for a traveling rider
- Severity: LOW-MEDIUM   Priority score: S×B×L ≈ 2×2×1 = 4 (narrow trigger population)
- Status: VERIFIED   Existing item: new — related to but distinct from A7's DST scenario row, which only checked the **backend scheduling math** (`scheduled_rides.py`, confirmed UTC-pinned and DST-safe); this finding is about the **client-side** picker/display, which A7 explicitly flagged as "Partial, unverified"
- Adversary: hostile clock/device (a rider's phone in a different system timezone than the Saskatchewan service area they're booking for)
- Evidence: `rider-app/app/ride-options.tsx:235-236,645,777,1177-1178,1191` and `rider-app/app/scheduled-rides.tsx:91-93` — every `Date` construction and every `toLocaleDateString`/`toLocaleTimeString('en-CA', …)` call in the scheduled-ride picker and list uses the device's current system timezone implicitly (no `timeZone` option passed, no `America/Regina`/service-area offset found anywhere in either file or in `shared/utils/`). Grepped both files plus `shared/utils/*.ts` for "Regina"/"Saskatoon"/"service_area.*timezone" — only an unrelated street-address string match.
- What happens (plain language): for the overwhelming majority of riders — physically in Saskatchewan, phone correctly set to Saskatchewan's timezone (CST year-round, no DST) — this is entirely correct and DST-proof, since the device zone *is* the service-area zone. The gap only surfaces for a rider who schedules a Saskatchewan pickup while physically outside Saskatchewan (e.g. booking a ride home from a trip): the picker interprets "3:00 PM" in the device's current zone, not the service area's, so the resulting UTC instant can be off by the zone difference from what the rider actually intended in Saskatchewan wall-clock time.
- Root cause: the picker and display both use the platform default (device-local) rather than pinning to the ride's service area.
- Recommendation: when constructing/displaying a scheduled-ride timestamp, pass an explicit `timeZone` derived from the ride's `service_area_id` (or a hardcoded `America/Regina`-equivalent constant if Spinr is single-timezone today per `regulatory-sk.md`'s Saskatchewan-first scope) rather than relying on the device's current zone.   Alternative considered: leave as device-local — reasonable as a *default* given how rarely riders book cross-timezone, but worth a one-line disclosure in the picker UI ("times shown in Saskatchewan time") as a cheaper interim fix if a full timezone-pin isn't prioritized now.
- Blast radius: `ride-options.tsx`'s scheduling UI, `scheduled-rides.tsx`'s list display — both isolated, no shared component identified that would widen this.
- Rollout: additive, no migration; low urgency given narrow trigger population.
- Verification to close: a test that mocks the device timezone to something DST-observing and asserts the picker either pins to the service-area zone or the UI discloses which zone is shown.

### RIDERJ-006 — Two A7 rows corrected by direct code read: WS-reconnect REST resync (upgrade to Handled), rider/driver WS-replay-protocol asymmetry (new)
- Hierarchy: L2 Ride Fulfillment › L3 Real-time sync › L4 WebSocket reconnect
- Severity: INFO / LOW   Priority score: n/a (mostly a positive correction; the asymmetry sub-finding is LOW)
- Status: VERIFIED   Existing item: n/a (corrects/extends A7, doesn't duplicate it)
- Adversary: hostile network (reconnect-timing race)
- Evidence: **(a) Upgrade.** `rider-app/hooks/useRiderSocket.ts:312-327` — `ws.onopen` unconditionally calls `useRideStore.getState().fetchRide(rideId)` with an explicit comment that server-sent events are not buffered for a disconnected client, so the client must pull fresh state on every reconnect. This fires on the very first connect and on every subsequent reconnect (the same handler), resolving A7's "did not verify whether reconnect triggers an explicit `fetchActiveRide()` refetch" gap directly: it does.
  **(b) New.** The backend does, separately, support a sequenced replay-outbox protocol for exactly this gap (`backend/routes/websocket.py:950-965` — `pubsub.get_outbox(connection_key)` replays any message with `seq > last_seq`), and `driver-app/hooks/useDriverDashboard.ts:1360` opts into it by appending `?last_seq=${lastSeqRef.current}` to its WS URL. `rider-app`'s equivalent connect call sends no `last_seq` parameter anywhere (grepped rider-app for `last_seq` — zero hits) — the rider client always reconnects "cold" and relies entirely on the coarser full-state `fetchRide()` refetch rather than the finer-grained replay the server and driver-app both support.
- What happens (plain language): in practice this is not a stranding bug — `fetchRide()` correctly repopulates ride state — but it means any WS-*only* signal that isn't reflected in the ride row itself (chat messages are the one clear example; the comment in `useRiderSocket.ts` even notes "chat screen polls its own messages for now" as the acknowledged workaround) would not be recovered on rider reconnect the way it would be for the driver. This is an undocumented client asymmetry, not present in `docs/known-forks.md`.
- Root cause: the sequenced-replay protocol was likely added for the driver client first (driver-side state is more real-time-sensitive — accept/offer timing) and never backported to the rider client, which already had a "good enough" full-refetch fallback.
- Recommendation: either wire `last_seq` into `useRiderSocket.ts`'s connect call to match driver-app (cheap, mechanical, closes the asymmetry) and register the pair in `docs/known-forks.md` if a deliberate reason to keep them different ever emerges, or explicitly document that rider-app intentionally relies on full-refetch-only as a simpler, sufficient strategy.   Alternative considered: leave as-is — acceptable near-term given the full-refetch fallback covers ride-state correctness; not acceptable to leave silently undocumented given `docs/known-forks.md` exists specifically to catch this pattern.
- Blast radius: `useRiderSocket.ts`'s connect function only; no backend change needed (the server already accepts an optional `last_seq`).
- Rollout: additive, no flag needed — purely makes an existing, already-supported server capability reachable from one more client.
- Verification to close: a WS integration test asserting a rider reconnecting with a `last_seq` receives any missed sequenced messages (e.g. a `stops_updated` sent while disconnected) via replay rather than only via the next full `fetchRide()`.

---

## (e) Feature completeness — rider L3 features

Dimensions per greenfield-extensions §3: `UX · API · backend · DB · events/WS ·
authz · audit log · privacy · compliance · analytics · metric+alert · tests (real) ·
support article · docs · training · incident runbook · rollback/flag · owner`.
Given this lane's time budget, cells are scored Y/N/Partial/Unknown at the row level
with the governing evidence, not exhaustively re-derived per dimension (most
dimensions beyond UX/API/backend/tests were not independently checked this pass —
marked Unknown rather than guessed).

| L3 feature | UX | API/backend | Events/WS | Tests | Support article | Verdict |
|---|---|---|---|---|---|---|
| Address/pickup/dropoff selection | Y | Y | N/A | Y | Unknown | Complete for its scope |
| Vehicle-type selection & booking | Y (a11y gap, RIDERJ-004) | Y | Y | Y | Unknown | Functionally complete, a11y gap |
| Fare estimate & surge disclosure | Y | Y | N/A | Not independently checked | Unknown | Complete — surge acknowledgment modal present pre-booking |
| Scheduled rides | Y (timezone gap, RIDERJ-005) | Y | Unknown (re-offer/cancel path not traced) | Y | Unknown | Mostly complete, narrow display gap |
| Matching/wait screen | Y | Y | Partial (DISPATCH-001: no rider WS push for `driver_assigned`, mitigated by 3s poll) | Y | Unknown | Functional, degraded real-time signal |
| In-trip tracking | Y | Y | Y | Y | Unknown | Complete |
| Mid-trip stop editing | **N — RIDERJ-001** | Y (backend only) | Y (backend only) | Unknown | Unknown | **Incomplete — backend without UX** |
| Trip completion / payment | Y | Y (R9 territory beyond UX) | Y | Y | Y (payment_failed deep-link) | Complete |
| Receipt | Y | Y | N/A | Y | Unknown | Complete; consolidated line-item design is deliberate (see INFO note below) |
| Rating & tip | Y | Y | N/A | Y | Unknown | Complete |
| Dispute/refund (rider-initiated) | **N — RIDERJ-003** | Y (backend only, role-agnostic) | N/A | N/A (no UI to test) | Unknown | **Incomplete — driver/admin only** |
| Support (generic) | Y | Y | N/A | Y | Y (FAQ endpoint) | Complete |
| Lost & found | Y | Unknown (backend not read) | Unknown | Y | Unknown | Likely complete, backend not verified |
| SOS/safety | Y (button present) | Out of scope (R11) | Unknown | Y (multiple SOS test files) | Unknown | Out of this lane's depth |
| Wallet/payment methods | Y | Out of scope (R9) | Unknown | Y | Unknown | Out of this lane's depth |
| Promotions/referral/loyalty | Y | Out of scope (R11/R9) | Unknown | Y | Unknown | Out of this lane's depth |
| Corporate rider (work profile/allowance) | Y (pre-booking banner only) | Y | Unknown | Y | Unknown | Mid-trip-exhaustion messaging not confirmed |
| Saved places | Y | Unknown (backend not read) | N/A | Y | Unknown | Likely complete; no a11y labels found (light check) |
| Notifications inbox | Y (known fork, cited A7 INFO-004) | Y | Y | Y | Unknown | Complete but forked from driver-app's pattern |
| Account settings / privacy | Y | Y | N/A | Y | Unknown | Complete |
| Account deletion | Y | Y (PIPEDA-aligned, actionable 409s) | N/A | Y | Unknown | Complete |
| Reactivation | Y | Y (OTP login) | N/A | Y | Unknown | Complete |
| Accessibility settings screen | Y (dedicated screen) | N/A | N/A | Y | Unknown | Present — screen exists, not audited for its own correctness this pass |

**INFO — receipt line-item consolidation is a deliberate, sound design, not a gap.**
`backend/services/fare_service.py:303-392` (`build_fare_breakdown_lines`) explicitly
merges base+distance+time into one "Ride fare (X km)" line — but does so having
*already* proven the total is unchanged (code comment: "the pre-surge ride fare + the
surge delta sum back to the exact same total"), while keeping booking fee, airport
surcharge, surge (shown with its multiplier, never hidden), and every tax line item
**separate**, matching CLAUDE.md's "every charge maps to a disclosed line item" intent
in spirit even though it technically collapses 3 of the 7 named items into 1 for
readability. Surge is also explicitly gated behind a rider-acknowledgment modal before
booking (`ride-options.tsx:666-681`) — this satisfies "surge must be visible before
booking, never applied retroactively" directly. Not filed as a finding; recorded per
the ground rules' steelman-first instruction so a future audit doesn't re-flag it.

---

## (f) §7.3 Rebuild Delta card — rider booking experience

## Epic: Ride Booking & Matching (rider booking experience)
- Verdict per inherited pattern: **MODIFY** (the core flow — estimate, surge
  disclosure, idempotent request, at-most-one-active-ride guard — is sound and
  should be kept; the gaps found this pass are additive fixes, not a rebuild case).
- Keep (already best-in-class): client-generated idempotency key bucketed by
  pickup/dropoff/time-window backed by a real DB unique constraint
  (`idx_rides_rider_idempotency_key`) — stronger than the client-side-only
  double-tap guard most apps at this stage rely on as their only layer; the
  pre-booking surge-acknowledgment modal, which does exactly what "surge must be
  visible before booking, never retroactive" requires; the consolidated,
  arithmetic-proven receipt line-item design.
- Uber/Lyft do: both show a live "driver found, confirming" intermediate state
  between request and accept (source: general rider-facing product knowledge, not
  independently fetched this pass — mark ASSUMED); both support in-trip stop
  editing from the rider side; both expose a structured, ride-linked dispute flow
  to riders directly in the app, not routed through generic support.
  Spinr today: has the UI *built* for the "driver confirming" state
  (`ride-status.tsx`'s `driver_assigned` copy/countdown) but it's WS-silent and
  reachable only via a 3s poll (DISPATCH-001); has the *backend* for mid-trip
  stops but no UI (RIDERJ-001); has the *backend* for a structured rider dispute
  but no UI (RIDERJ-003).
- Clean-sheet Spinr would: treat "backend capability ⇒ UI parity across both
  clients" as a release gate, not an assumption — the pattern in this lane (three
  separate instances of a fully-built backend endpoint with no rider-app caller)
  suggests backend and rider-app work streams are decoupling in a way that's
  invisible until an audit like this one greps for callers.
- Why (the edge it creates): closing RIDERJ-001/002/003 converts three points
  where a rider's only recourse today is "contact support" into self-serve paths
  — directly moving the needle on the charter's core question and on support
  cost/CSAT, without touching money logic or ride-state legality at all (every
  fix identified is additive UI wired to an already-existing, already-tested
  backend contract).
- How (architecture/pattern): no new backend architecture needed for
  RIDERJ-001/003 — wire existing endpoints. RIDERJ-002 is UI-only (surface
  staleness the backend already computes). RIDERJ-004/005 are prop-level fixes.
  Who (role/owner): rider-app team. When: **Now** for RIDERJ-004 (pure a11y prop
  addition, no product decision needed); **Next** for RIDERJ-002/003 (small,
  well-scoped, needs a short design pass for copy/CTA placement); **Next/Later**
  for RIDERJ-001 depending on whether mid-trip stop editing is actually wanted as
  a rider-facing feature (product decision, not purely technical).
- Incremental path from today (no big-bang): step 1 — ship RIDERJ-004 (a11y
  props, no flag needed) → step 2 — add the support deep-link for RIDERJ-002
  (additive, flagged) → step 3 — wire `POST /disputes` into `ride-details.tsx`
  for RIDERJ-003 (additive, flagged, small design pass on reason-picker UX) →
  step 4 — product decision on RIDERJ-001, then build if yes.
- Cost/effort: RIDERJ-004 = S. RIDERJ-002 = S-M. RIDERJ-003 = M. RIDERJ-001 = M-L
  (needs a design pass for the re-price-confirmation moment, not just wiring).
  Risk: low for all four (additive, no ride-state or money-path changes).
  Reversibility: high (flag off, or simply hide the new UI entry point).
  Build vs buy: build — all four are thin UI layers on existing/owned backend
  contracts, nothing to buy.
- Advantage type: operational/trust (closing RIDERJ-002/003 reduces support load
  and improves dispute-resolution speed/consistency — a trust and cost advantage,
  not a flashy feature; easy for a competitor to match once they think to look,
  so not deeply defensible, but currently a real gap vs. the stated Uber/Lyft
  baseline).
- "Why not?": the likely reason these three gaps exist is ordinary
  cross-team/cross-sprint drift (backend shipped ahead of, or independent of, the
  rider-app UI), not a considered decision — nothing in the code comments or
  `ACTION_ITEMS.md` suggests these were deliberately deferred. A simpler
  mitigation than a full audit-driven fix pass would be a lightweight
  "backend endpoint has zero client callers" CI check (grep-based, cheap) added
  to the same discipline that already produces `docs/known-forks.md` and the
  Cartographer's orphan-row detection — catching this class of gap continuously
  instead of only at audit time.

---

## (g) Accessibility labels — booking-flow primary actions

Scope: every primary (state-changing or navigation) control on `ride-options.tsx`
(the core booking screen) plus the immediately adjacent pickup/destination screens.
Reasoned about from source, not screen-reader-tested (per the top-of-file
disclosure).

| Control | Screen | `accessibilityLabel`/equivalent present? | Evidence |
|---|---|---|---|
| Back button | `ride-options.tsx` | Yes | `accessibilityLabel="Go back"` (line 916) |
| Remove promo code | `ride-options.tsx` | Yes | `accessibilityLabel="Remove promo code"` (line 1039) |
| Request WAV toggle | `ride-options.tsx` | Yes | `accessibilityLabel="Request wheelchair-accessible vehicle"` (line 1077) |
| Request quiet ride toggle | `ride-options.tsx` | Yes | `accessibilityLabel="Request quiet ride"` (line 1092) |
| Expand/collapse fare breakdown | `ride-options.tsx` | Yes | dynamic label based on state (line 1103) |
| Clear scheduled pickup time | `ride-options.tsx` | Yes | (line 1205) |
| Confirm/Schedule ride (primary CTA) | `ride-options.tsx` | Yes | dynamic label incl. vehicle type (line 1232); also `accessibilityState.busy` while booking (A7-cited) |
| **Vehicle-type option card (select ride type)** | `ride-options.tsx` | **No — RIDERJ-004** | no `accessibilityLabel`/`Role`/`State` found in the card component |
| Add a stop (pre-booking route planning) | `search-destination.tsx` | Yes | `accessibilityLabel="Add a stop"`, `accessibilityHint="Adds an intermediate stop to your route"` (lines 609-610) |
| SOS button (floating, in-trip) | `ride-in-progress.tsx` | Not independently re-checked this pass | component (`RiderSOS`) present at two call sites (lines 671, 729); its internal a11y props not read |
| Pickup pin drag / recenter | `confirm-pickup.tsx` | Not checked this pass | — |
| Saved-place row / add favorite | `saved-places.tsx` | **No** (light check) | grepped file for `accessibilityLabel` — zero hits |

---

## (h) NOT VERIFIED

- Driver-side of every scenario in §3.2/§3.3 (no-show timer, wait-time fee
  attribution, GPS-drift-to-state-transition plausibility bounds beyond the one
  cited call site) — out of this lane's rider-first scope; would need R5/R8 depth.
- `_check()` in `stale_in_progress_ride_alerter.py` — whether a real (non-stubbed)
  test exists for it; not located in the `__tests__` listing checked this pass.
- Whether the 409 "You already have an active ride"/"already have a scheduled
  ride" responses are surfaced verbatim to the rider by `ride-options.tsx`, or
  fall back to a generic error toast — the client-side rendering of these
  specific 409s was not read this pass.
- Whether a live "allowance running low" banner exists anywhere *during* an
  active corporate-paid trip (only the pre-booking banner and the post-hoc push
  notification were confirmed); the company-suspended-mid-trip fallback chain if
  the master-wallet reversal itself also fails — money-path depth, R9/R6
  territory.
- Guest booking, minor-unaccompanied handling, and extreme-cold/winter-mode
  features — searched for and not found in the files read this pass, but the
  search was not exhaustive enough to assert "does not exist anywhere in the
  codebase" with full confidence; recorded as gaps found, not proven absent.
- Contrast ratios, dynamic-type scaling, and any rendered/screen-reader
  verification anywhere in rider-app — no tool available in this environment;
  every accessibility claim above is a source-code read, consistent with the
  top-of-file disclosure.
- `routes/lost_and_found.py` (or equivalent) backend abuse/fee-evidence
  controls, `docs/runbooks/payment-dispute-evidence.md`, `cancellation_service.py`'s
  fault-attribution logic, `route_deviation_alerter.py` — named in the scenario
  table as out of scope, not independently read.
- Full cross-check of every rider L3 feature against `docs/PRD.md` for whether a
  gap (e.g. guest booking) is a genuine missing requirement vs. deliberately
  out of scope — this lane did not open `docs/PRD.md` to verify PRD intent
  per feature, only checked code presence/absence.
- `rider-app`'s own bundle size, cold-start time, and any winter/low-connectivity
  specific behavior beyond what's covered generically by the retry/idempotency
  layer already cited from A7 — not measured or read this pass.

---

## (i) Human-only questions

- Is mid-trip stop editing (RIDERJ-001) an intended near-term rider feature, or
  should the backend capability be documented as descoped/backend-only for now?
  This determines whether RIDERJ-001 is a build task or a documentation task.
- What is the acceptable time-to-human-response for a stale `in_progress` ride
  alert (RIDERJ-002)? The current 5-15 minute detection-to-admin-notice window
  (10 min staleness threshold + up to 5 min tick + however long until an admin
  looks at Sentry) needs an operational SLA decision, not just a code fix — is
  there an on-call rotation that watches for this specific alert tag today?
- Should the rider-side dispute UI (RIDERJ-003) reuse the exact reason taxonomy
  the driver-app uses, or does product want rider-specific reasons (e.g. "wrong
  route," "driver issue," "overcharged") distinct from whatever the driver-app
  offers — worth confirming before building both sides toward the same enum.
- Is guest booking (sweep-catalog §3.1 #10) in scope for Spinr at all? Nothing in
  the code or this pass's reading of `docs/PRD.md`'s footprint (not opened this
  pass) confirms either way.
- Does Spinr have a winter-operations product decision recorded anywhere
  (greenfield-extensions §12's flagged gap)? If a decision exists but wasn't
  implemented, that's a different priority than if it was never decided.

---

## (j) Escalations

- **RIDERJ-002** and **RIDERJ-003** both touch a live-tested, support-adjacent
  surface (CLAUDE.md's "rides" and "payments" domains) — per CLAUDE.md gate 9,
  flagging here rather than assuming the recommended fix's scope/copy is
  correct without a product/support-team sign-off on the exact wording shown to
  a rider mid-incident (RIDERJ-002) or the dispute-reason taxonomy (RIDERJ-003).
- **RIDERJ-001**'s recommendation depends entirely on a product decision (build
  vs. formally descope) this lane cannot make — escalating rather than assuming
  "build it" is the right call.
- The **RIDERJ-006(b)** WS-replay asymmetry (rider client never sends `last_seq`)
  is exactly the kind of drift `docs/known-forks.md` exists to catch and doesn't
  yet — recommend either registering it there with a stated reason, or fixing it,
  rather than leaving it as an undocumented, unintentional-looking difference
  between the two mobile clients.
