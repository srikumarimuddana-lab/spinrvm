# PR 5722 dashboard next-stop route fallback

Issue: the backend and route overlays selected the next uncompleted stop, but Directions proxy parameters and the native fallback still routed an active trip to the final dropoff. Invalid pending coordinates could also leave a misleading final route.

Fix: active-trip proxy and native Directions destinations now use the next pending stop; invalid pending stop coordinates suppress route requests until refreshed. Offered rides still target the final dropoff. The dashboard posts the stop snapshot on explicit completion, refreshes after success/conflict, and opts new clients into the server completion guard.

Blast radius: driver dashboard routing and complete-ride request payload only. Alternative considered: rely on server route guidance; fallback providers also need the same destination to avoid divergent map paths.

Verification: driver dashboard and ActiveRidePanel Jest suites passed (100 tests). Backend target tests passed (10). No native device or live Maps API test was run.

Rollback: revert dashboard target selection and completion payload together; existing server clients remain compatible because the new capability flag is optional. Limitation: invalid pending stops intentionally display no active route until route data is corrected.
