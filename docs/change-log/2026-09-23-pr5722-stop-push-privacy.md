# PR 5722: stop coordinates in minimal push offers

| Field | Detail |
|---|---|
| Issue / root cause | The minimal FCM exclusion set omitted stops, leaking intermediate exact coordinates and addresses through push infrastructure despite the flag. |
| Fix | Exclude the whole stops array when minimal_fcm_offer_payload_enabled is true. |
| Alternative | Redacting nested coordinate keys alone still sends exact addresses and risks future nested fields. Omitting the array follows pickup/dropoff handling and the authenticated offer refetch already returns stops. |
| Risk / blast radius | Matching push builder only; driver background offer refetch in ride_reads.get_ride_offer supplies stops and authenticated WebSocket dispatch keeps the complete list. Flag-off payload stays compatible. |
| UX | Driver still receives itinerary from authenticated offer data; OS minimal push carries no stop coordinates or addresses. |
| Rollback | Revert the exclusion or disable the existing flag, which restores legacy spatial push behavior and its privacy exposure. No live flag was changed. |
| Verification | Flag-on regression failed before the change; flag-off passed. Tests check the actual dispatch push and WebSocket payloads. |
| Limits | Mocked notification transport; no real FCM message sent, no native production build run. |

| File | Change | Why |
|---|---|---|
| backend/routes/rides/matching.py | Add stops to flag-gated exclusions | Prevent nested location disclosure |
| backend/tests/test_dispatch_notify_loop_branches.py | Synthetic intermediate stop in both flag cases | Assert push omission and WebSocket preservation |

Before: `minimal_exclusions = {pickup_lat, ..., rider_rating}`

After: `minimal_exclusions = {pickup_lat, ..., rider_rating, stops}`
