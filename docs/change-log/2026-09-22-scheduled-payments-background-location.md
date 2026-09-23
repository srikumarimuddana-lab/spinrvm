# Scheduled cancellation payments and background driver location

## User experience and remediation

This change addresses two reported failures: canceled/no-driver scheduled rides could retain an unresolved payment or display an unconfirmed refund as complete, and an iOS driver's delayed background uploads could make the Android rider map appear stuck or jump backwards.

The implementation was reviewed by GPT-6 Luna agents in developer and Spinr architect roles. This is a code review, not consultation with a verified former Uber employee. No live customer transaction was inspected or refunded during this work.

| Area | Behavior change | Affected surfaces |
|---|---|---|
| Refund lifecycle | Provider acceptance, pending, failure and success are distinct; durable operations support reconciliation; confirmed cumulative refunds update the ledger and ride summary atomically | Stripe helpers, cancellation, webhooks, payment retry, manual reconciliation |
| Scheduled dispatch | Authorization attachment is conditional on current ride state; cancellation-winning paths compensate holds | Scheduled dispatch and cancellation |
| Notice fee | Persist actual collected amount, outcome and reference separately from normal cancellation fees | Cancellation, operation recovery, rider trip details |
| Live driver marker | Database row lock accepts only newer captures, no older than 60 seconds and no more than 5 seconds ahead | REST v1/v2, live endpoint, WebSocket, driver repository |
| History | Queued history remains a breadcrumb record, not a fresh map position; rejected live markers do not refresh presence | Location ingestion and insurance distance filters |
| Rider map | Polling carries capture time and cannot rewind a newer observed fix; stale location text appears after 20 seconds | Active-ride resume, ride detail, approach and in-trip screens |
| iOS uploads | The existing 10-second per-request limit now includes native preparation and ACK parsing; late preparation cannot dispatch | Background location task, durable outbox |

All money semantics retain the existing fee eligibility policy. No-driver cancellation remains fee-free. A released authorization is a bank hold release, distinct from a refund of captured funds. Scheduled notice-fee policy remains controlled by its existing settings.

## Root cause

Code inspection found that provider acceptance and confirmed refund success were conflated, while ride refund totals and ledger entries were separate writes that could race. Pending polls also consumed a short retry budget. Scheduled dispatch could attach an authorization after cancellation. Location ingestion could promote delayed batches to the live marker, and the iOS upload deadline did not include native preparation or response-body parsing. These findings explain credible failure paths; they do not establish the outcome of any historical customer transaction.

The blast radius spans backend payment recovery, scheduled dispatch, location ingestion and both mobile apps. Riders can see corrected payment states and stale-location indicators during active sessions; drivers retain queued points after upload failures. The payment worker and webhook share the new transaction, and every live location writer shares the marker RPC. Existing fee eligibility and insurance policy are preserved. Rollout must coordinate these writers as described below.

## Database and rollout order

1. Apply additive migrations **444** (payment operations and ride outcome columns), **445** (capture timestamp and privileged atomic marker RPC), and **446** (atomic cumulative refund accounting) before the new backend. Existing backend can coexist with additive schema, but only upgraded writers enforce marker ordering and atomic refund accounting; complete the rollout across both serving backend installations before treating these guarantees as active. Do not run older manual refund repair scripts during or after the rollout.
2. Configure the existing Stripe webhook endpoint to deliver `refund.updated` and `refund.failed` as well as its existing events. Keep event signing and deduplication enabled. Validate the updated endpoint in Stripe test mode before production.
3. Deploy the backend and verify the existing payment-retry worker is healthy. Inspect unresolved operations and alert operational owners to exhausted/action-required rows; a database entry is not proof the customer received money.
4. Publish compatible rider/driver builds after the normal mobile checks. Driver upload URL/payload/auth contracts remain compatible. Older clients can omit capture timestamps for direct single WebSocket pings; queued untimed history is never promoted to a live marker. Existing driver rows without timestamps become known on the next fresh fix.
5. Verify the **actual environment value** of `background_location_fanout_enabled`. Migration 427 defaulted it off. This PR does not change production settings or silently enable it. After the device test below, deliberately enable it to obtain background REST-to-rider WebSocket delivery; with it disabled, riders still depend on polling.

Successful provider reads that report a pending refund are polled every 15 minutes without consuming the error budget. Consecutive reconciliation exceptions use bounded backoff and require operational review if exhausted. Requested or pending refunds never count as confirmed refunded money.

No production migrations, webhook settings, feature flags, refunds or mobile releases were executed by this PR preparation.

## Required device and provider verification

Use a physical iPhone driver and Android rider on a test ride. Verify foreground, screen locked, navigation app foreground, cellular/Wi-Fi handoff, temporary offline backlog, app reopen, and sign-out during queued work. Check capture-to-display latency, driver presence, history continuity and battery use. A backlog must not move the marker behind a newer fix. A stale indicator must clear only on a fresh sensor capture. Test app force-quit separately; normal background execution is not a promise of continuous updates after iOS termination.

