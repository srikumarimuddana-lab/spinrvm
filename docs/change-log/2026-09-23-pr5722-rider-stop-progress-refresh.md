# PR 5722 rider stop-progress refresh

Issue: the driver can now persist intermediate-stop completion, but the rider's active trip view would not refetch when that progress changed. Fix: handle the existing `stops_updated` event in `useRiderSocket` by fetching the ride snapshot. The event is emitted to the rider and driver channels after the stop write succeeds.

Blast radius: rider active-ride WebSocket only; other message handlers and local stop-edit actions are unchanged. Alternative considered: merge the event's partial stop list into the rider cache; refetching keeps the backend's full ordered stop array authoritative and handles concurrent edits.

| File | Change |
|---|---|
| `rider-app/hooks/useRiderSocket.ts` | Refetch ride after stop progress event |
| `rider-app/hooks/__tests__/useRiderSocket.reconnect.test.ts` | Verify stop event triggers fetch |

Rollback: revert handler and test; backend event is already an additive message and ignored by older clients. Verification: `jest --config jest.config.js --runInBand hooks/__tests__/useRiderSocket.reconnect.test.ts --no-watchman` passed (7 tests). Not verified: live rider socket or production ride; no visual regression applies to this nonvisual state refresh.
