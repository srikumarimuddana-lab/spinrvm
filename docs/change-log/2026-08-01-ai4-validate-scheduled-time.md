# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-01 |
| Author | Claude Code (background job) |
| Surface(s) | backend |
| Domain (Sentry tag) | ai |
| PR / commit link | branch `claude/ai4-validate-scheduled-time` (PR to be opened) |
| Related issue or gap ID | ACTION_ITEMS.md — AI4 (AI assistant / MCP guardrail backlog) |

## 1. Issue / gap identified

`backend/ai/tools_booking.py`'s `propose_ride_booking` tool accepted any
string up to 80 characters for `scheduled_time` and put it, unvalidated,
straight onto the booking proposal card the rider sees in chat. A
hallucinated or already-past time from the LLM would render on that card and
only actually get rejected much later, when the rider tapped Confirm and hit
`POST /rides` validation.

## 2. Root cause

`propose_ride_booking` builds the `_client_action` "booking_proposal" payload
directly from tool arguments with no validation on `scheduled_time` beyond
the JSON-schema `maxLength: 80` on the tool spec. The only real validation
(`schemas.CreateRideRequest.validate_scheduled_time` in `backend/schemas.py`,
enforcing ISO-8601 parseability and a ≥5-minute future lead) lives at the
`POST /rides` confirm step, one screen and one user action later than where
the bad value first becomes visible.

## 3. Fix / remediation

Added a proposal-time copy of the same validation rule directly in
`backend/ai/tools_booking.py`:

- New `_validate_scheduled_time(value: str) -> tuple` helper: parses with
  `datetime.fromisoformat` (same idiom already used in this file's
  `_iso_age_days` helper and in `schemas.py`'s validator — `Z` suffix
  normalized to `+00:00`, naive datetimes treated as UTC), and requires the
  parsed time to be at least 5 minutes ahead of `datetime.now(timezone.utc)`
  (the same "now" convention used throughout this file and in
  `utils/scheduled_rides.py`). Returns `(parsed_datetime, None)` on success
  or `(None, error_message)` on failure — it never raises.
