# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-19 |
| Author | Claude Code (agent), on behalf of vikas@ngitservices.com |
| Surface(s) | backend |
| Domain (Sentry tag) | admin |
| PR / commit link | local worktree commit `aa3a453` (not pushed / no PR — see task instructions) |
| Related issue or gap ID | `docs/audit/2026-08-18-full-fleet-whole-app-audit.md`, finding N11, ranked-blocker #8 (single-endpoint fix; ranked-blocker #18's broader ~12-endpoint sweep is out of scope here) |

## 1. Issue / gap identified

`POST /admin/redis/flush-prefix` in `backend/routes/admin/monitoring.py` could wipe every Redis key under an allowlisted prefix (e.g. `cache:user:`, potentially rate-limit or OTP-lockout state depending on the allowlist) with **zero audit-log row** anywhere — no forensic trail for a destructive, irreversible production action.

## 2. Root cause

The endpoint was built with a confirm-string gate (`confirm: "FLUSH"`) and a prefix allowlist (`_FLUSHABLE_PREFIXES`) as its only safeguards. It called `redis_delete_pattern()` and returned the result directly, never calling the repo's existing `utils.audit_logger.log_admin_action()` helper that every other destructive/money-moving admin action (e.g. `routes/admin/rides.py`'s payout-period-close, `routes/admin/stripe_import.py`'s Stripe-account-redirect) already uses. This looks like an oversight at the time the endpoint was written, not a deliberate decision — the docstring even says "the audit trail there is better" about the `redis-cli` alternative, implying the author assumed this endpoint's own trail was weaker but did not add one at all.

## 3. Fix / remediation

