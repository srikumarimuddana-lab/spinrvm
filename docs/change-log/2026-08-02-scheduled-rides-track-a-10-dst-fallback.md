# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | rides |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Scheduled Rides gap review — Finding #10 |

## 1. Issue / gap identified

`CreateRideRequest.validate_scheduled_time` guards the DST spring-forward
gap (a local time that doesn't exist, e.g. 2:30 AM when clocks jump 2:00→3:00)
but had no equivalent guard for the fall-back case — the repeated hour when
clocks go back an hour each November, during which a given local time
(e.g. 1:30 AM) corresponds to two different real UTC instants.

## 2. Root cause

The existing gap guard is a round-trip check (`fold=0` → UTC → back to tz,
compare hour/minute) that only catches nonexistent times. A round-trip on an
ambiguous (fall-back) time is internally consistent under a single fixed
`fold`, so it passes that check silently — the guard was never extended to
detect ambiguity specifically.

## 3. Fix / remediation

Added a second guard immediately after the existing gap check: compute the
UTC offset for the same naive local time under `fold=0` (first occurrence)
and `fold=1` (second occurrence). If they differ, the time is genuinely
ambiguous (occurs twice) and is rejected with a message telling the rider
to pick a different time or supply an explicit UTC offset — mirroring the
existing gap guard's reject-and-ask-again UX rather than silently picking
an interpretation, which would leave the regulatory trip-log record
undetermined about which of the two real instants was actually meant.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to the `tz_name` branch of `validate_scheduled_time`,
  after the existing gap check.** A gap-case time still raises at the
  existing check and never reaches the new one (verified: fold=0/fold=1
  offsets also differ for gap times, but that branch already returned by
  then). Grepped for other callers of this validator — same set as
  Findings #02/#11 (booking.py via the pydantic model, and the AI
  assistant's own separate `_validate_scheduled_time` in `tools_booking.py`,
  which has no `scheduled_timezone` parameter or DST logic at all — this fix
  doesn't touch that path).
- Real-world frequency: one hour, one date per year, per DST-observing
  timezone. Most of Spinr's Saskatchewan service area doesn't observe DST at
  all (Saskatchewan stays on CST year-round); this matters mainly for
  Lloydminster-area riders on Mountain time, and would matter more if Spinr
  ever expands beyond SK.
- No interaction with money, dispatch, or corporate billing — this is
  request-validation only.

## 5. User-experience effect

A rider (or the AI assistant, if it ever adds timezone-aware scheduling)
attempting to schedule a ride for the ambiguous repeated hour on a fall-back
date now gets a clear rejection with guidance, instead of silently having
the booking resolve to whichever instant `fold=0` happens to pick with no
indication to the rider that a choice was even made on their behalf.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/schemas.py` | New fold=0/fold=1 offset-comparison guard in `validate_scheduled_time`, right after the existing spring-forward gap check | Close the fall-back half of the DST edge case |
| `backend/tests/test_p2_scheduled_rides.py` | Three new tests in `TestDSTBoundary`: ambiguous hour rejected, a normal time on the same date unaffected, a UTC (no `scheduled_timezone`) timestamp on the same date unaffected | Mirror the existing gap-test coverage for the new guard |

## 7. Before / after

```python
# Before
back = local.astimezone(utc_tz).astimezone(tz)
if back.hour != naive.hour or back.minute != naive.minute:
    raise ValueError("... DST spring-forward gap ...")
# (fall-back ambiguity: unguarded)
```

```python
# After
back = local.astimezone(utc_tz).astimezone(tz)
if back.hour != naive.hour or back.minute != naive.minute:
    raise ValueError("... DST spring-forward gap ...")

fold0_offset = local.utcoffset()
fold1_offset = naive.replace(tzinfo=tz, fold=1).utcoffset()
if fold0_offset != fold1_offset:
    raise ValueError("... DST fall-back — this local time occurs twice ...")
```

## 8. Rollback plan

Plain code change, no migration, no data written. `git revert` fully
restores prior behavior (silently resolving to `fold=0`'s interpretation,
as it did before this fix).

## 9. Verification performed

- [x] Automated tests: `backend/tests/test_p2_scheduled_rides.py`, full
      file, 18 passed (15 prior + 3 new) via the session's venv.
- [x] `ruff check` — clean.
- [x] Manually verified the fold-offset-comparison logic against real
      `zoneinfo` behavior for all three cases (ambiguous, normal, gap) via a
      standalone script before writing tests — confirmed gap-case offsets
      also differ under fold=0/fold=1, but that branch already returns
      before reaching the new check, so there's no double-triggering.
- [ ] Manual repro in staging — not performed, no staging access.
- [x] Blast-radius grep performed (see §4).
- [x] Reviewed against CLAUDE.md's Saskatchewan-regulatory context — DST
      barely applies to the core SK service area, noted explicitly rather
      than overstating this fix's real-world impact.
- [ ] Feature-flagged — not applicable; this is a validation-only edge-case
      fix with no new user-visible feature.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to the common case — every previously-valid
      unambiguous scheduled time (the overwhelming majority) is unaffected;
      only the exact repeated hour on the exact fall-back date, in a
      DST-observing timezone, with `scheduled_timezone` supplied, changes
      from "silently resolved" to "explicitly rejected"

## What was NOT verified

The AI booking assistant's `_validate_scheduled_time` in `tools_booking.py`
has no `scheduled_timezone` parameter and no DST logic at all — it wasn't
touched by this fix and remains unguarded against DST entirely (both gap and
fall-back), which is consistent with today's design (it only validates
ISO-8601 + lead/max window) but is worth naming as an existing, unaddressed
asymmetry between the two validation paths rather than leaving it implied
that this fix covers both.
