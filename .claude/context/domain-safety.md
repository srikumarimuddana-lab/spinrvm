# Domain — Safety

_Load when working on: SOS flow, emergency contacts, insurance period transitions, driver-rider chat moderation, incident reports, share-trip._

## Key files

- `backend/routes/safety.py` — SOS trigger, emergency contact CRUD, incident reports
- `backend/services/insurance_period_service.py` — period transition logging
- `backend/utils/audit_logger.py` — append-only safety audit trail
- `backend/routes/chat.py` — in-ride chat with profanity/PII filters
- `rider-app/src/screens/SOSScreen.tsx`, `driver-app/src/screens/SOSScreen.tsx`

## SOS flow (non-negotiable behavior)

Spinr SOS is an **assist**, not a replacement for 911.

1. User holds SOS button 1.2 s (`SOS_HOLD_MS` in `shared/components/SOSButton.tsx`; prevents accidental trigger)
2. Client posts `/safety/sos` with `{ride_id?, lat, lng, user_id}` — fire-and-forget, retry 3×
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
4. App shows a one-tap **"Call 911"** button — we never auto-dial
5. Safety team opens live view of ride (driver ID, vehicle, route trace, audio recording if enabled)

Hard rules:
- **Never auto-dial 911.** Jurisdictional routing is unreliable; wrong PSAP wastes seconds.
- **Never claim to replace emergency services** in UX copy. Use "We'll alert your emergency contacts and our safety team."
- **Never gate SOS behind auth refresh.** If the JWT is expired, still accept the request with the user_id claim and flag for review.
- **Never silently drop an SOS** on DB failure. `trigger_emergency` (`backend/routes/rides/safety.py`) wraps the `safety_incidents` insert in a try/except (mirrors `backend/routes/safety.py`'s `POST /safety/report`, PR #2931) and returns a clean 503 instead of a 500 so the client's retry logic (see step 2) can recover. There is no non-DB-dependent fallback path today (e.g. a direct Twilio SMS bypassing the DB write) — a sustained outage across all client retries still means zero emergency-contact SMS and zero safety-team notification fire. **Decided 2026-08-01 (product call, relayed via engineering — not directly reviewed against this doc): not building this fallback.** The existing 3× client retry (1s/2s backoff) plus the persistent amber "Not Sent — Call 911 directly" fallback UI is judged sufficient residual-risk mitigation for the ~3-4s outage window a sustained failure across all retries implies. See `ACTION_ITEMS.md` B15(a) for the full rationale.

## Emergency contacts

- Max 5 per user, stored encrypted (`pgcrypto`) in `emergency_contacts`
- Phone number validated at add-time via OTP ping ("Jane added you as emergency contact — reply STOP to opt out")
- Contact can opt out any time via STOP keyword or dashboard
- Surfaced in SOS payload by relationship (`spouse`, `parent`, `friend`) for dispatcher context

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
- Link expires 2 h after `completed` — never permanent
- Shared view shows driver first name, vehicle plate, live location — never driver phone or last name
- Never share rider PII with the driver's shared contacts (no reverse-sharing)

## Night ride protections

- Rides between 22:00 and 05:00 local automatically enable:
  - Audio recording (rider + driver consented in ToS; jurisdictional check for two-party consent)
  - Route-deviation alert (> 500 m off suggested route for > 60 s → silent ping to safety team)
  - Forced check-in at 15 min mark for rides > 30 min

## Common pitfalls

- Don't log raw GPS coordinates in incident narratives — geohashed area only (PIPEDA)
- Don't expose emergency contact phone numbers in any API response to the driver or other party
- Don't mark an SOS as "resolved" automatically — requires safety team acknowledgment
- Don't skip the 1.2-second hold on SOS — accidental triggers erode response trust
- Don't reuse `safety_incidents.id` as a public reference — use a separate opaque `incident_ref` for user-facing correspondence
