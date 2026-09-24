# Driver availability — reconciled cross-team contract (PR #5727)

Date: 2026-09-24. Lead reconciliation of the two architect designs:

- backend: `.claude/plans/2026-09-24-driver-availability-backend-design.md`
- mobile/auth: `.claude/plans/2026-09-24-driver-availability-mobile-auth-design.md`

**This file wins wherever either design disagrees with it.** Anything not listed here follows the owning design as written.

## Migration map

| No. | File | Owner |
|---|---|---|
| 457 | `457_driver_availability_epoch.sql`, amended in place by F2. It is unmerged and unapplied anywhere; if that is ever found untrue, stop and escalate. | backend F-series |
| 458 | `458_driver_claim_epoch_fence.sql` | T4 |
| 459 | `459_offer_decision_atomicity.sql` | T5 |
| 460 | `460_offer_delivery_receipts.sql` | T6 |
| 461 | `461_driver_readiness_policy.sql` | T12 backend |
| 462 | `462_refresh_successor_commitment.sql` (settings flag only, default false) | T11 backend |

Before creating each file, recheck `ls backend/migrations | sort -V | tail -3` against `origin/main`.

## C1. Offer envelope (v2 only)

Every channel (WS `new_ride_assignment`, FCM data, stored-offer GET, snapshot `pending_offer`) carries:

- `offer_protocol: "v2"`
- `offer_id`: `ride_offers.id`; null or absent for admin-direct offers
- `claim_id`: uuid
- `online_epoch`: decimal string
- `server_time`: RFC 3339 UTC, database time
- `expires_at`: RFC 3339 UTC, absolute

`offer_expires_at` stays on the wire with the same value as `expires_at`, and `countdown_seconds` stays. FCM data values are strings.

Mobile treats a payload as v2 when `offer_protocol === "v2"`. It also accepts the case where `offer_id` and `claim_id` are both present but the marker is missing. Receipts are sent only when both `offer_id` and `claim_id` exist, so admin-direct offers send none.

## C2. Accept and decline

**Request.** Body for `POST /api/v1/drivers/rides/{ride_id}/accept` and `/decline`: `{offer_id, claim_id, online_epoch, request_id}`. Decline may also carry `reason`. Every field is optional. The server falls back to the offer row's values and `request_id = "{action}:{offer_id}:{session}"`. Mobile sends all four fields for v2 offers, using a stable `request_id` per user action so that retries are idempotent.

**Success responses:**
- accept: `{success: true, offer_id, already_accepted}`
- decline: `{success: true, outcome, already_resolved}`

**Losers.** HTTP 409 with body `detail = {code, reason_code?, offer_id?, expires_at?, server_time?, ride_status?, snapshot?}`.
- `code` is one of `OFFER_EXPIRED`, `RIDE_STATE_CONFLICT` (with `reason_code` `RIDE_TAKEN`, `RIDE_CANCELLED` or `RIDE_NOT_SEARCHING`), `SESSION_SUPERSEDED`, `ONLINE_EPOCH_STALE`, `DRIVER_OFFLINE`, `CLAIM_MISMATCH`, `OFFER_ALREADY_RESOLVED` or `IDEMPOTENCY_KEY_CONFLICT`.
- **The key for the fresh availability snapshot is `snapshot`**, with the same JSON as `GET /api/v1/drivers/me/availability`. It is best-effort and may be absent.
- 404 `OFFER_NOT_FOUND`.

Clients read `detail.code`. On `SpinrApiError`, that is `err.data.detail.code`, or `err.response.data.detail.code`.

## C3. Offer receipts (T6)

`POST /api/v1/drivers/offers/{offer_id}/receipts`

**Request body:** `{claim_id, event: "received"|"presented", channel: "ws"|"push"|"stored"|"android_auto", app_state: "active"|"background"|"inactive"|"unknown", remaining_ms: int}`

- **`app_state` uses React Native's own `AppState` names.** The backend design's `"foreground"` is replaced by `"active"`.
- `presented` is valid only with `app_state == "active"`. Otherwise the server returns 422 `INVALID_RECEIPT`.

**Response:**
- 200 `{recorded, offer_status, expires_at, server_time, late}`
- 404 `OFFER_NOT_FOUND`; 409 `CLAIM_MISMATCH` or `SESSION_SUPERSEDED`; 422 `INVALID_RECEIPT`

The client ignores 404 and 409 and never retries a receipt more than once.

## C4. Readiness (T12)

**Snapshot fields:**
- `ready_until` (already exists)
- `readiness_enforced` (bool): replaces the mobile design's `readiness_policy_enabled`; mobile maps it to `readinessPolicyEnabled`
- `readiness_prompt_at` (RFC 3339 or null)

**Confirm command:** `POST /api/v1/drivers/me/availability` with body `{"action": "confirm_ready", "online_epoch": "<decimal>", "request_id": "<=128 chars"}`.
- Returns the snapshot plus `"code": "OK"`.
- Errors are 409 `READINESS_EXPIRED`, `REQUESTS_PAUSED`, `ONLINE_EPOCH_STALE`, `SESSION_SUPERSEDED` or `DRIVER_OFFLINE`, and 422 `INVALID_AVAILABILITY_COMMAND`.
- The mobile design's PUT-status variant is **not** used, and mobile never falls back to `go_online`.

