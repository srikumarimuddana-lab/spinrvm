# Change Impact & Risk Log — sessions that logout can never tombstone, without locking users out

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-21 |
| Author | mkkreddy52@gmail.com (Claude Code assisted) |
| Surface(s) | backend, rider-app, driver-app, shared |
| Domain (Sentry tag) | auth |
| PR / commit link | PR #5662, branch `claude/auth-session-id-integrity` |
| Related issue or gap ID | Found by `/code-review` on PR #5654; reworked after `spinr-security-auditor` found the first attempt introduced a lockout hazard |

## 1. Issue / gap identified

Three composing defects on the session-establishment paths:

1. **`verify_otp`** logged a failed `users.current_session_id` write and carried on, minting a token
   whose session_id was never persisted.
2. **`reactivate_account`** does exactly the same thing.
3. **`refresh_access_token`** then fell back to the refresh row's **User-Agent** for the JWT's
   session_id.

Net effect: **logout silently failed to tombstone the session** and the access token stayed honoured
for its full TTL. Every user on the same client build also shared one "session id".

## 2. Root cause

`should_tombstone(payload_session_id, current_session_id)` (`utils/session_revocation.py:79`) returns
`payload_session_id == current_session_id`. A User-Agent never equals a uuid4, and a `None` never
matches either, so `revoke_session()` was never called on logout.

`refresh_tokens` has **no session-id column** (migration 25), so `row.get("user_agent")` was never a
session id — it was a client-supplied string that happened to be non-empty.

## 3. Fix / remediation

Four parts, which only work together:

1. **`verify_otp`** raises on the failed write — but with a **dedicated** `AUTH_SESSION_SETUP_FAILED`
   code, not a generic DB 503. See §4 for why that distinction is load-bearing.
2. **`reactivate_account`** raises too. A plain 503 is fine there (see §4).
3. **`refresh_access_token`** drops the `user_agent` fallback; a missing `current_session_id` now
   yields `session_id=None`, which `should_tombstone()` treats as "nothing to key on".
4. **`shared/api/client.ts`** excludes `/auth/verify-otp` from its blind 503 auto-retry, and both
   apps handle the new key by offering a **fresh code** instead of reporting a wrong one.

### Alternatives considered (gate 10)

- **Mint a fresh session_id in `refresh_access_token`** when `current_session_id` is null. Rejected:
  refresh is not a session-establishing operation, and two devices refreshing concurrently would
  fight over `current_session_id` and start kicking each other off — a worse live regression.
- **Reorder `verify_otp` to write the session before consuming the OTP.** Rejected for now: the
  user row may not exist yet at that point (the new-user branch creates it after verification), so
  it is a real restructure of the most safety-critical function in the file. The chosen fix removes
  the user-visible harm without that risk. Recorded here as the cleaner long-term shape.
- **Drop the raise entirely and keep only the `user_agent` fix.** Rejected: that leaves the original
  silent-degradation bug in place.

## 4. Risk & impact on existing functionality

**Blast radius: multi-surface** — backend + `shared/` + both mobile apps. Stated as such because the
backend half is *unsafe without* the client half.

### ⚠ The first version of this fix could have locked users out for 24 hours

`spinr-security-auditor` caught this; I verified every step against the source:

| Step | Evidence |
|---|---|
| The OTP is consumed **first** | `routes/auth.py:1032` — `delete_otp_record(...)`, before any session work |
| The 503 fires **after** | the `current_session_id` write, ~100 lines later |
| The client auto-retries any 503 **with the same body** | `shared/api/client.ts` gated on `url !== '/auth/refresh'` only; `retryFn = () => client.post(url, body, ...)` resends the same phone + same code |
| The retry can't match — the record is gone | `routes/auth.py:967` → `_record_otp_failure(phone)` → 400 `AUTH_OTP_INVALID` |
| Five of those in an hour | `core/config.py:225-227` — `OTP_MAX_FAILURES = 5`, window 3600s, lockout **86400s** |

So a correct code plus a transient DB hiccup produced *"That code didn't match"* **automatically**,
and a few confused re-taps could lock a rider or driver out of the app for a day — on the one
endpoint everyone must pass through to sign in. That is worse than the bug being fixed.

