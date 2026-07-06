# Runbook — Corporate Guest Booking (Spinr for Business)

A company employee books a ride for a customer by name + phone from the
company portal (`/company-portal` on the admin domain, own login at
`/company-login` using rider phone-OTP). The customer needs no app: SMS
carries the booked-by line, pickup OTP, and the public tracking link.

## Moving parts

| Piece | Where |
|---|---|
| Booking service | `backend/services/company_booking_service.py` |
| Guest identity | `backend/services/guest_user_service.py` (`users.is_guest`, auto-claimed at first OTP login) |
| Endpoints | `backend/routes/corporate_company_bookings.py` (`/company/{id}/bookings*`, `/sections*`; also mounted under `/api`) |
| SMS lifecycle | `backend/services/guest_notification_service.py` (hooks: booking, driver-accept, arrived, cancels) |
| Settlement | `payment_service.settle_corporate` (payer = `rides.corporate_member_id`) + `auto_settle_guest_corporate` on completion + sweep in `utils/payment_retry.py` |
| Portal auth | `admin-dashboard/src/store/companyAuthStore.ts`, `/company-login`, `company_token` cookie in `src/middleware.ts` |

## Metrics / alerts

- `spinr_corporate_guest_booking_total{scheduled}` — booking volume.
- `spinr_payment_settlement_total{path="corporate_guest_auto", outcome}` —
  settlement outcomes; alert on `outcome="failed"` growth.
- Guest SMS failures log as `guest SMS failed (<context>) ride=<id>` —
  errors carry sanitized Twilio codes only (PIPEDA: no phone/body in logs).

## Common incidents

**Customer says they got no SMS** — check logs for `guest SMS failed`
with the ride id; Twilio code 21211 = bad number (typo at booking: cancel +
rebook), 21608/trial = Twilio account issue. Confirm `twilio_*` keys in
app_settings.

**Ride completed but company not charged** — ride stuck
`payment_status=pending`. The payment-retry sweep re-drives guest
settlements every ~5 min (10-min grace after completion). If it stays
pending: check for `auto-settle failed` / `corporate_member_id ... invalid`
logs — a mismatched/inactive booking member fails loudly by design; fix the
membership, then the sweep settles it.

**Booker can't book: 403 allowance_low** — the employee's allowance needs
1.5× the fare headroom unless company policy allows master-wallet fallback.
Top up the allowance or set `allowed_payment_source` to `both`.

**Wrong-number booking** — blast radius is one ride: the pickup OTP only
starts that ride, the tracking page is PII-scrubbed and expires in 24 h.
Cancel from the portal (customer gets a cancel SMS) and rebook.

**Portal login loops** — the `company_token` cookie and staff `admin_token`
are separate; company users must use `/company-login`. A rider with no
`corporate_members` row gets "no company memberships" by design.

## Deploy notes

- Migrations 206 + 207 must be applied before bookings are attempted
  (`guest_booking` column write fails loudly otherwise). Index builds are
  CONCURRENTLY; verify `pg_index.indisvalid` for
  `idx_rides_corporate_account_created` and `idx_users_is_guest` post-apply.
- Booking POST is rate-limited 30/hour per client (SMS cost bound).
- Cancellation fees: company cancels currently ride the normal pre-trip fee
  logic via the guest's cancel path; if fee attribution to guests proves
  noisy, waive-on-company-cancel is the documented v1 fallback (plan risk #1).