**WebSocket events:**
- `availability_changed {online_epoch, state_version, reason_code, server_time}`: a server signal; mobile reconciles.
- `availability_readiness_prompt {online_epoch, ready_until, server_time}`: shows the prompt, then reconciles.
- `auto_offline` keeps its current shape. The backend also emits `availability_changed` for v2 pauses, which covers ask X9.

## C5. Location conflicts

**Live uploads.** Live and idle-batch uploads carry `online_epoch` when the client knows it. Trip batches never carry it.

**Batch conflicts under v2 (F5)** are structured:
- idle → 409 `{code: "DRIVER_OFFLINE", online_epoch}`
- trip → 409 `{code: "RIDE_STATE_CONFLICT", ride_status}`

Flag-off strings are unchanged.

**Ask X6 is rejected.** The v2 live-location epoch fence stays in front of the ride lookup, as published. Reasons:
- It is the fence that stops a stale callback from moving the rider-visible marker.
- Durable trip history is already preserved without an epoch.
- The app reconciles on cold start, so the gap is limited to the few seconds before the first snapshot. The mobile classifier handles the resulting `ONLINE_EPOCH_STALE` with a reconcile.

## C6. Structured 5xx and session codes

- **F1.** 5xx dict details pass through the error handler only with the allow-listed keys `{code, reason_code, online_epoch, state_version, retry_after_ms}`.
- **F3.** Status commands map `UNAUTHORIZED_SESSION` and `CONTROLLER_SESSION_MISMATCH` to `SESSION_SUPERSEDED`. The original goes in `reason_code`.

## C7. Controller rebind (F2-1; closes the "re-login can never go online" gap)

`go_online` from the caller's current session rebinds `controller_session_id` when any of these holds:
- the controller is NULL;
- the driver is offline;
- the existing controller is no longer `users.current_session_id`, meaning it was superseded by a newer login.

A rebind always increments the epoch. Taking over from a session that is still current and online is still refused with `SESSION_SUPERSEDED` / `CONTROLLER_SESSION_MISMATCH`. Active-trip takeover remains out of scope.

## C8. System actors

The T1 session argument `system:<source>` is accepted only for these actions: `stop_requests`, `pause_policy`, `pause_unreachable`, `pause_idle`, `pause_misses`.

The allowed sources are `policy`, `contact_gap`, `readiness`, `missed_offers`, `finalize`, `stale_intent` and **`logout`**. The last one is new, for the T11 backend.

## C9. Logout (ask X5; T11 backend)

For drivers whose `controller_session_id` is set and when v2 is enabled:
- `/auth/logout` runs T1 `stop_requests` with `system:logout` for the session being logged out, before revoking it. It does this only if that session is the controller.
- `logout-all` does the same for the controller.
- T5's `_finalize_deferred_availability_locked` completes the offline transition once no obligation remains.

The legacy raw-offline write stays for flag-off. The mobile side still sends v2 stop fields on its pre-logout PUT (mobile 8-10/8-11). The server path makes logout correct for every client.

## C10. Refresh-token hardening (T11 backend)

**Ask X7 is accepted.** A refresh-token lookup DB failure now raises `DatabaseError` and returns 503 instead of returning `None` and a 401. This is a CLAUDE.md "do not silently swallow errors" fix.
- It updates `test_lookup_refresh_token_db_error_returns_none` to the new contract.
- It keeps the not-found/revoked/expired responses indistinguishable, which is the anti-oracle rule.
- It also covers `routes/admin/auth.py`.

**Ask X8 is accepted as default-off.** Migration 462 adds `settings.refresh_successor_commitment_enabled`, default false, and implements the proposal/recovery design in the mobile-auth design §5 X8.
- `routes/auth.py` is a known fork. Its admin twin stays unchanged, and it keeps exactly five `get_real_client_ip(request)` calls.
- It requires a `spinr-security-auditor` pass before it is committed. The flag must not be enabled in this PR.

## C11. Rejected or deferred asks

- **X10:** keep `controller_session_id` in the snapshot. It is the driver's own session, and the client treats it as opaque.
- **Mobile D7, D8, D9:** out of scope.
- **Direct-pool v3 claim, H3 purge under v2, dead `DispatchService` methods:** non-goals, as in the backend design §6.

## C12. Verification rules for every developer

**Backend:**
- `python3 -m py_compile` and `ruff check` / `ruff format --check` on the changed files, using the repo ruff config.
- In loguru modules, no `exc_info=`, `extra=` or `%s`.
- Every migration is applied twice to the local PG16 scratch database, and the new direct_pool tests run locally:

```bash
cd backend && TEST_DATABASE_URL="postgresql://localhost:5432/postgres?user=postgres" REDIS_TEST_URL=redis://localhost:6379/0 \
  /tmp/claude-0/venv/bin/python -m pytest tests/direct_pool/<file> -c /dev/null --confcutdir=tests/direct_pool -q -p no:cacheprovider
```

- Mocked FastAPI unit tests **cannot run here**, because PyPI is blocked. Write them in the existing style and say "CI-only" in the commit body.

**Mobile:**
- Pure modules are type-checked with the global TypeScript (`/opt/node22/bin/node /opt/node22/lib/node_modules/typescript/bin/tsc`).
- Their jest-style tests run through the local shim runner described in `/tmp/claude-0/jestshim/README.md`.
- Anything that imports React Native, Expo or a jest module mock is CI-only. Say so in the commit body. Never claim a local Jest run.

**Commits:** each is one logical change, at most 3 files and about 200 lines. Messages use conventional-commit form and end with the session attribution lines. Never push; the lead integrates and pushes.
