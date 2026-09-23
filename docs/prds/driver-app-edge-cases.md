# Driver App — Edge Cases and Use Cases

Product contract for the Spinr driver app, built on the current codebase. Saskatchewan-first.
Drivers are independent contractors and keep 100% of the consumer fare. Monetization stays on
Spinr Pass and corporate accounts. SOS may offer a Call 911 button and must never dial by itself.

The one-paragraph summary in `docs/PRD.md` stays the index. This file is the full contract.
It does not flip `enforce_driver_eligibility_recheck` or `route_deviation_alert_enabled`.
Those flags are separate, explicit rollout changes.

Industry notes below are practice at Uber, Lyft, or Bolt. They are not claims about those
companies' current binaries.

## Founder decisions

Where reviews disagreed, these calls win:

- **No-show.** The cancel sheet has a "Rider no-show" row, but that row calls `/cancel`, which
  records a driver cancel and never hits `POST /drivers/rides/{id}/noshow`. **P0 bug.** The
  label exists. The fee and the attribution do not.
- **Route-deviation alerts.** The alerter exists and the flag defaults off. Turning it on
  without a triage path pages humans for gas stops and rider detours. **P1 rollout after a
  written false-positive rule, not a blind production flip.**
- **Safety check-in.** Backend prompts the rider on long `in_progress` trips. The driver app
  has no check-in screen. **P1 driver prompt. Never auto-dial 911.**

## Use cases already in the product

A driver can sign in with phone OTP, recover a pending deletion, finish a profile, and walk a
five-step become-driver wizard that resumes after the app is killed (`login.tsx`, `otp.tsx`,
`become-driver.tsx`, `reactivate-account.tsx`). OTP expiry, five-fail lockout, and the
non-production `1234` bypass are server-side. The same phone can be both rider and driver.

A driver can upload documents, see rejection reasons, re-upload, and is blocked from going
online when mandatory documents are expired, or when the account is pending, suspended, or
banned (`documents.tsx`, `backend/routes/drivers/status.py`). Appeals reject a second pending
appeal. Legacy imported drivers can be held off the new app until consent is current.

Going online is the driver's own switch. Availability is computed. Go-online refuses missing
background location, airplane mode, unpaid Spinr Pass, and expired documents. The offer is one
exclusive card with a server-timed countdown (default 15s), push plus in-app, and a 409 when
another driver already won (`RideOfferPanel.tsx`, `useDriverDashboard.ts`, `driverStore.ts`).
Decline and timeout both release the ride. Reconnect reloads the active ride from
`offer_expires_at`. Destination mode keeps trips that move the driver toward a chosen point.
The offer card shows earnings, distance, minutes, pickup and dropoff, and badges for surge,
WAV, quiet ride, scheduled, and cash.

On trip, Arrive is geofenced (about 150m in the UI, 200m on the server). A 4-digit PIN starts
the trip. Wait time counts up after arrival. Navigation falls through to a web map if no nav
app is installed. Android Auto can offer, navigate, arrive, and complete; PIN start stays on
the phone. GPS points queue in SQLite and replay without duplicates. Killing the app restores
the ride from the server. Complete-far-from-dropoff asks the driver to confirm. Cancel after
`in_progress` is rejected. In-trip chat works.

Earnings show fare, tip, bonus, and tax. Weekly Stripe payout, payout history, T4A by email,
quests, and referrals (self-referral blocked, daily velocity cap) are live. SOS is a 1.2s hold,
texts emergency contacts and the safety team, then offers Call 911 as a separate tap. Insurance
periods are appended when the offer is claimed, not only when the driver taps Accept, and a
reconciler repairs bad rows. Lost-and-found, a demand heatmap with empty/stale/error states,
driver-to-rider star rating, and a WAV self-declaration toggle exist.

## Edge cases already handled

