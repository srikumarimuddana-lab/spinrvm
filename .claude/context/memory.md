# Memory — Cross-Session Decisions & Resolved Ambiguities

This file is the project's memory log: durable, cross-session context that isn't ADR-worthy
(no significant *technical* choice with consequences) but shouldn't be re-litigated or
re-discovered from scratch every time it comes up. It's distinct from the `domain-*.md` files
in this directory, which are reference material for a subsystem, not a decision history.

**When to add an entry here:**
- An ambiguous request got resolved a specific way and the reasoning would otherwise be lost
  (e.g. "does X mean A or B" — asked, answered, here's the answer so it isn't re-asked).
- A recurring question got a standing answer (e.g. "why doesn't Y do Z").
- A past assumption turned out wrong and got corrected — record the correction, not just the fix,
  so the same wrong assumption doesn't get made again elsewhere.

**When NOT to add an entry here:**
- A significant, reversible-with-cost technical decision with real consequences → that's an ADR
  (`docs/adr/`), not this file.
- Anything that already has a durable home — regulatory rules (`regulatory-sk.md`), domain
  mechanics (`domain-*.md`), brand assets (`brand-spinr.md`), or a Change Impact Log entry
  (`docs/change-log/`) — link to it instead of duplicating it here.
- Sprint/task status — that belongs in `ACTION_ITEMS.md`, not here.

**Format:** one entry per resolved item, newest first. Keep each entry to a few lines — this is
a pointer/rationale log, not a narrative.

---

## Entries

- **2026-09-11 — D5, in-app VoIP: premise was stale, corrected but still
  unscoped.** D5's original text claimed "Twilio Proxy PSTN masking already
  covers the need" — false. `backend/routes/rides/chat.py:64` and
  `test_call_endpoint_removed` (`backend/tests/test_coverage_rides.py`)
  confirm the `GET /{ride_id}/call` endpoint was deliberately **removed**
  in 2026-06: rider↔driver contact is chat-only by privacy decision (real
  phone numbers were exposed to the other party, which is why it was
  pulled). `docs/API_REFERENCE.md` still documented the removed endpoint
  as live — corrected in the same change. Asked the user how to handle
  it — they chose to fix the stale doc and correct D5's premise only, not
  to scope a build. **D5 remains open and unscoped** (no
  Action/Files/Acceptance criteria) — it just no longer rests on a false
  premise. If a future session is tempted to build VoIP or reintroduce
  masked PSTN calling, know that doing so means explicitly revisiting the
  2026-06 chat-only privacy decision, not just picking up a scoped ticket —
  that's a product call, not an engineering one. See `ACTION_ITEMS.md` D5
  for full history.

- **2026-09-11 — A28, "total rides" definitions: intentionally different,
  document only, no behavior change.** The audit (Phase 3 cross-surface
  finding #10) originally framed this as a simple 2-way split ("admin:
  all-status lifetime; rider-app: completed-only, period-scoped"). Re-
  grounding against current code found it's actually a 3-way split: admin's
  per-rider count (`routes/admin/users.py:254`) is all-status/lifetime;
  rider-app's own profile hero stat (`routes/auth.py:1643`'s `GET /me`,
  feeds `account.tsx`) is completed-only/lifetime; rider-app's activity-tab
  stat (`routes/rides/queries.py:268`'s `GET /rides/stats`, feeds
  `activity.tsx`) is completed-only/period-scoped. Asked the user for a
  decision with 4 concrete options (document only / align rider-app's own
  two numbers / full 3-way reconciliation / no action) — they chose
  **document only**. One-line clarifying comments were added at all 3 call
  sites; no behavior changed. If a future session is tempted to "fix" one
  of these numbers to match another, this was already asked and answered —
  re-ask only if the user raises it again or a real user complaint ties
  back to the mismatch. See `ACTION_ITEMS.md` A28 for full history.

- **2026-09-11 — N14, rider email verification: no gating, ever, beyond the
  pre-existing corporate/join-domain check.** The verify-email flow
  (backend + rider-app UI) has been fully built and shipped since
  2026-08-11; the only thing left open was "should anything require a
  verified email?" Asked the user directly with three concrete options
  (gate referral/promo payouts; a non-blocking UI nudge only; or no new
  gates) — they chose **no new gates**. Verified email stays purely
  opt-in/informational for riders. The one existing consumer,
  `routes/corporate_rider.py`'s `POST /corporate/join-domain` (403s
  `ERR_EMAIL_UNVERIFIED`, migration 252), is unaffected and was not part
  of this decision — it already worked as originally intended once the
  verify flow shipped. If a future session is tempted to add a gate
  (booking, payouts, promo eligibility, etc.) on `email_verified`, this
  was already asked and answered — re-ask only if the user raises it
  again or the product context has materially changed. See
  `ACTION_ITEMS.md` N14 for full history.

- **2026-09-14 — car-marker/turn-by-turn latency audit, item #7:
  no client-side telemetry for the `snapToRoute` 35m skip threshold.**
  During the deep-dive into reported car-marker "not straight on route"
  and turn-by-turn latency complaints, one candidate follow-up was
  instrumenting how often `snapToRoute` (`shared/utils/vehicleTracking.ts`,
  `MAX_ROUTE_SNAP_M = 35`) falls back to the unsnapped raw position, to
  know whether the threshold itself needs tuning. Researched whether a
  reusable client telemetry pipeline exists first: it does not.
  `shared/analytics/index.ts` is a deliberate no-op stub (Firebase
  Analytics removed); `shared/analytics/meta.ts` is a narrowly-scoped
  Meta ad-attribution pipe, not a generic event bus; there is no backend
  ingest endpoint for lightweight client metric pings; the only viable
  primitive is Sentry via `captureMessage()` in
  `shared/services/errorReporting.ts` (already imported as
  `captureException` in both `CarMarker.tsx` files for an unrelated
  failure mode). Decision: **do not build this now** — it would mean
  standing up new per-tick sampling/aggregation infra (the ticker runs
  every 500ms; Sentry bills per event) for a single diagnostic counter
  with no existing rollup path, and there is no production complaint
  driving the need. If a future session wants this signal, the cheapest
  path is a manual/local debug-build `console.log` during a test drive
  first, not new production instrumentation. If a real incident later
  ties back to this threshold, re-open the telemetry question then rather
  than building it speculatively now.

- **2026-09-14 — car-marker/turn-by-turn latency audit, item #3:
  `PLAYBACK_DELAY_MS = 5000` (marker smoothness/staleness buffer) kept
  as-is.** The same audit flagged this Lyft-style playback-buffer delay
  (`shared/utils/markerPlayback.ts`) as the single largest deliberate
  contributor to perceived car-marker "lag" — the icon always renders
  5 seconds behind the driver's real GPS fix, by design, to keep motion
  smooth between pings. This is a genuine smoothness-vs-staleness product
  tradeoff, not a bug, so it was explicitly not touched without the
  user's own call. Asked the user directly; they chose **leave it at
  5000ms**. Rationale discussed: the two code fixes landed in the same
  audit (Android Auto route-snap, PR #5369's WS/location-batch latency
  fixes) likely address most of what read as "car not straight on the
  route / lag" — this buffer is unrelated to either. 5s also matches the
  Uber/Lyft precedent this technique is modeled on. If a future session
  is tempted to shrink this value, this was already asked and answered —
  re-ask only if riders report staleness specifically (e.g. "shows
  arrived" while the driver is still visibly a block away) after the
  other two fixes have had time to be felt in production, not as a
  speculative tuning pass.
