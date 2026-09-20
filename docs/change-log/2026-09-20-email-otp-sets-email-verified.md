# Change Impact & Risk Log — company email-OTP login marks the address verified

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-20 |
| Author | Claude Code session (2026-09-20 review, Phase 1 item C5) |
| Surface(s) | backend |
| Domain (Sentry tag) | auth / corporate |
| PR / commit link | branch `claude/loving-thompson-amjk8b` |
| Related issue or gap ID | 2026-09-20 review, 🚨 C5 |

## 1. Issue / gap identified

`POST /corporate/join-domain` is gated on `current_user["email_verified"]`, but **nothing set that flag for company-email accounts**. `verify_company_email_otp` creates or refreshes the user without it, and the only writer anywhere in the backend is `routes/users.py`'s rider-app verify flow — which that file itself notes has no UI for this population. So the endpoint returned `403 ERR_EMAIL_UNVERIFIED` permanently, to exactly the employees it exists for: someone who had just proved inbox ownership by completing an emailed OTP could not join their own company's domain.

Separately, the same handler resolved the address as `current_user.get("phone_or_email") or current_user.get("email")`. `phone_or_email` is written by nothing in the backend (that line was its only occurrence in production code), so it was always `None`.

## 2. Root cause

The `email_verified` gate was added by the prior audit's fix for finding #15 ("unverified email accepted at profile creation is trusted as a corporate-billing identity"). That fix correctly refused to trust an unverified address but did not add the writer for the one flow that *does* prove the address — an over-correction that turned a security hole into a dead endpoint.

## 3. Fix / remediation

Both branches of `verify_company_email_otp` now stamp the flag — the OTP **is** the proof:

- existing user: `email_verified` / `email_verified_at` ride along with the `current_session_id` write already happening, in the same `update_one` (no extra round trip), and the local `user` dict is updated so the response and the minted token see it.
- new user: the same two fields on the row handed to `create_user`.

The dead `phone_or_email` fallback is removed.

**Alternative considered:** drop the `email_verified` gate from `join-domain` and rely on the domain match alone. Rejected — that reinstates exactly the hole finding #15 closed: a user could set `email` to `anyone@bigcorp.com` at profile creation and join that company's account without ever proving control of the inbox.

## 4. Risk & impact on existing functionality

- `email_verified` / `email_verified_at` (migration 252) are the only fields added; both already exist. No schema change.
- **Readers of `email_verified`**, grepped: `routes/corporate_rider.py` (the join-domain gate — now reachable), `routes/users.py:148-150` (resets it to `False` on any email change — unaffected; a company-email user who later changes their address correctly loses the flag and must re-verify), and `routes/users.py:1399` (the rider-app writer). No other consumer.
- **Security direction:** this only ever sets the flag where an emailed OTP was just verified against a hashed code with lockout enforcement, so it cannot mark an unproven address verified. It does not widen who can join a domain: the `corporate_allowed_domains` check is unchanged and still runs.
- Removing the `phone_or_email` fallback is a no-op in production (always `None`), but it removes a latent inconsistency: had anything ever populated that key from client-controlled input, the flag checked and the domain matched would have described two different addresses.

## 5. User-experience effect

- **Rider on a company email:** `join-domain` works. Previously always 403. Visible immediately.
- **Rider / driver / admin otherwise:** none.
- No copy change.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/auth.py` | `verify_company_email_otp` sets `email_verified` + `email_verified_at` in both the existing-user and new-user branches | the OTP is the proof; nothing else writes it for this population |
| `backend/routes/corporate_rider.py` | `user_email` reads `email` only | `phone_or_email` was dead and could have desynced flag from domain |
| `backend/tests/test_company_email_login.py` | success test asserts the user row is stamped verified; new test for the account-creation branch | pin both branches |
| `backend/tests/test_corporate_rider_routes.py` | two tests moved from `phone_or_email` to `email`; new test that a stray `phone_or_email` is ignored | see §9 — they were green against a fallback production never used |
| `docs/change-log/2026-09-20-email-otp-sets-email-verified.md` | this file | |

## 7. Before / after

```python
# Before — existing-user branch
await db_supabase.update_one("users", {"id": user["id"]}, {"current_session_id": session_id})
```

```python
# After
_verify_patch = {
    "current_session_id": session_id,
    "email_verified": True,
    "email_verified_at": datetime.now(timezone.utc).isoformat(),
}
await db_supabase.update_one("users", {"id": user["id"]}, _verify_patch)
user.update(_verify_patch)
```

Scenario: employee at `acme.com` receives the OTP, enters it, opens Work Profile and taps "Join Acme". **Before:** 403 `ERR_EMAIL_UNVERIFIED`, with no in-app way to ever clear it. **After:** the domain check runs and they join.

## 8. Rollback plan

Code-only, no migration, no flag: `git revert` + deploy. Rows already stamped `email_verified=true` stay stamped — which is correct regardless, since each one did complete an OTP. No cleanup needed.

## 9. Verification performed

- `ruff check` / `ruff format` / `py_compile` clean.
- The join-domain email resolution was simulated for every affected test's user dict, confirming each still reaches its expected branch (domain check / 400 no-email).
- **Two existing tests were found to be passing only because of the dead key**: `test_join_domain_unauthorized_domain_is_403` and `test_join_domain_success` supplied the address exclusively via `phone_or_email`, and `_FAKE_USER` carries no `email`. They were therefore exercising a fallback real traffic never took. Both now set `email`, so they test the production path.

## 10. What was NOT verified

- **pytest was not run** (sandbox cannot reach PyPI). CI is the gate: `tests/test_company_email_login.py`, `tests/test_corporate_rider_routes.py`, `tests/test_email_verification_corporate.py`.
- Not exercised end-to-end against a real Supabase — whether `create_user` persists the two new keys was read from the helper, not observed.
- No check of whether any *existing* company-email user needs a backfill: accounts created before this change still have `email_verified=false` and must complete one more OTP login to clear it. Whether that is acceptable or wants a one-off backfill is a product call, not made here.
