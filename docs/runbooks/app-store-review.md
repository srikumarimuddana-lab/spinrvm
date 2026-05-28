# Runbook — App Store / Play Store reviewer login

Spinr authenticates with phone + SMS OTP. App Store and Play Store reviewers
run in a datacenter and **cannot receive your SMS**, and the `1234` dev bypass
is disabled in production. This runbook sets up a reviewer login that works in
production for an explicit allow-list of numbers only — without weakening auth
for real users.

## How it works

`/auth/send-otp`, for a phone in the `REVIEW_LOGIN_ACCOUNTS` allow-list, stores
a **pre-shared fixed code** and **does not send an SMS**. `/auth/verify-otp` is
unchanged — it just hash-compares the submitted code against the stored one, so
the existing expiry, lockout, and reuse protections all still apply. Every
other number in production continues to get a real SMS. Reviewer logins are
logged (`Reviewer-account OTP issued without SMS for ...NNNN`).

The allow-list lives in a **backend env secret** (not `app_settings`) so it
can't be edited from the admin dashboard — a compromised admin can't add a
bypass number.

## Setup (per submission)

### 1. Pick reviewer numbers + codes

Use test numbers in a region you control (Saskatchewan = `+1306…`). Codes are
**4 digits** (must match `OTP_LENGTH`). Use one number per app:

| App | Example phone | Example code |
|---|---|---|
| Rider | `+13065550100` | `4821` |
| Driver | `+13065550101` | `4821` |

### 2. Set the backend env var (Railway)

```
REVIEW_LOGIN_ACCOUNTS=+13065550100:4821,+13065550101:4821
```

Comma-separated `phone:otp` pairs, E.164 phone. Redeploy. **Leave this empty
except while a review is in flight; clear it once the build is approved.**

### 3. Rider reviewer account — nothing to seed

The rider account is auto-created on first `verify-otp`. The reviewer can log
in and exercise the full UI: map, pickup/destination, fare estimate, request,
wallet, history, profile, safety/SOS.

Mock payments are enabled for allow-listed reviewer accounts in production, so
if the reviewer reaches a payment/confirmation screen they can complete it with
a `pi_mock_*` intent — **no real card is charged**. (For everyone else, mock
payments remain rejected in production.)

### 4. Driver reviewer account — pre-approve once

A reviewer logging into the **driver** app with a brand-new account lands on
onboarding. Pre-provision a driver so the reviewer can go online:

**Recommended (supported path):** run the driver through normal onboarding once
on staging/prod, then in the admin dashboard set the account to **active**,
assign a vehicle type, and set all document expiry dates to a far-future date.

A driver must satisfy `go_online` to receive offers:
- `status = 'active'`
- All document expiry dates in the future: `license_expiry_date`,
  `insurance_expiry_date`, `vehicle_inspection_expiry_date`,
  `background_check_expiry_date`, `work_eligibility_expiry_date`
- A `vehicle_type_id` (see `seed_vehicle_types.py`)
- Spinr Pass is **not** required unless the admin toggle
  `require_driver_subscription` is on (off by default pre-launch)

**Optional SQL template** (adjust column names to your schema; run in Supabase
SQL editor against the review environment, not blind in production):

```sql
-- after the reviewer driver has logged in once (users row exists)
update users set role = 'driver', is_driver = true
 where phone = '+13065550101';

update drivers d
   set status = 'active',
       is_verified = true,
       vehicle_type_id = (select id from vehicle_types limit 1),
       license_expiry_date            = now() + interval '2 years',
       insurance_expiry_date          = now() + interval '2 years',
       vehicle_inspection_expiry_date = now() + interval '2 years',
       background_check_expiry_date   = now() + interval '2 years',
       work_eligibility_expiry_date   = now() + interval '2 years'
  from users u
 where d.user_id = u.id and u.phone = '+13065550101';
```

> Note: a live ride match still needs supply/demand to line up. Reviewers do
> not require a completed real trip — see the review notes below.

## What to put in the store consoles

### App Store Connect → App Review Information

- **Sign-in required:** Yes
- **Phone number:** `+1 306 555 0100`
- **Verification code:** `4821`
- **Notes:**
  > Spinr signs in with a phone number + a 4-digit SMS code. This is a demo
  > account that accepts the fixed code above without an SMS. Enter the phone,
  > tap Send Code, then enter `4821`. You can browse the map, set a pickup and
  > destination, view the fare estimate, and request a ride. Live driver
  > matching depends on a driver being online in the area, so a request may
  > stay in "Finding your driver" if none is available — this is expected and
  > not a defect. No real payment is charged on this demo account.

Submit the driver app with the driver number/code and an analogous note (go
online, receive/accept an offer when one is dispatched).

### Google Play Console → App access

Add the same phone/code under "All or some functionality is restricted" with
instructions identical to the Apple notes.

## Security hygiene

- Treat `REVIEW_LOGIN_ACCOUNTS` like a credential. **Clear it after approval.**
- Rotate the codes for every submission.
- Use throwaway test numbers you control — never a real customer's number.
- Reviewer logins are audit-logged; mock-payment use is scoped to these
  accounts only and only in addition to the normal non-production allowance.
