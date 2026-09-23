# Driver app PRD waves

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-22 |
| Surface(s) | backend, driver-app |
| Domain | drivers, rides, safety, payments, auth |

## 1. Issue / gap identified

The driver-app requirements listed launch gaps that were either dark-shipped or missing in the app: eligibility not enforced on go-online, no-show counted as a driver cancel, batch offers droppable by going offline, no service-animal refusal block, stops invisible to the driver, SOS "I'm OK" did not cancel the incident, accept with no network left the offer card up, and a paid-out driver was not held back after a rider refund.

## 2. Root cause

Several checks existed behind flags or only on the server. The driver UI called the cancel endpoint for no-shows, did not render `stops_updated`, and the refund path left `driver_earnings` unchanged after a completed payout.

## 3. Fix / remediation

Go-online now enforces Saskatchewan eligibility when the fields are present, and requires current background-check consent when that legal text is published. A rejected application resubmit returns to pending. Batch-offer go-offline returns 409 without the old flag. No-show calls the no-show endpoint. Service-animal decline and cancel are rejected and audited. Offers list stops and a service-animal badge. SOS "I'm OK" marks the incident `false_alarm` within 60 seconds. Accept with no HTTP response reconciles from the server. A completed payout followed by a rider refund inserts one clawback payout row per ride. Upcoming scheduled trips, an earnings-issue dispute from ride detail, an Android battery prompt, queued offline completion, and a kick of the previous login session are included. There is no driver-to-rider call button, no masked calling, and no instant pay.

## 4. Risk & impact on existing functionality

- Go-online can newly reject drivers who have a non-Class-5 licence, a vehicle 10 years or older, under 3 years licensed, or a date of birth under 18. Missing fields stay unblocked.
- Drivers with a published CRC consent text and no current consent cannot go online.
- Going offline during a pending batch offer now 409s even when `go_offline_live_offer_guard_enabled` is off.
- A new OTP login revokes the previous session and kicks that device's socket.
- A rider refund after the driver was already paid reduces future payable balance by one clawback row. Unpaid trips are unchanged.
- Blast radius: driver go-online, offer/cancel, SOS, payouts, and login. Rider dispute creation still works when `rider_id` matches.

## 5. User-experience effect

Drivers see a deactivation screen when banned or suspended, a Stripe restricted banner, a no-show action after arrival, upcoming trips, and an earnings-issue action. Chat stays. Call to the rider is not shown. Weekly payout copy is unchanged in schedule. A second phone signs the first phone out.

## 6. Files modified

See the diff. Behavior lives in driver status, decline/cancel, SOS, offer payload, payouts webhook, disputes, auth session rotation, and the driver app screens listed in the requirements.

## 7. Rollback plan

Revert the commit. Clawback rows already inserted are money-out records; do not delete them to "undo" a refund. A driver blocked by eligibility or consent can be unblocked only by correcting the profile or consent, not by turning the old flag back on.

## 8. Verification performed

`pytest -o addopts=` on the service-animal cancel test, the service-animal decline test, and the rejected-resubmit test: 3 passed. No production build of driver-app. No live Supabase. Driver-app has no screenshot suite.

## 9. What was NOT verified

Not run against live Stripe refunds, live OTP on a second device, or a production Android battery-settings screen. Route-deviation remains flag-off. Masked calling and instant pay were not built.
