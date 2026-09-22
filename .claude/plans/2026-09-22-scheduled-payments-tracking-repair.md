# Scheduled payments and background tracking repair

Goal: implement the confirmed defects in the 22 September review and open one PR. User authorized fixes and architect/developer execution after reading the report. No live data repairs, production settings changes, merge, or deployment are included.

## Design and constraints

Extend existing cancellation, reconciliation, marker, and upload paths. Separate refund request acceptance from provider success. Make compensation durable and idempotent. Enforce location freshness and capture ordering at the database mutation, not only in Redis. Bound the entire upload including preparation/body parsing. Preserve existing fee policy, authentication and insurance calculations.

Alternative considered: patch only UI labels and increase polling frequency. Rejected because money obligations and stale database coordinates would remain incorrect. Prefer small service helpers and additive migrations over a new payment/tracking subsystem.

Use root CLAUDE.md conventions: Decimal money, dual imports, no raw GPS/credentials in logs, no swallowed DB/payment failures, append-only migrations, service-role-only privileged operations. Split each implementation subtask into at most three files and commit it before starting the next. Each behavior commit carries its impact/validation note in the commit body; aggregate impact log will accompany the PR. Migration numbers reserved: 444 refund lifecycle, 445 location timestamp/order; coordinate any additional migrations with controller.

## Task 1: refund lifecycle and recovery

Files: backend/utils/stripe_charge.py and focused tests first; then an additive refund-obligation schema/helper, cancellation integration, webhook integration, and existing reconciler wiring in separate <=3-file commits.

- [ ] Failing tests: pending/failed Refund object must not return refunded; pending never duplicated, existing success not repeated.
- [ ] Only confirmed provider success finalizes refund accounting; save refund identifier and actual status.
- [ ] Persist cancellation refund obligations before external money movement; process provider status updates and bounded retries safely across replicas.
- [ ] Historical repair must distinguish pending from success and avoid blind duplicate refunds.
- [ ] Verify cancellation refund exceptions and provider-success/DB-failure recovery. Commit and architect review.

## Task 2: scheduled cancellation and fee outcome

Files: backend/utils/scheduled_rides.py plus tests; backend/routes/rides/cancellation.py plus tests, separated commits.

- [ ] Failing forced-interleaving test: rider cancels while dispatch preauthorizes.
- [ ] Guard PI attachment by current ride state, compensate cancelled hold, prevent stale searching/push continuation.
- [ ] Persist notice-fee actual amount/provider status/reference without changing fee eligibility; failed attempts must remain visible/recoverable.
- [ ] Verify no-driver zero fees, scheduled window flag off/on, partial wallet collection. Commit and review.

## Task 3: live marker freshness and ordering

Files: migration445 and repository helper/test; REST ingestion/test; websocket ingestion/test; rider response types/store/tests in separate commits.

- [ ] Failing tests for >60-second history point mutating live marker and delayed old write replacing newer point.
- [ ] Add nullable location_captured_at; update accepted marker atomically under captured timestamp condition across REST/WS producers.
- [ ] Retain history and insurance accounting while rejecting stale live-position side effects.
- [ ] Return capture timestamp; polling must reject stale/older fixes and expose stale state in rider map.
- [ ] Keep existing fanout rollout flag; document enable/rollback and don't flip production values.
- [ ] Verify mock ingestion plus local SQL concurrency if runtime available. Commit and review.

## Task 4: iOS upload deadline

Files: driver-app/utils/backgroundLocation.ts plus focused helper/test, at most3 per commit.

- [ ] Failing tests for unresolved Firebase preparation and response body.
- [ ] One total deadline covers preflight/fetch/body, releases reservations, preserves durable outbox, fences late dispatch.
- [ ] Verify auth/session checks retained and sibling consumers audited. Commit and review.

## Task 5: combined verification and PR

- [ ] Review task diffs against report and integrate only reviewed work.
- [ ] Focused Python/Jest regression tests and lint on changed files; local SQL test when available.
- [ ] Record unavailable native build/device and live-provider checks without implying they ran.
- [ ] Impact log: actual files/blast radius, before/after behavior, rollout/rollback, verification boundaries.
- [ ] Architect reviews whole branch, resolve material findings, push feature branch and open PR.

## Cross-task review focus

| Pair | Shared interface | Rule |
|---|---|---|
| 1/2 | cancellation.py, provider results | Task1 completes first; Task2 preserves new refund state |
| 1/3 | migrations | Distinct reserved prefixes; schema independent |
| 3/4 | captured_at and live/history requests | Preserve client payload shape; backend owns ordering |
| 3/rider | location_captured_at | Additive field; accept legacy missing timestamp only under bounded fallback |
| 1/5 | operational recovery | No real refunds from local tests; status updates idempotent |

Every task's tests exercise its intended behavior. Important cases: duplicate webhook, cancel during preauth, reordered cross-replica GPS, offline backlog, permanently unresolved native promise.
