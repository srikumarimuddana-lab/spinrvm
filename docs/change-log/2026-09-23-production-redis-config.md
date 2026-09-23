# Change Impact & Risk: production utility Redis configuration

| Field | Value |
|---|---|
| Date | 2026-09-23 |
| Author | Codex |
| Surface(s) | Backend |
| Domain (Sentry tag) | admin / configuration |
| PR / commit link | PR #5725 |
| Related issue or gap ID | PR 5725 implementation plan, Task 8 |

## 1. Issue / gap identified

Production startup checked only rate-limit Redis storage. Core utilities could still start with their process-local fallback because `utils.redis_client` reads `REDIS_URL` directly from the environment.

## 2. Root cause

`Settings.REDIS_URL` is not the runtime source consumed by the Redis utility client, and the production guard did not inspect `os.environ["REDIS_URL"]`.

## 3. Fix / remediation

Fail production startup when the actual utility Redis URL is missing, unsupported, or malformed. Diagnostics name the setting but never echo its credential-bearing value. Development retains its early return.

## 4. Risk & impact on existing functionality

- Blast radius: single-surface backend startup configuration.
- `utils.redis_client` consumers include driver presence, payment retry, reconciliation, capacity/presence sweepers, maps budget, and T4A job state; they now require the shared Redis URL in production.
- Rate-limit storage remains independently validated from `RATE_LIMIT_REDIS_URL`. `core.lifespan` and `utils.ws_pubsub` continue using their existing settings/fallback logic.
- A production environment missing `REDIS_URL` will now fail closed at middleware initialization. This is the intended protection against per-process utility state.
- No background-loop logic, ride state, or money deltas changed.

## 5. User-experience effect

No direct rider or driver UX change. Misconfigured production deployments stop serving requests at startup. No customer-facing copy changed.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/core/middleware.py` | Validate the actual `REDIS_URL` environment value and URL shape | Utility Redis reads the process environment directly |
| `backend/tests/test_middleware_production_config_guard.py` | Cover missing/unsupported/malformed URLs, valid redis/rediss, redaction, and dev behavior | Pin the production gate and unchanged development behavior |
| `docs/change-log/2026-09-23-production-redis-config.md` | Record change impact and verification | Required runtime change record |

## 7. Before / after

```python
# Before: only rate-limit Redis storage was checked.
redis_url = (settings.RATE_LIMIT_REDIS_URL or "").strip()
```

```python
# After: independently check the URL consumed by utils.redis_client.
utility_redis_url = (_os.environ.get("REDIS_URL") or "").strip()
```

## 8. Rollback plan

If a valid production Redis URL is incorrectly rejected, revert this guard and redeploy. The change alters startup validation only and touches no persisted data or live state.

## 9. Verification performed

- [x] Automated: `/tmp/pr5725-venv/bin/python -m pytest backend/tests/test_middleware_production_config_guard.py -q --no-cov` (24 passed).
- [ ] Manual repro in staging: not run; no production/staging changes were made.
- [x] Blast-radius grep: `REDIS_URL` references under `backend/utils` and `backend/core`.
- [x] Reviewed production-vs-development guard behavior and credential redaction.
- [x] No feature flag needed; behavior is startup configuration validation.

## 10. Sign-off

- [x] Rollback is an isolated code revert and redeploy; no data remediation applies.
- [x] Blast radius and user-visible effect are stated.
- [x] Existing development behavior is covered by a test.
