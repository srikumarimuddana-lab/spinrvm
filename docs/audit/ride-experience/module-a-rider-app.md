# Module A — Rider App: Ride Experience Industry Benchmark Audit

**Scope:** `rider-app/app/{ride-options,confirm-pickup,search-destination,driver-arriving,driver-arrived,ride-in-progress}.tsx` and the hooks/stores they call directly (`hooks/useRideStatusNotification.ts`, `store/rideStore.ts` surface used by these screens, `app/_layout.tsx`'s notification wiring). Receipt-experience and notification-center coverage extends one hop past the named screens into `app/ride-completed.tsx` / `app/ride-details.tsx` / `app/notifications.tsx` because TASK explicitly names "receipt experience" and "notifications" as rider-side areas to assess and no screen in the SCOPE list owns them.
**Method:** static read of the six scope files (full) plus the receipt/notification screens (targeted), cross-checked against `ACTION_ITEMS.md`, and live WebSearch research on Uber/Lyft/Bolt/Ola/Grab conducted 2026-09-12 (all citations below are dated; re-verify before relying on them past a few months — pricing/technique pages change).
**Not verified:** no device/simulator was available in this session — every rendering/animation claim below is a code-level read, not an on-device observation. No production telemetry (ride volume, crash rate, notification delivery rate) was available; where a claim needs it, that's stated inline. This is a superset of the general "What was NOT verified" note Module E will restate.

---

## Summary — feature area → maturity → gap

| Feature area | Maturity (1–5) | Gap | One-line why |
|---|---|---|---|
| Map rendering during booking search | 4 | GREEN | Full-screen map, service-area overlay, live nearby-driver markers — matches category baseline |
| Car marker (rider-side consumption) | 4 | GREEN* | Smooth, route-snapped, camera-anchored on the marker's own delayed position — *device-unverified, see REC-A-02 |
| Route preview — backend-computed polyline path | 5 | GREEN | Progressive erase-behind-vehicle, gradient line, backend-first — ahead of a plain static-line baseline |
| Route preview — client-side Directions fallback | 2 | RED | Bundled API key called directly from the device, bypasses the backend's budget/circuit-breaker entirely |
| Pickup selection | 4 | GREEN | Drag-pin + 50 m radius + curated venue PIN chooser matches Uber/Lyft's own airport/venue pattern |
| Destination selection & multi-stop | 4 | GREEN | Correct session-token billing discipline, saved places, 3-stop cap at/above Uber's typical 2 |
| Fare display & surge transparency | 4 | GREEN | Itemized breakdown, active surge acknowledgment before booking, "100% to driver" framing |
| Receipt — content & delivery | 4 | GREEN | In-app itemized view, email receipt, on-demand PDF — matches category baseline |
| Receipt — PDF generation architecture | 3 | YELLOW | Two independent receipt-PDF builders (client JS + backend Python) computing the same money math separately |
| Notifications — live ride-status tracking | 5 | GREEN | iOS Live Activity (ActivityKit) + Android ongoing notification, server-driven content — mirrors Uber's own published architecture |
| Notifications — in-app center | 4 | GREEN | Deep-links per notification type, distinguishes error from empty state |

\* Car marker maturity reflects the code; whether it *renders* at that quality on a real phone is the open question in REC-A-02 (`EXTENDS-C90`).

---

### REC-A-01: Map rendering during the ride-options booking screen

- **As-is decision:** `rider-app/app/ride-options.tsx:766-845` renders a full-screen, non-interactive (`pointerEvents="none"`) `MapView` behind a draggable bottom sheet, auto-fitting to pickup+dropoff+stops+route (`:413-437`) with a 40%-reserved bottom inset (`:118-122`, explicitly commented as a fix for a prior bug where the full-route fit crammed the pickup pin into the top-left corner). It overlays a translucent service-area polygon (`:298-309`, `:830-838`) and live nearby-driver markers via the shared `CarMarker` (`:812-829`).
- **Industry technique:** Uber/Lyft/Bolt all use the same full-bleed map + draggable bottom sheet pattern for the "choose a trip" screen, with the map itself non-interactive during vehicle selection (per Uber Help's rider-selection article, checked 2026-09-12: the vehicle list occupies the lower sheet and the map is a fixed backdrop showing the route). Service-area/geofence overlays are common in secondary/regional markets to set rider expectations about pickup availability.
- **Verdict:** **GREEN, maturity 4.** This matches the category-standard "map backdrop + sheet" layout, including the deliberate reserved-space fix for full-route framing — a real UX bug (pin crammed into a corner) already caught and fixed, not a theoretical concern.
- **Recommendation:** No change — already at parity for this specific screen.
- **Value to users:** Riders see the whole route and nearby supply at a glance without having to pan/zoom themselves.
- **Value to Spinr:** Reduces booking abandonment from riders who can't tell if drivers are actually nearby.
- **Cost:** None (no change recommended).
- **De-dup tag:** NEW (first time this screen's map layout has been benchmarked against competitors under Dimension 24).
- **Priority:** P4 (informational — no action item).

---

### REC-A-02: Car marker rendering on rider-facing screens (nearby drivers + assigned driver)

- **As-is decision:** All four post-search screens consume the shared `CarMarker` from `@shared/components/CarMarker` — `ride-options.tsx:819-828` (nearby drivers, deterministic fallback heading via `fallbackHeading()` at `:59-65` instead of the old `Math.random()`-per-render jitter), `driver-arriving.tsx:530-539` (route-snapped to the driver→pickup leg, pulsing amber ring), `driver-arrived.tsx:222-233` (pulsing amber, driver stationary at pickup), `ride-in-progress.tsx:766-779` (route-snapped to the live trip route, static non-pulsing ring since the trip is already under way). Three of these screens (`driver-arriving.tsx:298-304`, `driver-arrived.tsx` has no camera-anchor need since the driver is stationary, `ride-in-progress.tsx:142-148`) read the marker's own reported, route-snapped, playback-delayed position via an `onPositionChange` callback and anchor the follow-camera on *that* position rather than the raw GPS fix — an explicit fix (commented in both files) for a real bug where anchoring on the raw fix let the rendered icon and the camera drift apart at driving speed.
- **Industry technique:** the underlying techniques (Kalman-filtered smoothing, playback-delay buffering, Catmull-Rom interpolation, route-snapping) are Module C's territory to benchmark in depth; from the rider-app consumption side, the pattern that matters is **camera-anchors-on-rendered-position, not raw-GPS-position** — this is the same fix class Uber/Lyft-style map SDKs solve by keeping the visual smoothing and the camera-follow logic reading from one shared position source (checked via general GIS/telematics literature and the existing `docs/proposals/` context, 2026-09-12 — no single public Uber engineering post was found that documents this exact camera/marker desync bug, but it's a well-known category of bug in any interpolated-marker + follow-camera system).
- **Verdict:** **GREEN, maturity 4** for the code as written — but this is exactly the class of fix `ACTION_ITEMS.md` **C90** already flags as unverified on a real device: three behavior-changing `CarMarker.tsx` fixes (route-segment continuity, GPS pre-smoothing/implausible-jump rejection, ring-change re-arm) have "never been watched actually render on a phone," and C90 explicitly calls out the exact screen-transition path (`ride-options` → `driver-arriving` → `driver-arrived` → `ride-in-progress`) this module's SCOPE covers. rider-app has zero automated visual-regression tooling (per CLAUDE.md §6), so nothing but a human on a device would catch a visually-wrong-but-non-crashing regression here.
- **Recommendation:** No new recommendation beyond what C90 already states — this module's job per the audit's own Dimension-24 rule is to confirm the gap is known and owned, not re-diagnose it. Flagging here purely to close the "governance" loop: a rider-app-scoped audit that didn't cross-reference C90 would risk exactly the kind of independent-rediscovery this whole audit exists to stop.
- **Value to users:** Correct camera framing/marker smoothness is invisible when right and jarring when wrong (icon lag, edge-of-map drift, a frozen ring) — riders notice a bad marker experience even if they can't name it.
- **Value to Spinr:** Closing C90 removes a standing "looks right in code, unverified in life" risk on the single most-viewed real-time element in the app.
- **Cost:** None new — C90's own action item already estimates this as a device/emulator pass, not new engineering.
- **De-dup tag:** **EXTENDS-C90.**
- **Priority:** P2 (matches C90's own framing: real but not merge-blocking, needs a device pass before considered fully closed).

---

### REC-A-03: Route preview — backend-computed polyline (the primary path)

- **As-is decision:** All four post-search screens prefer a backend-supplied route (`routePolyline` from the estimate response, or `driverOriginSnapshot`/`activeDriverRouteCoords`/`activeRideRouteCoords` cached in the store) drawn via the shared `RouteLine` component, which erases the traveled portion behind the vehicle as it advances — see `driver-arriving.tsx:492-499`'s comment ("Uber/Lyft-style live tracking") and the identical pattern in `ride-in-progress.tsx:748-757`. `ride-in-progress.tsx` additionally polls a self-hosted-OSRM-backed `/rides/{id}/live-route` every 20s (`:298-328`) for a road-snapped in-trip line and ETA, falling back to a zero-cost haversine ETA (`:270-291`, `_haversineEtaMin`) when OSRM is unreachable — explicitly chosen to avoid ~60 metered Directions calls over a 15-minute ride (comment at `:41-43`).
- **Industry technique:** progressive line-erase-behind-the-vehicle and self-hosted/cheap-first routing with a metered-API fallback are both standard at rideshare scale — checked against general rideshare live-tracking UX conventions and this repo's own already-documented architecture decision (the OSRM-primary choice is called out favorably in this audit's own §6.1 seed findings as "the right cost decision already made"), 2026-09-12.
- **Verdict:** **GREEN, maturity 5.** Erasing the traveled line plus a metered-API-avoidant ETA strategy is ahead of a naive "always call Directions" baseline, not merely at parity.
- **Recommendation:** No change.
- **Value to users:** Smooth, low-latency ETA updates without any perceptible cost cutting corners.
- **Value to Spinr:** This is the "right cost decision already made" the pre-audit research called out — worth stating explicitly as a strength so a future cost-cutting pass doesn't accidentally regress it by "simplifying" toward a metered call.
- **Cost:** None (already built and running).
- **De-dup tag:** NEW (first formal GREEN rating under Dimension 24; the underlying architecture is already documented elsewhere, but not previously benchmarked against competitors).
- **Priority:** P4 (informational).

---

### REC-A-04: Route preview — client-side `MapViewDirections` fallback bypasses the backend's cost/budget guard

- **As-is decision:** Every one of the four post-search screens also mounts a `react-native-maps-directions` (`MapViewDirections`) component as a fallback that fires only when the backend-first path hasn't produced a route yet: `ride-options.tsx:780-796` (`GOOGLE_MAPS_API_KEY && routeCoordinates.length === 0`), `driver-arriving.tsx:471-487` (driver→pickup leg) and `:503-515` (pickup→dropoff leg), `driver-arrived.tsx:192-209`, `ride-in-progress.tsx:705-742`. In every case, `GOOGLE_MAPS_API_KEY`/`apikey={process.env.EXPO_PUBLIC_GOOGLE_MAPS_API_KEY}` is a key bundled into the mobile binary, and the resulting Google Directions call goes **directly from the rider's device to Google**, never through `backend/utils/maps_budget.py`'s Redis-backed daily budget ceiling or any server-side rate limit.
- **Industry technique:** category-leading apps proxy all metered mapping calls through their own backend specifically so cost, abuse, and outage behavior are centrally controlled — a client-bundled key that can call a metered API directly is a well-known anti-pattern flagged in standard mobile API-key-hygiene guidance (Google's own Places/Maps API key restriction docs recommend application restrictions plus server-side proxying for exactly this reason; checked 2026-09-12). This is also explicitly named in Dimension 24's own severity table (`audit-framework/dimensions/24-industry-benchmark-cost-value.md`): "A mobile-client-direct paid API call using a bundled key, bypassing the backend's budget guard" is rated **HIGH** on the defect scale.
- **Verdict:** **RED** (defect: HIGH per Dimension 24's own table) despite the *primary* path being GREEN — this is exactly the "maturity-4/5 feature can still carry a real defect finding" case Dimension 24 calls out by name. As designed today it's a fallback path (fires rarely, only when the backend didn't already return a polyline), which lowers real-world frequency but does not change the severity of what happens on every occurrence: an unmetered, unbudgeted, unlogged call leaves the device with no server-side visibility or cap.
- **Recommendation:** Do not remove the fallback outright (it's a legitimate resilience path when the backend polyline is missing) — instead, either (a) route this fallback through a backend-proxied Directions endpoint the way `routes/maps_proxy.py` already does for autocomplete/details/geocode, or (b) if client-direct must stay for latency reasons, add an Android/iOS API-key **application restriction** (bundle ID / package name) in the Google Cloud Console so a extracted key can't be replayed from outside the app, and add basic client-side telemetry (a Sentry breadcrumb or analytics event) so Spinr has *any* visibility into how often this path actually fires. This mirrors the flagship Module D finding at `backend/routes/rides/_shared.py:100-134` (also an uncached/unbudgeted Directions call) — the two should likely be fixed as one project, not two.
- **Value to users:** None directly — riders don't notice which path drew the line. This is a pure cost/security cleanup.
- **Value to Spinr:** Closes a real budget-visibility gap and reduces the blast radius of a leaked/extracted API key (Spinr's own Google Cloud project bears any abuse cost, not a rate-limited proxy Spinr controls).
- **Cost:** **M** engineering (a backend proxy endpoint plus swapping ~4 call sites to it, or **S** for the narrower app-restriction + telemetry option). Third-party spend delta: **reduces** risk of unbounded Google Directions API spend (no current dollar figure available — this audit has no GCP Billing access, see boundary note at top); today's exposure is unbounded-in-theory, bounded-in-practice-by-low-fallback-frequency.
- **De-dup tag:** **NEW** — this exact finding is *pre-identified* in this same audit prompt's own §6.1 seed findings (not independently rediscovered by this module), and no `ACTION_ITEMS.md` entry currently tracks it as a cost/security item (confirmed via grep for `MapViewDirections`/`EXPO_PUBLIC_GOOGLE_MAPS_API_KEY`/"bundled key" — all existing hits are test-coverage entries, none frame it as a cost/security governance gap). Module D's report should own the authoritative version of this finding since it also covers `maps_proxy.py`'s existing budgeted pattern; this entry exists so Module E doesn't miss the rider-app-side call sites when reconciling.
- **Priority:** **P2** (real cost/security exposure, but low current frequency as a fallback-only path and no evidence of active abuse — not P0/P1, but not P4 either).

---

### REC-A-05: Pickup selection (`confirm-pickup.tsx`)

- **As-is decision:** `confirm-pickup.tsx` implements a fixed-center-pin-over-a-draggable-map pattern (`:171-195`) with a 50 m suggested-radius circle (`PICKUP_RADIUS_M`, `:24`, `:182-188`), debounced reverse-geocoding (`:102-111`, 400 ms), a too-far warning past the radius (`:248-255`), a free-text note-for-driver field (`:267-275`), and — distinctively — a server-driven curated venue pickup-point chooser: on confirm, `GET /maps/pickup-points` (`:124-131`) checks whether the pin sits inside a known venue (mall/airport) and, if so, presents a modal of driver-reachable named meeting points (`:291-325`) instead of booking an arbitrary, possibly unreachable, pin.
- **Industry technique:** this is functionally identical to Uber's and Lyft's own airport/venue PIN systems — both introduced dedicated pickup-point features at high-density venues specifically because an arbitrary pin drop in a large venue produces unreachable or ambiguous pickups (TechCrunch's 2019 coverage of Uber's Portland PIN pilot and Lyft's PIN rollout to LaGuardia, and therideshareguy.com's explainer on how PIN pickups work generally, all checked 2026-09-12 — note these are 2019-era sources; no 2025/2026-specific redesign of this feature was found in this search pass, so treat "still current" as inferred from it remaining a live rider-help-center feature, not freshly reconfirmed).
- **Verdict:** **GREEN, maturity 4.** The drag-pin + radius + curated-venue-chooser combination matches the shape of what category leaders ship for the same problem (imprecise geocoding at large venues), including the same underlying motivation (driver-reachability, not just address precision).
- **Recommendation:** No change — already at parity. (One nuance for the record, not a finding: this audit's own §6.1 seed findings describe the app as lacking "map long-press point selection." That's technically true — there's no tap-anywhere-to-drop-a-pin gesture — but the fixed-center-pin-drag-to-move pattern this screen and `pick-on-map.tsx` (`rider-app/app/pick-on-map.tsx`) both use is the same pattern Uber's own "set location on map" feature uses, not a lesser substitute for long-press. Worth Module E noting so the roadmap doesn't list "add long-press pin drop" as a gap to close — it would duplicate an already-equivalent interaction.)
- **Value to users:** Riders at malls/airports/campuses get a pickup spot a driver can actually reach, instead of a pin the driver has to call/text about.
- **Value to Spinr:** Fewer driver-cancels-can't-find-rider incidents at high-density venues; matches SGI/Saskatchewan accessibility expectations no worse than competitors.
- **Cost:** None (already built).
- **De-dup tag:** NEW.
- **Priority:** P4 (informational).

---

### REC-A-06: Destination selection, saved places, and multi-stop (`search-destination.tsx`)

- **As-is decision:** `search-destination.tsx` uses `usePlacesAutocomplete` (`@shared/hooks/usePlacesAutocomplete`) with a real Google Places session token, rotated on each place selection (`rotateSessionToken()` at `:249`, called right after `place_id` details are fetched) — the exact lifecycle Google's own billing docs require to avoid per-request charging. It biases predictions to the rider's live/pickup location (`:52-56`) with a location-not-ready gate (`:63-82`) so a cold GPS fix doesn't return Canada-wide results before location resolves. It supports Home/Work quick chips and arbitrary Favourites (`:661-742`), Recent Searches with `place_id` re-resolution rather than replaying stale stored coordinates (`:368-403`, with an explicit code comment describing a real past incident: a stored pair whose address and pin had drifted apart), and up to 3 intermediate stops (`stops.length >= 3` guard, `:343`).
- **Industry technique:** Google's own Session Tokens documentation (Places API/Places SDK for Android and iOS, checked 2026-09-12) specifies exactly this lifecycle — unique token per session, rotate after a Place Details call, or be billed per-request — and Spinr's implementation matches it precisely. On multi-stop: Uber's rider help center (checked 2026-09-12) documents "up to 2 extra stops" as the typical rider-facing cap (with some city/product variance), so Spinr's 3-stop cap is at or slightly above the category norm, not behind it.
- **Verdict:** **GREEN, maturity 4.** Both the cost-correctness of the autocomplete billing lifecycle and the multi-stop cap are at parity or better.
- **Recommendation:** No change.
- **Value to users:** Fast, locally-relevant destination search; multi-stop trips (errands, school runs) are supported at least as generously as Uber.
- **Value to Spinr:** Correct session-token usage is a direct cost control — the alternative (reusing tokens or omitting them) bills every keystroke as a separate Autocomplete request, which is a real, avoidable per-character cost at scale. Confirming this is implemented correctly is itself valuable given Module D's parallel finding that a *different* call site (`_shared.py`'s Directions call) has no cost governance at all — this screen shows the pattern that call site should be copying.
- **Cost:** None (already correct); recommend Module D reference this file as the "what good looks like" example when writing its own remediation for the ungoverned Directions call.
- **De-dup tag:** NEW.
- **Priority:** P4 (informational).

---

### REC-A-07: Fare display and surge transparency (`ride-options.tsx`)

- **As-is decision:** The bottom sheet shows a Lyft-style animated vehicle-card list (`AnimatedVehicleCard`, `:1462-1596`) with per-card price, ETA, capacity, and a surge badge (`:1549-1554`) shown on *every* card that carries a multiplier, not just the selected one. A collapsible, collapsed-by-default fare breakdown (`:1032-1079`) itemizes each `fare_breakdown` line plus a "100% goes to your driver" badge on the ride-portion line (`:1057`) and a static "0% Commission" badge in the section header (`:886-889`). Booking at `surge_multiplier > 1.0` requires an explicit active acknowledgment via a confirm sheet before the request is sent (`handleBookRide`, `:605-627`) — the badge alone is passive; this sheet forces the rider to see the multiplier and total before confirming.
- **Industry technique:** Uber's Upfront Fares documentation and its rider help center (checked 2026-09-12) confirm riders are shown the price — surge included — before confirming a ride, matching Spinr's active-acknowledgment gate. Uber's vehicle-selection help article also documents per-option ETA/price display and a comparative "Faster" badge on whichever option has the lowest ETA (checked 2026-09-12) — Spinr has no equivalent comparative badge (cheapest/fastest) across cards today.
- **Verdict:** **GREEN, maturity 4.** The core requirement — surge visible and actively acknowledged before booking, itemized breakdown, transparent commission framing — is at parity or ahead (the "100% to driver" / "0% commission" framing is a Spinr-specific differentiator, not something Uber/Lyft show, since they take a cut). The missing comparative badge (fastest/cheapest) is a minor, separate gap, not folded into this verdict's maturity score since it doesn't affect transparency or correctness.
- **Recommendation:** No change required for parity. Optional, low-priority polish: a "Fastest" or "Best value" badge on whichever card has the lowest ETA/price, mirroring Uber's pattern — purely a discoverability nicety, not a transparency or cost issue.
- **Value to users:** Riders already get a clear, honest price before committing; a comparative badge would save a few seconds of manual scanning across cards, nothing more.
- **Value to Spinr:** The "100% to driver / 0% commission" framing directly reinforces Spinr's stated differentiation (CLAUDE.md "What Spinr Is NOT" — not a commission-taking marketplace) at the exact moment a rider is comparing prices; this is a monetization-narrative asset, not just a UI detail worth preserving as-is.
- **Cost:** **S** if the optional badge is ever pursued (pure client-side, no new API calls or backend fields needed — ETA/price are already returned per-estimate).
- **De-dup tag:** NEW.
- **Priority:** P4 (the core behavior is already correct; the optional badge is a nice-to-have, not a gap).

---

### REC-A-08: Receipt experience — content, itemization, and delivery channels

- **As-is decision:** Post-ride, riders get three receipt surfaces: (1) an itemized in-app view on `ride-details.tsx` (`normalizedBreakdown`, `:200-229`, consolidating multi-line fare entries into one "Ride fare" line and injecting promo/tip lines when the server breakdown omits them) with a "You paid" total (`:465`, `:525`); (2) an emailed receipt via `POST /rides/{id}/email-receipt` (`ride-completed.tsx:230-242`, `ride-details.tsx:143-155`); (3) an on-demand, shareable PDF generated client-side via `expo-print`/`expo-sharing` from `buildReceiptHtml()` (`ride-details.tsx:40-129`, invoked at `:157-176`), itemizing base fare, distance, time, booking fee, subtotal, GST/PST (from `tax_breakdown`, per-jurisdiction, `:62-76`), tip, and total — with a PIPEDA-conscious driver block showing name + vehicle only, explicitly commented as "NO phone/plate" (`:39`).
- **Industry technique:** rideshare receipts across Uber and Lyft are, per general receipt-format guidance (checked 2026-09-12), itemized documents covering base fare, distance/time charges, surge (if applicable), fees, tax, and tip, delivered by email after every trip; in-app trip history with the same breakdown is also standard. Spinr's three-surface approach (in-app, email, on-demand shareable PDF) covers the same ground the category expects, and the GST/PST-as-separate-line-items behavior additionally satisfies the Saskatchewan-specific regulatory requirement CLAUDE.md already mandates.
- **Verdict:** **GREEN, maturity 4.** Content and channel coverage matches category expectations; the tax-line-item and PII-minimization details are correctly implemented per Spinr's own regulatory context.
- **Recommendation:** No content/channel change needed.
- **Value to users:** Riders can get a receipt however they need it (glance in-app, forward the email for expensing, save/share a PDF) without extra taps.
- **Value to Spinr:** Matches CRA/SK 7-year retention and GST/PST disclosure requirements while also meeting rider expectations set by the incumbents.
- **Cost:** None.
- **De-dup tag:** NEW (content/channel coverage has not previously been benchmarked against competitors; correctness of the GST/PST line items themselves is Dimension 08/12's job per Dimension 24's own scoping note, not re-verified here).
- **Priority:** P4.

---

### REC-A-09: Receipt experience — two independent PDF-generation implementations

- **As-is decision:** The rider-facing, on-demand "Download Invoice" PDF is built **entirely client-side** in JavaScript: `buildReceiptHtml()` (`rider-app/app/ride-details.tsx:40-129`) re-derives fare lines, tax lines (with its own fallback — `if (!hadTax) { gap = grand_total - total_fare; ...}`, `:73-76`), and the grand total using plain-JS `parseFloat`/`toFixed` helpers (`_num`/`_money`, `:32-36`) — entirely independent of the **backend's** `backend/utils/receipt_pdf.py::generate_receipt_pdf` (confirmed present, `_fare_lines`/`_split_surge_delta`/`_money` at lines 35-184), which is the PDF attached to the *emailed* receipt and which does use the backend's Decimal-based money helpers. Both read from the same `ride` record but compute independently, in two different languages, with two different rounding/formatting code paths.
- **Industry technique:** general receipt-architecture practice for anything treated as a legal/financial record (which this is — 7-year CRA/SK retention per CLAUDE.md) is a single source-of-truth renderer, so a downloaded PDF and an emailed PDF for the same trip are byte-for-byte (or at least line-item-for-line-item) identical. No industry-specific Uber/Lyft technical documentation on their internal receipt-rendering architecture was found in this research pass (neither company publishes that level of internal detail) — this verdict is based on general financial-document engineering practice, not a competitor-specific citation.
- **Verdict:** **YELLOW** (defect scale: MEDIUM — a shared-outcome divergence risk, not a live-money-path bug; the two renderers each read the *same already-settled* `ride.grand_total`/`fare_breakdown`/`tax_breakdown` fields, so this cannot itself cause a rider to be overcharged or undercharged). On the maturity/gap scale this sits at **maturity 3** — functional today (both receipts show correct current totals because they both read the same settled fields) but a below-baseline architecture that gets worse over time: `ACTION_ITEMS.md` already documents that `receipt_pdf.py`'s tax line items were fixed once (the "surge as a real Decimal dollar line item" fix, referenced at `ACTION_ITEMS.md:605-611`) — a fix to the backend renderer's tax/surge formatting has no mechanism to also reach `buildReceiptHtml()`, and vice versa. The two will drift the next time either is touched in isolation, and nothing will catch it (`buildReceiptHtml` is unit-tested for its own tax/driver/promo/tip logic per `ACTION_ITEMS.md:10282`, but there is no cross-check test asserting the two renderers agree).
- **Recommendation:** Do not rewrite either renderer as part of this audit (report-only). For a future fix: either (a) have the client fetch a server-rendered receipt (HTML or PDF bytes) for the "Download Invoice" action instead of re-deriving it client-side — the backend already generates this exact document for email — or (b) if client-side generation must stay (e.g. for instant/offline PDF creation), add a regression test that renders both paths from the same fixture `ride` object and asserts the line items and total match, so a future edit to one is forced to keep the other honest.
- **Value to users:** None visible today (both currently agree); the risk is a future rider seeing an emailed receipt and a downloaded PDF disagree on a line item after only one side gets fixed — a trust-damaging, hard-to-explain support ticket.
- **Value to Spinr:** Removes a latent support/compliance risk on a document with a 7-year regulatory retention requirement, and removes a "fix it twice" tax on any future receipt-format change (the CLAUDE.md "float()-arithmetic fixed five times piecemeal" pattern this whole audit was partly commissioned to catch is the same shape of problem: one bug class, multiple independent fix sites, no systemic guard).
- **Cost:** **S–M** for a cross-renderer parity test (no product behavior change, pure test addition); **M** for the "fetch server-rendered PDF" architecture change (removes the `expo-print` client dependency, adds a backend endpoint or reuses the email one with a different transport). No new third-party spend either way — this is an internal-architecture fix, not an API-cost issue.
- **De-dup tag:** **NEW** — not found in `ACTION_ITEMS.md` (grepped for `receipt_pdf`, `buildReceiptHtml`, `expo-print`: all existing hits are about test coverage or a specific tax-line-item fix to the *backend* renderer alone; none frame the two-renderer divergence itself as a finding).
- **Priority:** **P3** (real but not urgent — no current user-visible symptom; worth a scoped fix before the next time either renderer's fare/tax logic changes, so the fix doesn't have to be done twice again).

---

### REC-A-10: Notifications — live ride-status tracking (Live Activity / ongoing Android notification)

- **As-is decision:** Two parallel, platform-native mechanisms keep the rider informed of ride status **outside the app**: (1) Android — `useRideStatusNotification()` (`rider-app/hooks/useRideStatusNotification.ts`) renders an ongoing Notifee notification while the app's JS is alive, rebuilding title/body per ride status (searching/assigned/accepted/arrived/in-progress, `:54-82`) from flat scalar fields (not whole store objects) specifically so it doesn't re-post/flicker on unrelated ride-poll fields (`:116-124` comment); a backend data-only `live_activity` FCM push drives the *same* notification ID when the app is backgrounded/killed, explicitly designed to never duplicate (`:8-11` file header comment). (2) iOS — `rider-app/app/_layout.tsx:554-613` starts a native ActivityKit Live Activity (via a `Voltra`/`@use-voltra/ios-client` wrapper) once a driver accepts, ending it on a terminal ride state, with an explicit privacy choice baked into the content builder: `dropoffArea: null` with a code comment "exact address never on the lock screen" (`:582`).
- **Industry technique:** Uber's own engineering blog ("Pickup in 3 minutes: Uber's implementation of Live Activity on iOS," checked 2026-09-12) describes exactly this architecture: a server-driven content model that updates both the iOS Live Activity and Android push notifications from one logical source, rolled out across 1,200+ cities starting February 2023 (per MacRumors' contemporaneous coverage, also checked 2026-09-12). Uber's Android side uses ongoing push notifications rather than a native Dynamic-Island equivalent (Android has no OS-level Live Activity primitive) — which is exactly Spinr's Notifee-based approach.
- **Verdict:** **GREEN, maturity 5 (differentiated relative to a "notifications-only" baseline, at parity with Uber's specific published architecture).** This is a real strength worth stating plainly, not just noting in passing: Spinr independently arrived at the same server-driven, dual-platform, privacy-conscious pattern Uber describes publicly, including the same reasoning for why Android doesn't get a native equivalent.
- **Recommendation:** No change — already at parity with the specific competitor whose architecture is publicly documented. (Not verified: whether the iOS Live Activity actually renders correctly in the Dynamic Island/Lock Screen on a real device — this is a code-level read only, and falls under the same "no device access" boundary as REC-A-02/C90, though no ACTION_ITEMS entry currently tracks a device-verification gap specifically for Voltra the way C90 tracks it for `CarMarker.tsx`.)
- **Value to users:** Riders can glance at their lock screen or Android notification shade for live ETA/status without unlocking the app — the single highest-frequency "is my ride here yet" interaction during the pickup wait.
- **Value to Spinr:** A differentiated, competitor-matching feature Spinr can market as parity with Uber on a feature riders may specifically compare (Live Activities are visible, screenshot-able, word-of-mouth features).
- **Cost:** None new (already built); ongoing cost is the same FCM/Expo push infrastructure already in place, no incremental API spend identified.
- **De-dup tag:** NEW (existing `ACTION_ITEMS.md` hits for `Voltra`/`rideLiveNotification`/`Notifee` are all test-coverage entries — none benchmark this against competitors).
- **Priority:** P4 (informational — a strength to preserve, and worth flagging to Module E as a case where "go find gaps" would have missed a real differentiator if this audit only hunted for problems).

---

### REC-A-11: Notifications — in-app notification center

- **As-is decision:** `rider-app/app/notifications.tsx` (297 lines) renders a typed notification list with per-type icons (`getTypeIcon`, `:39`) and deep-links each row to the relevant screen by type — lost-and-found chat (`:118-119`), driver chat (`:122`), ride receipt (`:125`), or an in-progress pickup screen (`:129`) — and distinguishes a genuine fetch error (`cloud-offline-outline`, `:223`) from a real empty inbox (`notifications-off-outline`, `:232`), per the design system's documented pattern of treating those as different states rather than one generic "nothing here."
- **Industry technique:** an in-app notification/activity center with type-specific deep-linking is standard across Uber, Lyft, and most consumer apps generally — checked against general mobile-app notification-center conventions, 2026-09-12 (no rideshare-specific citation needed; this is table-stakes UX, not a differentiator either way).
- **Verdict:** **GREEN, maturity 4.** Table-stakes functionality, correctly implemented, with the error-vs-empty distinction being a small but real polish detail above a bare-minimum implementation.
- **Recommendation:** No change.
- **Value to users:** Riders who miss a push notification (phone on silent, app closed) can still find and act on it later, with the button taking them to the right place rather than a generic "ride history" dead end.
- **Value to Spinr:** Reduces support tickets from riders who "never got notified" but actually did — the notification was simply not seen at delivery time.
- **Cost:** None.
- **De-dup tag:** NEW.
- **Priority:** P4.

---

## Cross-references for Module E

- **REC-A-02** and **REC-A-10**'s device-verification caveats both trace to the same root boundary (no device/simulator access in any recent agent session) — Module E should fold these into one "device verification debt" line rather than counting them as two separate risks.
- **REC-A-04** (client-side Directions fallback, rider-app side) and Module D's flagship finding (`_shared.py:100-134`, backend side) are two halves of the same underlying problem — uncontrolled Google Maps spend surface — and should be scoped as one remediation project in the roadmap, not two separate line items competing for priority.
- **REC-A-09** (dual receipt-PDF renderers) is a rider-app-side finding Module D should be aware of when it writes its own receipt-industry-parity section, since Module D owns `backend/utils/receipt_pdf.py` in its SCOPE and this finding is about the *relationship* between that file and the client-side one Module D wouldn't otherwise read.
- No finding in this module recommends anything touching commission structure, surge cap, or ad-style tracking — confirmed consistent with CLAUDE.md's "What Spinr Is NOT" guardrails.

===MODULE-A-COMPLETE===
