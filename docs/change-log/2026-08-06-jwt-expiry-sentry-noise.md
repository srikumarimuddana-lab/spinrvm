# Change Impact & Risk Log — routine JWT expiry no longer pages Sentry

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-06 |
| Author | Claude Code (session: JWT token expiration) |
| Surface(s) | backend |
| Domain (Sentry tag) | auth |
| PR / commit link | branch `claude/jwt-token-expiration-fi0ee7` |
| Related issue or gap ID | Sentry: `JWT verification failed: 401: Token has expired` (culprit `/api/admin/drivers/approval-queue`, env=development) |

## 1. Issue / gap identified

Every expired access token raised a Sentry **error** event — `JWT verification failed: 401: Token has expired` — even though an expiring access token is the normal, self-healing end of the token lifecycle. The reported event fired on `/api/admin/drivers/approval-queue`: an admin's 1-hour token aged out while the dashboard was open, the dashboard silently refreshed and retried, and the admin saw nothing. Nothing was broken; Sentry was told otherwise.

## 2. Root cause

Two things compound:

1. `backend/dependencies/__init__.py::get_current_user` wrapped `verify_jwt_token` in a bare `except Exception` and logged at `logger.error`. It could not tell "the token expired, as designed" apart from "the JWT layer itself is broken" — both got ERROR.
2. `backend/server.py:541` bridges loguru into Sentry with `logger.add(_loguru_sentry_sink, level="ERROR")`. **The log level chosen at that call site *is* the Sentry filter.** So every token expiry across every authenticated endpoint minted an event.

Token TTLs make this high-volume by design: 15 min (rider/driver), 1 hr (admin). Clients are built to absorb it — the rider/driver Axios interceptor and `admin-dashboard/src/lib/api/client.ts:94` both refresh-and-retry on 401. The noise floor buried real auth defects, which is the failure mode that matters: an actual JWT misconfiguration would have looked identical to the routine churn.

## 3. Fix / remediation

Split the handler by rejection reason and pick the level per CLAUDE.md observability conventions (security-relevant events → info log; degraded-but-recovered → metric, never Sentry):

| Reason | Level | Reaches Sentry |
|---|---|---|
| Expired signature | `info` | No |
| Invalid / malformed / bad signature | `warning` | No |
| Anything else out of `verify_jwt_token` | `error` | **Yes** |

Added counter `spinr_auth_token_rejected_total{reason=expired\|invalid\|error}` so rejection *rate* stays alertable — a forged-token spike or an auth outage is now a metric threshold instead of an inbox flood. Named the two detail strings (`JWT_DETAIL_EXPIRED` / `JWT_DETAIL_INVALID`) so the classification reads a constant rather than re-parsing prose.

## 4. Risk & impact on existing functionality

**Blast radius: isolated to observability.** No auth decision changed — same 401, same `detail="Invalid token"`, same order of checks. What changed is the log level, plus one new in-process counter.

Blast-radius grep performed:

- `grep -rn "JWT verification failed"` across the repo (excluding `node_modules`/`.git`) — **zero** consumers outside the new test. No alert rule, dashboard query, or log-based monitor keys off the old string.
- `verify_jwt_token` callers: only `get_current_user` (the changed path) and `get_token_session_id`, which swallows every exception by design and is untouched.
- `get_current_user` callers include `get_current_user_allow_expired` (SOS grace path, `routes/rides/safety.py`). Verified it re-decodes the token itself with `verify_exp: False` and branches on `exc.status_code == 401` — it never reads the detail string or the log. Unaffected. Its own `[auth] expired-token grace used on safety endpoint` warning was already at `warning` (non-Sentry) before this change.
- WebSocket auth (`routes/websocket.py`) already handled expiry without an error log — no divergence introduced.
- New import `utils.metrics.inc` into `dependencies/__init__.py`: `utils/metrics.py` is a leaf module (stdlib only), so no import cycle.

Real risk being accepted: a **genuine** auth outage that manifests as mass token rejection (e.g. `JWT_SECRET` rotated without redeploying, so every live token fails signature check) now logs at `warning` and does **not** page via Sentry. Mitigated by `spinr_auth_token_rejected_total{reason="invalid"}` — but only once someone writes an alert on it, which this change does not do. Flagged below.

No interaction with the ride state machine, money/wallet deltas, or any of the background loops in `core/lifespan.py`.

## 5. User-experience effect

