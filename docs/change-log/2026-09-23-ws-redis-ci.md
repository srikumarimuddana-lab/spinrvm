# WebSocket Redis integration test in CI

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-23 |
| Author | Codex |
| Surface(s) | backend |
| Domain (Sentry tag) | dispatch |
| PR / commit link | PR #5725 follow-up |
| Related issue or gap ID | Wave 2 WS replay/resume coverage |

## 1. Issue / gap identified

The durable WebSocket replay Lua script had been exercised against local Redis but not by the standard backend CI workflow.

## 2. Root cause

The backend CI job provisioned PostgreSQL only; it did not provide Redis for the opt-in WebSocket Redis integration test.

## 3. Fix / remediation

Add an ephemeral Redis 6.2 service to the backend-test job and run the real-Redis integration test in its own step with the explicitly guarded loopback URL for database 15.

## 4. Risk & impact on existing functionality

- Blast radius: isolated CI infrastructure and test execution. Production Redis settings and deploy workflows are unchanged.
- The test rejects non-loopback hosts and databases other than 15, uses a unique client ID, and deletes only its own sequence and outbox keys.
- If Redis is unhealthy, the dedicated test step fails the backend check instead of silently skipping the real-server test.
- No runtime user flow, database state, money flow, or background loop changes.

## 5. User-experience effect

Nobody directly; this adds a merge-time backend check. No active sessions, UI, copy, or notifications are affected.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `.github/workflows/ci.yml` | Add disposable Redis service and dedicated integration-test step | Run the real Lua/order/retention contract in CI |
| `docs/change-log/2026-09-23-ws-redis-ci.md` | Record CI impact and rollback | Keep the change reviewable |

## 7. Before / after

This is additive CI wiring; production behavior does not change.

## 8. Rollback plan

Remove the Redis service and dedicated step from `.github/workflows/ci.yml`. No application data or production configuration needs cleanup.

## 9. Verification performed

- [ ] Automated workflow run: pending CI.
- [x] Local real-server test passed against disposable Redis 6.2.14, database 15.
- [x] Reviewed workflow scope: only the `backend-test` job is changed; production deployment config is untouched.

## 10. Sign-off

- [x] Rollback is a reversible CI-only workflow change.
- [x] Blast radius is stated.