The codebase already anticipated this **class** of problem for a different trigger: `consent_required`
fires after the OTP is spent, and both apps handle it by prompting for a fresh code rather than
claiming the code was wrong (see the comment on that branch in `rider-app/app/otp.tsx`). This change
follows that established pattern exactly, rather than inventing one.

**`reactivate_account` is different and a plain 503 is correct there:** it is authenticated by a
single-purpose reactivation token, not a just-consumed OTP, so a retry costs the user nothing and
burns no OTP-failure attempt.

### Correction to an earlier revision of this entry

This log previously stated: *"line 1390 (`reactivate_account`) has no try/except, so an error already
propagates."* **That was false** — `routes/auth.py:1389-1393` is a try/except that swallowed and
continued. I wrote it from a two-line grep that didn't show the enclosing block, and my own AST scan
missed it because that handler logs "could not **set** session_id" while the scan filtered on
"update session_id". The guard now matches on **AST shape**, not log wording (§6).

### Other consumers

- **Every `session_id` consumer already handles `None`** — independently traced: `create_jwt_token`
  (omits the claim for falsy), `get_token_session_id` (returns `Optional[str]`), `should_tombstone`
  (`if not payload_session_id: return False`), `is_session_revoked` (fail-open), and their callers in
  `dependencies/__init__.py`, `routes/drivers/location.py`, `routes/websocket.py`, `ai/mcp_server.py`.
  Nothing uses the claim's *presence* to grant trust — it can only deny or no-op, never escalate.
- **No client reads the `session_id` claim.** Zero hits across `rider-app/store`, `driver-app/store`,
  `shared/api` (other `session_id` names are Stripe/recording identifiers).
- **No ride-state, money, wallet, dispatch, or insurance-period interaction.**

**Residual gap — and it is worse than "legacy users".** `/auth/refresh` only ever *reads*
`current_session_id`; no refresh path repairs it. So a user who lands in the null state stays
untombstoneable for the life of that refresh-token chain — up to the full 30-day refresh lifetime for
an app that never does a fresh login — not merely until their next request. Fix 1 and 2 stop new
users entering that state; they do not repair those already in it. A backfill is out of scope here.

## 5. User-experience effect

- **Riders and drivers:** on a DB failure during OTP verification they now see *"Your code was
  correct, but we couldn't finish signing you in. Tap Resend for a new code."* with the resend
  cooldown cleared, instead of either a silently-degraded session (before) or a false *"That code
  didn't match"* plus a lockout risk (the first version of this fix). Normal-path login is unchanged.
- **Reactivation:** a retryable 503 on the same class of failure.
- **Mid-session visibility:** none. Both paths run at login, not during a trip.
- **Corporate/internal admin:** no change.
- **Copy:** one new user-facing string, in both apps, mirroring the existing `consent_required` copy.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/auth.py` | `verify_otp` raises `AUTH_SESSION_SETUP_FAILED`; `reactivate_account` raises a 503; `refresh_access_token` drops the `user_agent` fallback | Stop minting untombstonable sessions without burning correct OTPs |
| `backend/utils/error_handling.py` | New `ErrorCode.AUTH_SESSION_SETUP_FAILED = 1009` | Distinguish "correct code, setup failed" from "wrong code" |
| `backend/utils/error_keys.py` | New `AUTH_SESSION_SETUP_FAILED` message key | Client-side routing of the above |
| `shared/api/client.ts` | `/auth/verify-otp` excluded from the blind 503 auto-retry | The request is **not** idempotent — the OTP is already spent |
| `rider-app/app/otp.tsx`, `driver-app/app/otp.tsx` | Handle the new key: clear the code, reset the resend cooldown, prompt for a fresh code | Don't tell a user their correct code was wrong |
| `backend/tests/test_auth_session_id_integrity.py` | Behavioural test + AST-**shape** guard (was substring-based) | Regression cover that cannot be defeated by rewording a log line |

## 7. Before / after

```python
# Before — verify_otp and reactivate_account both swallowed
except Exception as e:
    logger.error(f"Could not update session_id ...: {e}", exc_info=True)
