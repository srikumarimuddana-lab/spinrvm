# Change Impact & Risk Log — F01: reject anonymous Firebase identities

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-08 |
| Author | Claude Code (agent), for the PR #5138 remediation plan |
| Surface(s) | backend |
| Domain (Sentry tag) | auth |
| PR / commit link | branch `claude/pr-5138-implementation-27zn2l` |
| Related issue or gap ID | F01, the AI security assessment on PR #5138, remediation order 1 |

## 1. Issue / gap identified

A cryptographically valid, correctly-audienced Firebase ID token was treated as
proof of a verified phone/email customer. It is not: Firebase issues exactly such
a token for **anonymous** sign-in. `/auth/firebase` accepted an anonymous payload,
created a rider row with an empty phone, and issued Spinr access/refresh tokens
that the shared AI authentication dependency then accepted. Found by the
AI security assessment (PR #5138), which reproduced it with an offline probe.

## 2. Root cause

`verify_id_token` answers "did Firebase sign this, for this project, unexpired?"
— an *authentication* question. Spinr's customer-only guarantee depends on an
*eligibility* question ("is this a verified contact-owning customer?") that no
code asked. `firebase.sign_in_provider` was never read on any of the three
Firebase entry points, and `phone_number` being empty was tolerated rather than
rejected (the row was created with `"phone": ""`).

## 3. Fix / remediation

New `backend/utils/firebase_identity.py` holds one eligibility policy:

1. reject `firebase.sign_in_provider == "anonymous"`;
2. require the provider to be on `FIREBASE_ALLOWED_SIGN_IN_PROVIDERS`;
3. require a verified contact — a `phone_number`, or an `email` with
   `email_verified` true.

It is called from all **three** Firebase entry points, which each duplicate
verification rather than sharing one path. Rejection happens *before* the user
lookup and before any row is created or token minted, so an ineligible identity
leaves no residue.

## 4. Risk & impact on existing functionality

**Blast radius: cross-surface, and deliberately so — this is an auth path.**

Grep performed for every Firebase verification call site:
`grep -rn "verify_id_token" backend/` → three production entry points, all now
gated, plus test mocks:

| Consumer | Effect |
|---|---|
| `routes/auth.py::firebase_auth_login` (driver-app token exchange) | gated |
| `dependencies/__init__.py::get_current_user` (rider-app raw-token auth, every request) | gated |
| `routes/websocket.py::websocket_endpoint` (WS handshake) | gated |
| `routes/admin/*`, OTP/JWT login, refresh | untouched — no Firebase verification |

**The regression that matters:** a real Firebase ID token always carries the
`firebase.sign_in_provider` claim, so real riders/drivers keep authenticating.
If that assumption were wrong for some client, *every* Firebase-authenticated
user would be 401'd at once. That is precisely why the kill switch in §8 exists
and why it is a config value rather than a code path.

The JWT fallback branch in `get_current_user` and the WS JWT branch are
untouched — an already-issued Spinr JWT is unaffected by this change. That is a
deliberate limit, not an oversight: closing the *existing* anonymous-provisioned
JWTs is F02's revocation work, not this change's.

No ride-state, money, wallet, insurance-period, or background-loop interaction —
this change writes nothing and only rejects earlier.

## 5. User-experience effect

- **Rider / driver:** no visible change for any account that signed in with
  phone, password, Google or Apple — which is every supported onboarding path.
- **Mid-session visibility:** none for eligible users. A user *provisioned
  anonymously before this change* (if Firebase anonymous auth was ever enabled
  upstream) loses access at their next request or socket reconnect, with a 401
  `ERR_IDENTITY_INELIGIBLE`. That is the intended effect of the finding.
- No copy or notification change. The rejection message is a generic auth
  failure; it deliberately does not tell a caller which of the three checks it
  failed.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/firebase_identity.py` | **New.** The eligibility policy + `FirebaseIdentityRejected`. | One policy, three call sites — a copy in each would drift. No FastAPI import so the WS handshake and offline probes can use it. |
| `backend/core/config.py` | Added `FIREBASE_ALLOWED_SIGN_IN_PROVIDERS`, `FIREBASE_IDENTITY_POLICY_ENFORCED`. | Provider allowlist widening + the emergency rollback lever, both without a redeploy. |
| `backend/routes/auth.py` | Gate in `firebase_auth_login` after the audience check, before provisioning. | Entry point 1. Ordered before `create_user` so a rejected identity leaves no row. |
| `backend/dependencies/__init__.py` | Gate in `get_current_user`'s Firebase branch, before the user lookup. | Entry point 2 — the dependency the AI routes share. Also saves a Supabase read on rejection. |
| `backend/routes/websocket.py` | Gate in the handshake after the audience check. | Entry point 3. Duplicates verification, so it needed its own call. |
| `backend/tests/test_firebase_identity_policy.py` | **New.** 20 tests across the policy and all three entry points. | Regression cover for the finding. |
| `backend/tests/test_dependencies_auth_gaps.py` | Added `_eligible()` helper; stamped 6 payload fixtures. | Fixtures predate the gate; their subject is a later check. |
| `backend/tests/test_auth_remaining_endpoints.py`, `test_p1_auth_hardening.py`, `test_websocket_auth.py`, `test_websocket_auth_ack.py` | Stamped 12 Firebase payload fixtures with the provider claim. | Same reason. |

## 7. Before / after

```python
# Before — routes/auth.py, after the audience check
uid: str = payload.get("uid") or payload.get("user_id") or ""
phone: str = payload.get("phone_number") or ""
# ... anonymous payload continues here: creates {"phone": ""} and mints tokens
```

```python
# After
try:
    enforce_customer_eligibility(payload, surface="auth_exchange")
except FirebaseIdentityRejected as e:
    raise SpinrException(
        message=e.message,
        error_code=ErrorCode.AUTH_INVALID_CREDENTIALS,
        status_code=401,
        message_key=ErrorKeys.AUTH_INVALID_CREDENTIALS,
    ) from e

uid: str = payload.get("uid") or payload.get("user_id") or ""
phone: str = payload.get("phone_number") or ""
```

## 8. Rollback plan

Set **`FIREBASE_IDENTITY_POLICY_ENFORCED=false`** and restart. No redeploy, no
code change; the gate returns immediately and logs at `error` on every call so
the disabled state is loud rather than quiet. Restores exactly the pre-F01
behaviour on all three entry points at once.

Narrower lever, if the failure is one legitimate provider being excluded rather
than the gate as a whole: add it to `FIREBASE_ALLOWED_SIGN_IN_PROVIDERS`
(comma-separated). `anonymous` is stripped from that list unconditionally, so
this lever cannot re-open the finding.

No data-level remediation is needed: this change writes nothing. Rows previously
provisioned by an anonymous identity are *not* deleted by this change — see
§10.

## 9. Verification performed

- [x] **Blast-radius grep performed** — `verify_id_token` across `backend/`
      (3 production call sites, all gated; JWT/OTP paths confirmed untouched).
- [x] **Offline probe** — 13 synthetic cases against the real policy module with
      a stubbed settings object, mirroring the assessment's own probe method:
      anonymous with and without a phone claim, absent `firebase` claim, custom
      provider, no contact, unverified email, four accept cases, allowlist
      containing `anonymous`, empty allowlist, kill switch. **All 13 pass.**
      Script: `scratchpad/probe_f01.py` (not committed — a throwaway harness;
      the same cases are committed as pytest in
      `tests/test_firebase_identity_policy.py`).
- [x] `ruff check` and `ruff format --check` clean on all changed files. (Two
      pre-existing lint errors in `test_dependencies_auth_gaps.py` /
      `test_auth_remaining_endpoints.py` were confirmed pre-existing via
      `git stash` and left alone.)
- [x] Reviewed against `CLAUDE.md`: dual-import pattern followed; no PII in the
      new log lines (provider name and presence booleans only, never the phone
      or email); DB/auth errors surface rather than being swallowed.
- [x] **Feature-flagged** — `FIREBASE_IDENTITY_POLICY_ENFORCED`, default on.
- [ ] Manual repro in staging — **not done**, see §10.

## 10. What was NOT verified

- **The pytest suite was not run.** PyPI is unreachable from this environment
  (the gateway answers 403 to `pypi.org`/`files.pythonhosted.org`), so the
  backend's dependencies cannot be installed and `pytest` cannot execute. The
  new tests and the 18 stamped fixtures are **unrun** and must go green in CI
  before merge. The offline probe above is real evidence for the policy logic
  only — it does not exercise `firebase_auth_login`, `get_current_user`, or the
  WS handshake.
- **No staging or live Firebase check.** Whether anonymous sign-in is actually
  enabled in the production Firebase project is still unverified — the
  assessment flagged this as a production-configuration question and it remains
  open. This change makes the backend reject anonymous identities regardless,
  which is the point, but the upstream provider configuration should still be
  confirmed.
- **The claim "every real Firebase ID token carries `firebase.sign_in_provider`"
  is from Firebase's documented token shape, not from an observed production
  token.** It is the single assumption that could cause a broad outage; a
  staging login on each client (rider app, driver app, WS reconnect) is the
  cheap way to confirm it before this reaches production.
- **Existing anonymously-provisioned rows are not cleaned up.** If any exist,
  they keep their `users` row and any still-unexpired Spinr JWT keeps working
  until `exp` — the JWT branch is not gated by this change. Auditing for such
  rows (`users` with an empty `phone`) and revoking their sessions is follow-up
  work, and overlaps F02.
- No load/latency measurement. The gate is pure dict reads with no I/O, added
  before an existing Supabase round-trip, so the auth-refresh P95 (<200 ms)
  should be unaffected — reasoned, not measured.

---

## Review follow-up (2026-09-09)

**The default provider allowlist was wrong and §5 above overstated the safety.**
It listed `phone,password,google.com,apple.com`. Only phone sign-in is actually
implemented (`signInWithCredential` over a phone credential) — and `password` in
particular was a trap: an account whose email is unverified passes the provider
check, then fails the verified-contact check, producing a **silent, permanent
401 on every request and socket, after the account already exists**, with no
self-service recovery. §5's claim of "no visible change for any account that
signed in with phone, password, Google or Apple" was wrong for that case.

The default is now `phone` alone, and the empty-setting fallback matches. An
unlisted provider is rejected at sign-in *before* any account exists, which is
the far better failure mode. Pinned by
`test_default_allowlist_admits_only_what_the_apps_use`.
