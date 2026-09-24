# PR #5748 Codex review fixes

| Field | Value |
|---|---|
| Date / author | 2026-09-24 / Codex, with Luna implementation and independent reviews |
| Surface / domains | Backend and database; auth, drivers, dispatch, rides, insurance |
| PR | https://github.com/srikumarimuddana-lab/spinrvm/pull/5748 |
| Scope | Eight inline Codex findings from review 5299676390; this is not certification of every earlier PR requirement |

## Findings, causes and remediation

| Review comment | Issue and root cause | Fix |
|---|---|---|
| 4089810465 | Admin creation ignored a rejected claim and notified a driver for a ride already marked assigned. | V2 creation starts searching/unassigned. The claim RPC commits assignment with the driver claim and Period 2. API rejects failed admission before notifications; no legacy fallback on a mid-request flag change. |
| 4089810478 | A settings exception was treated as proof that v2 was disabled, enabling raw logout cleanup. | Return `failed`, log the cause, and suppress legacy cleanup. |
| 4089810490 | Failed deferred finalization returned the same value as a legacy driver, causing a guessed Period 1 write. | Distinguish not-applicable, completed, and unresolved. Unresolved finalization leaves the period untouched and records a skipped-transition metric. |
| 4089810498 | Python system actors had no corresponding SQL authorization path. | New migration 464 allows exact backend source/action pairs while retaining ordinary session checks, epoch fencing, obligation locks and service-role-only execution. System maintenance does not fabricate device contact. |
| 4089810510 | An exception handler let claims/offers commit even when Period 2 failed; non-success results were ignored. | Require `ok`/`noop` and propagate failures, rolling back the entire RPC transaction. |
| 4089810517 | Go Online used a fixed 62-minute interval instead of the configured helper. | Migration 464 uses `driver_ready_window()` for the initial deadline. |
| 4089810522 | Request IDs replaced the loaded URL ride's offer identity without comparison. | Compare supplied offer UUID, claim UUID and epoch against that row before accept/decline RPC execution. |
| 4089810533 | Truthiness of an earlier permissive parse bypassed strict validation. | Always run the optional-body parser for v2 offer declines; malformed/non-object JSON is 422. Empty bodies remain supported. |

## Risk and impact on existing functionality

- **Session cleanup:** `routes/auth.py` logout and `_offline_driver_for_logout_all` (also used for logout-all/session supersession) consume the service result. A settings outage now defers availability cleanup instead of performing unfenced raw writes. Auth revocation behavior is unchanged; admission and existing reconciliation remain responsible for fencing stale availability.
- **Shared insurance release:** consumers include rider cancellation/completion (`routes/rides/cancellation.py`, `lifecycle.py`), matching timeouts (`matching.py`), driver completion/cancellation/no-show (`ride_complete.py`, `ride_cancel.py`), accept-loser/decline release (`ride_flow.py`), and admin cancellation/completion (`routes/admin/rides.py`). Applicable unresolved stops no longer write Period 1. This change does not introduce an immediate retry queue; unresolved work still needs successful reconciliation/finalization. Legacy/accepting rows retain their existing derivation.
- **SQL transitions:** callers include availability commands, presence contact-gap handling, readiness reconciliation, missed-offer handling, deferred finalization, policy suspension, stale-intent cleanup and session end. System actors cannot Go Online, Go Offline or displace a controller. Finalize may repeat any of the five deferred stop/pause reasons. An active trip remains online but nonaccepting and retains its Period 2/3 ride identity.
- **Claims:** automatic dispatch and admin direct assignment share migration 459's RPC. An insurance failure now rejects the whole batch, including any earlier claims in that transaction; callers must retry instead of sending offers without coverage. Existing legacy claim RPCs are unchanged.
- **Admin assignment:** a ride-row lock and pending/accepted-offer check prevent overlap with automatic dispatch. Older in-flight callers with an already-assigned ride remain supported only when its driver matches the candidate. An unsuccessful admission leaves the new ride searching/unassigned; it is not automatically dispatched to a different driver. A transport failure can have an uncertain commit outcome, so the API returns the ride ID for reconciliation rather than guessing or compensating raw state.
- **Promo/UX consequence:** promo redemption and ride creation precede admission. On assignment rejection they remain attached to that created ride, and neither promo nor driver push is sent. The admin must inspect/reconcile or cancel that ride rather than create another request; an unassigned ride can occupy the rider's active-ride slot until existing cleanup. A 409 carries `detail.ride_id`; a 503 uses the existing `ServiceUnavailableException` contract, `error.details.ride_id`, so the shared 5xx sanitizer preserves it.
- **Offer decisions:** accept/decline consumers in `routes/drivers/ride_flow.py` now return the existing conflict codes for stale/mismatched identities. No-body compatibility and legacy offer paths remain. This can be visible mid-session when an old notification is tapped.
- No frontend, fare calculation, payment, wallet or corporate module was changed. `docs/known-forks.md` contains no sibling mapping for these changed backend functions.

