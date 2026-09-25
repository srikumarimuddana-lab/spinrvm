# Change Impact & Risk Log — destination mode expiry and push-token ownership (C136)

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-24 |
| Author | continuation of draft PR #5769 |
| Surface(s) | backend, driver-app |
| Domain (Sentry tag) | dispatch |
| PR / commit link | https://github.com/srikumarimuddana-lab/spinrvm/pull/5769 |
| Related issue or gap ID | C136 — destination mode hid a live Regina driver from every offer, and the same phones had each other's push tokens |

## 1. Issue / gap identified

On 2026-09-24 a verified, online Regina driver received zero offers because destination ("heading home") mode had been left on with no expiry and no on-screen reminder. The same investigation found one phone's FCM token still registered on the other account, so a push for one person can ring the other person's device.

## 2. Root cause

`drivers.destination_mode` is a sticky boolean. Setting it never stamped an expiry, and going offline did not clear it, including the v2 availability `go_offline` path which returns before the legacy driver-row write. Dispatch then drops that driver unless the ride drop-off is at least 5% closer to the saved destination, and `filter_and_rank_drivers` recorded no reason. Separately, `POST /notifications/register-token` writes a token onto the current user and never removes that same token from any other user.

## 3. Fix / remediation

- Migration 465 adds nullable `destination_set_at` and `destination_expires_at`. Setting a destination stamps expiry at now + 2 hours (`DESTINATION_MODE_TTL` in code, not an `app_settings` column). NULL or unparseable expiry means the filter is off, so existing stuck rows stop filtering as soon as this backend is deployed on top of the migration.
- Legacy go-offline clears destination columns in the same write as `went_offline_at`. If migration 465 is missing, the PGRST204 retry drops those columns and the offline flip still lands. v2 `go_offline` clears them in a follow-up write after the availability transition succeeds; `stop_requests` does not. A failure of that follow-up write is logged and does not fail the offline response.
- `GET /drivers/destination` returns `destination_expires_at` and a server-computed `active` flag. The driver app shows a "Heading home" banner with a turn-off action, and the settings row shows on/off, in en/es/fr.
- `filter_and_rank_drivers` keeps the same return value and now emits one info log per call (`dispatch candidate exclusions`) plus `spinr_dispatch_candidate_excluded_total{reason=...}`. Destination exclusions include driver ids only.
- Registering a push token deletes that token from other users' `push_tokens` rows and nulls it on their `fcm_token` / `fcm_token_rider` / `fcm_token_driver` columns. The current user's other column is left alone. If detach fails, registration still proceeds and the error is logged with the token redacted. `backend/scripts/dedupe_shared_push_tokens.py` is dry-run by default and has not been run against production.
- The rider estimate query in `backend/routes/rides/estimates.py` deliberately does not select `destination_expires_at`. That path only counts nearby cars; it does not apply the destination filter. Selecting the new column there would 400 the estimate screen until migration 465 is applied.

## 4. Risk & impact on existing functionality

