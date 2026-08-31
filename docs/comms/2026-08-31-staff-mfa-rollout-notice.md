# Staff MFA rollout notice — draft

Ready-to-send message for ACTION_ITEMS.md C4. Not sent by this session —
no Slack/email integration available here; a human sends this through
whatever channel the team actually uses.

## Current state (verified 2026-08-31, read-only against production)

- `admin_staff` currently has **2 rows total**, both `role='super_admin'`,
  both `is_active=true`, **both already `mfa_enabled=true`**. The
  "≥2 active super_admin accounts for the lost-phone reset path"
  requirement in C4's acceptance criteria is already satisfied — not
  assumed, confirmed via a direct read-only query.
- `ADMIN_MFA_ENFORCED` defaults to `true` in `backend/core/config.py`
  (env-var controlled per deployment, not a DB row — not independently
  re-verified against the live Fly/Railway env this pass, see C3).
- Because only these 2 accounts exist today and both already have MFA
  enabled, there is currently no admin who would hit a *surprise* forced
  enrollment prompt. This notice is still worth sending — it documents the
  policy for the next staff account created, and confirms today's 2
  accounts intentionally.

## Draft message

> **Subject: Admin dashboard now requires authenticator MFA on every login**
>
> Hi team,
>
> The Spinr admin dashboard now enforces two-factor authentication
> (`ADMIN_MFA_ENFORCED`) — every admin login requires an authenticator app
> code (Google Authenticator, Authy, 1Password, etc.), not just your
> password.
>
> **What this means for you:**
> - If you already have MFA enrolled, nothing changes.
> - If you don't yet, your next login will prompt you to scan a QR code and
>   set up your authenticator app before you can continue.
> - Keep your authenticator app's backup codes somewhere safe — losing your
>   phone without a backup code means a super_admin has to reset your MFA
>   for you.
>
> **Lost-phone recovery:** two super_admin accounts (kmuddana@spinr.ca,
> vmethre@spinr.ca) can reset another admin's MFA enrollment. If you're
> locked out, reach one of them directly.
>
> Questions, ping [internal channel].

## Still open after sending

- Confirm `ADMIN_MFA_ENFORCED` is actually set on **both** Fly and Railway
  production environments (not just the code default) — this is C3's own
  open scope, not re-verified here.
- If/when new admin_staff accounts are added, this same notice should go
  to them at onboarding time rather than being a one-time send.
