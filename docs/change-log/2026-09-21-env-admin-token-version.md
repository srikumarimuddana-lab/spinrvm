# Change Impact & Risk Log — the env super admin (`admin-001`) becomes revocable

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-21 |
| Author | Claude Code session (2026-09-20 review, Phase 1 item C8) |
| Surface(s) | backend, admin-dashboard (behaviour only, no client change) |
| Domain (Sentry tag) | auth / admin |
| PR / commit link | branch `claude/loving-thompson-amjk8b` |
| Related issue or gap ID | 2026-09-20 review, 🚨 C8 |

## 1. Issue / gap identified

`admin-001` — the super admin defined by `ADMIN_EMAIL`/`ADMIN_PASSWORD` rather than by an
`admin_staff` row — bypassed every authoritative revocation control the admin pipeline has.
`dependencies/__init__.py`'s `_verify_admin_payload` gated the staff-active check, the
`token_version` check and the 30-minute idle timeout behind a bare `elif user_id != "admin-001"`,
so none of them ran for the single highest-privilege account in the system.

What remained was the per-JTI Redis denylist, which **fails open** on a Redis error by design
(documented and deliberately pinned by `tests/test_admin_revocation_failopen.py`), and
`/admin/auth/logout-all`, which returned **400** for this account with the message "Rotate
ADMIN_PASSWORD in the environment to kill all super-admin sessions."

Net: a leaked super-admin token could not be revoked by any in-app control — only by an env-var
change and a redeploy — and during a Redis outage could not be revoked at all.

## 2. Root cause

The bypass is structural, not an oversight in a check: all three controls read from an
`admin_staff` row, and this account has none. Rather than give it an equivalent store, the
original code branched around the whole block. `/logout-all`'s 400 then documented the gap as if
it were a design ("uses env-var creds and has no persisted token_version") instead of closing it.

## 3. Fix / remediation

`admin-001` gets the one control it was missing, with staff semantics: a monotonic
`token_version`, stored on the `settings` singleton row (migration 434) because there is no
`admin_staff` row to hold it.

- **Mint** (`/admin/auth/login`, and `/admin/auth/refresh`) stamps the current stored value into
  the token instead of a hardcoded `0`.
- **Verify** (`_verify_admin_payload`) compares the claim against the stored value and returns
  401 `ERR_SESSION_REVOKED` on a stale token, reusing the existing `_token_version_mismatch`
  helper so the comparison is literally the same code staff go through.
- **Revoke** (`/admin/auth/logout-all`) bumps the counter instead of returning 400, and falls
  through to the *same* WS-kick and response tail as the staff branch.

All three paths read one definition in `backend/utils/env_admin_tokens.py`.