- `propose_ride_booking` now calls this at the very top, before any Maps
  spend (`_dropoff_pair_refusal` task creation, pickup reconciliation) or
  card construction, and returns `{"error": <message>}` on failure — the
  same tool-result shape already used elsewhere in this file for hard
  refusals (e.g. `_OUT_OF_AREA_ERROR`, `_places_available`'s budget/key
  errors, `find_place`'s Maps-failure errors). This is a normal tool result,
  not a raised exception, so the orchestrator's tool-call loop is unaffected
  and the model can relay the message and re-prompt the rider.
- The Confirm-time validator (`schemas.CreateRideRequest.validate_scheduled_time`
  in `backend/schemas.py`) is **unchanged** — it remains the authoritative,
  later check (defense in depth). This change only adds an earlier copy of
  the same rule; nothing was removed or weakened downstream.
- Also updated the `scheduled_time` tool-spec description ("at least 5
  minutes from now") so the model has a chance to self-correct before even
  calling the tool.

## 4. Risk & impact on existing functionality

**Blast radius: isolated to `backend/ai/tools_booking.py`'s
`propose_ride_booking` handler.**

Grepped for every other caller/consumer of `propose_ride_booking` and of
`scheduled_time` in the AI chat path:

- `propose_ride_booking` has exactly one call path in the backend: it is
  registered as a `ToolSpec` (`backend/ai/tools_booking.py:1653`) and invoked
  generically through `backend/ai/tools.py`'s `TOOL_REGISTRY` /
  `execute_tool` by the orchestrator (`backend/ai/orchestrator.py`) when the
  model issues a `propose_ride_booking` tool call. There is no other direct
  Python caller.
- The **admin AI console** (`backend/routes/admin/ai_console.py`) runs "the
  same orchestrator/tool path" (per its own module docstring) as the rider
  chat surface, so this fix applies uniformly to both — there is no separate
  admin-side handler that needed a matching change or that could now diverge.
- `backend/ai/prompts.py` references `propose_ride_booking` only in prompt
  copy (instructions to the model) — no logic to update.
- Frontend consumers of the `scheduled_time` field on the resulting
  `booking_proposal` card (`rider-app/components/bookingProposal.ts:138-139`,
  `admin-dashboard/src/app/dashboard/ai-console/page.tsx:239`) only ever
  *display* the value (`new Date(proposal.scheduled_time)` /
  `Scheduled ${p.scheduled_time}`) — they do not validate it. They are
  unaffected by this change except that they will now simply never receive a
  bad value to render, which is the intended effect.
- `backend/routes/rides/booking.py`'s `POST /rides` confirm path
  (`schemas.CreateRideRequest.validate_scheduled_time`) is a **separate**
  code path with its own copy of the rule; not touched by this change.
- `get_fare_quote` (the other proposal-adjacent tool in this file) does not
  accept `scheduled_time` at all — out of scope, not touched.
- AI5 (`find_place` out-of-service-area street addresses) is a distinct,
  separate gap in the same backlog section and was explicitly left alone per
  task scope.

**Could this regress a flow that currently works?** Only if a rider or the
model previously relied on an *invalid* `scheduled_time` string reaching the
proposal card and somehow being useful there — it never was (it always
failed at Confirm), so there is no legitimate flow that depended on the old,
unvalidated behavior. The only behavior change for a *comfortably future,
valid* `scheduled_time` is none: it passes straight through unchanged (see
Verification).

## 5. User-experience effect

- **Rider-facing** (chat booking flow) and **internal-admin-facing** (AI
  console, which shares the same tool path): if the rider (or an admin
  testing via the console) asks the assistant to schedule a ride for a
  hallucinated, malformed, or too-soon time, the assistant now gets a clear
  tool error immediately and — per the existing system prompt rules — should
  relay that to the rider and ask for a corrected time, instead of showing a
  confirmation card that would only fail later at Confirm. This is a
  strictly earlier and clearer failure, not a new way to fail.
- **Not** visible mid-session in the sense of a rider on an active ride
  seeing anything change — this is a booking-time-only code path (a rider
  proposing a *new* scheduled ride via chat), not something touched by an
  in-progress trip.
- No new user-facing copy/notification was added beyond the existing
  `{"error": ...}` message text the model is expected to relay conversationally
  (same pattern as existing refusals like the out-of-area error); not a
  scripted/templated notification, so the "customer-centric tone standard"
  review doesn't apply the way it would to a fixed notification string — the
  exact wording shown to the rider is still mediated by the LLM.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/ai/tools_booking.py` | Added `_validate_scheduled_time()` helper and a check at the top of `propose_ride_booking` that returns `{"error": ...}` on an invalid/past/too-soon `scheduled_time`, before any Maps call or card is built. Updated the `scheduled_time` tool-spec description to note the ≥5-min requirement. | AI4 — validate `scheduled_time` at proposal time, not only at Confirm. |
| `backend/tests/test_ai_tools_booking.py` | Added `TestScheduledTimeValidation` (4 new tests: past time rejected, malformed time rejected, time inside the 5-min window rejected, omitted `scheduled_time` unaffected). Fixed the pre-existing `test_proposal_emits_client_action_and_no_writes` test, whose hardcoded `scheduled_time="2026-06-12T20:00:00-06:00"` is now in the past relative to the current test-run date and would otherwise fail under the new validation — replaced with a dynamically computed future timestamp so the happy path still asserts the exact same proposal-card behavior as before. | Cover the new validation; keep the existing happy-path test meaningful instead of accidentally passing by coincidence of date. |
| `ACTION_ITEMS.md` | Marked the AI4 bullet `[x]` with a short "done" note. | Close out the backlog item per repo convention. |

## 7. Before / after

```python
# Before (backend/ai/tools_booking.py, propose_ride_booking)
async def propose_ride_booking(
    user: Dict[str, Any],
    pickup_lat: float,
    ...
    scheduled_time: Optional[str] = None,
    ...
) -> Dict[str, Any]:
    dropoff_check = asyncio.create_task(_dropoff_pair_refusal(dropoff_lat, dropoff_lng, dropoff_address))
    ...
    if scheduled_time:
        proposal["scheduled_time"] = scheduled_time  # any <=80-char string, unvalidated
```

```python
# After
async def propose_ride_booking(
    user: Dict[str, Any],
    pickup_lat: float,
    ...
    scheduled_time: Optional[str] = None,
    ...
) -> Dict[str, Any]:
    # Cheap and synchronous — check before any Maps spend or card is built,
    # so a hallucinated/past time never triggers wasted geocoding.
    if scheduled_time:
        _parsed_schedule, schedule_error = _validate_scheduled_time(scheduled_time)
        if schedule_error:
            return {"error": schedule_error}

    dropoff_check = asyncio.create_task(_dropoff_pair_refusal(dropoff_lat, dropoff_lng, dropoff_address))
    ...
    if scheduled_time:
        proposal["scheduled_time"] = scheduled_time  # only reached once ISO-8601 + >=5min validated
```

## 8. Rollback plan

This is a pure code change with no migration, no `app_settings` flag, and no
data written anywhere (per this module's own docstring: "Nothing in this
module writes to the database" — the whole booking-proposal flow is
read-only until the rider taps Confirm on the native card, which goes
through the unmodified `POST /rides` path). No live data (Stripe charges,
wallet deltas, ride state, insurance-period rows) is touched by this change
or by a revert of it.

- **Rollback**: `git revert` the commit on this branch (or simply don't
  merge the PR). There is nothing to unwind at the data layer — the worst
  case of reverting is that `scheduled_time` again reaches the proposal card
  unvalidated, i.e. exactly today's pre-existing behavior, which the
  Confirm-time validator in `schemas.py` still catches before any ride is
  actually created.
- No feature flag was introduced: this tightens validation on a tool-call
  argument path with no legitimate prior use of an invalid value (see §4),
  so an additive/flagged rollout was judged unnecessary — there is no valid
  input this change rejects that used to succeed.

## 9. Verification performed

- [x] Automated tests run: `pytest backend/tests/test_ai_tools_booking.py -v`
  using a pre-existing venv (`/tmp/spinr-venv`, already had
  `backend/requirements.txt` installed). Full file: **92 passed, 0 failed**
  (baseline on `origin/main` before this change, re-verified via
  `git stash` / `git stash pop`: **88 passed** — this is the real current
  baseline; `ACTION_ITEMS.md`'s A7 entry states 72, which is stale relative
  to this file's current test count and is reported honestly here rather
  than assumed correct). The 4 new tests in `TestScheduledTimeValidation`
  plus the fixed `test_proposal_emits_client_action_and_no_writes` all pass;
  no other test in the file regressed.
- [x] Also ran `ruff check` and `ruff format --check` on both changed Python
  files — clean.
- [ ] Manual repro steps followed in staging — **not done**, see below.
- [x] Blast-radius grep performed: `propose_ride_booking` callers (only the
  tool registry / orchestrator dispatch — no other direct callers), the
  admin AI console (shares the same orchestrator path, not a separate
  handler), frontend `scheduled_time` consumers in `rider-app` and
  `admin-dashboard` (display-only, unaffected), and `schemas.py`'s
  Confirm-time validator (deliberately left untouched).
- [x] Reviewed against relevant `CLAUDE.md` conventions: matched the
  existing `datetime.fromisoformat` idiom already used in this same file
  (`_iso_age_days`) and in `schemas.py`'s validator; matched this file's
  existing `{"error": ...}` tool-result shape for hard refusals rather than
  raising; did not touch the "do not silently swallow errors" surface since
  this is new input validation, not an existing DB/auth/payment error path.
- [ ] Feature-flagged: **not flagged** — justified above (§8) as a pure
  input-validation tightening with no valid prior use case being rejected,
  and no data/state written by the module being changed.

## What was NOT verified

- **No end-to-end run against a live/staging backend or real LLM.** Only the
  unit test suite (mocked Supabase, mocked/patched Maps calls) was
  exercised. The actual chat orchestrator loop (`backend/ai/orchestrator.py`)
  receiving this tool's `{"error": ...}` result and relaying it to a real
  model, and that model producing a sensible re-prompt to the rider, was
  **not** observed live — only that the tool function itself returns the
  correct structured result is verified.
- **No `admin-dashboard` or `rider-app` build was run.** This is a
  backend-only change (`backend/ai/tools_booking.py` +
  `backend/tests/test_ai_tools_booking.py`); no frontend files were modified,
  so `npm run build` was not applicable and was not run. The frontend
  card-rendering code that displays `scheduled_time`
  (`rider-app/components/bookingProposal.ts`,
  `admin-dashboard/.../ai-console/page.tsx`) was read and reasoned about,
  not exercised.
- **No visual/snapshot regression tooling exists for this surface** (chat
  proposal cards) — not applicable here since no rendering code changed, but
  flagging per the standing gap noted in `CLAUDE.md`.
- The stale `72 passed` baseline in `ACTION_ITEMS.md`'s A7 entry was not
  reconciled/corrected in this change — only reported honestly here as a
  discrepancy; updating that entry is out of scope for AI4.
- AI5 (`find_place` out-of-service-area street addresses) is explicitly out
  of scope and was not touched, per the task instructions.

## 10. Sign-off

- [x] Rollback plan is concrete and testable — `git revert`, no data-layer
  remediation needed (read-only module, no migration, no flag).
- [x] Blast radius is stated, not assumed — isolated to
  `propose_ride_booking`'s handler; every other caller/consumer enumerated
  in §4.
- [x] No silent behavior change to an already-shipped flow without the UX
  field filled in — §5 states plainly what the rider/admin-console user now
  sees differently (an earlier, clearer tool error instead of a doomed
  proposal card) and confirms the happy path (valid future time) is
  byte-for-byte unchanged.
