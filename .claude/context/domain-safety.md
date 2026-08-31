# Domain — Safety

_Load when working on: SOS flow, emergency contacts, insurance period transitions, driver-rider chat moderation, incident reports, share-trip._

## Key files

- `backend/routes/safety.py` — SOS trigger, emergency contact CRUD, incident reports
- `backend/utils/insurance_periods.py` — period transition logging
- `backend/utils/audit_logger.py` — append-only safety audit trail
- `backend/routes/rides/chat.py` — in-ride chat with profanity/PII filters
- `rider-app/app/report-safety.tsx`, `driver-app/app/report-safety.tsx`

## SOS flow (non-negotiable behavior)

Spinr SOS is an **assist**, not a replacement for 911.

1. User holds SOS button 1.2 s (`SOS_HOLD_MS` in `shared/components/SOSButton.tsx`; prevents accidental trigger)
2. Client posts **`POST /rides/{ride_id}/emergency`** (there is no `/safety/sos`
   endpoint — that path never existed) with `{message, latitude, longitude,
   idempotency_key}`. Retry is **3 attempts, 1 s/2 s backoff, in `SOSButton`
   only** — `rideStore.triggerEmergency` deliberately has no ladder of its own,
   because until 2026-08-16 the two multiplied to as many as 9 real POSTs per
   press. `idempotency_key` is generated once per *press* and reused across that
   press's attempts, so a retry after a lost response returns the original
   incident instead of duplicating it and re-alerting contacts (migration 315).
   Not fire-and-forget: the response is awaited and read.
3. Backend creates `safety_incidents` row (append-only) and in parallel:
   - Notifies all emergency contacts via SMS (Twilio) with rider name + last-known location link
   - Broadcasts a WS event to the admin dashboard, emails the safety distribution list, and logs a
     `logger.critical()` line
   - Pages on-call via `utils/safety_paging.py::page_on_call` (ACTION_ITEMS.md B15(b), built
     2026-08-01) — a best-effort, non-blocking webhook POST (PagerDuty Events API v2 shape by
     default: `{"routing_key", "event_action": "trigger", "payload": {...}}`; provider-agnostic —
     pointing `sos_paging_webhook_url` at an Opsgenie webhook that accepts/adapts that shape is a
     config change, not a rewrite) called from `trigger_emergency`
     (`backend/routes/rides/safety.py`) right alongside the `notify_safety_team` call.
     **Currently dark/disabled by default**: `sos_paging_webhook_url` in `app_settings` is unset,
     since no real PagerDuty/Opsgenie account exists yet to configure it against — `page_on_call`
     logs at debug and returns `False` with no HTTP call in that state, and never raises, so its
     absence changes nothing about today's SOS flow. An admin can turn it on by setting
     `sos_paging_webhook_url` (+ optionally `sos_paging_routing_key`, masked like other
     credentials) via the admin settings API — `super_admin`-only, since redirecting the
     destination is an SSRF/exfil risk (mirrors the `lms_api_base_url`/`lms_api_key` gate). The
     payload carries only IDs (`incident_id`, `ride_id`, `reported_by_user_id`) and a geohashed
     area (`utils.pii.geohash`) — never raw lat/lng, name, email, or phone (PIPEDA).
   - Pushes `sos_acknowledged` WS event back to the user app
4. App shows a one-tap **"Call 911"** button — we never auto-dial. The
   accompanying copy branches on what actually happened to the emergency
   contacts (reached / none reached / none saved / unknown), derived from the
   response via `deriveContactOutcome` in `shared/types/safety.ts`. **Never
   claim a notification the response does not support** — until 2026-08-16 this
   dialog asserted contacts had been notified on any 200, including when every
   SMS failed and when the user had no contacts saved.
5. Safety team opens live view of ride (driver ID, vehicle, route trace, audio recording if enabled)

