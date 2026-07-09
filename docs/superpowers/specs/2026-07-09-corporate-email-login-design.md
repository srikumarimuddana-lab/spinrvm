# Corporate Email Login Design

## Goal

Company users should sign in to Spinr for Business with a work email address, matching the expected Uber for Business style flow. Phone OTP remains available to rider and driver apps, but the company portal should lead with email-based access.

## Current State

`/company-login` currently asks for a phone number and uses `/api/portal/auth/send-otp` plus `/api/company-auth/verify-otp`. Corporate membership is already email based: company admins invite members with `corporate_members.invited_email`, and invite acceptance activates the row for the signed-in user. The mismatch is that users identify themselves with a phone number before the system can attach the email invite.

## Proposed Flow

1. A company owner or admin invites `employee@company.com`.
2. The employee opens `/company-login` or the invite link.
3. The employee enters their work email.
4. The backend sends a short-lived numeric code to that email through the existing transactional email provider.
5. The employee enters the code.
6. The backend finds or creates a rider-compatible `users` row for that email.
7. Any pending `corporate_members` invite for that email is accepted for the user.
8. The portal fetches memberships and redirects the employee to the correct company portal landing page.

## Backend Design

Add dedicated company email OTP endpoints under the existing portal auth namespace:

- `POST /api/portal/auth/send-email-otp`
- `POST /api/portal/auth/verify-email-otp`

The endpoints live in `backend/routes/auth.py` so they can reuse token issuance, refresh-token issuance, CSRF behavior, OTP hashing style, and audit conventions already used by phone OTP.

Email OTP state uses a new service-role-only table:

- `corporate_email_otp_records`
- fields: `id`, `email`, `code_hash`, `expires_at`, `verified`, `created_at`
- RLS enabled, no anon/authenticated policies
- explicit service-role grants, because Supabase is moving toward no automatic Data API grants for new public tables

Failure behavior:

- Invalid or expired code returns the same clean client-facing failures as phone OTP.
- DB and email provider failures surface loudly with 503/502-style responses.
- Raw email addresses are not logged; logs use a stable hash or generic message.

## Frontend Design

Update `/company-login` to ask for email first. Keep the same two-step shape:

- Step 1: email input, "Send code"
- Step 2: code input, "Verify and sign in"

The company auth store remains unchanged: it stores the access token in memory, keeps refresh tokens in HttpOnly route-scoped cookies, and keeps company auth isolated from staff admin auth.

Add frontend API helpers:

- `sendCompanyEmailOtp(email)`
- `verifyCompanyEmailOtp(email, code)`

Add a Next route handler:

- `/api/company-auth/verify-email-otp`

This mirrors the existing phone verify route by proxying to the backend, stripping the refresh token from the JSON body, and setting the company refresh/CSRF cookies.

## Testing

Backend tests cover:

- sending an email OTP stores a hashed code and sends mail
- verifying a valid code issues tokens and accepts a pending invite
- invalid codes do not create sessions

Frontend tests cover:

- company login renders email copy/input
- email submit calls the email OTP helper
- verify path calls the email verification helper and redirects after memberships load

## Out Of Scope

- SAML/SCIM enterprise SSO
- passwords for company users
- removing phone OTP from rider/driver auth
- changing corporate billing or dispatch logic
