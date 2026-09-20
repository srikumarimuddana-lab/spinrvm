# Change Impact & Risk Log — per-user rate-limit buckets require a verified JWT signature

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-20 |
| Author | Claude Code session (2026-09-20 full-repo review, Phase 0 item C6) |
| Surface(s) | backend |
| Domain (Sentry tag) | auth / dispatch |
| PR / commit link | branch `claude/loving-thompson-amjk8b` |
| Related issue or gap ID | 2026-09-20 review, 🚨 C6; follows up `docs/change-log/2026-08-07-rate-limit-user-keying.md` |

## 1. Issue / gap identified

`utils/rate_limiter.py::_extract_unverified_user_id` decoded the bearer JWT with `verify_signature=False` and keyed per-user rate-limit buckets on the *claimed* `user_id`. Its docstring argued this was safe because a forged token "can only land in a throwaway bucket for a request that then 401s". That is wrong in one direction: the bucket is shared. An unauthenticated client could mint `{"alg": "none", "user_id": "<victim>"}` and burn a named driver's quota — 20 requests against `ride_action_limit` (20/min) and the victim is 429'd on accept/arrive/start for the rest of the window; `cancel_ride_limit` (10/hour), `ride_read_limit`, `location_update_limit` and `get_ai_chat_key` were poisonable the same way.

## 2. Root cause

The 2026-08-07 CGNAT fix moved keying from IP to user id and reused the middleware's unverified log-correlation decode for speed; the shared-bucket consequence was not considered.

## 3. Fix / remediation

`_extract_verified_user_id` decodes with `settings.JWT_SECRET` and an explicit algorithm allow-list (never the token's own `alg` header — that is the alg-confusion attack). The list carries both signing paths' algorithms: `dependencies.JWT_ALGORITHM` (rider/driver, hardcoded `HS256`) and `settings.ALGORITHM` (admin, MFA-challenge and break-glass tokens — an overridable field that merely defaults to `HS256`), so an admin-token algorithm rotation cannot silently drop every admin back to IP keying. A token whose signature does not verify — wrong secret, `alg=none`, garbage — falls back to the IP bucket like an anonymous request. Expiry and audience are deliberately not checked for keying: an expired token is still the real user's (it 401s downstream regardless), so a 15-minute access-token rotation cannot hand a user a fresh quota, and the SOS route's `get_current_user_allow_expired` path keeps its per-user bucket.

**Alternative considered:** key on `sha256(raw bearer token)`. Rejected — every token refresh would reset the user's quota (an authenticated abuser could refresh to evade the limit), and it is no cheaper than one HMAC verify.

## 4. Risk & impact on existing functionality

- Every `get_user_or_ip_key` / `get_ai_chat_key` consumer (ride actions, cancel, ride reads, location updates, AI chat, admin SIN endpoints). For legitimately signed tokens the key is byte-identical to before (`user:<id>`), so no user's bucket or limit changes.
- **Regression risk:** any Spinr-minted token signed with a different secret or algorithm would silently fall back to IP keying (re-opening the CGNAT self-429 for that population). The security auditor traced every issuer — rider/driver access and reactivation tokens (`dependencies/__init__.py`), admin access/session/MFA-challenge/MFA-enroll/break-glass tokens (`routes/admin/auth.py`) — and all sign with the single `settings.JWT_SECRET` (unified there for exactly this reason). Its one warning, that admin tokens use `settings.ALGORITHM` rather than the hardcoded `HS256` this originally pinned, is fixed: both are in the allow-list. No corporate/company-specific signer exists (corporate users ride the rider/driver path); `utils/apns_client.py`'s ES256 token is outbound to Apple, not an inbound bearer.
- `core/middleware.py::_extract_user_id` still decodes unverified — it feeds `request.state.user_id` for log correlation only; an accepted residual (a forged token can mislabel a log line's user id, never a bucket). Follow-up candidate.
- Kill switch unchanged: `RATE_LIMIT_USER_KEYING=off` still reverts to pure IP keying.

## 5. User-experience effect

None for legitimate users. A forged-token attacker now only consumes their own IP bucket.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/rate_limiter.py` | `_extract_unverified_user_id` → `_extract_verified_user_id` (signature-verified decode, exp/aud skipped); docstrings | forgery-proof buckets |
| `backend/tests/test_rate_limit_user_keying.py` | tokens signed with `settings.JWT_SECRET`; new tests: wrong secret → IP, `alg=none` → IP, forged token never shares the victim's bucket, expired-but-genuine token keeps its bucket | regression pins |
| `backend/tests/test_coverage_boost.py` | `TestGetAiChatKey` signs with the real secret; the test that asserted a wrong-secret token *is* accepted is inverted | it documented the vulnerability |
| `backend/tests/test_admin_sin_rate_limiting.py` | token signed with the real secret; docstring | keep the per-user assertion meaningful |
| `docs/change-log/2026-09-20-rate-limit-key-verified-signature.md` | this file | |

## 7. Before / after

```python
# Before
payload = jwt.decode(auth[len("Bearer "):], options={"verify_signature": False})
```

```python
# After
payload = jwt.decode(
    auth[len("Bearer "):], settings.JWT_SECRET, algorithms=["HS256"],
    options={"verify_exp": False, "verify_aud": False},
)
```

Scenario: attacker sends 20 requests/min to `POST /rides/{id}/arrive` with `Authorization: Bearer <alg=none, user_id=driver_X>`. Before: bucket `user:driver_X` is exhausted; driver X's real accept is 429'd. After: the requests land in `ip:<attacker>`; driver X is unaffected.

## 8. Rollback plan

Code-only; `git revert` + deploy. `RATE_LIMIT_USER_KEYING=off` (`fly secrets set`) is the no-deploy lever if per-user keying itself misbehaves.

## 9. Verification performed

- `ruff check` / `ruff format` / `py_compile` clean on all four files.
- `spinr-security-auditor` run against the diff before commit (see its findings folded into §4).

## 10. What was NOT verified

- **pytest not run in this session** (sandbox cannot reach PyPI). CI is the gate: `test_rate_limit_user_keying.py`, `test_coverage_boost.py::TestGetAiChatKey`, `test_admin_sin_rate_limiting.py`.
- Not load-tested: one HMAC-SHA256 verify per rate-limited request (~µs) — no SLA path is expected to notice.