In Stripe test mode exercise: cancel before dispatch; cancel while preauthorization is outstanding; no drivers; captured cancellation with full and partial refund; pending refund later succeeded/failed; duplicate and out-of-order webhook; worker restart after provider success but before database update; concurrent workers; and scheduled notice-fee card/wallet outcomes. Check provider objects, ride summaries and ledger entries together.

For previously affected real rides, use the existing reconciliation script in **dry-run** mode first, compare each PaymentIntent/refund and cancellation fee to the ledger, and review the proposed corrections. Newly durable operations do not automatically backfill historical rows. Do not infer a bank refund from a `cancelled` ride or an old `refunded` string alone.

## Verification performed locally

- 174 targeted Python location/repository/WS/API tests passed; one pre-existing physical-device test is intentionally xfailed.
- Complete driver Jest suite and coverage gates: **1,895 passed across 159 suites**. This includes 86 background-location tests. Four new tests fail against the original source and pass with the deadline fix (Firebase init, App Check, fetch ignoring abort, stalled response body). Late native completion cannot start a location request after timeout, and queued points remain unacknowledged.
- 34 rider Jest tests passed across sensor ordering, stale-state display, cancellation payment messages and ride-detail route contracts.
- Both complete mobile TypeScript checks (`tsc --noEmit`) passed.
- Migration 445 executed in a local PostgreSQL WASM runtime against a minimal schema: newer/older/stale/future captures, preservation of insurance fields, and denial of client-role execution checked. This is SQL execution, not a live Supabase or multi-replica load test.
- Final integrated backend verification: **548 passed**, one physical-device-only xfail across 29 targeted payment, cancellation, scheduling, marker, WebSocket, API and existing refund/orphan contract files. All changed Python files pass Ruff.
- Migration 446 executed in local PostgreSQL WASM: cumulative 100→200 cents books two 100-cent deltas; stale and duplicate calls do not book more; zero confirmed refunds preserve paid status; tax metadata, amount/PI validation, rollback on conflicting ledger UUID, full-refund status, separate paid fee preservation on replay, failed-fee handling and client-role denial passed. This validates SQL transactions sequentially, not concurrent production replicas.
- The repository migration safety check passes for all three new migrations.
- Migration 444 also executed in local PostgreSQL WASM: backend access, unique operation key, nonnegative cents and client-role denial passed.
- GPT-6 Luna architect review found no remaining money or location blockers after the atomic accounting, separate-fee replay, orphan handling and email-preservation corrections.

No native build, physical-device trip, live Stripe transaction or live Supabase migration was run. Full repository coverage gates and hosted CI remain separate checks. Hosted rider tests have three failures in unchanged `shared/components/__tests__/SupportScreen.contact.test.tsx`; the same three failures were reproduced locally on base commit `0b6689b`. A hosted driver `locationIntegrity` mock assertion also failed; isolated tests pass on both base and PR source, and the complete local PR suite passes 1,895 tests (unchanged base passes 1,891). These checks are not represented as green.

## Risk & impact on existing functionality

Postgres prevents a delayed writer from replacing a newer stored marker. The rider store orders the samples it has observed. A fresh WebSocket sample whose database write was deliberately coalesced is still delivered for latency reasons; it is not independently proven newer than an unseen persisted position. Polling/resume timestamps provide a baseline once received. Missing sensor times on older client single pings retain a compatibility fallback.

Refund push/email delivery remains best-effort. If cancellation, `refund.updated` or a worker applies the accounting delta before `charge.refunded`, the later charge event can have no new delta and omit a notification. Ride history and confirmed accounting remain available; reliable cross-path notification delivery needs a separate notification-outbox change.

The existing Period 1 accumulator uses absolute read-modify-write values. Its pre-existing concurrent-batch lost-increment risk is not repaired by this marker change; replacing it with simple increments would introduce duplicate increments on ambiguous retries. Fresh explicitly untrusted points and mocked historical points do not gain new permission to increment it.

The timeout bounds each upload request, not native scheduling, the entire multi-batch flush, or every earlier storage/auth operation. Durable data remains queued until a valid server acknowledgement.

## Rollback plan

Disable background REST fanout if delivery causes a problem; polling and durable history remain available. Roll back mobile/backend code together as needed, retaining additive database columns/tables and unresolved payment operations for reconciliation. Do not drop payment obligations or erase timestamps to roll back application code. Stop a faulty retry worker before reviewing unresolved provider actions. Migration 445/446 functions may be removed only after no deployed backend or repair script calls them; schema removal is unnecessary for an application rollback.

## External contracts checked

- [Stripe refund statuses](https://docs.stripe.com/api/refunds)
- [Stripe idempotency retention and replay semantics](https://docs.stripe.com/api/idempotent_requests)
- [Expo location background requirements](https://docs.expo.dev/versions/v54.0.0/sdk/location/)
- [Apple background location configuration](https://developer.apple.com/documentation/corelocation/cllocationmanager/allowsbackgroundlocationupdates)
