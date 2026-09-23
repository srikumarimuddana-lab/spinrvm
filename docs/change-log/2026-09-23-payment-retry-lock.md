# Payment retry Redis gate — PR 5725

| Field | Value |
|---|---|
| Issue / root cause | Retry loop continued without a distributed lock on Redis error, and won a local lock when Redis was missing. |
| Fix | Import strict SET NX under the existing module-local alias, skip failed/contended ticks, preserve heartbeat and retry cadence; emit lock-unavailable counter and redacted error. |
| Risk / blast radius | `payment_retry_loop` is spawned by lifespan. Gate covers failed ride payment retries, stuck-payout notices, guest corporate settlements and durable payment-operation reconciliation. Those helpers and their callers retain DB claims/idempotency; only this loop's entry gate changes. Redis outage delays collection/recovery, so lock-unavailable telemetry must be monitored. |
| Alternative | Globally fail closing the existing Redis helper risks non-money consumers; explicit per-loop migration is bounded. |
| UX effect | Payment retry/recovery can be delayed during a Redis outage. No new UI/copy/build. |
| Rollback | Restore Redis health first; a code rollback requires redeploy and restores fail-open behavior. Do not alter payment state or charge keys to drain backlog. Inspect existing payment-operation/Stripe records before any operator replay. |
| Verification | Error+recovery and absent-Redis cases failed before implementation. Focused retry/replay suites run before commit. Existing DB claim and deterministic-key tests retained. |
| Not verified | Live Redis outage, actual Stripe deduplication and deployed canary. |

Files: `backend/utils/payment_retry.py`, `backend/tests/test_payment_retry_coverage.py`, this record.

Before: `except Exception: got_lock = True` / local-capable `redis_set_nx`.
After: `redis_set_nx_strict as redis_set_nx`; error counter and `got_lock = False`.

Plan adjustment: changed the existing coverage module containing the old fail-open assertion instead of the original test module, keeping a coherent three-file commit. The original retry and replay suites still run. Lease expiry is not mutual exclusion for a whole tick; DB claims and Stripe idempotency remain authoritative.
