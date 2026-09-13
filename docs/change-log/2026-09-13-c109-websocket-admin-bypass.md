# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-13 |
| Author | Claude Code (session `session_01Aq7xVA8zptrqFwExvqVrwN`) |
| Surface(s) | backend |
| Domain (Sentry tag) | auth |
| PR / commit link | branch `mvapps/dreamy-faraday-2wz914-c107-rls-reachability` |
| Related issue or gap ID | ACTION_ITEMS.md C109 (new, closed same session) |

## 1. Issue / gap identified

`backend/routes/websocket.py`'s admin-connection gate trusted the raw
`users.role` database column to decide whether a WebSocket connection could
register as an admin socket — the same class of bug already found and
fixed for the HTTP admin dependency (`get_admin_user`) and the AI-tool MCP
gate, but never applied to this call site.

## 2. Root cause

`_verify_admin_payload()` (`backend/dependencies/__init__.py`) is the only
function that runs the full admin verification pipeline (aud=spinr:admin +
JTI denylist + admin_staff active + token_version + idle timeout) and, on
success, stamps a private `_admin_verified: True` marker on the returned
dict. It correctly returns `None` — not an error, just "not an admin
token" — for an ordinary rider/driver JWT, since that's a legitimate,
expected case (any mobile token reaching this function during normal
`get_current_user` flow).

The WebSocket handler's fallback for that `None` case
(`backend/routes/websocket.py`, around the JWT auth branch) does a bare
`db_supabase.get_user_by_id(payload["user_id"])` lookup — necessary so a
normal rider/driver token still resolves to a user for its own client
type, but that plain dict carries whatever `users.role` says with no
verification behind it. The admin-gate check further down the function
then did `user.get("role") not in _ADMIN_ROLES` against whatever dict
`user` ended up being — including this unverified fallback dict. Any
account whose `users.role` database value was ever set to an admin-shaped
string (a legacy row, an ops data-fix, a migration bug) could open an
admin WebSocket connection using a completely ordinary 15-minute mobile
token.

Found while an adversarial security review (`spinr-security-auditor`,
CLAUDE.md gate #10) was checking a specific question raised while closing
ACTION_ITEMS.md C107: "does any other code path treat \[the one legacy
`role='admin'` row found in production\] as a live admin?" — the review's
answer was yes, this one.

## 3. Fix / remediation

Changed the WebSocket admin gate from:
```python
if user.get("role") not in _ADMIN_ROLES:
```
to:
```python
if not user.get("_admin_verified"):
```
— the exact pattern `get_admin_user` (`backend/dependencies/__init__.py:777`)
already uses, and the exact reasoning `mcp_server.py`'s gate documents.
Removed the now-unused `_ADMIN_ROLES` module constant (this was its only
reference in the file).

**Alternative considered:** patch the fallback lookup itself to strip or
never trust `role` from `get_user_by_id`'s result for admin purposes,
rather than changing the gate check. **Rejected** because the gate check is
where every other instance of this exact bug class was fixed
(`get_admin_user`, `mcp_server.py`) — changing the gate keeps this file
consistent with the established, already-tested pattern, and doesn't risk
affecting the same `user` dict's use elsewhere in the function (it's also
used for the non-admin `driver`/`rider` client types, where `role` from a
plain lookup is exactly what's needed and correct).

## 4. Risk & impact on existing functionality

- **Other readers/writers of `_ADMIN_ROLES` in this file:** none — grepped
  `backend/routes/websocket.py`, the only reference was the line being
  fixed. Safe to remove.