**Alternatives considered** (raised with the user before implementing, since this is an auth
surface with a real availability trade-off — CLAUDE.md pre-merge gate #9):

- *Fail-closed Redis allowlist, mirroring break-glass* — the review's literal suggestion, and
  rejected. `utils/redis_client.py` falls back to an **in-process dict** when `REDIS_URL` is
  unset, so a token minted on one replica would be unverifiable on another; and a Redis blip
  would lock the super admin out of the live dashboard mid-incident, which is exactly the
  outcome the JTI denylist's fail-open comment says it was avoiding. Break-glass can accept that
  because it is a rare emergency token; `admin-001` is a routine login.
- *Retire the env admin entirely* (seed a real `admin_staff` row and delete the special case) —
  architecturally the cleanest and it removes the whole class of bug, but it needs a migration
  plus a bootstrap path and changes how an operator recovers when the DB row itself is wrong.
  Largest blast radius of the three; left as a possible follow-up.

The DB was chosen because it is already a hard dependency of every admin request, so keying on it
adds no new failure mode, and because it makes the revocation authoritative rather than
best-effort.

## 4. Risk & impact on existing functionality

**Blast radius — every place an `admin-001` access token is created or checked**, grepped
(`_mint_admin_access_token` has **5** call sites — an earlier draft of this table said 6, having
counted the `def` line; corrected by the security audit below):

| Call site | Reachable as admin-001? | Touched? |
|---|---|---|
| `auth.py:365` login, env-admin branch | yes | **yes** — reads stored version |
| `auth.py:552` refresh, env-admin branch | yes | **yes** — see below |
| `auth.py:448` login, staff branch | no (loads `admin_staff`) | no |
| `auth.py:1062` MFA confirm | no (staff-only) | no |
| `auth.py:1185` MFA verify | no (staff-only) | no |
| `_mint_mfa_enroll_token` (`:456`) | no (staff-only) | no |
| `dependencies/__init__.py` `_verify_admin_payload` | yes | **yes** — version check added |

**The refresh path was a real defect this change would otherwise have introduced.** `auth.py`'s
refresh handler hardcoded `token_version = 0` in its `admin-001` branch. Harmless while nothing
ever bumped the counter — but after the *first* `/logout-all`, every refresh would mint a token
`_verify_admin_payload` rejects immediately, silently downgrading the super admin from "refreshes
for 30 days" to "re-login every hour" with no visible cause. Found by grepping the mint call
sites rather than by testing; now reads the same stored value, and
`test_no_mint_path_hardcodes_a_zero_version_for_the_env_admin` fails the suite if any mint path
reintroduces a literal `0`.

**Other consumers checked:**
- `admin-dashboard/src/lib/api/auth.ts:113` types the logout-all response as
  `{ success: boolean; revoked_refresh_tokens: number }`. The env-admin branch was deliberately
  refactored to fall through to the shared tail rather than return its own shape — an earlier
  draft returned `{success, token_version, refresh_tokens_revoked}`, which would have broken that
  type. No client change is needed.
- No test anywhere asserts the old 400 (grepped for the message text and for `logout_all` in both
  `backend/tests` and `admin-dashboard/src`), so nothing pins the behaviour being replaced.
- `tests/test_admin_revocation_failopen.py` uses `admin-001` *specifically because* it skipped the
  DB lookup. It now performs one. Verified safe: the autouse `mock_supabase_client` returns
  `data = []`, so `find_one` → `None` → version `0`, which matches those payloads' `token_version:
  0` and leaves all three tests asserting exactly what they assert today. The Redis fail-open
  behaviour they pin is untouched.

**The idle timeout still does not apply to `admin-001`** — it is driven by
`admin_staff.last_activity_at`, which this account has no row for. Out of scope here; the 1-hour
`ADMIN_ACCESS_TOKEN_TTL_HOURS` remains its only time bound.

**Failure posture is fail-closed on all three paths**, which is a deliberate reversal of the
denylist's fail-open stance and the main risk this change carries: if the `settings` row cannot be
read, login and refresh return 503 and existing requests return 503 rather than being let through.
Treating an unreadable counter as `0` would silently un-revoke every token an operator had just
killed, which is the hole being closed. The `settings` row is read on nearly every request path
already, so a failure here means the DB is down and admin requests are failing regardless.

**Concurrency:** two simultaneous logout-alls can both read N and write N+1, so the counter may
advance by one instead of two. Harmless — what matters is that the stored value ends up strictly
greater than any already-minted token's claim, which a single increment achieves. It is a
revocation generation, not a count.

## 5. User-experience effect

- **Internal admin (`admin-001` operator):** `/logout-all` now works instead of returning an error
  telling them to redeploy. Pressing it signs out every super-admin session immediately — that is
  the intended effect, and it is newly possible to do to yourself. No copy change in the
  dashboard; the button and its response shape are unchanged.
- **Internal admin (staff):** none. Same branch, same response, same WS kick.
- **Rider / driver / corporate admin:** none.
- Mid-session visibility: an operator already logged in as `admin-001` keeps their session; the
  version check passes (their token's claim equals the stored value) until someone bumps it.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/env_admin_tokens.py` | new: `ENV_ADMIN_USER_ID`, `get_env_admin_token_version`, `bump_env_admin_token_version` | one definition read by mint, verify and revoke so the three cannot drift |
| `backend/migrations/434_env_admin_token_version.sql` | new: `settings.env_admin_token_version INTEGER NOT NULL DEFAULT 0` | somewhere to put the counter for an account with no `admin_staff` row |
| `backend/dependencies/__init__.py` | `elif user_id != "admin-001"` → explicit env-admin branch with a fail-closed version check | the bypass itself |
| `backend/routes/admin/auth.py` | login + refresh stamp the stored version; logout-all bumps it and shares the staff tail; docstring corrected | mint/revoke halves of the same mechanism |
| `backend/routes/admin/settings.py` | `_mask_credentials` drops an `_INTERNAL_ONLY_FIELDS` set, holding `env_admin_token_version` | see §4 — the new column would otherwise round-trip to every staff account via `GET /admin/settings` |
| `backend/tests/test_env_admin_token_version.py` | new: 13 tests across verify, helpers, logout-all, mint-path parity and the settings-GET strip | pin all of it, including the fail-closed posture |
| `docs/change-log/2026-09-21-env-admin-token-version.md` | this file | |

## 7. Before / after

```python
# Before — dependencies/__init__.py: the account skipped the whole block
elif user_id != "admin-001":
    staff_rows = await db_supabase.get_rows("admin_staff", {"id": user_id}, limit=1)
    ...  # is_active, token_version, idle timeout
```

```python
# After
elif user_id == ENV_ADMIN_USER_ID:
    try:
        _env_admin_version = await get_env_admin_token_version()
    except Exception as _env_err:
        logger.opt(exception=True).error(...)          # loguru: not exc_info=
        raise HTTPException(status_code=503, detail="Sign-in is temporarily unavailable. ...")
    if _token_version_mismatch(payload, {"token_version": _env_admin_version}):
        raise HTTPException(status_code=401, detail="ERR_SESSION_REVOKED")
else:
    staff_rows = await db_supabase.get_rows("admin_staff", {"id": user_id}, limit=1)
    ...
```

```python
# Before — routes/admin/auth.py: the one account that could not be logged out
if not user_id or user_id == "admin-001":
    raise HTTPException(status_code=400, detail="Super admin cannot force-logout here. "
                        "Rotate ADMIN_PASSWORD in the environment ...")
```

```python
# After
if user_id == ENV_ADMIN_USER_ID:
    new_version = await bump_env_admin_token_version()
else:
    ...  # staff branch
revoked = await revoke_all_for_user(user_id)   # shared tail: WS kick + response
```

**Concrete scenario.** A super-admin token leaks at 09:00.

- **Before:** the operator presses "Log out all sessions" → 400. Their only option is editing
  `ADMIN_PASSWORD` in the environment and redeploying; until that lands the leaked token works on
  every endpoint. If Redis is also down, even the per-JTI denylist cannot stop it.
- **After:** they press the same button. `env_admin_token_version` goes 0 → 1 and every refresh
  token for `admin-001` is revoked. The leaked token (claim 0 < stored 1) is rejected with 401
  `ERR_SESSION_REVOKED` on its very next request, and its refresh token no longer mints a new one.
  The operator logs in again and receives a token stamped 1.

## 8. Rollback plan

Two independent levers, neither needing a second deploy:

1. **Data-only, restores the pre-change posture exactly:**
   `UPDATE public.settings SET env_admin_token_version = 0 WHERE id = 'app_settings';`
   Every token then compares 0 against 0 and passes, which is what happened before 433. Note this
   *un-revokes* anything previously revoked this way — that is the point of the lever, and the
   reason to reach for it only if the check is misfiring.
2. **Code:** `git revert` + deploy. Safe in either order relative to the migration, because the
   column is additive and the reverted code simply stops reading it.

Migration 433 itself needs no rollback to make the revert safe; `DROP COLUMN` is documented in the
migration header for when the readers are retired.

Ordering note for the forward deploy: **the migration must land BEFORE the code.** An earlier
draft of this change tolerated a missing column by reading it as `0`, which made the deploy order
free — and was rejected in review as a fail-open hole (§9): the comparison is `claim < stored`, so
a stored `0` passes every token ever minted, meaning absent data silently un-revokes everything an
operator just killed. The read now selects the column by name and fails closed on any error, so
deploying the code against a database without the column returns 503 on env-admin auth until the
migration runs. That is the deliberate trade: a recoverable deploy-ordering constraint in exchange
for a revocation control that cannot silently do nothing.

## 9. Verification performed

- `ruff check` / `ruff format --check` / `py_compile` clean on all four changed Python files.
- **Dual-import symmetry** checked by running this repo's own AST detector
  (`tests/test_dual_import_symmetry.py`'s `_asymmetric_blocks`) across every backend module:
  0 asymmetric blocks. Both new import blocks bind the same names in each branch.
- **loguru convention** checked with an AST scan of the changed file: `dependencies/__init__.py`
  imports `from loguru import logger`, so the `exc_info=True` I first wrote there would have been
  silently swallowed and the traceback lost. Rewritten as
  `logger.opt(exception=True).error(...)`. `routes/admin/auth.py` uses stdlib `logging`, so its
  `%s` + `exc_info=True` calls are correct as written — the two files genuinely differ.
- The new `test_no_mint_path_hardcodes_a_zero_version_for_the_env_admin` regex guard was executed
  directly against the current `routes/admin/auth.py` to confirm it passes now (and it was written
  *after* seeing it match the pre-fix source, so it is not a vacuous assertion).
- Blast radius for the response-shape change traced to `admin-dashboard/src/lib/api/auth.ts:113`
  and resolved by converging both branches on the existing tail rather than adding a second shape.
- Migration numbering: `433` is free. **Corrected after merging `main`:** the branch was 12 commits
  behind, and `432_admin_role_rls_unreachable_phase1.sql` (PR #5592) had landed there in the
  meantime — colliding with this branch's own `432_uncollected_rides_excluded_from_payable_flag.sql`
  from the C1 work, which `migration-check.yml` CHECK B hard-fails. That file was renumbered to
  `434` (never applied anywhere, so renaming it is safe; the runner keys on the full filename, so
  only *already-applied* migrations are frozen). `433` here was unaffected and stands.
- **`spinr-security-auditor` was run against the diff** per CLAUDE.md's pre-commit gate. Verdict:
  no blockers. It independently confirmed the branch chain is mutually exclusive (no token can
  skip both checks, no staff token can enter the env-admin branch), that the fail-closed 503 is
  not reachable by an unauthenticated party (all three call sites already require a correct
  password, a valid refresh token, or a validly-signed access token), that `/logout-all` derives
  `user_id` only from the caller's own bearer token so no cross-account revoke exists, and that
  `PUT /admin/settings` cannot clobber the column (it is absent from `SettingsUpdateRequest`, and
  `update_one` issues a column-scoped PostgREST PATCH, never a full-row overwrite).
  Two low-severity findings, both fixed in this change:
  1. **Real disclosure.** `GET /admin/settings` returns every column of the settings row and
     `_mask_credentials` only masks *string* credentials, so the new int column would round-trip
     unmasked to any staff account. Now dropped via `_INTERNAL_ONLY_FIELDS`, with a test for the
     strip and a second test pinning that credential masking still works (the new `continue` sits
     above the masking branch, so a mistake there would silently unmask secrets). Both were
     executed directly through the interpreter, not just reasoned about.
  2. `admin_refresh`'s branch guard still used the `"admin-001"` literal while the other two
     branches of the same trio use `ENV_ADMIN_USER_ID`. Swapped. Three further literals remain in
     the file (the mint below it and two staff-only guards); they are not part of the
     mint/verify/revoke trio and were left alone rather than churned.
  It also corrected a factual error in §4 of this log: 5 call sites, not 6.
- **An architecture review pass was run against the same diff**, and found two defects the
  security pass did not. Both fixed here, both verified by executing the changed code paths
  directly (pytest being unavailable):
  1. **Fail-open on absent data — a hole in the control this change exists to add.** The
     fail-closed posture only covered *raised exceptions*. A missing `settings` row, or a missing
     column, returned `0`; since `_token_version_mismatch` is `claim < stored`, a stored `0`
     passes every token ever minted. An operator's revocation would silently do nothing. The
     read now raises `DatabaseError` (503) when the row is absent and selects the column by
     name so a missing column is an error rather than a `0`. Two tests that had codified the old
     behaviour as *intended* were inverted.
  2. **`/logout-all` could report success having written nothing.** `bump_env_admin_token_version`
     discarded `update_one`'s return, and `update_one` returns `None` on a zero-row match without
     raising (`repositories/_base.py:1286`); the settings row can legitimately be absent, which
     `routes/admin/settings.py:813` proves by branching to `insert_one` for that case. So the
     endpoint could answer `200` with a new version for a revocation that never reached the
     database — the operator believing a leaked super-admin token was dead while it stayed live
     until its own expiry. The write is now verified.
  It also found that the read pulled `SELECT *` — ~130 columns including `stripe_secret_key`,
  `twilio_auth_token`, `apns_p8_key` and four `ai_api_key_*` values — into an auth function's
  frame on every super-admin request. Narrowed to `id,env_admin_token_version`, which also turns
  a missing column into the hard error that finding 1 requires. The previously-unused
  `DatabaseError` import it flagged as dead is now the exception both paths raise.

## 10. What was NOT verified

- **pytest was not run** — this sandbox cannot reach PyPI, so no test in this repo has been
  executed locally. CI is the gate. The most load-bearing assumption is that the three existing
  `tests/test_admin_revocation_failopen.py` tests still pass now that the `admin-001` path does a
  DB read; that was established by reading `mock_supabase_client` (returns `data = []`) and
  `find_one` (returns `None` on an empty list), not by observation.
- **Not exercised against a real Supabase.** Whether `update_one("settings", ...)` with a plain
  (non-`$set`) patch document behaves as the staff branch's `{"$set": ...}` form does was taken
  from the repository layer's own handling of both shapes, not observed on a live row. Worth one
  manual check on staging before relying on the bump.
- **The idle timeout still does not apply to `admin-001`** (§4). Deliberately out of scope — it
  needs a `last_activity_at` store this account has no home for, which is really the
  "retire the env admin" option.
- **`_resolve_inner` unwrapping of the SlowAPI decorator** is copied from the established pattern
  in `tests/test_logout_all.py`; it is assumed to resolve `admin_logout_all` the same way it
  resolves the handlers there. If CI shows it returning the wrapper, those two logout-all tests
  will fail loudly rather than silently pass.
- **No load or concurrency testing** of the extra per-request `settings` read on the admin path.
  It is one uncached read on admin requests only (staff already pay an equivalent `admin_staff`
  read), so it is not expected to move any SLA in the Performance table, but that is reasoning,
  not measurement.
- Backend-only; no UI diff, so admin-dashboard's Playwright visual-regression baselines are not
  implicated.