- Wrong, expired, and locked-out OTP. Dev bypass only outside production.
- Duplicate email. Wizard draft after process death. Serialized token refresh.
- Past document expiry rejected on upload. Re-upload after rejection. Vehicle year checked in the become-driver schema.
- Double-tap accept. Accept after timeout. Loser of the accept race. Duplicate push and websocket offers. Stale offer after reconnect. Documents re-checked at accept.
- Arrive too far. Start before arrive. Wrong PIN with lockout. Rider cancel before the trip starts. Complete with no network on the GPS upload, with the completion request itself still online. Idempotent complete if the server already finished the trip.
- Offer timer uses server `offer_expires_at`, so a wrong phone clock does not decide the winner.
- Quest progress counts completed trips only. $0 promo trips do not count. Cancelled trips do not advance a quest.
- Duplicate tip rejected. Failed payout shows `error_message`. Duplicate payout reserved by a unique key.
- SOS with no contacts, and SOS with no network, both stay failed and point at 911. They do not show success.
- Heatmap empty, stale, and error. Inbox error versus empty. Second appeal blocked.

## P0 — launch-complete

1. **Saskatchewan eligibility on every go-online.** Reject under-18 (date of birth collected in onboarding), non-Class-5 without `sgi_approved`, under 3 years licensed, and vehicle 10 years or older. Today `enforce_driver_eligibility_recheck` defaults off, and missing `license_class` / `vehicle_year` / `license_issue_date` is left unblocked (`status.py`). The same 10-year rule and the same sentence must appear in `become-driver.tsx` and `vehicle-info.tsx`.
2. **Criminal-record consent before go-online.** If the published consent version is not on file, go-online returns an action that opens `crc-consent`. The become-driver wizard cannot skip that step once the legal text is published.
3. **Service animal.** A driver cannot decline or cancel solely because of a service animal. The offer shows a Service animal badge when the rider set that flag. Safety concerns go to the safety report, which already attaches `ride_id`. Existing audit of the old decline reason stays.
4. **No-show is not a driver cancel.** After arrival and the configured wait, the primary action calls `POST /drivers/rides/{id}/noshow`. Before arrival, "Rider no-show" cannot be submitted. The ride records `cancellation_type=noshow`, Activity shows the fee, and the driver-cancel rate excludes it.
5. **Stops the driver can see.** If the ride has stops, the offer lists them in order. After accept, a rider add or remove updates the driver list, earnings, and navigation target within 5 seconds of `stops_updated`.
6. **Batch offer cannot be abandoned by going offline.** `POST /drivers/status` with `is_online: false` returns 409 while that driver has a pending offer, even when `go_offline_live_offer_guard_enabled` is off. Copy says to decline the offer or finish the trip, not a generic failure toast.
7. **Accept with no network.** If accept gets no HTTP response, the app says the offer may have expired and calls `fetchActiveRide()`. The offer card closes when the server no longer has this driver on the ride.
8. **SOS "I'm OK".** Within 60 seconds of a successful SOS, "I'm OK" sets the incident to `false_alarm`, tells the safety team, and sends no further contact SMS. The button still never opens `tel:911` without a separate tap.
9. **Rejected application re-queues.** A driver with `status=rejected` who resubmits returns to pending review.
10. **Banned or suspended login.** After OTP, those drivers see a deactivation screen with appeal status, not the normal dashboard.
11. **Independent contractor acceptance.** Before the first go-online, the driver accepts a versioned Independent Contractor Agreement. Onboarding copy does not say employee, uniform, or required shift.
12. **Stripe restricted is not "all set".** When `payouts_enabled` is false, the payout screen shows what to fix. Weekly payout copy states the $10 minimum and the weekly schedule.
13. **Offer earnings.** If `driver_earnings` is null, the card does not show `$0.00` as a real fare. Accept stays disabled until a fare is present.

## P1 — before scale