# ...falls through and mints a token anyway

# Before — refresh_access_token
session_id = user.get("current_session_id") or row.get("user_agent") or ""
```

```python
# After — verify_otp (OTP already spent, so the code must not be reused)
raise SpinrException(
    message="We couldn't finish signing you in. Please request a new code.",
    error_code=ErrorCode.AUTH_SESSION_SETUP_FAILED,
    status_code=503,
    message_key=ErrorKeys.AUTH_SESSION_SETUP_FAILED,
) from e

# After — refresh_access_token
session_id = user.get("current_session_id") or ""
```

Scenario — a rider enters the **correct** code and the session write hiccups:

```
Originally:   session minted anyway; logout never tombstones it (silent bug)
First fix:    generic 503 -> client auto-retries same code -> "That code didn't match"
              -> OTP failure counted -> 5 in an hour -> 24-HOUR LOCKOUT
This version: AUTH_SESSION_SETUP_FAILED -> no auto-retry -> "Your code was correct,
              but we couldn't finish signing you in. Tap Resend." -> no failure counted
```

## 8. Rollback plan

`git revert` is sufficient. No schema change, no migration, no backfill, **no live data mutated** —
every edit changes only what is computed per-request, which exception is raised, and client-side
error routing.

**Deploy-order caveat (real, and the reason blast radius is `multi-surface`):** the backend half is
unsafe without the client half. If backend ships first, an app build without the new branch treats
`AUTH_SESSION_SETUP_FAILED` as a generic error — the user sees the fallback copy, though the
`client.ts` exclusion (shipped in the same app build) is what prevents the *auto-retry* lockout.
Until both apps carry this build, a 503 on that path is still user-visible on old clients. Prefer
shipping the app update first, or accept that old clients keep the pre-existing behaviour.

## 9. Verification performed

- [x] **Blast-radius**: every `current_session_id` write site; every `should_tombstone`/
      `is_session_revoked` caller; `refresh_tokens` schema (migrations 25/35); every `session_id`
      claim consumer; both apps' OTP screens; `client.ts` retry gating.
- [x] **`ruff check` / `ruff format --check` / `py_compile`** — pass on all 4 Python files.
- [x] **New AST guard executed standalone** (stdlib only): **3 handlers found, 0 offenders** — versus
      2 found by the old substring version, which is exactly the blind spot that produced §4's
      retracted claim.
- [x] **TypeScript syntax check** on the 3 changed TS/TSX files: no `TS1xxx` syntax errors, and every
      reported diagnostic is a missing-`@types/node` / React-Native-global artifact of running
      without `node_modules` or the project tsconfig. **None fall on changed lines.**
- [x] **`spinr-security-auditor`** run against the previous revision (gate 10) — its blockers are what
      produced this one.
- [ ] **pytest / jest / a real app build** — see §10.

## 10. What was NOT verified

- **No test suite ran.** PyPI and npm are both blocked by this environment's network policy, so
  neither `pytest` nor the RN test tooling could be installed. The AST guard was executed standalone;
  the behavioural test and every TS change are verified by **reading and static checking only**.
  CI is the first real execution.
- **No app build, no simulator, no screenshot.** The new toast copy, the resend-cooldown reset, and
  the branch ordering in both `otp.tsx` files were **reasoned about, not run**. Per CLAUDE.md,
  rider-app and driver-app have **no visual-regression tooling at all**, so this disclosure is
  mandatory rather than optional.
- **The lockout scenario was not reproduced**, only traced through source. It needs a forced DB
  failure on the `current_session_id` write to exercise end-to-end.
- **The residual null-`current_session_id` population is not repaired** (§4) — no backfill attempted.
- **`reactivate_account`'s new 503 was not exercised** against a real failing write.

## 11. Sign-off

- [x] Rollback plan is concrete and testable, with its deploy-order caveat stated
- [x] Blast radius is stated, not assumed — and corrected once it was found wrong
- [x] No silent behavior change to an already-shipped flow without the UX field filled in