Hard rules:
- **Never auto-dial 911.** Jurisdictional routing is unreliable; wrong PSAP wastes seconds.
- **Never claim to replace emergency services** in UX copy. Use "We'll alert your emergency contacts and our safety team."
- **Never gate SOS behind auth refresh.** If the JWT is expired, still accept the request with the user_id claim and flag for review.
- **Never silently drop an SOS** on DB failure. `trigger_emergency` (`backend/routes/rides/safety.py`) wraps the `safety_incidents` insert in a try/except (mirrors `backend/routes/safety.py`'s `POST /safety/report`, PR #2931) and returns a clean 503 instead of a 500 so the client's retry logic (see step 2) can recover. There is no non-DB-dependent fallback path today (e.g. a direct Twilio SMS bypassing the DB write) — a sustained outage across all client retries still means zero emergency-contact SMS and zero safety-team notification fire. **Decided 2026-08-01 (product call, relayed via engineering — not directly reviewed against this doc): not building this fallback.** The existing 3× client retry (1s/2s backoff) plus the persistent amber "Not Sent — Call 911 directly" fallback UI is judged sufficient residual-risk mitigation for the ~3-4s outage window a sustained failure across all retries implies. See `ACTION_ITEMS.md` B15(a) for the full rationale.

## Emergency contacts

> **Corrected 2026-08-31** (issue #4599 finding 3 — the 2026-08-16 correction
> below itself went stale). Encryption, the hard cap, and STOP/consent handling
> have since been built (migrations 357–359, `services/sos_contact_consent.py`).
> Do not re-read the "Intended, not built" list further down as still
> unimplemented — it is now the current state, kept here for the migration
> references.

**What actually exists today:**
- Stored **encrypted** (`pgcrypto`, via the Vault-backed helpers added in
  migration 357, fixed up in migration 359) — no longer plaintext `TEXT`.
- **Hard cap of 3** enforced at insert-time in `routes/users.py:874-879`
  (`MAX_EMERGENCY_CONTACTS = 3`). `routes/rides/safety.py` reads with
  `limit=5` (not backed by the same constant, but safe today since 3 < 5 —
  see `ACTION_ITEMS.md`/#4599 finding 4 for the divergence-risk note).
- **Consent/opt-out handling exists.** Migration 358
  (`sos_contact_suppressions`) plus `services/sos_contact_consent.py` back a
  STOP-keyword do-not-contact suppression list for these third-party numbers
  (PIA finding R-002). Note: this is a suppression list, not an opt-in OTP
  ping at add-time — a contact is notified by default and can STOP out
  after the fact, not asked to opt in before.
- `relationship` is stored but is **not** included in the SOS payload or the
  SMS body; nothing surfaces it to a dispatcher.
- Per-contact delivery status **is** returned by `POST /rides/{id}/emergency`
  (`contacts[{id,name,notified}]` + `contacts_notified`) and is consumed by both
  apps as of 2026-08-16.

**Still not built:**
- No OTP ping at add-time — a contact isn't validated/notified before being
  stored, only able to STOP out after an SOS message reaches them.
- Relationship surfaced in the SOS payload for dispatcher context.

## Insurance period transitions (audit-critical)

Every driver state change emits a row into `driver_insurance_periods` (see CLAUDE.md for the 0/1/2/3 table).

Rules specific to safety domain:
- Period rows are **append-only**. Corrections go into a separate `driver_insurance_period_corrections` table with justification.
- Period 3 (passenger aboard) requires a non-null `ride_id` — enforce via CHECK constraint
- Transition from Period 2 → 3 happens on ride state `in_progress`, not on driver UI tap
- Transition from Period 3 → 1 happens on `completed` OR `cancelled_in_progress` (rare — passenger safety cancel)
- Gaps in period coverage (driver online but no row) are a **P0 audit finding** — alert on any gap > 30 s

## Driver-rider chat

- End-to-end messages mediated by backend (no direct P2P) so we retain audit trail
- Auto-filter: phone numbers, email addresses, and off-platform payment terms ("e-transfer", "cash") blocked before delivery
- Messages retained 90 days, then purged except for rides flagged in an incident
- Chat closes 30 minutes after `completed`; any later messages go through Support

## Incident reports

- Types: `harassment`, `unsafe_driving`, `vehicle_condition`, `accident`, `other`
- File via `/safety/incidents` with `{ride_id, category, narrative, evidence_url[]}`
- Rider-filed → driver sees "Report filed — under review" (no details, no timeline)
- Driver-filed → rider sees same
- Safety team triages within 4 h (P1) or 24 h (P2). Response SLA is in support_tickets domain.
- Serious incidents (accident, assault, weapon) → immediate ride disable for the involved party pending investigation

## Share trip / live location

- Rider can share live ETA + route with up to 3 phone numbers per ride
- **Link expiry is 24 h from when the token was minted** — not the "2 h after
  `completed`" this doc promised until 2026-08-16. The code has always
  implemented 24-h-from-creation (`routes/rides/sharing.py::track_shared_ride`).
  Which window is correct is a product decision that has not been made; the doc
  is corrected here to describe what actually ships. **Nothing is permanent**:
  until 2026-08-16 a token minted by `GET /rides/{id}/share` was never stamped
  with a creation time and so never expired at all — fixed, with legacy
  unstamped tokens expiring once their ride reaches a terminal state.
- Shared view shows driver first name, vehicle plate, live location — never driver phone or last name
- Never share rider PII with the driver's shared contacts (no reverse-sharing)
- The driver can also share their own trip link from the Safety overlay
  (driver-app, behind `driver_discreet_sos_enabled`)

## Night ride protections

> **Corrected 2026-08-16.** There is no night-ride branch in the code at all —
> nothing keys off 22:00–05:00. Of the three protections listed here, one exists
> in a different form and two do not exist.

**What actually exists today:**
- **Safety check-in** — real, but not night-scoped and not at 15 min.
  `utils/safety_checkin_loop.py` runs for **every** ride: at **20 minutes**
  `in_progress` it sends a silent "Are you okay?" push, and escalates to an open
  safety incident if there's no response within 90 s. Replay-safe across replicas
  via three Redis keys.
- **Audio recording** — does not exist. No recording code, no storage, no consent
  capture. Blocked on legal review before it could be built: Saskatchewan is
  one-party consent, but a platform recording *both* parties needs explicit
  consent from each plus retention/access rules.
- **Route-deviation safety alert** — does not exist. `utils/route_validation.py`
  computes a `deviation_pct`, but that is **GPS-spoofing fraud detection** on
  completed trips; it does not run live and pings nobody on the safety team.

**Intended, not built:** the 22:00–05:00 auto-enable, audio recording, the
>500 m/60 s live deviation ping, and the 15-minute check-in threshold.

## Common pitfalls

- Don't log raw GPS coordinates in incident narratives — geohashed area only (PIPEDA)
- Don't expose emergency contact phone numbers in any API response to the driver or other party
- Don't mark an SOS as "resolved" automatically — requires safety team acknowledgment
- Don't skip the 1.2-second hold on SOS — accidental triggers erode response trust
- Don't reuse `safety_incidents.id` as a public reference — use a separate opaque `incident_ref` for user-facing correspondence
- Don't add throttling/coalescing to any `drivers`-row write path without checking the Period 1 accumulator: `routes/drivers/location.py` folds `period1_accum_km`/`period1_accum_since` into the same UPDATE as lat/lng, and `utils/location_write_gate.py` must always let those writes through (`force=True`) — a skipped one silently under-counts a regulated SGI figure
