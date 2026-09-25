# C136: Destination mode silently hides drivers from dispatch, and push tokens cross accounts on shared devices

**Found:** 2026-09-24, live test in Regina. Kiran booked repeatedly as a rider; driver `phone ending 9097` got **zero** offers all day while driver `phone ending 3304` got them normally.

**Owner:** Ravi (SDLC lead). Reports to Pandi.

---

## What actually happened (verified against production, 2026-09-24)

Both drivers were online, available, verified, in the Regina service area, with fresh GPS and push tokens on file. The one material difference:

| | phone ending 3304 (offers) | phone ending 9097 (no offers) |
|---|---|---|
| `destination_mode` | false | **true**, destination south Regina |
| Offers on 2026-09-24 | 1 | **0** |

`dispatch_service._ride_brings_driver_closer_to_destination` drops a destination-mode driver from the candidate list unless the ride's drop-off lands at least 5% closer to the destination than the driver currently is. Every test ride went elsewhere, so driver 9097 was filtered out every time.

Three things turned a legitimate feature into a "the app is broken" report:

1. **Destination mode never expires.** It was switched on at some point and stayed on indefinitely, including across go-offline/go-online.
2. **Nothing tells the driver it's on** once they leave the settings screen. The main driver screen shows "Online" with no hint that the driver is only receiving a filtered subset of requests.
3. **Nothing records the filter decision.** No log line, metric, or admin view explains why a driver was excluded from a ride. Diagnosis required reading production rows by hand.

### Second finding (same investigation): push tokens crossed between accounts

The same two phones were used to log into both accounts. Production state afterwards:

| Account | `fcm_token_rider` | `fcm_token_driver` |
|---|---|---|
| 5203304 | its own iPhone | **9097's iPhone** |
| 6009097 | **5203304's Android** | its own Android |

`POST /notifications/register-token` writes a token onto the current user but never removes that same token from any **other** user who previously registered it. A device that switches accounts therefore stays reachable under both. Result: a push meant for one person can ring a different person's phone. That's a privacy issue (rider/driver trip details on someone else's device) as well as a reliability one, and it will happen in real life (shared family phones, a driver re-registering under a new account, reinstalls).

---

## Scope

### T1 — Destination mode auto-expires (backend) — P0
- Add `destination_set_at timestamptz` and `destination_expires_at timestamptz` to `drivers` (new migration, next free number; check `ls backend/migrations | sort -V | tail -1`). Append-only, forward-compatible, rollback plan in the header comment.
- `set_destination_mode` stamps both: `destination_expires_at = now + DESTINATION_MODE_TTL` (default **2 hours**; make it a single named constant, readable from `app_settings` if a column already exists for tunables. Do not invent a new settings column unless trivially cheap, since per `spinr-dispatch-engine`, an unapplied settings column breaks the entire admin settings save).
- **Going offline clears destination mode** (the `PUT /drivers/{driver_id}/status` offline path in `routes/drivers/status.py`). A driver who ends their shift shouldn't come back tomorrow still filtered.
- `_ride_brings_driver_closer_to_destination`: treat an expired destination as off (no filter). Needs `destination_expires_at` in the dispatch candidate column lists (`matching.py` has two copies, plus `dispatch_candidates.py` `_CANDIDATE_COLUMNS`). **Keep all three in sync.**
- Missing/NULL `destination_expires_at` on existing rows = **expired** (fail open). Existing destination-mode rows then stop filtering the moment this ships, which is the intended fix for 9097. Document this in the change-log.
- `GET /drivers/destination` returns `destination_expires_at` and an `active` boolean computed server-side.

### T2 — Driver sees destination mode is on (driver-app) — P0
- Persistent, visible banner on the main online driver screen whenever destination mode is active: "Heading home: only rides toward <area> · ends in 1h 42m · Turn off". One tap turns it off (calls existing `DELETE /drivers/destination`).
- Settings row for destination mode shows current on/off state, not just a description.
- i18n keys added to every locale file the app already ships (grep `destinationMode.` in `driver-app/locales` or equivalent). No hardcoded English.
- Jest test for the banner (renders when active, hidden when inactive/expired, turn-off calls the API).
- No native code change, so this ships as an OTA update. Confirm that holds for whatever you touch.

### T3 — Record why a driver was filtered out (backend observability) — P1
- In `filter_and_rank_drivers`, count exclusions by reason (`destination`, `wav`, `service_area`, `rating`, `radius`, etc.) and emit one structured info log per dispatch attempt with the counts plus the excluded `driver_id`s for the destination reason specifically. IDs only; no names or phones, and **no raw lat/lng** (PIPEDA rule in AGENTS.md).
- Metric `spinr.dispatch.candidate_excluded.count{reason=...}` following the repo's metric naming convention.
- Out of scope: an admin "why didn't driver X get ride Y" UI. Log/metric first; the UI is a follow-up only if this proves insufficient.

### T4 — A device token belongs to exactly one account (backend) — P1
- In `register_push_token`: before writing the token to the current user, **detach it from every other user**:
  - delete `push_tokens` rows with the same `token` and a different `user_id`
  - null out `fcm_token`, `fcm_token_rider`, `fcm_token_driver` on any **other** `users` row whose value equals this token
- This is the same "one device, one owner" rule the logout path (`_clear_push_token_on_logout` in `routes/auth.py`) already applies on explicit logout. Extend it to "logged in as someone else without logging out first."
- Must not remove the token from the **current** user's other column (a dual-role user legitimately has the same token as both rider and driver).
- Tests: register token T as user A, then as user B → A no longer has T anywhere; B has it. Dual-role same-user registration keeps both columns. Batch the lookups (`$in` chunking at 150 per `spinr-dispatch-engine` if any list lookup is added).
- Don't log tokens. Log `user_id`s and a count of detached rows only.

### T5 — One-time cleanup of the already-crossed tokens — P2, needs Pandi + Kiran sign-off before running
- Script (not a migration) that finds tokens present on more than one user and keeps only the most recently `push_tokens.updated_at` owner. **Dry-run by default**, prints counts and user_ids only. Commit the script; **do not run it against production**. Pandi runs it with Kiran's go.

---

## Explicitly out of scope
- Changing the 5% "closer to destination" threshold or the destination filter logic beyond the expiry check. The feature itself is fine.
- Limiting uses per day (Uber-style 2/day). Product decision, not this PR.
- The admin "why didn't this driver get the offer" UI (T3 is log/metric only).
- The unrelated `operator does not exist: text = uuid` error on every live driver-marker write, seen in prod logs during this investigation. Real, separate, filed as its own follow-up. Do not fold it into this PR.
- Any production write, flag flip, deploy, or migration apply. Branch + PR only.

## Definition of done
- Branch `fix/c136-destination-mode-and-push-token-ownership` off `main`, PR against `main` (repo's current integration branch; `staging` is thousands of commits behind).
- Backend tests added and passing for T1, T3, T4; driver-app Jest test for T2.
- `ruff check` / `ruff format` clean on touched backend files; `yarn lint` clean on touched driver-app files.
- A change-log entry in `docs/change-log/` per repo convention.
- Named reviews in the PR: Divya (T4, privacy/token ownership), Tara (test coverage across T1–T4). Say plainly which reviews happened and what was substituted if a persona wasn't available.
