# Change Impact & Risk Log — a failed session_id write minted an untombstonable session

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-21 |
| Author | mkkreddy52@gmail.com (Claude Code assisted) |
| Surface(s) | backend |
| Domain (Sentry tag) | auth |
| PR / commit link | branch `claude/auth-session-id-integrity` |
| Related issue or gap ID | Found by `/code-review` (max effort) reviewing PR #5654; raised there, fixed here |

## 1. Issue / gap identified

Two bugs that compose: `verify_otp` logged a failed `users.current_session_id` write and carried on,
minting a token whose session_id was never persisted — and `refresh_access_token` then fell back to
the refresh row's **User-Agent string** for the JWT's session_id. Net effect: **logout silently
failed to tombstone the session**, and the access token stayed honoured for its full TTL.

## 2. Root cause

Two independent defects, one feeding the other:

1. **`routes/auth.py` (`verify_otp`)** — `except Exception as e: logger.error(...)` then fell
   through to `redis_set` + `create_jwt_token(session_id=session_id)`. The user held a token whose
   session_id was not in `users.current_session_id`.
2. **`routes/auth.py` (`refresh_access_token`)** —
   `session_id = user.get("current_session_id") or row.get("user_agent") or ""`. `refresh_tokens`
   has **no session-id column** (migration 25: `user_id`/`token_hash`/`audience`/`user_agent`/`ip`/
   `issued_at`/`expires_at`/`revoked_at`/`replaced_by`), so that fallback is a client-supplied UA.

`should_tombstone(payload_session_id, current_session_id)` (`utils/session_revocation.py:79`)
returns `payload_session_id == current_session_id`. A UA never equals a uuid4, so `revoke_session()`
was never called on logout. Two further consequences: every user on the same client build shared one
"session id", and the value was attacker-influencable (a client picks its own `User-Agent`).

## 3. Fix / remediation

1. `verify_otp` now raises `SpinrException(status_code=503, ErrorCode.DATABASE_ERROR)` — matching
   `firebase_auth_login`, which already did exactly this on the identical failure.
2. The `row.get("user_agent")` fallback is removed. A missing `current_session_id` now yields
   `session_id=None`, which `should_tombstone()` treats as "nothing to key on".

### Alternative considered (gate 10)

Mint and persist a fresh session_id inside `refresh_access_token` when `current_session_id` is null.
**Rejected:** refresh is not a session-establishing operation, and two devices refreshing
concurrently would fight over `current_session_id` — single-device enforcement would start kicking
real users off mid-session. That is a worse live-surface regression than the gap it closes. Fix 1
shrinks the null population to legacy rows anyway.

## 4. Risk & impact on existing functionality

**Blast radius: isolated — one file, two call sites, no schema or API change.**

Greps performed: every `current_session_id` write in `routes/auth.py` (7 sites); every
`should_tombstone` caller; `revoke_session`; the `refresh_tokens` schema across migrations 25/35.

- **The other `current_session_id` writes are unaffected.** Lines 648/673/1236/1518 write it inside a
  larger create/update payload (a failure there already fails the whole call); line 1390
  (`reactivate_account`) has no try/except, so an error already propagates. Only **two** paths had a
  dedicated try/except around this write — `verify_otp` (swallowed, fixed here) and
  `firebase_auth_login` (already correct, untouched). *(The review that found this said three paths
  raise; the AST scan says two. Two is correct.)*
- **Behaviour change #1 — `verify_otp` can now return 503 where it previously returned 200.** Only on
  a DB write failure that previously produced a half-valid session. The client retries; the rider
  sees a retryable error instead of a silently-degraded session.
- **Behaviour change #2 — tokens minted by `/auth/refresh` for a user with no `current_session_id`
  now omit `session_id`** instead of carrying a UA. `create_jwt_token` already had
  `session_id=session_id if session_id else None`, so the omission path is pre-existing and
  exercised; only which values reach it changes.
- **No change for the normal case.** A user with a persisted `current_session_id` — the overwhelming
  majority — is completely unaffected: same session_id, same token, same logout behaviour.
- No ride-state, money, wallet, dispatch, or insurance-period interaction.

**Residual gap, stated rather than implied:** a legacy user with a null `current_session_id` still
cannot have their session tombstoned on logout — `should_tombstone(None, ...)` is False. This change
makes that **honest** (no session_id in the token) instead of masking it behind a fake value that
also collided across users. Closing it properly needs a backfill or a session_id mint-on-refresh,
both out of scope here.

## 5. User-experience effect