- **Other consumers of the `user` dict this function builds:** the admin
  branch's `user` (now gated on `_admin_verified`) is used afterward only
  for `connection_key = f"{client_type}_{user['id']}"` and subsequent
  admin-broadcast logic — `id` is present on both the verified-admin dict
  (`_verify_admin_payload`'s return) and the plain-lookup fallback dict, so
  no other code path depended on the old, unverified `role` value being
  present in `user` past the gate.
- **Legitimate admin logins unaffected:** confirmed by reading
  `_verify_admin_payload()`'s full control flow — every success path
  (regular staff via `admin_staff` lookup, break-glass emergency token, and
  the `admin-001` env-var identity) reaches the function's single shared
  `return {..., "_admin_verified": True, ...}` statement. No success path
  skips setting the marker, so no real admin connection is affected by this
  change.
- **Blast radius grep performed for the same bug class elsewhere:**
  searched the backend for every ADMIT-direction check against
  `user.get("role")`/`current_user.get("role")` for platform-admin
  purposes (excluding the unrelated corporate company-role checks in
  `corporate_company_bookings.py`/`dependencies/company_guard.py`, a
  different authorization dimension entirely). Every other hit sits
  downstream of a FastAPI dependency (`get_admin_user` or equivalent) that
  already enforces `_admin_verified` before the route body runs — those
  further `role == 'super_admin'`-style checks are a safe, fine-grained
  distinction between already-verified admin tiers, not a repeat of this
  bug. The two DENY-direction checks (`ai_console.py`, `mcp_server.py`)
  are the safe direction for an over-broad `role` check by design. This
  WebSocket gate was the only unguarded ADMIT-direction instance found.
- **Today's actual exposure:** contingent on the one legacy `role='admin'`
  row (found during the C107 investigation) being able to complete an
  ordinary OTP/Firebase login — the app's real login system, distinct from
  the Supabase-Auth question C107/C108 were about. That row has separately
  been reset to `role='rider'` in production (see
  `docs/change-log/2026-09-13-c107-rls-admin-reachability.md`), and
  `chk_users_role_not_admin` is now fully validated, so no `users` row can
  hold an admin-shaped role value going forward — but this code fix closes
  the actual vulnerable code path regardless of whether any row currently
  exploits it, which is the correct fix (the row cleanup alone would not
  have protected against a *future* stray admin-role value).

## 5. User-experience effect

None for any legitimate user. A real admin's WebSocket connection behaves
identically before and after (verified via the full existing
`test_admin_auth_success_is_sent_immediately` test, unchanged and passing).
The only behavior change is that a connection attempting to register as
`client_type=admin` using a non-admin-verified token now correctly receives
`admin_access_required` and is closed — which is the intended, secure
behavior, not a regression to any working flow (no legitimate client ever
relied on the old, insecure path succeeding).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/websocket.py` | Admin WebSocket gate now checks `_admin_verified` instead of the raw `role` column; removed the now-unused `_ADMIN_ROLES` constant | Closes the privilege-escalation path; matches the already-established fix pattern for the same bug class elsewhere in the codebase |
| `backend/tests/test_websocket_auth_ack.py` | Added `test_ordinary_token_cannot_ride_a_stray_admin_role_column_into_admin_socket` | Regression test pinning the exploit is closed; confirmed it fails without the fix and passes with it |

## 7. Before / after

```python
# Before
elif client_type == "admin":
    if user.get("role") not in _ADMIN_ROLES:
        await websocket.send_json({"type": "error", "message": "admin_access_required"})
        await websocket.close()
        return
```

```python
# After
elif client_type == "admin":
    # P0: gate on the private _admin_verified marker that ONLY
    # _verify_admin_payload sets after the full admin pipeline ...
    if not user.get("_admin_verified"):
        await websocket.send_json({"type": "error", "message": "admin_access_required"})
        await websocket.close()
        return
```

## 8. Rollback plan

`git-revert-safe` — a plain revert of the one-line gate change and the
removed constant restores prior behavior exactly. No data migration, no
config change, no coordinated deploy needed. (Reverting is not
recommended, obviously, since it restores a real privilege-escalation
path — noted here only because the template requires a rollback plan for
every entry.)

## 9. Verification performed

- [x] Automated test added and confirmed to catch the exact exploit: ran
      the new test against the pre-fix code (via `git stash` on just
      `routes/websocket.py`) and confirmed it fails with the exploit
      succeeding (`auth_success` sent instead of `error`); restored the
      fix and confirmed it passes.
- [x] Full regression run: `pytest tests/test_websocket_auth_ack.py
      tests/test_admin_privilege_escalation.py tests/test_websocket_auth.py
      tests/test_websocket_token_revocation.py tests/test_logout_all.py
      tests/test_refresh_token_reuse_detection.py` — 92 passed, 0 failed.
- [x] `ruff check` / `ruff format --check` on both touched files — clean.
- [x] Traced `_verify_admin_payload`'s full control flow by reading the
      function source directly (not assumed from its docstring) to confirm
      every success path sets `_admin_verified`.
- [x] Repo-wide grep for the same ADMIT-direction bug pattern elsewhere —
      see §4's blast-radius paragraph. No second instance found.
- [ ] Not run: a real end-to-end connection against a deployed environment
      (mobile app + live backend) — this fix was verified at the unit/
      integration-test level only, consistent with how the original
      `get_admin_user`/`mcp_server.py` fixes for the same bug class were
      verified per `test_admin_privilege_escalation.py`'s own existing
      scope.

## 10. Sign-off

- [x] Rollback plan is concrete and testable — plain `git revert`.
- [x] Blast radius stated: one file, one now-provably-safe gate change,
      confirmed no other consumer of the changed check, confirmed no other
      instance of the same bug class in the codebase.
- [x] No silent behavior change to a working flow — §5 states plainly the
      only behavior change is the vulnerable path now correctly failing;
      every legitimate flow is unchanged and test-confirmed.

## What was NOT verified

- Whether this exact code path has ever been exploited in production
  (i.e., whether the one legacy `role='admin'` account ever actually
  opened an admin WebSocket connection) — no application-level audit log
  of past WebSocket admin connections was available to check in this
  session. The fix closes the path going forward regardless.
- Real end-to-end verification against a live deployed environment (see
  §9) — unit/integration-test coverage only, matching this bug class's
  established verification bar elsewhere in the codebase.