## Files modified

| File | Change and reason |
|---|---|
| `backend/services/driver_session_end_service.py` | Fail closed on settings lookup errors. |
| `backend/utils/insurance_periods.py` | Preserve unresolved deferred finalization. |
| `backend/services/driver_offer_service.py` | Bind decision identifiers to the loaded offer. |
| `backend/routes/drivers/ride_flow.py` | Strict v2 decline-body parsing. |
| `backend/migrations/459_driver_claim_epoch_fence.sql` | Transactional insurance requirement and atomic admin assignment; this migration is unmerged. |
| `backend/migrations/464_driver_availability_transition_hardening.sql` | Append-only replacement of the merged 457 transition; 457 remains unchanged. |
| `backend/routes/admin/rides.py` | Gate before writes, defer assignment, and stop on unsuccessful claims. |
| `backend/tests/test_driver_availability_session_end.py`, `test_insurance_release_helper.py` | Failure-path regressions. |
| `backend/tests/test_driver_offer_service.py`, `test_offer_decision_accept_v2.py` | Mismatched identifiers and malformed request regressions. |
| `backend/tests/test_admin_rides_coverage.py` | HTTP-level v2 success/failure, promo retention, notifications, and settings validation. |
| `backend/tests/direct_pool/test_driver_availability_readiness.py`, `test_offer_decision_atomicity.py` | SQL authorization, deadline, obligation preservation, insurance rollback, and admin/automatic-dispatch interaction cases. |

## Before / after behavior

```text
settings read raises: legacy cleanup -> failed, no raw fallback
applicable finalizer fails: guess Period 1 -> unresolved, no guessed period
request offer identity differs: resolve body IDs -> conflict before RPC
malformed v2 decline JSON: default to live offer -> 422 before RPC
system logout/readiness: UNAUTHORIZED_SESSION -> allowlisted stop/pause only
Go Online deadline: now + 62 minutes -> now + configured ready window
Period 2 raises/returns race: claim survives -> whole claim RPC rolls back
admin claim rejected: assigned + notify + success -> searching/unassigned + 409 + no notify
```

Concrete dry runs: a paused driver fails admin admission without a claim, Period 2 or notification; an admissible driver obtains assignment, claim and Period 2 together. If another driver's pending batch offer already exists, admin admission fails under the ride lock. A driver with an in-progress trip receives a system logout stop but keeps the trip and Period 3 while accepting no new requests.

Alternative approaches rejected: guessing legacy state on failures, accepting all `system:` prefixes, or compensating admin assignment through separate raw writes would lose the existing authorization/transaction guarantees. The selected changes reuse the existing RPCs and error contracts.

## Verification performed

- **119 passed** in the combined focused session/auth cleanup, insurance release, offer service, decision route and matching decision suites.
- **23 passed** in admin create/v2 HTTP tests, including the existing legacy creation cases and promo behavior.
- Embedded PostgreSQL (PGlite) executed the actual fixture schema and migration chain through 462 plus 464. Verified system logout with/without an active trip, unchanged device contact, rejected system Go/unknown actors, a configured 15+2 minute Go deadline, readiness expiry, new and legacy admin assignment, paused-driver rejection, pending-offer conflict, and claim rollback for both a thrown insurance error and a returned `race` status.
- Independent Luna reviews plus root diff review found no outstanding defects in these fixes. `git diff --check` passed.
- No frontend was modified; no production app build or visual test was run.

## Verification boundaries and release gate

- The real PostgreSQL direct-pool tests are committed but skip in this container: there is no `TEST_DATABASE_URL` and the container cannot start a native PostgreSQL server. PGlite is supplemental SQL execution, not proof of concurrent lock scheduling, wire-protocol behavior or production Supabase/PostgREST behavior. Its harness strips `CONCURRENTLY` from index creation because its batch executor wraps SQL in a transaction.
- No production Supabase data was changed, no migration was applied remotely, and no feature flag was enabled. Fly/mobile end-to-end behavior, full repository CI and the earlier PR's remaining scope were not certified.
- Before release, run the direct-pool suites against disposable PostgreSQL and stage the migration/API combination. Preserve the PR's existing do-not-merge gate until its broader release requirements are satisfied.

## Rollback

Keep availability v2 and readiness disabled during review. If enabled later, first stop new admissions and drain/reconcile v2 offers and active obligations before disabling `driver_availability_v2_enabled`; disable `driver_readiness_policy_enabled` as well. Do not flip into legacy handling while live v2 claims are being resolved. Migration 464 documents restoring only the prior transition function after that drain. Do not drop claim, decision or insurance history, reset epochs, or rewrite applied migration 457.

A retained admin ride/promo or an uncertain RPC outcome requires inspection of that specific ride ID through existing admin reconciliation/cancellation; reverting code does not undo those writes. These review fixes themselves were not deployed, so no production data rollback was performed.
