# PR 5722: independent dispute claimants

| Field | Detail |
|---|---|
| Issue / root cause | The open-dispute lookup used ride and status only, so one party suppressed the other's claim. |
| Fix | Include the authenticated claimant's user ID in duplicate lookup. |
| Alternative | Separate rider/driver dispute tables would duplicate existing ownership logic; the current user_id contract already distinguishes claimants. |
| Risk / blast radius | create_dispute serves riders and assigned drivers; admin review and user dispute-list/detail consumers continue using the stored user_id. Existing ride_id index narrows the lookup; no new query pattern/table is introduced. Same-user duplicates remain blocked. |
| UX | Riders and drivers can independently report a problem on the same completed/cancelled ride. |
| Rollback | Revert the predicate; preserve already-created disputes and their audit history. |
| Verification | New regressions failed for both claimant directions before the fix; same-claimant rejection already passed. Targeted route/dispute suite results recorded in PR verification. |
| Limits | Synthetic mocked repository responses; no live claims created or production build run. |

| File | Change | Why |
|---|---|---|
| backend/routes/disputes.py | Scope lookup by user_id | Preserve independent claims |
| backend/tests/test_routes_disputes_coverage.py | Both directions and same-claimant duplicate cases | Prevent suppression regression |

Before: `filters = {ride_id, status}`

After: `filters = {ride_id, user_id=current_user['id'], status}`

Dry run: rider has an open dispute and assigned driver reports missing earnings. Previously HTTP 400; now a driver-owned dispute is created. A second driver-owned open dispute still yields HTTP 400.