- **Fare dispute and clawback.** Ride detail can open a ticket with `ride_id` and the earnings snapshot. A rider refund after the driver was already paid debits or holds future payouts so payable balance is not overstated.
- **Unsettled trip.** A completed ride that is not `payment_status=paid` shows "Payment processing" and stays out of withdrawable earnings.
- **Subscription expiry mid-trip.** The current trip can finish. The next go-online is blocked with a renew action.
- **Scheduled rides.** An Upcoming list shows accepted scheduled trips. A nudge fires if navigation has not started by the lead time. Cancelling one shows the rider impact.
- **Long trip.** Above a configurable distance (default 50 km), accept is a second explicit tap.
- **Destination mode.** Auto-clears after a configurable number of hours (default 8). A driver with the flag on and no coordinates is treated as not in destination mode.
- **WAV accept guard.** Accept returns 403 when `requires_wav` is true and `is_wav` is false. The card hides Accept.
- **Driver safety check-in.** On a long trip the driver gets an in-app prompt. No response alerts the safety team and does not dial 911. Threshold is configurable. The rider check-in (20 minutes, 90 seconds) is the pattern.
- **Insurance period label.** While online, the dashboard names the current period in plain language (offline, available, on the way, passenger aboard) within 5 seconds of a transition. The label is not an insurance contract.
- **Route deviation.** Enable only after a rule exists for rider-requested detours. One incident per episode, with `ride_id`.
- **Trip-context help.** Help from the active ride or ride detail sends `ride_id`, status, and city.
- **Mid-trip suspension.** Suspend or ban during `in_progress` shows "finish this trip, then contact support". Complete still works. The next go-online returns 403.
- **Rating explanation.** The profile number opens a screen that matches how the backend aggregates ratings. No "rating protection" claim until exclusion logic exists.
- **Low rider rating.** A score of 2 or below requires a reason. Optional "do not match again" is consumed by dispatch for a configurable window.
- **Reconnect copy.** If the socket is down while the server still has the driver online, the pill says reconnecting and not receiving offers.
- **Notification deep links.** `ride_cancelled`, `ride_noshow`, and `ride_update` open ride detail, or "this trip is no longer available".
- **Offline completion.** If complete fails because the network is down while `in_progress`, store the completion and the last fix, and retry on reconnect without a second tap. Required before a highway-heavy service area.
- **Android battery exemption.** First successful go-online on a restricted OEM battery list shows one explainer and the system setting.
- **iOS mock locations.** Client flags spoofed fixes the way Android already flags `mocked`, and the server can reject them.
- **Second device.** A new login ends the other device's online session within 30 seconds with "You signed in on another phone".
- **Document expiry during a trip.** New accepts stop. An `in_progress` trip is allowed to finish. The driver sees "renew before the next trip".
- **WAV supply copy.** When no WAV driver is online, the rider sees unavailable plus a standard-vehicle action. A wait estimate only if the API actually has one.
- **Background-check outcome.** A failed or rejected check produces an in-app reason and the remediation step.
- **Toll receipt.** Driver can submit a toll within 24 hours. Adjustment is capped and audited. Airport zone chip stays a map hint, not a FIFO queue.
- **Arrive while moving.** Auto-arrive requires low speed or a dwell. Manual arrive at the geofence remains.

## P2 — later

Multiple vehicles, CarPlay, child seat, pet, luggage, round trip, pool rides, favorite-area
matching beyond destination mode and the heatmap, in-app T4A download, cash tips, bank-change
wording beyond reopening Stripe, quest toast after a rider cancel, heatmap copy when GPS is
outside the registered service area, QR proof of coverage, SGI quarterly export tooling,
night-ride automatic protections, contact OTP before the first SOS SMS, and low-storage-specific
upload copy.

## Out of scope

Do not add per-trip commission, surge above the 2.5× cap, hidden fees, mandatory shifts,
offline penalties, uniforms, ad SDKs, auto-dial 911, in-trip audio or dashcam, a required
airport queue, a driver-to-rider phone call (chat is the contact path), masked calling, or
instant pay. Payouts stay weekly.

## Build waves

Wave 1 wires behavior that already exists on the server or is a guard on an existing screen:
eligibility on go-online, CRC consent gate, no-show button, batch-offer offline 409,
rejected re-queue, deactivation screen, Stripe restricted banner, null-fare offer guard.
There is no driver-to-rider call button.

Wave 2 is new driver behavior: service-animal badge and blocked refusal, stops on the offer
and on `stops_updated`, SOS false-alarm, accept with no network.

Wave 3 is scale: dispute ticket plus clawback, scheduled Upcoming list, offline completion,
battery prompt, second-device logout. Masked calling and instant pay are out of scope.
