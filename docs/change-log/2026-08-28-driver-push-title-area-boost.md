# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-28 |
| Author | Claude Code (mkkreddy52@gmail.com) |
| Surface(s) | backend |
| Domain (Sentry tag) | dispatch |
| PR / commit link | branch `claude/driver-notification-area-boost-usaor4` |
| Related issue or gap ID | Driver report: push notification fare ≠ in-app offer fare |

## 1. Issue / gap identified

The ride-offer push notification a driver receives (title: `New ride · $X.XX`)
showed only the base ride earnings, excluding any active area boost / ride
incentive. The in-app offer panel — and the driver-app's own locally-rendered
Notifee notification — show fare **+** bonus, so a boosted ride advertised a
lower number in the push tray than the app displayed on tap.

## 2. Root cause

In `backend/routes/rides/matching.py`, the dispatch notify loop builds the push
title from `ride["driver_earnings"]` alone, while the incentive total is
computed in the same function (`_fetch_incentives()` → `_total_bonus`) and sent
in the WebSocket/FCM payload as `total_bonus`. The client combines the two
(`notifeeService.ts`: `offer.fare + (offer.total_bonus || 0)`); the server-sent
push title never did. Two independent title builders, only one of which knew
about the bonus.

## 3. Fix / remediation

The server-side push title now uses `driver_earnings + _total_bonus`, matching
the client's `totalEarnings` formula exactly. Display-only; nothing about what
the driver is actually paid changed.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** The only consumer of `earnings_label` is the FCM
  notification title built ~10 lines below it in the same function. It is not
  written to the DB, not part of `dispatch_payload`, and not read by any other
  route, loop, or client field. `_total_bonus` was already computed
  unconditionally on this path (same `asyncio.gather`) — no new query, no added
  dispatch latency, so the < 2 s dispatch-offer SLA is unaffected.
- **No money-path change.** `driver_earnings`, `ride_incentives`, wallet
  deltas, and payout logic are untouched; this only formats a string.
- **No ride-state / insurance-period / WS-event change.**
- Divergent surfaces that still show base fare only, deliberately left alone
  (pre-existing, not introduced here): `backend/routes/offer_card.py` renders
  the push's BigPicture banner from `driver_earnings` with no incentive lookup;
  adding one would put a DB query on an image-render path. Flagged, not fixed.
- Failure mode is unchanged: a non-numeric earnings value still falls back to
  the `"New fare"` label (the `+ float(_total_bonus or 0)` is inside the same
  `try`).

## 5. User-experience effect

- **Driver-facing, visible immediately** to any driver receiving an offer push
  on a ride in an incentivised area. The tray number now matches the in-app
  offer number instead of under-stating it. For rides with no active incentive
  the title is byte-identical to before.
- No new copy — the same `New ride · $X.XX` format, corrected value.
- Riders, corporate admins, and internal admins see nothing.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/rides/matching.py` | Push title earnings label now adds `_total_bonus` to `driver_earnings` | Match the in-app offer panel and the driver-app's local notification |
| `backend/tests/test_dispatch_notify_loop_branches.py` | Two regression tests: title with an active incentive, and title with none | Lock the two branches so the title can't silently drift from the payload again |

## 7. Before / after

```python
# Before
earnings_label = f"${float(ride.get('driver_earnings') or 0):.2f}"
# ride with driver_earnings=12.50 + "Area Boost" $5.00 → "New ride · $12.50"
```

```python
# After
_offer_total = float(ride.get("driver_earnings") or 0) + float(_total_bonus or 0)
earnings_label = f"${_offer_total:.2f}"
# same ride → "New ride · $17.50"  (matches the offer panel)
```

## 8. Rollback plan

Revert the four-line change in `matching.py` (`git revert` of the commit is a
complete rollback here). No migration, no feature flag, no persisted state: the
value is computed per push at send time, so the next dispatch after a redeploy
uses whichever formula is live. Nothing already delivered needs correcting —
no money, wallet, or ride row was written differently.

## 9. Verification performed

- Static: `python3 -m py_compile` on both changed files — passes.
- Reviewed every consumer of `earnings_label` (single call site) and of
  `_total_bonus` (WS payload, FCM data payload) to confirm no other reader
  changes behavior.
- Cross-checked the new formula against the client's:
  `driver-app/services/notifeeService.ts:222` (`offer.fare + (offer.total_bonus || 0)`)
  and `driver-app/lib/androidAuto/carCard.ts` (`totalEarningsLabel`).

## 10. What was NOT verified

- **The new tests were not executed.** This environment's network policy blocks
  PyPI (`pip install` → 403 via the agent proxy), so pytest and the backend
  dependencies could not be installed. The two added tests are written against
  the existing `test_dispatch_notify_loop_branches.py` harness and are unrun —
  CI is the first execution.
- Not tested against a real device/FCM delivery, nor against live Supabase; the
  incentive rows in the tests are mocked.
- No end-to-end check that a real boosted ride's tray title now matches the
  in-app panel on a physical driver device.
- `backend/routes/offer_card.py`'s banner fare was reviewed but not changed or
  tested.