- Blast radius is cross-surface: dispatch candidate reads, driver online/offline, and push registration. No fare math, wallet delta, Stripe charge, or ride-state transition changes.
- `backend/services/dispatch_service.py` `filter_and_rank_drivers` is the shared ranker. Callers that already passed a pool keep the same filtered list. The new log and counter run on every call, including tests and the estimate-adjacent dispatch path. A logging failure would surface as a normal logger exception from that call; the filter result is computed before the log.
- `backend/routes/rides/matching.py` and `backend/services/dispatch_candidates.py` now select `destination_expires_at`. PostgREST returns 400 for the whole candidate select if migration 465 is not applied first, which fails every dispatch attempt. That is why rollout order is migration, then backend.
- `backend/routes/drivers/status.py` offline path writes extra columns. The pre-migration retry still sets `is_online` false. v2 go-offline can succeed while the destination clear fails; the 2-hour TTL still bounds a leftover filter.
- `backend/routes/notifications.py` `register_push_token` now deletes and nulls tokens on other accounts before the current user's upsert. A dual-role user who registers the same token as rider and driver keeps both of their own columns. A detach error leaves the previous owner's token in place (today's behavior) and still saves the token for the signed-in user.
- `backend/migrations/465_drivers_destination_mode_expiry.sql` is additive nullable columns with no backfill and no index. Existing `destination_mode=true` rows have NULL expiry and stop being filtered. That is the intended fix for the stuck Regina driver, and it is visible as soon as the new backend reads those rows.
- The one-time cleanup script is not invoked by startup, cron, or a request handler.

## 5. User-experience effect

- Drivers who already have destination mode on, with no expiry stored, start receiving offers in every direction after this backend ships. That is a behavior change for someone mid-shift. The driver app banner ("Heading home", time remaining, turn off) appears only after the driver-app update; until then the backend expiry still applies and the main screen can still say only "Online".
- A driver who sets heading home sees it expire after 2 hours, and going offline clears it so the next shift is not filtered. Pausing requests (`stop_requests`) keeps the destination until the TTL.
- The next time a phone registers its push token, the previous account on that device stops receiving pushes for that token. Someone still logged in on the old account can miss ride offers or rider updates until that account registers again. No notification copy change.
- Riders are unaffected except that a previously hidden nearby driver can be offered their ride. The estimate "cars available" count does not apply the destination filter.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/465_drivers_destination_mode_expiry.sql` | Adds `destination_set_at` and `destination_expires_at` | Destination mode had no expiry |
| `backend/services/dispatch_service.py` | 2-hour TTL, expired/NULL means filter off, exclusion log and `spinr_dispatch_candidate_excluded_total` | Stuck destination mode and no record of why a driver was dropped |
| `backend/routes/rides/matching.py` | Candidate selects include `destination_expires_at` | The ranker cannot see expiry if the column is omitted |
| `backend/routes/drivers/status.py` | Legacy and v2 go-offline clear destination columns; v2 clear failure does not fail offline | A shift end must not leave heading-home on |
| `backend/routes/drivers/profile.py` | Set/get destination stamps and returns expiry plus `active` | The app needs a server-computed on/off |
| `backend/services/dispatch_candidates.py` | Dispatch column list includes `destination_expires_at` | Same select contract as matching |
| `backend/routes/notifications.py` | Detach a token from every other account before saving it | One device token, one account |
| `backend/scripts/dedupe_shared_push_tokens.py` | Dry-run cleanup of tokens already shared | Not run in production |
| `driver-app/components/DestinationModeBanner.tsx` | Heading-home banner and turn-off | Driver can see the filter is on |
| `driver-app/app/driver/(tabs)/index.tsx` | Mounts the banner on the idle online screen | Persistent reminder off the settings page |
| `driver-app/app/driver/settings.tsx` | Settings row shows on/off | The row was description-only |
| `backend/tests/services/test_dispatch_service.py` | Expiry and exclusion-count tests | Ranker behavior and the new log/metric |
| `backend/tests/test_go_offline_clears_destination.py` | Legacy, pre-migration retry, and v2 go-offline / stop_requests | Both offline paths |
| `backend/tests/test_p3_push_notifications.py` | Register as B removes the token from A; dual-role keeps both columns | Token ownership |

## 7. Before / after

```python
# Before — destination filter ignores expiry; nothing is counted
if not _ride_brings_driver_closer_to_destination(d, ride):
    continue
```

```python
# After — same keep/drop decision, then one log and a counter per reason
if not _ride_brings_driver_closer_to_destination(d, ride):
    exclusion_counts["destination"] += 1
    ...
logger.info("dispatch candidate exclusions ride_id=%s ...", ride.get("id"), extra={...})
_metric_inc("spinr_dispatch_candidate_excluded_total", labels={"reason": reason}, by=count)
```

Going offline previously left `destination_mode` true. Both the legacy status write and a successful v2 `go_offline` now set `destination_mode` false and null the destination columns.

## 8. Rollback plan

Apply migration 465 before the backend that selects `destination_expires_at`. If the new backend is already serving and dispatch 400s, the recovery is to apply 465, not to drop the columns.

To roll the behavior back after 465 is applied: redeploy the previous backend first (it does not select or write the new columns), then run the rollback already in the migration header:

```sql
ALTER TABLE public.drivers
  DROP COLUMN IF EXISTS destination_expires_at,
  DROP COLUMN IF EXISTS destination_set_at;
```

Dropping the columns while the new backend is still deployed 400s every dispatch select. There is no `app_settings` flag; `DESTINATION_MODE_TTL` is a code constant. The driver-app banner is JS-only and can be reverted with an OTA or a store build of the previous bundle. The cleanup script has not been executed, so there is no token data to restore from it. Tokens already detached by live `register-token` calls are not restored by a git revert; the affected account must register its token again.

## 9. Verification performed

- `tests/test_go_offline_clears_destination.py`: 6 passed, including v2 `go_offline` clears destination, `stop_requests` does not, and a clear failure still returns offline success.
- `tests/services/test_dispatch_service.py`: 69 passed. Covers NULL/expired/garbage expiry fail-open, destination exclusion id in the log, radius counted separately, and the ranked list unchanged. `ruff check` clean on `dispatch_service.py` and this test file.
- `tests/test_drivers_extended.py -k destination`: 10 passed.
- `tests/test_p3_push_notifications.py`: 53 passed (5 in `TestRegisterPushTokenOwnership`). Exit code 1 on that single-file run was the repo coverage floor, not a failure.
- Driver-app: `driver-dashboard-route.test.ts` 7/7 with the contract test unedited. Banner, settings, dashboard, and WAV-toggle Jest 113/113. `DestinationModeBanner.tsx` and `destinationModeState.ts` are ESLint-clean. Repo-wide `yarn lint` is still red on older driver-app files that this change does not touch.
- `estimates.py` was read: the nearby-cars loop does not call the destination filter, so `destination_expires_at` stays off that select.
- Named reviews called for in the plan (Tara for coverage, Divya for token privacy) were not done. This continuation reviewed the token detach against `notifications.py` and the ownership tests and found no code change required. Detach-on-failure still logs and continues, which remains a product call.
- Not run: the full backend suite, a production or staging migration apply, the shared-token cleanup script, or a live driver-app session. rider-app and driver-app have no visual regression tooling; the banner was checked by Jest, not a screenshot.

## 10. Sign-off

- Rollback is the migration-header SQL, and only after the previous backend is deployed again.
- Blast radius is dispatch selects, driver offline writes, and push registration, listed above.
- The fail-open of already-stuck destination mode is a visible mid-shift change and is described in the user-experience section.