- After a successful flush, write an audit row via the existing `log_admin_action()` helper (`utils/audit_logger.py`, same `audit_logs` table/schema every other admin route uses): `action="redis_flush_prefix"`, `entity_type="redis"`, `entity_id=<prefix>`, `details={prefix, outcome: "success", deleted_keys}`. `actor_id` is taken from the admin JWT's `id` claim by the shared helper — no admin name/email is logged, per CLAUDE.md's PIPEDA logging rules.
- Audited **after** acting (act-then-log), matching the precedent in `rides.py`'s `payouts_period_closed` and `stripe_import.py`'s `stripe_account_update` audits, both of which log outcome data (counts/ids) that is only known once the operation has run.
- Added an `info`-level structured log line (`extra={"domain": "admin", "prefix": ..., "deleted_keys": ..., "admin_id": ...}`) alongside the audit row, matching the `domain` Sentry-tag convention used elsewhere in `routes/admin/`.
- If the Redis delete itself raises, the error is now: logged at `error` level with the real exception (`exc_info=True`), audited as a `redis_flush_prefix` row with `outcome: "failure"` and the error string, and re-raised as `HTTPException(503)` — not silently swallowed, per CLAUDE.md's "do not silently swallow errors" rule.
- `log_admin_action()` itself already never raises and self-logs (`logger.error`) on its own write failure (see `utils/audit_logger.py` docstring: "Failures are logged but never re-raised — an audit write failure must not roll back the underlying mutation"). The endpoint additionally logs a second `error`-level line at the call site if `audit_id` comes back `None`, so an audit-write failure surfaces loudly without blocking or corrupting the flush endpoint's own success response.
- Response payload gained one new field, `audit_id` (the written row's id, or `None` if the audit write failed) — additive only.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated, single endpoint, single caller.** Grepped for every consumer:
  - Backend: only `backend/routes/admin/monitoring.py`'s `flush_redis_prefix` function defines/calls this behavior. No other backend module calls `redis_delete_pattern` from this route or references `flush_redis_prefix`.
  - Admin dashboard: exactly one API wrapper, `admin-dashboard/src/lib/api/live-monitoring.ts`'s `flushRedisPrefix()`, re-exported through `admin-dashboard/src/lib/api.ts`. Its only call site is `admin-dashboard/src/app/dashboard/monitoring/redis/page.tsx` (line 212). No other page or component imports it.
  - The frontend wrapper's TS return type (`{ prefix, deleted_keys, admin_id }`) does not declare the new `audit_id` field. This is harmless: it's a plain `fetch`+`JSON.parse` wrapper with no runtime schema validation, so an extra JSON field is simply ignored by existing consumers (structural typing, not exact-shape validation) — no `page.tsx` code reads `audit_id`, so nothing breaks and no frontend edit is required for this fix.
- **Could the audit-table write ever block or corrupt the actual flush?** No. The flush (`redis_delete_pattern`) always runs and completes (or fails and is reported) before the audit write is even attempted; `log_admin_action()` is `try/except`-wrapped internally and never raises. A slow or failing audit write cannot delay, block, or alter the Redis operation or its reported `deleted_keys` count.
- **`audit_logs` table impact**: this reuses the existing table and helper used by dozens of other admin routes (see `utils/audit_logger.py` callers across `routes/admin/*.py`). No schema change. Adds one row per successful or failed flush call — negligible volume (this is a manual, rare admin action).
- No interaction with the 16 background loops, the ride state machine, or money/wallet deltas — this endpoint only ever touches ephemeral Redis cache state and the `audit_logs` table.

## 5. User-experience effect

- Rider/driver/corporate-admin facing: none.
- Internal-admin facing: the admin dashboard's Redis flush-prefix UI (`monitoring/redis/page.tsx`) behavior is unchanged from the operator's point of view — same confirm flow, same success/error handling, same response shape plus one unused extra field. Not visible mid-session to any rider/driver.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/monitoring.py` | Added `log_admin_action` import (both dual-import branches); `flush_redis_prefix` now wraps the Redis call in try/except, writes a success or failure audit row via `log_admin_action`, adds an info/error structured log line, re-raises Redis failures as `HTTPException(503)` instead of letting them propagate unaudited/untagged, and returns the new `audit_id` field | Close audit N11 / ranked-blocker #8: no forensic trail existed for this destructive admin action |
| `backend/tests/test_admin_monitoring_coverage.py` | Extended `test_flush_redis_prefix_deletes_allowlisted_prefix` to assert the audit row is written with expected args; added `test_flush_redis_prefix_admin_auth_gate_unchanged`, `test_flush_redis_prefix_redis_failure_is_audited_and_surfaced`, `test_flush_redis_prefix_audit_write_failure_does_not_block_response` | Regression coverage for the new audit-logging behavior and its error paths |

## 7. Before / after

```python
# Before
    deleted = await redis_delete_pattern(f"{prefix}*")
    return {
        "prefix": prefix,
        "deleted_keys": deleted,
        "admin_id": current_admin.get("id"),
    }
```

```python
# After
    try:
        deleted = await redis_delete_pattern(f"{prefix}*")
    except Exception as exc:
        logger.error("redis flush-prefix failed", extra={"domain": "admin", "prefix": prefix, ...}, exc_info=True)
        await log_admin_action(current_admin, "redis_flush_prefix", "redis", prefix,
                                {"prefix": prefix, "outcome": "failure", "error": str(exc)})
        raise HTTPException(status_code=503, detail="Redis flush failed; retry.") from exc

    logger.info("redis flush-prefix succeeded", extra={"domain": "admin", "prefix": prefix,
                                                         "deleted_keys": deleted, "admin_id": current_admin.get("id")})
    audit_id = await log_admin_action(current_admin, "redis_flush_prefix", "redis", prefix,
                                       {"prefix": prefix, "outcome": "success", "deleted_keys": deleted})
    if audit_id is None:
        logger.error("redis flush-prefix audit log write failed after a successful flush", extra={...})

    return {"prefix": prefix, "deleted_keys": deleted, "admin_id": current_admin.get("id"), "audit_id": audit_id}
```

## 8. Rollback plan

No feature flag / `app_settings` toggle applies here — this is a pure observability addition to a manual, operator-triggered admin action with no effect on rider/driver-facing state, ride state, or money. If this change needs to be reverted:
- `git revert aa3a453` (the code+test commit) restores the prior behavior exactly — safe here specifically because this commit did not touch any live data, wallet, or ride-state row; it only adds new `audit_logs` rows and log lines going forward. (Per CLAUDE.md, a plain `git revert` is *not* a valid rollback plan for changes touching live data — this one is exempt because it doesn't.)
- No migration was added (reuses the existing `audit_logs` table), so there is no migration rollback needed.
- No redeploy-only path was required; a revert alone fully undoes the change.

## 9. Verification performed

- [x] Automated tests run: `pytest` via the project venv — `/tmp/spinr-venv/bin/pytest tests/test_admin_monitoring_coverage.py -q --no-cov` → **22 passed** (includes the 4 new/modified flush-prefix tests plus the file's other 18 pre-existing tests, unaffected). Also ran with coverage enabled (`--no-cov` omitted) to confirm no import/collection errors — same 22 passed (the file-level coverage-threshold failure printed is expected/pre-existing when running a single test file in isolation against the repo's global `--cov-fail-under`, not a regression).
- [x] `ruff check backend/routes/admin/monitoring.py backend/tests/test_admin_monitoring_coverage.py` → All checks passed.
- [ ] Manual repro in staging — **not performed** (no staging environment available in this session; verified via mocked `mock`/`AsyncMock` unit tests only, per CLAUDE.md's unit-test convention of mocking Supabase/Redis, not hitting real infra).
- [x] Blast-radius grep performed: backend (`flush_redis_prefix`, `redis_delete_pattern` call sites) and admin-dashboard (`flush-prefix`, `flushPrefix`) — see Section 4 for what was found (single caller each side).
- [x] Reviewed against relevant CLAUDE.md conventions: Observability (audit table + info log for admin actions), "do not silently swallow errors" (DB/Redis error now surfaces as 503 + error log + failure-outcome audit row, not a masked 200), PIPEDA logging (actor_id only, no name/email).
- [x] Feature-flagging: not applicable — this is an additive observability change to an existing, already-gated (confirm string + allowlist) admin-only endpoint; no new user-visible behavior, no validation-rule change, no new UX to gate.
- **Not run**: no frontend build (`npm run build`) — this fix is backend-only; `admin-dashboard` was not modified (the new `audit_id` response field is additive and untyped-but-harmless per Section 4, so no frontend change was needed or made).

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`, safe because no live data is touched).
- [x] Blast radius is stated, not assumed (single backend endpoint, single frontend caller, both named above).
- [x] No silent behavior change to an already-shipped flow: the flush behavior (what gets deleted, when, under what confirm/allowlist gate) is unchanged; only observability was added, plus a new 503 error path on Redis failure that previously would have propagated as an unhandled 500 with no audit trail either way — so the failure-path change is a *fix*, not a regression, and is called out explicitly here.
