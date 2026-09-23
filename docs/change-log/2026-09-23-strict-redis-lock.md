# Strict Redis lock primitive — PR 5725

| Field | Value |
|---|---|
| Issue / root cause | `redis_set_nx` can grant local ownership when Redis is absent or client initialization fails. Local ownership is not distributed. |
| Remediation | Add `redis_set_nx_strict`; only a successful Redis SET NX EX grants ownership. Contention returns false; unavailability raises. |
| Risk / blast radius | Additive API with no current production callers. Existing `redis_set_nx` and `try_acquire_leader_lock` consumers remain unchanged. Money callers migrate separately. TTL expiry still permits overlap; DB claims and Stripe idempotency remain required. |
| Alternative | Changing all existing callers would change intentional non-money/development fallback behavior; an explicit primitive limits scope. |
| UX effect | None until individual consumers opt in. No frontend change/build. |
| Rollback | Remove additive helper before consumer rollout; after rollout revert dependent consumer commits first. No data mutation or schema change. |
| Verification | Four regressions failed before implementation: absent URL, cold initialization failure, command error, and contention/expiry. Full Redis coverage suite is run before commit. |
| Not verified | Real multi-process Redis, live Supabase/Stripe and production deployment. |

| File | Change | Why |
|---|---|---|
| backend/utils/redis_client.py | Strict additive primitive | Explicit distributed ownership contract |
| backend/tests/test_redis_client_coverage.py | Failure/contended/expired ownership tests | Prevent fallback regression |
| This record | Impact and limits | Review evidence |
