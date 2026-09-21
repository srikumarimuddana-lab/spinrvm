# Change Impact & Risk Log — JWT Expiration Sentry Noise

**Date**: 2026-09-21
**Sentry issue**: CRIMSON-SMOKE-7445-E (1,446 events since 2026-06-10)

## Issue / gap identified

Expired JWT tokens on `/notifications` polls generated `logger.error` → Sentry
events at a rate of ~147/week. The client's 401 handler recovers silently by
refreshing and retrying, but the backend had already emitted an error-level log.

## Root cause

Two contributing factors:

1. **Backend** (`dependencies/__init__.py`): the JWT catch block logged every
   failure — including normal token expiration — at `error` level. Sentry
   captures all error-level logs, so each expired-token 401 became a Sentry
   event even though the client recovers automatically.

2. **Frontend** (`shared/hooks/queries/notificationQueries.ts`): the
   `useNotifications` query never called `ensureFreshToken()` before hitting the
   API. Rider-app polls every 3 min, driver-app every 5 min; with 15-min access
   tokens, the last poll before expiry routinely sends a stale token. On app
   foreground resume, `ensureFreshToken()` is fire-and-forget while
   `focusManager.setFocused(true)` triggers an immediate notification refetch —
   a race the expired token usually wins.

## Fix / remediation

1. **Backend**: split the `except Exception` into `except HTTPException` (checks
   `e.detail == "Token has expired"` → `logger.warning`) and a remaining
   `except Exception` (keeps `logger.error`). Expired tokens no longer reach
   Sentry; tampered/invalid tokens still do.

2. **Frontend**: added `await ensureFreshToken()` at the top of the
   `useNotifications` queryFn. Every notification poll now proactively refreshes
   the token when < 2 min remaining, eliminating the race on foreground resume
   and the stale-token poll during long sessions.

## Risk & impact on existing functionality

| Consumer | Impact |
|---|---|
| `backend/dependencies/__init__.py` `get_current_user()` | Direct change — JWT error logging split. All other callers (`Depends(get_current_user)` across ~30 route files) are unaffected: the function still raises 401 with the same detail string. |
| `shared/hooks/queries/notificationQueries.ts` `useNotifications()` | Direct change — adds `ensureFreshToken()` call. Used by `rider-app/app/(tabs)/index.tsx` and `driver-app/app/driver/(tabs)/index.tsx` for badge + inbox. |
| `shared/api/client.ts` `ensureFreshToken()` | Read-only; already called by other hooks and the foreground handler. No change to its contract. |
| Other `queryFn` hooks in `shared/hooks/queries/` | Not changed. They rely on the reactive 401 interceptor and may see the same stale-token race, but at much lower volume since they aren't polled on a timer. |

**Risk**: Low. The backend change is log-level only (no behavior change to
callers). The frontend change adds a fast no-op (token not near expiry) or a
network call (refresh needed) before each notification poll — worst case adds
~200 ms latency to a background poll, which has no SLA.

## User experience effect

None visible. Riders and drivers already see successful notification loads
(the 401 interceptor retried transparently). The fix eliminates a ~1s jitter
window where a poll could briefly fail before the retry, and reduces Sentry
noise by ~147 events/week.

## Files modified

| File | What changed | Why |
|---|---|---|
| `backend/dependencies/__init__.py` | Split JWT `except` block: expired → warning, other → error | Stop expired tokens from generating Sentry events |
| `shared/hooks/queries/notificationQueries.ts` | Added `ensureFreshToken()` import and call in `useNotifications` queryFn | Proactively refresh token before notification polls |
| `backend/tests/test_auth.py` | Added `test_expired_token_logs_warning_not_error` and `test_invalid_token_still_logs_error` | Regression tests for the log-level split |

## Before / after

**Backend** (`dependencies/__init__.py`, lines ~555-570):

Before:
```python
except Exception as e:
    logger.error(f"JWT verification failed: {e}")
    raise HTTPException(status_code=401, detail="Invalid token") from e
```

After:
```python
except HTTPException as e:
    if e.detail == "Token has expired":
        logger.warning("JWT expired (normal client refresh cycle)")
    else:
        logger.error(f"JWT verification failed: {e}")
    raise HTTPException(status_code=401, detail="Invalid token") from e
except Exception as e:
    logger.error(f"JWT verification failed: {e}")
    raise HTTPException(status_code=401, detail="Invalid token") from e
```

**Frontend** (`notificationQueries.ts`, queryFn):

Before:
```typescript
queryFn: async () => {
    const res = await api.get(`/notifications?limit=${limit}&offset=0`);
    return res.data;
},
```

After:
```typescript
queryFn: async () => {
    await ensureFreshToken();
    const res = await api.get(`/notifications?limit=${limit}&offset=0`);
    return res.data;
},
```

## Rollback plan

`git-revert-safe` — both changes are pure code, no schema or data involved.
Reverting restores the previous log level (expired tokens go back to Sentry as
errors) and removes the proactive refresh (client falls back to the reactive 401
interceptor, which still works).

## Verification performed

- Two new regression tests passed (`test_expired_token_logs_warning_not_error`,
  `test_invalid_token_still_logs_error`)
- Manual code review of all `get_current_user` callers — the function's return
  type and exception contract are unchanged
- Confirmed `ensureFreshToken` is already exported from `shared/api/client.ts`
  and used elsewhere (foreground handler, WebSocket connect)

## What was NOT verified

- Not tested against live Supabase or a real expired token flow end-to-end
- No visual regression tooling exists for rider-app/driver-app — the change is
  non-visual (network timing only), so reasoned about, not screenshotted
- The race condition on foreground resume was analyzed from code, not reproduced
  in a device test
