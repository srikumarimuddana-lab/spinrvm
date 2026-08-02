# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | drivers |
| PR / commit link | (this branch: `claude/fix-3175-ws-batch-ack`) |
| Related issue or gap ID | #3175 |

## 1. Issue / gap identified

`backend/routes/websocket.py`'s `location_batch`/`driver_location_batch`
handler never sends a `location_batch_ack` when the client submits a
well-formed but empty `points: []` list — the driver app gets no
acknowledgment for a legitimate empty offline-recovery flush.

## 2. Root cause

The ack-and-persist block was gated by
`if driver_id and isinstance(points, list) and points:` — the trailing
`points` truthiness check evaluates `False` for `[]`, so the whole block,
including the ack send, was skipped. A sibling branch already acked with
`count: 0` for the "not a list" case; there was no equivalent for
"valid list, just empty."

## 3. Fix / remediation

Added an explicit branch immediately before the existing persist block:
`if driver_id and isinstance(points, list) and not points:` sends
`{"type": "location_batch_ack", "count": 0}` and continues, mirroring the
existing not-a-list ack. No other logic in the handler changed.

## 4. Risk & impact on existing functionality

- Blast radius: **isolated** to the `location_batch`/`driver_location_batch`
  branch of `websocket_endpoint`'s message loop. Grepped `backend/` for other
  callers of this code path — there are none; it's only reachable via the
  WS message dispatch itself.
- The non-empty-points path (`dict_points = [...]`, rate limiting,
  `persist_ride_breadcrumbs`, rider fan-out, admin broadcast) is completely
  unchanged — the new branch only intercepts the previously-silent empty-list
  case before reaching it.
- No interaction with ride state, money, or auth. Driver location persistence
  and insurance-period classification are untouched.

## 5. User-experience effect

Driver-app-facing, but narrow: a driver app that flushes an empty batch
(nothing buffered during an offline period) now receives a prompt
`location_batch_ack` instead of waiting for the next heartbeat/its own
client-side timeout. Not visible to riders or admins. No mid-session
behavior change for a batch that actually contains points.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/websocket.py` | Added explicit ack for empty-but-valid `points` list in `location_batch` handling | Fixes missing ack, per issue #3175 |
| `backend/tests/test_websocket_coverage.py` | Added `test_location_batch_empty_points_list_acks_zero` regression test | Pin the fixed contract |
| `docs/change-log/2026-08-02-fix-3175-ws-batch-ack.md` | New change-log entry | Required per CLAUDE.md for a behavior-changing fix |

## 7. Before / after

```python
# Before
if driver_id and isinstance(points, list) and points:
    dict_points = [p for p in points if isinstance(p, dict)]
    ...
# empty points: falls through with no response sent
```

```python
# After
if driver_id and isinstance(points, list) and not points:
    await websocket.send_json({"type": "location_batch_ack", "count": 0})
    continue

if driver_id and isinstance(points, list) and points:
    dict_points = [p for p in points if isinstance(p, dict)]
    ...
```

## 8. Rollback plan

`git revert` — pure additive branch, no schema/migration, no live-data
mutation. Reverting restores the previous (silently-no-ack) behavior with no
other side effects.

## 9. Verification performed

- [x] Automated tests run: `pytest backend/tests/test_websocket_coverage.py -q --no-cov` — 36 passed (including the new regression test).
- [x] Full backend suite run: `pytest backend/tests/ -q --no-cov` — see PR body for final pass/fail counts.
- [x] Blast-radius grep performed: confirmed no other code path reads/depends on the previous no-ack-on-empty behavior.
- [x] Reviewed against CLAUDE.md conventions: no money arithmetic, no ride-state-machine interaction, no auth/JWT change; WebSocket ack contract now consistent across all three `points` shapes (not-a-list, empty list, non-empty list).
- [ ] Feature-flagged — not applicable; this is a bugfix restoring a missing ack on an existing message type, not new user-visible behavior requiring staged rollout.

## 10. What was NOT verified

- Not verified against the real driver-app client — confirmed only that the backend's ack contract is now consistent via the backend test suite. Whether the driver app's offline-recovery flush logic actually depends on this ack (vs. just timing out and retrying) was not checked client-side.
- No staging environment exists for this repo (tracked separately, ACTION_ITEMS E1) — not manually reproduced against a live WS connection outside the test suite.
