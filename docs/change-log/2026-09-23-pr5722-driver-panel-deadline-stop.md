# PR 5722 driver panel deadline and stop navigation

Issue: the no-show button read legacy countdown keys and fell back to a hard-coded 300 seconds; the trip panel always opened final dropoff and allowed completion with pending intermediate stops. Root cause: the panel's fields did not match the server deadline payload, and stop progress was not reflected in its destination or actions.

The panel now snapshots the server/local clock offset and counts down to `noshow_eligible_at`, with the existing 300-second fallback only for older payloads without a deadline. In-progress navigation selects the first uncompleted stop, offers an explicit stop-completion action, and hides final trip completion while a valid or malformed pending stop remains. Invalid destinations block navigation rather than silently falling through to final dropoff. Rider edits are handled by the tracking refresh in the dashboard layer.

Blast radius: `ActiveRidePanel` is used by the driver dashboard only. Other no-show countdown consumers were searched; the rider app does not expose this driver eligibility action. Alternative considered: compute the no-show deadline from `driver_arrived_at`; the server timestamp is authoritative because it includes service-area policy and scheduled pickup adjustments. Alternative stop progression by geofence was rejected in favor of an explicit driver action, avoiding automatic completion at an adjacent address.

| File | Change |
|---|---|
| `driver-app/components/dashboard/ActiveRidePanel.tsx` | Server-clock deadline, next-stop routing/action, completion guard | Correct driver controls |
| `driver-app/__tests__/components/ActiveRidePanel.test.tsx` | Deadline, stop target, explicit action, and dynamic Maps destination regressions | Pin behavior |

Before, the panel read `noshow_seconds_remaining`/`noshow_eligible` and always used final dropoff. After, it uses `noshow_eligible_at`/`noshow_server_now`, routes to the next pending stop, and requires each stop to be explicitly marked complete.

Rollback: revert the panel and tests; server stop fields remain optional and the additive endpoint is independent. Verification: `jest --runInBand __tests__/components/ActiveRidePanel.test.tsx` passed (36 tests). No production build or device Maps handoff was run; driver-app has no visual regression tooling, so the UI change was reasoned about and tested by component behavior, not screenshots.
