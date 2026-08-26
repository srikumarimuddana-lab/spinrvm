# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-12 |
| Author | Claude Code |
| Surface(s) | docs (root `CLAUDE.md`) |
| Domain (Sentry tag) | drivers / safety |
| PR / commit link | (this branch: `claude/fix-4441-insurance-period2-doc`) |
| Related issue or gap ID | #4441 |

## 1. Issue / gap identified

Root `CLAUDE.md`'s Insurance Periods section stated Period 2 (TNC primary commercial
coverage, "en route to pickup") begins at `driver_assigned`, "because the driver is
already obligated to the ride." The shipped code disagrees: `record_period_transition`
for Period 2 is only ever invoked from the ride-**accept** handler
(`backend/routes/drivers/ride_flow.py:308`), never at assignment time.

## 2. Root cause

The doc's stated rationale doesn't hold under this repo's actual dispatch model.
Spinr uses batch-offer dispatch — a ride can be offered to multiple drivers
concurrently, and `driver_assigned` reflects that an offer exists, not that any one
driver has committed to it. The code's own inline comment (added when this was
implemented) explains the deliberate choice: a driver isn't obligated to a ride
until they accept, since another driver could accept first. The doc was never
updated to match, or was written aspirationally before the batch-offer model was
finalized — not determined which, and not relevant to the fix.

## 3. Fix / remediation

Per explicit user decision (code is correct, doc is stale — see #4441's resolution),
corrected `CLAUDE.md`:
- Insurance Periods table: Period 2's ride-state column changed from
  `` `driver_assigned` or `driver_accepted` or `driver_arrived` `` to
  `` `driver_accepted` or `driver_arrived` `` (removed `driver_assigned`, since a
  driver in that state hasn't accepted yet and is still Period 1).
- The bullet rule changed from "Period 2 starts on `driver_assigned` (not
  `driver_accepted`) because the driver is already obligated to the ride" to the
  corrected trigger, with the actual reasoning (batch-offer dispatch, obligation
  begins at acceptance) and a pointer to the real code location.

No application code changed — this is a documentation-only correction. Grepped
`.claude/context/*.md` and the rest of `CLAUDE.md` for any other repetition of the
stale claim — found none; this was the only place it appeared.

## 4. Risk & impact on existing functionality

- Blast radius: **zero application-code impact**. This changes only prose/table
  content in `CLAUDE.md`, read by developers and AI agents as a reference — it does
  not change any runtime behavior, database write, or API response.
- Downstream effect: any future agent or developer reading `CLAUDE.md` for "when
  does Period 2 start" now gets an answer that matches the actual code, instead of
  one that would lead them to add a transition call at the wrong point if asked to
  "match the documented behavior."

## 5. User-experience effect

None — documentation-only change, not user-facing.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `CLAUDE.md` | Insurance Periods table + Period-2 rule corrected to match shipped code | Resolve doc/code disagreement, #4441 |
| `docs/change-log/2026-08-12-fix-4441-insurance-period2-doc.md` | New change-log entry | Required per CLAUDE.md's own convention for any tracked-gap closure |

## 7. Before / after

```diff
-| 2 | En route to pickup | `driver_assigned` or `driver_accepted` or `driver_arrived` | TNC primary commercial |
+| 2 | En route to pickup | `driver_accepted` or `driver_arrived` | TNC primary commercial |

-- Period 2 starts on `driver_assigned` (not `driver_accepted`) because the driver is already obligated to the ride
+- Period 2 starts on `driver_accepted` (not `driver_assigned`) — in the batch-offer dispatch model a ride can be offered to multiple drivers at once, so a driver isn't obligated to it until they accept; `record_period_transition(..., 2, ...)` is only ever called from the accept handler (`routes/drivers/ride_flow.py`). A driver in `driver_assigned` state who hasn't yet accepted is still Period 1.
```

## 8. Rollback plan

`git revert` — pure documentation change, no live-data footprint, no migration, no
application code.

## 9. Verification performed

- [x] Confirmed the actual code behavior before writing the fix: grepped every
  `record_period_transition(..., 2, ...)` call site in the backend
  (`routes/drivers/ride_flow.py`, `routes/admin/rides.py`) — confirmed no call
  exists at `driver_assigned` time, only at accept.
- [x] Grepped `.claude/context/*.md` and the rest of `CLAUDE.md` for any other
  instance of the stale claim — found none.
- [x] Explicit user decision obtained (via `AskUserQuestion`) on which side —
  doc or code — should change, before touching anything, per this repo's own
  rule that regulatory-facing content needs sign-off, not an agent's unilateral
  call.
- [ ] No automated test applicable — this is a prose/table edit in a markdown
  reference doc with no corresponding test suite.

## 10. What was NOT verified

- Did not audit whether any *other* document (outside `.claude/context/` and
  `CLAUDE.md` itself) repeats the stale claim — e.g. an external compliance
  filing, a runbook, or a slide deck outside this repo. Only this repo's own
  markdown files were checked.
- Did not investigate why the doc and code diverged in the first place (whether
  the doc was ever accurate at an earlier point in the dispatch model's history)
  — not necessary for resolving which is currently correct, and out of scope for
  a doc-only fix.
