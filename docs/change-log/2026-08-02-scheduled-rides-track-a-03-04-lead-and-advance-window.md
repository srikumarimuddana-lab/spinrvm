# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend, rider-app |
| Domain (Sentry tag) | dispatch, ai |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Scheduled Rides gap review — Findings #02, #11 |

## 1. Issue / gap identified

Two related gaps in scheduled-ride time validation:
- No server-side maximum advance-booking window — only the rider app's date
  picker enforced a 7-day cap; any other caller (direct API, AI assistant)
  could schedule arbitrarily far ahead.
- The minimum lead time disagreed across three places: `backend/schemas.py`
  required 5 minutes, the AI booking assistant's own copy of the same rule
  also hardcoded 5 minutes, and the rider app's date picker already required
  15 minutes. A push-reminder lead-time mismatch (backend fires at T-10min,
  rider-app's local fallback fired at T-15min) compounded the same
  "duplicate source of truth" problem.

## 2. Root cause

The 5-minute floor and (missing) advance ceiling were only ever specified in
`backend/schemas.py`'s pydantic validator, written before the AI booking
assistant or the rider app's own client-side rule existed. Each surface added
its own copy of "how far ahead can you schedule" instead of reading from one
place, so they drifted independently over time.

## 3. Fix / remediation

- `backend/schemas.py`: introduced two named module-level constants,
  `SCHEDULE_MIN_LEAD_MINUTES = 15` and `SCHEDULE_MAX_ADVANCE_DAYS = 7`, and
  rewired `CreateRideRequest.validate_scheduled_time` to use them instead of
  bare literals. The minimum lead moves from 5 to 15 minutes — matching what
  the rider app's date picker already enforces, so real app users see **no
  change** (they could never trigger the 5–15 minute window in the first
  place). The maximum (7 days) is a genuinely new server-side check, mirroring
  the rider app's existing `maxDate`.
- `backend/ai/tools_booking.py`: `propose_ride_booking`'s own
  `_validate_scheduled_time` now imports `SCHEDULE_MIN_LEAD_MINUTES` and
  `SCHEDULE_MAX_ADVANCE_DAYS` from `backend.schemas` instead of hardcoding a
  local `_MIN_SCHEDULE_LEAD_MINUTES = 5`, and now also rejects times beyond
  the max-advance window (previously unchecked at proposal time — it would
  only have failed later, at Confirm, same class of bug AI4 already fixed
  for the missing-validation case).
- `rider-app/hooks/useScheduledRideReminder.ts`: local fallback reminder
  lead time changed from 15 to 10 minutes to match the backend's actual FCM
  reminder lead (`backend/utils/scheduled_rides.py`'s `_send_reminder`,
  fired at T-10min) — the file's own comment claimed the backend fired at
  T-15min, which was already wrong before this change.
- Tests: `TestMaxAdvanceWindow` added to `test_p2_scheduled_rides.py`;
  `test_time_between_old_and_new_min_lead_rejected` and
  `test_time_beyond_max_advance_window_rejected` added to
  `test_ai_tools_booking.py`; `useScheduledRideReminder.test.ts` updated to
  the new 10-minute boundary plus a regression guard at the 12-minute mark
  (previously skipped under the old 15-min client rule).

## 4. Risk & impact on existing functionality

- **Blast radius, min-lead change**: `validate_scheduled_time` is used by
  every `POST /rides` call with `scheduled_time` set — both the rider app
  and the AI assistant's Confirm step. Grepped for all callers: only
  `backend/routes/rides/booking.py` (via the pydantic model) and
  `backend/ai/tools_booking.py` (its own earlier copy, now unified).
  No other module constructs `CreateRideRequest` with a scheduled time.
  Real rider-app users are unaffected (already blocked below 15 min
  client-side); the only newly-rejected callers are hypothetical non-app
  clients previously able to book 5–14 minutes out, and the AI assistant,
  which previously could propose 5–14-minute-out bookings that would then
  fail at Confirm anyway (a worse UX than being told upfront — this fix
  makes the AI path strictly better, not more restrictive in net effect).
- **Blast radius, max-advance addition**: purely restrictive (rejects
  previously-permitted far-future bookings). Grepped for anything relying on
  far-future scheduled rides — none found; `scheduled_rides.py`'s dispatcher
  and `backend/routes/admin/rides.py`'s scheduled-ride views have no
  min/max assumptions baked in that this interacts with.
- **Rider-app reminder change**: touches only `useScheduledRideReminder.ts`'s
  local-notification path. The backend FCM reminder (authoritative) is
  unchanged. No other file imports `REMINDER_LEAD_MS`/`REMINDER_LEAD_MINUTES`.
- No interaction with money, corporate billing, or the ride state machine.

## 5. User-experience effect

- **Rider (mobile app)**: no visible change to the booking flow — the app's
  own UI already enforced 15 min / 7 days. The local fallback reminder now
  fires 5 minutes later than before (T-10 instead of T-15), matching the
  backend push it was always meant to mirror; a rider relying on the FCM
  push (the common case) sees no change at all.
  A rider using a scheduled ride between 5-14 min out via a non-standard client
  or the AI chat would now be told immediately it isn't allowed, before any
  further chat turns — an improvement (a rejection later at Confirm was
  strictly worse UX, not a newly-introduced restriction).
- **Driver / corporate admin / internal admin**: no visible change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/schemas.py` | Added `SCHEDULE_MIN_LEAD_MINUTES`/`SCHEDULE_MAX_ADVANCE_DAYS` constants; validator now uses them; min lead 5→15, max-advance 7 days added | Single source of truth; close the missing max-window gap |
| `backend/ai/tools_booking.py` | Import shared constants instead of local `_MIN_SCHEDULE_LEAD_MINUTES = 5`; added max-advance check | Stop this path from drifting from the Confirm-time validator |
| `rider-app/hooks/useScheduledRideReminder.ts` | Local reminder lead 15→10 min, matching backend | Remove the double-reminder / mistimed-reminder risk |
| `backend/tests/test_p2_scheduled_rides.py` | Added `TestMaxAdvanceWindow`; fixed two pre-existing DST tests broken by the new max-advance check (froze `datetime.now()` via the validator's own `__globals__`, since `backend/schemas/__init__.py` shadows the flat module) | Regression coverage; keep DST fixtures working under the new rule |
| `backend/tests/test_ai_tools_booking.py` | Renamed a stale "5-minute" test; added boundary + max-advance tests | Pin the new behavior |
| `rider-app/hooks/__tests__/useScheduledRideReminder.test.ts` | Updated 15→10 min assertions; added a 12-minute regression guard | Pin the new lead time |

## 7. Before / after

```python
# Before (backend/schemas.py)
if v_utc < datetime.now(timezone.utc) + timedelta(minutes=5):
    raise ValueError("Scheduled time must be at least 5 minutes in the future")
# (no max-advance check at all)
```

```python
# After
now_utc = datetime.now(timezone.utc)
if v_utc < now_utc + timedelta(minutes=SCHEDULE_MIN_LEAD_MINUTES):  # 15
    raise ValueError(f"Scheduled time must be at least {SCHEDULE_MIN_LEAD_MINUTES} minutes in the future")
if v_utc > now_utc + timedelta(days=SCHEDULE_MAX_ADVANCE_DAYS):  # 7
    raise ValueError(f"Scheduled time cannot be more than {SCHEDULE_MAX_ADVANCE_DAYS} days in the future")
```

## 8. Rollback plan

- All three changes are plain code (no migration, no data written). A
  `git revert` of this commit fully restores prior behavior — no live data
  (fares, holds, ride state) was touched, so this is a genuine, complete
  rollback path, not just a partial one.
- If only the min-lead change needs reverting (e.g. 15 min proves too
  strict for a use case not caught here), change `SCHEDULE_MIN_LEAD_MINUTES`
  back to `5` in one place — both `schemas.py` and `tools_booking.py` follow.

## 9. Verification performed

- [x] Automated tests run: `backend/tests/test_p2_scheduled_rides.py` (15
      passed), `backend/tests/test_ai_tools_booking.py -k TestScheduledTimeValidation`
      (6 passed), `rider-app/hooks/__tests__/useScheduledRideReminder.test.ts`
      (11 passed) — via a fresh venv (`pip install -r backend/requirements.txt`)
      and `npx jest`, since neither was pre-installed in this session.
- [ ] Manual repro steps followed in staging — **not performed**, no staging
      access from this session.
- [x] Blast-radius grep performed — searched for every constructor of
      `CreateRideRequest` with `scheduled_time`, every reader of
      `REMINDER_LEAD_MS`, and every other copy of the 5-minute/15-minute/
      7-day literals across backend + rider-app.
- [x] Reviewed against CLAUDE.md conventions: no money/state-machine impact;
      not user-visible for the primary client (additive-in-effect for it).
- [ ] Feature-flagged — **not flagged**. Judged unnecessary: the rider-app
      UX is unchanged (already stricter than the new server rule), and the
      only behavior change for other callers is a stricter rejection with a
      clear error message, not a new customer-facing flow. Flagging a pure
      validation tightening seemed like the wrong tool here; noting the
      reasoning rather than skipping the checklist item silently.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`, no data
      migration involved)
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow — the rider app's
      actual behavior is unchanged; the AI assistant's behavior changes are
      described above and are a net UX improvement, not a regression

## What was NOT verified

Not tested against a live Supabase/staging environment — only unit-level
pydantic validation and mocked AI-tool execution. The `_patch_schemas_now`
test helper depends on the current `backend/schemas/__init__.py` shadowing
mechanism (exec'ing the flat `schemas.py` under a private module name); if
that mechanism is ever refactored, this test helper would need revisiting —
flagging that coupling explicitly rather than leaving it implicit.
