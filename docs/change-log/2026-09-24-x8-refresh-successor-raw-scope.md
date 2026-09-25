# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-24 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | auth |
| PR / commit link | (opened alongside this file) |
| Related issue or gap ID | #5750 (finding 1) |

## 1. Issue / gap identified

`POST /auth/refresh`'s X8 "successor commitment" feature (`refresh_successor_commitment_enabled`,
default off) let a client-supplied `proposed_refresh_token` become the raw secret material for the
*next* refresh token on the normal rotation path — not just the intended "recover a lost rotation
response" path.

## 2. Root cause

`classify_committed_replay()` returns `("no_match", None)` whenever the presented parent token
hasn't been committed-replayed before — i.e. on every normal, first-time refresh. `refresh_access_token()`
only branched on `verdict == "recover"` (early return) and `verdict == "dead"` (raise); on `no_match` it
fell through to the standard lookup/rotate flow with the local `proposed` variable still holding the
client's value. That fallthrough then reached `issue_refresh_token(..., **({"raw": proposed} if proposed
else {}))`, so `issue_refresh_token`'s `raw = raw or _generate_raw_token()` skipped the server-random
generator and hashed the client-chosen plaintext instead.

Concrete failure scenario (requires the flag on, which it is not in any environment today): an
attacker with a one-shot XSS/script-injection opportunity in a rider/driver webview — able to shape
an outgoing `fetch('/auth/refresh', {credentials:'include'})` body but not read the HttpOnly
`refresh_token` cookie — sets `proposed_refresh_token` to 64 bytes of their choosing (only a shape
regex, no entropy check, gates acceptance). The browser auto-attaches the victim's real cookie; the
server authenticates via that cookie, then mints the next refresh token using the attacker's chosen
plaintext as its raw secret. The attacker now holds a valid ~30-day refresh token without ever
intercepting a response.

## 3. Fix / remediation

Clear the local `proposed` variable when `classify_committed_replay` returns `no_match`, before
falling through to the normal rotation call. The `recover` branch (the only case where re-serving a
server-committed successor's raw value is correct) already returns early and is unaffected. `dead`
already raises and is unaffected. Existing test `test_no_match_rotates_with_proposed_raw` — which
asserted the vulnerable behavior was correct — renamed and corrected to
`test_no_match_rotates_without_the_client_proposed_raw`, now asserting `raw` is absent from the
`issue_refresh_token` call on that path.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated** — one function, `refresh_access_token` in `backend/routes/auth.py`.
  Grepped for other callers of `issue_refresh_token` with a `raw` kwarg: none outside this function
  and `classify_committed_replay`'s own recover-path re-serve (unaffected).
- **What else reads/writes the same state**: the `refresh_tokens` table (rotation, hash storage) —
  unaffected; this change only prevents one specific kwarg from being populated on one branch, it
  doesn't change the rotation/revocation logic itself.
- Admin refresh (`/admin/auth/refresh`) has no X8 twin by design (confirmed by
  `test_admin_twin_is_untouched`, unaffected) — this fix doesn't touch it.
- **Flag state**: `refresh_successor_commitment_enabled` defaults off and is off in every environment
  today, so this fix changes zero live behavior right now — it closes a bug in code that was dormant
  but merge-ready and DB-toggleable without redeploy.

## 5. User-experience effect

None — the flag is off everywhere. If/when the flag is ever turned on, the only behavior difference
is that a normal (non-recovery) refresh now always gets a server-random raw token, exactly as the
module's own "refresh tokens are OPAQUE, unguessable random bytes" invariant already promised.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/auth.py` | Clear `proposed` on the `no_match` verdict before falling through to normal rotation | Stop a client-supplied value from ever reaching `issue_refresh_token`'s `raw` kwarg outside the `recover` branch |
| `backend/tests/test_refresh_successor_route.py` | Renamed/corrected `test_no_match_rotates_with_proposed_raw` → `test_no_match_rotates_without_the_client_proposed_raw`; assertion now checks `raw` is absent | The old test asserted the vulnerable behavior as intended; it needed to change with the fix |

## 7. Before / after

```python
# Before
if proposed:
    verdict, successor = await classify_committed_replay(refresh_token_from_cookie, proposed)
    if verdict == "recover" and successor:
        ...
        return _build_refresh_response(response, user, proposed, refresh_expires_at)
    if verdict == "dead":
        raise TokenExpiredException(...)
# `proposed` still truthy here on a "no_match" verdict
...
new_raw, _, refresh_expires_at = await issue_refresh_token(
    ...,
    **({"raw": proposed} if proposed else {}),  # leaks client value on no_match
)
```

```python
# After
if proposed:
    verdict, successor = await classify_committed_replay(refresh_token_from_cookie, proposed)
    if verdict == "recover" and successor:
        ...
        return _build_refresh_response(response, user, proposed, refresh_expires_at)
    if verdict == "dead":
        raise TokenExpiredException(...)
    proposed = None  # no_match: never let the client value reach issue_refresh_token
...
new_raw, _, refresh_expires_at = await issue_refresh_token(
    ...,
    **({"raw": proposed} if proposed else {}),  # always {} on the no_match/normal path now
)
```

## 8. Rollback plan

`git revert` — no migration, no data backfill. The flag is off everywhere, so there is no live-data
state to unwind either way.

## 9. Verification performed

- [x] Automated tests: `pytest tests/test_auth.py tests/test_auth_driver_session_cleanup.py tests/test_auth_logout_availability_v2.py tests/test_auth_remaining_endpoints.py tests/test_auth_repo.py tests/test_auth_send_otp.py tests/test_auth_session_id_integrity.py tests/test_authorize_incremental_shape.py tests/test_refresh_generation_binding.py tests/test_refresh_successor_commitment.py tests/test_refresh_successor_route.py tests/test_refresh_token_reuse_detection.py tests/test_refresh_tokens_lifecycle.py` — 242/242 pass
- [x] Blast-radius grep: every caller of `issue_refresh_token`, every use of the local `proposed` variable in the function
- [x] Traced the exploit path directly against source (see `#5750`'s original finding) before writing the fix
- [ ] Manual repro in staging — not available from this session; flag is off in every environment so there's nothing to repro against live traffic anyway

## 10. Sign-off

- [x] Rollback plan is concrete (`git revert`, no data cleanup, flag off everywhere)
- [x] Blast radius stated: isolated to one function, one branch
- [x] No silent behavior change to a shipped flow — flag is off in every environment, so this fixes latent code, not live behavior

## What was NOT verified

Not tested against a real Postgres/Supabase `refresh_tokens` table — only `mock_supabase_client`-backed
unit tests, this repo's standard tier for auth-route changes. The flag being off everywhere means there
is no way to exercise this against live traffic from this session regardless.
