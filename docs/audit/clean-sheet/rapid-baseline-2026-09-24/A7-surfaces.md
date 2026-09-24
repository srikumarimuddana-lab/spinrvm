# A7 — Surfaces: rider, driver, admin, website

**Lane:** A7 · **Model:** sonnet / spinr-edge-case-reviewer · **Returned:** 2026-09-24 ~14:28 UTC (partial, 25-minute time box) · **Orchestrator note:** lane output pasted verbatim below; only this header was added. No `website/` directory exists in the repo (lane confirmed). rider-app/driver-app have no visual-regression tooling — every UI claim here is reasoned about from code, not screenshotted.

---

SPINR EDGE-CASE AUDIT — Lane A7 (rider-app, driver-app, admin-dashboard; no `website/`/marketing dir exists in repo, confirmed by directory listing)

TIME-BOX NOTE: partial sweep within budget. Depth achieved on booking/payment retry-safety, app-lifecycle reconciliation, token-refresh race, WS reconnect, OTA/forced-upgrade mid-ride, GPS plausibility, and 3 known-forks. Lighter/INFERRED-only on: driver phone-dies-mid-trip backend sweep internals, admin corporate-account concurrent-edit (checked drivers.py only, not corporate_accounts.py), scheduled-ride DST display formatting, WCAG contrast (no tool), bundle-size measurement (no analyzer run).

## (a) §7.2 SCENARIO TABLE

