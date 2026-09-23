# PR 5722 stop progress concurrency and compatibility correction

Issue: stale client route state could complete the wrong stop, out-of-order completion could skip a waypoint, completion could race a rider route edit, and unconditional completion enforcement could strand older mobile clients during rollout.

Fix: stop completion now requires the client's exact expected stop snapshot, permits only the first incomplete stop, and updates via JSONB snapshot CAS. Ride completion also includes the exact stops snapshot in its final status CAS. New driver clients send `stop_progress_enabled`; the pending-stop completion gate is enabled for opted-in clients, while older clients retain their existing completion behavior during rollout. Rider and driver stop notifications refresh active ride data.

Blast radius: active ride stop editing, completion and WebSocket refresh; no schema migration or settlement calculation changes. Alternative considered: force the gate on all clients; rejected because older installed apps do not expose stop-completion actions.

Verification: backend stop/route tests passed (10); rider socket Jest passed (7); driver dashboard and active ride component Jest passed (100). No live database or device session was used.

Rollback: revert the endpoint/CAS and opt-in changes together. Existing JSON fields are harmless and need no data rollback. Limitation: old clients remain able to complete through the legacy API until adoption; the new dashboard blocks completion with pending stops and new clients are server-gated.
