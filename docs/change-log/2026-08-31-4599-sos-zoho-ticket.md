# Change Impact & Risk Log — raise a Zoho ticket on SOS trigger (#4599 Finding 2)

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-31 |
| Surface(s) | backend |
| Domain (Sentry tag) | safety |
| Related issue | #4599 (safety/SOS swarm audit) Finding 2 |

## Issue/gap identified

`trigger_emergency` (in-ride SOS) and `trigger_emergency_rideless` (standalone panic button) notified only
the admin WS broadcast, the safety-team email, and a `logger.critical` line. Neither raised a ticket in the
Zoho Desk queue safety-ops actually works from day to day — unlike the routine, self-filed `/safety/report`
path, which already calls `create_ticket_for_safety()`. With `page_sos_on_call` dark by default (no
on-call webhook configured), a real SOS had no queue entry, no SLA clock, and no "unresolved" state anywhere
outside of someone actively watching the admin dashboard's live WS feed or reading the safety inbox in real
time.

## Root cause

An oversight, not a documented design decision — no comment anywhere explained why the higher-severity SOS
path lacked the same ticket-creation call its lower-urgency sibling already had.

## Fix/remediation

Added `_deps.spawn(create_ticket_for_safety(incident))` to both `trigger_emergency` and
`trigger_emergency_rideless`, placed right after the `safety_incidents` row is successfully persisted —
same call shape and placement as `routes/safety.py::submit_safety_report`.

## Risk & impact on existing functionality

- **Blast radius: isolated to the two SOS trigger handlers.** `create_ticket_for_safety` is a shared,
  already-tested, best-effort function (`services/zoho_desk_integration.py`) — used by 3 other call sites
  before this change (`/safety/report`, and its own coverage suite); none of those are touched.
  `_link_ticket` never raises into the caller (catches `ZohoDeskError` and generic `Exception`, logs and
  returns), so a Zoho outage or missing config cannot turn a successful SOS response into an error.
- Fired via `spawn()` (fire-and-forget), matching the existing SOS-confirmation-push pattern in the same
  functions — never blocks the SMS-to-emergency-contacts loop or the HTTP response.
- No new dedup concern: `create_ticket_for_safety`'s own `_link_ticket` stamps `zoho_ticket_id` on the
  `safety_incidents` row by `record["id"]`, and the SOS idempotency guard (migration 315) already prevents
  a retried SOS from creating a second `safety_incidents` row in the first place — so no duplicate ticket
  risk from an SOS retry either.

## User experience effect

None visible to riders/drivers — this only changes what the safety team's internal Zoho queue sees.

## Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/rides/safety.py` | Import `create_ticket_for_safety`; call it (via `_deps.spawn`) right after the incident insert in both `trigger_emergency` and `trigger_emergency_rideless` | Give SOS the same ticket-queue tracking the routine report path already has |
| `backend/tests/test_p2_sos.py` | Added `test_sos_raises_zoho_ticket_for_safety_team` | Regression coverage for the new call |
| `backend/tests/test_sos_rideless.py` | Added `test_sos_raises_zoho_ticket_for_safety_team` | Regression coverage for the rideless path |

## Before/after

```python
# Before (both trigger_emergency and trigger_emergency_rideless, abridged)
await _deps.db_supabase.insert_one("safety_incidents", incident)
...
await _deps.manager.broadcast_to_admins({"type": "emergency_alert", "incident": incident})
# -- no Zoho ticket

# After
await _deps.db_supabase.insert_one("safety_incidents", incident)
...
_deps.spawn(create_ticket_for_safety(incident))
await _deps.manager.broadcast_to_admins({"type": "emergency_alert", "incident": incident})
```

## Rollback plan

`git-revert-safe` — pure code addition, no data/schema change.

## Verification performed

- New tests: `pytest tests/test_p2_sos.py::TestTriggerEmergency::test_sos_raises_zoho_ticket_for_safety_team tests/test_sos_rideless.py::TestTriggerEmergencyRideless::test_sos_raises_zoho_ticket_for_safety_team` → both pass.
- Full `tests/test_p2_sos.py` + `tests/test_sos_rideless.py` → 43 passed.
- `ruff check` on all changed files → clean.

## What was NOT verified

- No manual end-to-end check against a real Zoho Desk sandbox — mocked at the call boundary, per this
  repo's standard unit-test convention.
- Ticket volume/dedup at scale (e.g. many SOS presses in a short window across different rides) was not
  load-tested — the existing SOS idempotency guard (migration 315) is the only safeguard exercised here.