- **Rider/driver:** effectively none in the normal case. On a DB failure during OTP verification they
  now get a retryable 503 ("Could not update session, please try again") instead of a session that
  looks fine but cannot be fully signed out. That is a **better** failure, and it is the only
  user-visible difference.
- **Mid-session visibility:** none. Nothing changes for a rider mid-ride or a driver online — both
  paths run at login/refresh, not during a trip.
- **Corporate admin / internal admin:** no change.
- **Copy:** reuses the existing `ErrorKeys.SYSTEM_DATABASE` message; no new string.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/auth.py` | `verify_otp`'s session_id-write handler raises 503 instead of continuing; `refresh_access_token` drops the `row.get("user_agent")` session_id fallback | Stop minting sessions that logout cannot tombstone |
| `backend/tests/test_auth_session_id_integrity.py` | New. A behavioural test (null `current_session_id` → `session_id=None`, never the UA) and an AST guard (no session_id-write handler may log-and-continue) | Regression cover for both halves |

## 7. Before / after

```python
# Before — backend/routes/auth.py (verify_otp)
except Exception as e:
    logger.error(f"Could not update session_id for existing user: {e}", exc_info=True)
# ...falls through and mints a token anyway

# Before — backend/routes/auth.py (refresh_access_token)
session_id = user.get("current_session_id") or row.get("user_agent") or ""
```

```python
# After (verify_otp)
except Exception as e:
    logger.error(f"Could not update session_id for existing user: {e}", exc_info=True)
    raise SpinrException(
        message="Could not update session, please try again",
        error_code=ErrorCode.DATABASE_ERROR, status_code=503,
        message_key=ErrorKeys.SYSTEM_DATABASE,
    ) from e

# After (refresh_access_token)
session_id = user.get("current_session_id") or ""
```

Concrete scenario — a rider on `okhttp/4.12.0` whose session_id write failed at OTP:

```
Before:  JWT session_id = "SpinrRider/9.9.9 (okhttp/4.12.0)"
         logout -> should_tombstone(UA, None) -> False -> revoke_session() never called
         -> access token honoured for its full 15 minutes after sign-out
         -> and every user on that build carries the same "session id"

After:   the OTP write failure returns 503, so the session is never minted.
         For a legacy row that is already null: JWT session_id = None
         -> still no tombstone, but no fake value and no cross-user collision
```

## 8. Rollback plan

`git revert` is sufficient and complete. No schema change, no migration, no backfill, and **no live
data is mutated** — both edits change only what is computed per-request and whether an exception is
raised. Reverting restores the prior behaviour immediately on the next deploy; tokens minted while
this was live remain valid and are strictly better-formed than what they replaced.

No feature flag: flagging would mean carrying both the swallow-and-continue path and the raise path
through the auth hot path, which keeps the defect alive behind a toggle.

## 9. Verification performed

- [x] **Blast-radius grep** — all 7 `current_session_id` writes, `should_tombstone` callers,
      `revoke_session`, and the `refresh_tokens` schema across migrations 25/35. Enumerated in §4.
- [x] **`ruff check` / `ruff format --check`** — pass on both files.
- [x] **`python -m py_compile`** — both files parse.
- [x] **AST guard executed standalone** (stdlib only, no pytest needed): 2 handlers found, 0
      offenders — i.e. the new test's own assertion passes against the fixed file.
- [x] **Reviewed against CLAUDE.md** — "never log a DB/auth error and continue; return a clean
      HTTPException (usually 503 for DB)"; dual-import pattern untouched; no PII added to logs.
- [ ] **`spinr-security-auditor`** — see §10.
- [ ] **Behavioural test executed** — see §10.

## 10. What was NOT verified

- **`pytest` never ran.** This environment's network policy blocks PyPI (403 at the agent proxy), so
  the suite could not be installed. The AST guard was executed standalone, but
  `test_refresh_never_uses_user_agent_as_session_id` has **not** been run — its patch targets and the
  `create_jwt_token` kwarg assertion are verified by reading, not by execution. CI is its first real
  run; watch that test specifically.
- **No staging deploy**, and the 503 path was not exercised against a real failing DB write.
- **The residual legacy-null gap is not closed** (see §4) — no backfill of `current_session_id` was
  attempted.
- **Merge-order note:** this branch is cut from `main`, so it does not contain PR #5654's change to
  the same function. The two touch different lines and should merge cleanly in either order, but the
  new test file was written to work under **both** (it supplies `cf-connecting-ip` rather than
  patching whichever client-IP helper is present).

## 11. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed
- [x] No silent behavior change to an already-shipped flow without the UX field filled in