| Scenario | Actor | Trigger | Expected (industry) | Spinr today (evidence) | Status | Dispute risk |
|---|---|---|---|---|---|---|
| App killed mid-ride, relaunched | Rider | Force-kill during `driver_assigned`→`in_progress` | Reconcile to real server state on relaunch, not stale local UI | `rider-app/app/_layout.tsx:807-849` foreground `AppState` handler calls `fetchActiveRide()` and routes to `targetPathForRideStatus`; separately reloads JS bundle if backgrounded >5min (`STALE_THRESHOLD_MS`, line 810). Tested: `rider-app/store/__tests__/rideStore.restart.test.ts` pins `hydrateActiveRide()` (optimistic AsyncStorage restore + validate against `GET /rides/active`) | **Handled** | Low |
| Network drop during booking (double-tap / retry) | Rider | Timeout after tapping "Request ride" | No duplicate ride/charge; idempotent | `rider-app/store/rideStore.ts:883-888` sends `Idempotency-Key` (pickup/dropoff/2-min bucket); server dedups via DB unique index (`backend/routes/rides/booking.py:469-478`, `:1656-1670`, constraint `idx_rides_rider_idempotency_key`) — returns existing ride on both pre-check and DB-race paths. `ride-options.tsx:693-694` also has a client-side `isBooking` re-entrancy guard | **Handled** | Low |
| Network drop during payment/pay-now | Rider | Timeout submitting fare payment on completed ride | No duplicate charge | `rider-app/app/ride-completed.tsx:398-399,519` — `isSubmitting` guard ("prevent double tap"), settlement keyed by `rideId` not a new payment record (per code comment at line 219) | **Handled** (money-math depth out of this lane's scope — see A5) | Low |
| Duplicate tap "Request ride" / "Pay" | Rider/Driver | Fast double-tap | Second tap no-ops, no dupe | Booking: `isBooking` guard, disables button + `accessibilityState.busy` (`ride-options.tsx:1229-1230`). Payment: `isSubmitting` guard (`ride-completed.tsx:932,961`). Driver accept/start/complete buttons: `disabled={isLoading}` throughout `ActiveRidePanel.tsx` | **Handled** | Low |
| Token refresh race (concurrent 401s) | Rider/Driver | Two in-flight requests both 401 near token expiry | One refresh call, others queue and retry with new token | `shared/api/client.ts:126-270` — `_refreshPromise` dedup + `_refreshSubscribers` queue; proactive refresh (`ensureFreshToken`, 2-min buffer) on foreground + 60s interval to pre-empt the race entirely (`rider-app/app/_layout.tsx:857-869`, mirrored in driver-app). Refresh-token rotation race between foreground and driver-app's background location task specifically handled (`client.ts:991-1007`) | **Handled** | Low |
| Stale ride state after WS reconnect | Rider | WS drops and reconnects mid-ride | Client re-syncs state, doesn't trust stale cached status | `rider-app/hooks/useRiderSocket.ts` — exponential backoff [1,2,5,10,30s] (line 36), resets attempt counter and reconnects on `AppState` foreground (lines 416-431). Did **not** verify in this pass whether reconnect triggers an explicit `fetchActiveRide()` refetch vs. relying solely on the next WS event to correct state — **INFERRED gap, not confirmed** (A4 found a server-side sequence-numbered replay outbox at `backend/routes/websocket.py:960-968`, which narrows this) | **Partial (unverified)** | Medium |
| Driver phone dies mid-trip | Rider | Driver's app stops sending location/heartbeat | Trip completes via server-side fallback, not left open forever; rider told | `CLAUDE.md` lists a "stuck-ride sweeper" background loop (`backend/core/lifespan.py`); not read in this pass to confirm its trigger threshold or rider-facing messaging | **Handled (INFERRED from doc, not read)** | Medium — confirm sweeper threshold and rider UX copy |
| Rider changes destination mid-trip | Rider | Taps "add/change destination" during `in_progress` | Re-price disclosed before applying | Not read this pass — flagged as **UNKNOWN**, out of time-box | **Unknown** | Medium |
| Time-zone/DST (Saskatchewan, no DST) | System | Scheduled ride / reminder crossing a DST boundary elsewhere-observing default | Computations pinned to a fixed offset, not a DST-following default | `backend/utils/scheduled_rides.py` uses `datetime.now(timezone.utc)` / `timezone.utc` throughout (lines 14, 326, 447, 846) — UTC-pinned, DST-safe for scheduling math. Client-side "15 min from now" check in `ride-options.tsx` compares device-local deltas to itself (DST-neutral). Did not verify admin-dashboard *display* formatting of scheduled times uses a fixed `America/Regina`-equivalent offset rather than a DST-aware JS `Intl`/date-lib default | **Handled (scheduling logic); Partial (display, unverified)** | Low |
| Background location permission revoked mid-ride | Driver | Driver revokes location permission while backgrounded, mid-trip | Rider sees "driver location unavailable" rather than a frozen marker forever | `rider-app/components/DriverLocationStatus.tsx` — shows "Waiting for driver location" / "Location updates delayed · showing last known position" once `capturedAt` age exceeds 20s, `accessibilityLiveRegion="polite"`. This degrades gracefully by **elapsed time**, not by an explicit permission-revoked signal from the driver device — so it correctly fails loud on staleness regardless of cause | **Handled** | Low |

## (b) FINDING CARDS

### CONCURRENCY-001 — Admin driver-record edit has no staleness/conflict check (last-write-wins)
- Hierarchy: L2 Admin/Ops › L3 Driver management › L4 Edit driver record › L5 concurrent edit
- Severity: MEDIUM   Priority score: S×B×L = 2×2×2 = 8
- Status: VERIFIED   Existing item: new (grepped `ACTION_ITEMS.md` for "concurrent edit"/"last-write-wins"/"optimistic lock" + admin driver context — only hit is C66, which is scoped to `admin_complete_ride`, not driver-record edits)
- Adversary: two internal admins (or an admin + a background reconciliation job) editing the same driver simultaneously
- Evidence: `backend/routes/admin/drivers.py:1771` `admin_update_driver` — no `updated_at`/version/ETag check on the write. `admin-dashboard/src/app/dashboard/drivers/page.tsx:495-522` `saveEdits()` diffs the submitted form against the `selected` snapshot captured when the edit dialog was opened, then PUTs only the diffed fields — reduces (but does not eliminate) blast radius vs. a full-record overwrite: if two admins both edit the *same* field from two stale snapshots, the second save silently wins with no conflict signal to either admin.
- What happens (plain language): Admin A opens driver X's edit dialog. Admin B opens the same dialog seconds later and changes, say, `vehicle_make`. Admin A — unaware — edits `vehicle_make` differently based on their now-stale snapshot and saves. Admin B's change is silently overwritten; neither admin is told.
- Root cause: `PUT /admin/drivers/{driver_id}` accepts a raw field dict with no precondition (`If-Match`/`updated_at` check) on the current row.
- Recommendation: pass the row's `updated_at` (or a version column) in the edit-dialog payload; server compares against current `updated_at` before applying and returns 409 on mismatch, prompting the admin to reload.   Alternative considered: full audit-log diff review after the fact — rejected because it doesn't prevent the clobber, only reveals it post-hoc, and this field set includes SGI-approval/regulatory fields where a silent revert has compliance consequences.
- Blast radius: same pattern likely exists on other admin PUT/PATCH endpoints without a version check — this pass only read `drivers.py`; `corporate_accounts.py`/other admin editors not checked in this lane.
- Rollout: additive — new optional `expected_updated_at` field, 400/409 only when provided and mismatched, so old admin-dashboard builds keep working during rollout (backward-compatible, matches CLAUDE.md's additive-over-destructive gate).
- Verification to close: unit test simulating two sequential PUTs with a stale `expected_updated_at` on the second, asserting 409.

### RELIABILITY-002 — Dead cookie-based `apiClient.ts` co-exists with the real fetch-based `shared/api/client.ts`, inconsistent retry/idempotency model if ever revived
- Hierarchy: L2 Rider App › L3 API layer › L4 HTTP client
- Severity: LOW   Priority score: S×B×L = 1×1×1 = 1
- Status: VERIFIED   Existing item: new
- Adversary: none directly — this is a dead-code/drift risk, not an active exploit
- Evidence: `rider-app/utils/apiClient.ts:1-58` — an Axios instance with `withCredentials: true` (cookie-based auth) and a naive 401→refresh→retry interceptor with **no refresh-dedup, no subscriber queue, no 503/429 handling, no idempotency-key support** — a materially less safe implementation than `shared/api/client.ts`. `grep` across `rider-app/` for importers of `utils/apiClient` returns zero hits — nothing in the app imports this file.
- What happens (plain language): no current user-facing effect (unused). Risk is latent: a future engineer, grepping for "the api client," finds this file first (it's in `utils/`, closer to app code than `shared/api/`) and wires a new screen to it, silently regressing on every retry-safety property documented above.
- Root cause: dead file never removed after the app migrated from cookie-based to header/SecureStore-based auth (evidenced by the file's `withCredentials`/cookie model being fundamentally incompatible with the in-memory-token + SecureStore model `shared/api/client.ts` uses everywhere else).
- Recommendation: delete `rider-app/utils/apiClient.ts` (surgical — file has no importers, is safe to remove) or, if the user wants it kept as historical reference, add a top-of-file comment marking it dead/unused and pointing to `shared/api/client.ts`.   Alternative considered: leave as-is — rejected, this is exactly the kind of silent one-way drift `docs/known-forks.md` exists to catch, except this fork has no registry entry and no active sibling relationship (it's not "intentionally forked," it's abandoned).
- Blast radius: zero current callers (confirmed by grep); safe, isolated removal.
- Rollout: n/a (no flag needed, additive-safe deletion of dead code — CLAUDE.md gate 2 doesn't apply since nothing observes this file at runtime).
- Verification to close: `grep -r "utils/apiClient" rider-app/` returns nothing after removal; existing test suite unaffected (no test imports it either, per the same grep).

### A11Y-003 — WAV/service-animal accommodation UI not independently audited this pass (regulatory-flagged surface)
- Hierarchy: L2 Rider App › L3 Booking › L4 Accessibility accommodations
- Severity: MEDIUM   Priority score: S×B×L = 2×2×1 = 4
- Status: PARTIAL-VERIFIED (spot-checked, not exhaustive)   Existing item: new (not found in ACTION_ITEMS via grep)
- Adversary: regulator (Saskatchewan Human Rights Code, WCAG 2.1 AA)
- Evidence: `rider-app/app/ride-options.tsx:1064-1077` — WAV toggle has `accessibilityLabel="Request wheelchair-accessible vehicle"` and dims (`opacity:0.45`) + `disabled` when `wavCount===0`, but the disabled/unavailable state is conveyed via **opacity alone** on the row (`styles.optionRow, wavDisabled && {opacity:0.45}`, line 1064) with a text label change ("No WAV drivers nearby") accompanying it — so this specific case does pair color/opacity with text, which is correct. Not verified: whether a screen reader announces the *reason* wav is disabled (`disabled` prop alone doesn't narrate "no drivers nearby" to VoiceOver/TalkBack without an explicit `accessibilityHint` or the label incorporating it — the label text itself does state "No WAV drivers nearby" per line 1069, which is good, INFERRED to be announced since it's `accessibilityLabel`, not just visual text).
- What happens (plain language): likely compliant based on code read, but this is reasoned-about, not screen-reader-tested (per accessibility-reviewer's mandatory tooling-gap disclosure).
- Root cause: n/a — this is a coverage note, not a defect.
- Recommendation: keep as a manual-verification item for the next full a11y pass; no code change indicated by what was read.   Alternative considered: n/a.
- Blast radius: n/a.
- Rollout: n/a.
- Verification to close: manual VoiceOver/TalkBack pass on `ride-options.tsx`'s WAV/quiet-ride toggles specifically.

### INFO-004 — `known-forks.md` notifications.tsx divergence confirmed still present, unchanged since 2026-09-14 entry
- Hierarchy: L2 Rider/Driver App › L3 Notifications inbox
- Severity: RECOMMENDATION   Priority score: n/a
- Status: VERIFIED   Existing item: already tracked (`docs/known-forks.md` row 2, "currently-undecided divergence")
- Adversary: n/a (maintenance/drift risk, not a live bug)
- Evidence: `rider-app/app/notifications.tsx:1,111,117-118` hand-rolls `useState` for notification list/loading; `driver-app/app/driver/notifications.tsx` does not show the same hand-rolled pattern in its import list (checked lines 1-31) — consistent with the registry's claim that driver-app goes through the shared query hooks and rider-app doesn't. No mechanical parity guard exists for this fork (registry states this explicitly). Not re-litigating — de-duped per this lane's instructions since it's already an open, documented item; flagging only that the sweep independently confirms the registry's description is still accurate as of this pass, not stale.
- What happens: no new information — confirms existing tracked gap is real and unresolved.
- Recommendation: none beyond what the registry already states (reconcile onto the shared hook, or formally accept the fork with a parity guard).
- Verification to close: n/a — tracked in registry, not re-opening.

### INFO-005 — GPS plausibility, OTA-mid-ride carve-out, and DST-safe scheduling are already correctly handled (steelman positives, not findings)
- Severity: PASS
- Evidence: `backend/routes/drivers/ride_flow.py:933-954` references `evaluate_gps_plausibility()`/`check_location_integrity()` gating location-derived ride-state writes against "impossible speed/teleport" before a location update is trusted — addresses sweep-catalog §3.2 item 12 directly. `backend/core/middleware.py:221-349` (`_is_ride_carveout_path`) exempts a driver's own active-ride completion/location endpoints from the forced-upgrade 426 gate, explicitly citing "a min version bump landing mid-trip strands a passenger" — addresses §3.6 item 44/48. `backend/utils/scheduled_rides.py` computes entirely in `timezone.utc` — addresses §3.6 item 47's DST concern for the scheduling *logic* (display formatting elsewhere unverified, see scenario table).
- Why flagged as INFO rather than skipped: per the ground rules' steelman-first mandate, these are exactly the "why was it built this way" positives worth recording so a future audit doesn't re-flag them as gaps without reading the code.

## (c) FOUR-STATES TABLE (8 sampled screens)

| Screen | Loading | Empty | Error | Success |
|---|---|---|---|---|
| rider-app `ride-options.tsx` | Yes — `isLoading`/skeleton (line 972) | Yes — `allUnavailable` branch (line 956) | Yes — `fetchError`, "Tap to retry" (line 356) | Yes |
| rider-app `wallet.tsx` | Yes — `ActivityIndicator` (lines 281,305) | Not verified this pass | Yes — "Tap to retry" (line 220) | Yes |
| rider-app `notifications.tsx` | Yes — `isLoading` branch (line 277) | Not explicitly verified (list component likely handles; not read) | Yes — `isError` + retry button (lines 289,301) | Yes |
| rider-app `promotions.tsx` | Yes — `ActivityIndicator` (line 131) | Not verified this pass | Yes — `catch(err)` present (line 58); explicit error UI not confirmed | Yes |
| driver-app `driver/notifications.tsx` | Yes — `ActivityIndicator` (line 339) | Not verified this pass | Yes — retry button (line 346) | Yes |
| driver-app `ActiveRidePanel.tsx` | Yes — `isLoading` prop threaded through buttons (line 132+) | N/A (not a list screen) | Yes — inline error + retry per code comment (line 477) | Yes |
| admin-dashboard `dashboard/drivers/page.tsx` | Yes — `loading` state (line 50) | Not verified this pass | Yes — toast on every mutation failure (7 sites, e.g. lines 415,429,598) | Yes |
| admin-dashboard `dashboard/monitoring/page.tsx` | Not explicitly grepped for a loading flag | Yes — explicit empty-state hint for vehicle-type toolbar (comment line 436) | Yes — rides kept in place rather than swallowed to `[]` on error (comment line 321) | Yes |

Reasoned-about-not-screenshotted disclosure: rider-app and driver-app have zero visual-regression tooling per CLAUDE.md; the above is static code reading of state-branch presence, not a rendered/visual verification. admin-dashboard has real Playwright visual-regression on 6 seeded pages (per CLAUDE.md) but `dashboard-drivers` behavior beyond the seeded snapshot (e.g. toast copy on a specific failure) was not re-verified against a live run in this pass.

## (d) STEELMAN BULLETS

- The retry/idempotency layer (`shared/api/client.ts`) is unusually mature for a live-testing product: refresh-dedup, subscriber queueing, a bounded single 503-retry with a documented non-idempotent exclusion list (OTP verify), SOS-exempt from the 401 auth-clear path, and an explicit engine-vs-network-vs-backend error taxonomy so a JS crash never reaches a rider as raw "undefined is not a function" copy. Every non-obvious choice has an inline comment citing the incident that motivated it (SPR-T9NYPB, the 2026-08-24 alert storm, the 12.12→16.46km re-price incident) — this is exactly the "read the ADRs/comments before assuming it's wrong" discipline the ground rules ask for.
- Booking and payment retry-safety were clearly built with the exact failure mode this lane audits in mind: client-generated idempotency key bucketed by time+coordinates, backed by a real DB unique constraint with a race-condition fallback path (`idx_rides_rider_idempotency_key`) — this is stronger than a client-side-only "don't double tap" guard, which most of this codebase also has as a second layer.
- App-lifecycle reconciliation on both foreground-resume and app-kill-relaunch is implemented and has a dedicated regression test (`rideStore.restart.test.ts`) — the kind of test most codebases at this maturity stage skip because it's hard to simulate.
- The forced-upgrade (426) gate ships with an explicit "don't strand a mid-trip passenger" carve-out already designed in, rather than as a later patch — evidence of the edge case being considered at build time, not bolted on after an incident.

## (e) NOT VERIFIED (time-box boundary)

- Rider changes destination/adds stops mid-trip and the re-pricing disclosure UX (sweep-catalog §3.3 item 22) — not read.
- Corporate allowance exhausted / company suspended mid-trip (§3.4 item 38) — out of scope for this lane per money-domain carve-out, only flagging it wasn't touched.
- Stuck-ride sweeper's actual trigger threshold and rider-facing copy when a driver's phone dies mid-trip — only confirmed the loop exists per CLAUDE.md's lifespan.py registry, did not read its implementation.
- Whether WS reconnect explicitly re-fetches ride state via REST or relies solely on the next WS event to self-correct (scenario table row 6) — code not read past the reconnect-timing logic itself.
- Admin-dashboard concurrent-edit pattern checked only on `admin/drivers.py`; corporate account/policy editors (CLAUDE.md's own explicit example) not checked in this lane.
- Contrast ratios anywhere in `shared/theme/` — no tool available, would need the dedicated a11y pass with `brand-spinr.md` loaded.
- Bundle size / startup timing — no analyzer run, no measured numbers found in `docs/` within the time-box; Hermes engine not explicitly configured in either app's `app.config.ts` (relying on Expo SDK default, which is Hermes since SDK 43+ — INFERRED, not confirmed by reading the installed Expo SDK version).
- Two other `known-forks.md` entries (CarMarker.tsx, the auth.py real-client-IP pair) were confirmed to have active/recent parity guards per the registry's own text but not independently re-diffed line-by-line in this pass beyond confirming the files still differ as expected.

VERDICT (lane): EDGE-CASE SOUND, with two follow-ups — CONCURRENCY-001 (admin driver-edit conflict signal, MEDIUM) worth a ticket, and RELIABILITY-002 (dead `apiClient.ts` cleanup, LOW, purely hygiene). No CRITICAL/HIGH blockers found in what this pass covered; the areas most likely to carry an actual live-testing risk (booking/payment retry safety, app-kill reconciliation, token-refresh race, OTA-mid-ride, GPS plausibility) were checked in depth and are genuinely well-handled with test coverage and incident-driven design comments — not just "looks fine," verified against actual code paths and, where claimed, against a real regression test. Recommend a follow-up pass on the items in the NOT VERIFIED list before calling this lane fully closed.