**None.** Rider, driver, corporate admin, and internal admin behaviour is byte-identical: same status code, same response body, same client-side refresh-and-retry. This is a server-log/Sentry-routing change only. Nothing is visible mid-session to a rider mid-ride or a driver online. No copy or notification change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/dependencies/__init__.py` | Split the `verify_jwt_token` failure handler into `except HTTPException` (expected — info/warning + metric) and `except Exception` (defect — error). Added `JWT_DETAIL_EXPIRED`/`JWT_DETAIL_INVALID` constants and the `utils.metrics.inc` import. | Stop routine token expiry from minting Sentry events while keeping genuine JWT-layer defects paging. |
| `backend/tests/test_auth_token_rejection_log_level.py` | New: pins log level per rejection reason, asserts the C4 static client message survives, asserts the signing secret never reaches a log line. | The level *is* the Sentry filter; without a test, a future edit silently reopens the flood. |

## 7. Before / after

```python
# Before — every expiry is an ERROR, and server.py ships ERROR to Sentry
    try:
        payload = verify_jwt_token(token)
    except Exception as e:
        logger.error(f"JWT verification failed: {e}")
        raise HTTPException(status_code=401, detail="Invalid token") from e
```

```python
# After — classify, then choose the level (Sentry only for the defect case)
    try:
        payload = verify_jwt_token(token)
    except HTTPException as e:
        expired = e.detail == JWT_DETAIL_EXPIRED
        _metric_inc("spinr_auth_token_rejected_total", {"reason": "expired" if expired else "invalid"})
        if expired:
            logger.info(f"JWT rejected: {JWT_DETAIL_EXPIRED} — client should refresh")
        else:
            logger.warning(f"JWT rejected: {e.detail}")
        raise HTTPException(status_code=401, detail=JWT_DETAIL_INVALID) from e
    except Exception as e:
        logger.error(f"Unexpected error verifying JWT: {type(e).__name__}: {e}")
        _metric_inc("spinr_auth_token_rejected_total", {"reason": "error"})
        raise HTTPException(status_code=401, detail=JWT_DETAIL_INVALID) from e
```

## 8. Rollback plan

`git revert` is a complete rollback here. This change writes no data, touches no migration, no `app_settings` value, and no live-data state (no Stripe charge, wallet delta, ride row, or insurance-period row) — the caveat in the template about `git revert` not being a rollback plan does not bite. Reverting restores the previous ERROR-level log on the next deploy, and Sentry starts receiving expiry events again immediately.

Not feature-flagged: there is no user-visible surface to ship dark, and gating a log level behind an `app_settings` read on the auth hot path would add a lookup to every failed request for no benefit. If Sentry visibility is needed urgently before a redeploy, `SENTRY_DSN` and log-level config are already deploy-time settings.

## 9. Verification performed

- [x] Automated tests run — **unit**:
  - `backend/tests/test_auth_token_rejection_log_level.py` (5 new tests) — **5 passed**.
  - Existing auth suites `test_admin_security.py`, `test_dependencies_auth_gaps.py`, `test_auth.py`, `test_websocket_auth.py`, `test_account_deletion_auth.py`, `test_p1_auth_hardening.py`, `test_admin_auth_log_redaction.py` — **105 passed**.
  - Full `pytest -m unit` tier — **1926 passed, 1 skipped, 1 failed**. The failure is `test_scheduled_rides_coverage.py::TestCheckScheduledRides::test_lock_not_acquired_still_proceeds_to_fetch`, which is **pre-existing and unrelated** (scheduled-dispatch lock acquisition, no auth involvement): confirmed by re-running it on a clean stashed tree at HEAD, where it fails identically.
- [x] `ruff check` + `ruff format --check` clean on both changed files.
- [x] Blast-radius grep performed — searched `JWT verification failed` repo-wide, all `verify_jwt_token` callers, all `get_current_user_allow_expired` callers, and `utils/metrics.py` imports for cycles. Listed in §4.
- [x] Reviewed against `CLAUDE.md` observability conventions (log-level table; "degraded-but-recovered → warning log + metric, never Sentry"; "security-relevant events → info log") and against the C4 static-client-message rule (`detail="Invalid token"` preserved and asserted in test).
- [x] Not feature-flagged — justified in §8 (no user-visible surface).
- [ ] Manual repro in staging — **not done**, see §10.

## 10. What was NOT verified

- **No staging repro.** The Sentry-side effect (expiry events stop arriving; a forced `TypeError` in `verify_jwt_token` still arrives) was reasoned from `server.py:541`'s `logger.add(_loguru_sentry_sink, level="ERROR")`, not observed against a live DSN. The loguru→Sentry bridge itself is unchanged and untested by this diff.
- **No production build run** — backend-only change, no `admin-dashboard`/`rider-app`/`driver-app` file touched, so the `npm run build` gate does not apply.
- **Tests mock the logger**, so they prove which loguru method is called, not that loguru's sink routing behaves as expected end-to-end. That routing is pre-existing behaviour.
- **The new counter has no alert attached.** `spinr_auth_token_rejected_total` is emitted and scrapeable at `/metrics`, but nothing alerts on it yet. Until someone adds a threshold on `reason="invalid"`, a mass-rejection auth outage is quieter than it was before this change. This is the one real regression in coverage and should be picked up as a follow-up.
- **Not tested against live Supabase** — the rejection path fails before any DB read, so no DB interaction exists to test.
