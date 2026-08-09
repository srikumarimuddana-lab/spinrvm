# Notification channel coverage — who gets told what, and how

**Audited 2026-08-08.** Every rider- and driver-facing event in the product,
with the channels that actually fire for it.

The question that prompted this: are drivers and riders alerted **by email** for
signup, verification, admin actions, document expiry, and ride completion — and
do those emails carry Spinr branding?

The short answer at audit time was no. Push carried nearly everything (~97 call
sites); email existed for 14 flows, only one of which was a rider lifecycle
event (the ride receipt). No Spinr email contained the Spinr logo.

Part C records what has since been fixed — the driver surface first, then the
rider surface. Part D is what remains open.

**The tables below show the state after those fixes**, so a ✅ in the Email
column may be new. Anything still ❌ is a real, current gap.

## How to read this

- **Push** — FCM/Expo via `features.send_push_notification`. Also writes the
  in-app inbox row automatically (`_record_inbox_notification`), so the two are
  coupled: an event with no push has no inbox row either. Exceptions are
  `new_ride_assignment` and `live_activity`, deliberately excluded as transient.
- **Email** — `utils/email_provider.send_transactional_email` (AWS SES
  ca-central-1 primary → Resend fallback, suppression gate, `email_send_log`).
- **SMS** — Twilio. Reserved for OTP, corporate guest bookings, and SOS
  emergency contacts. Not a general lifecycle channel, by design.

✅ covered · ⚠️ partial or broken · ❌ not covered

---

## Part A — Driver

### A1. Signup & verification

| # | Scenario | Where | Push | Email |
|---|---|---|---|---|
| D1 | Driver registers | `routes/drivers/profile.py:284` | ❌ | ❌ |
| D2 | Onboarding nudge (pending only, daily 08:00, cap 7/type) | `utils/driver_onboarding_reminders.py:176` | ✅ | ❌ |
| D3 | Driver uploads a document → `needs_review` | `documents.py:284` | ✅ | ❌ |
| D4 | "We received your document" acknowledgement | — | ❌ | ❌ |
| D5 | Admin approves a document (can flip `needs_review`→`active`) | `routes/admin/documents.py:416` | ✅ | ✅ |
| D6 | Admin rejects a document | `routes/admin/documents.py:485` | ⚠️ | ❌ |
| D7 | Driver verified | `routes/admin/drivers.py:1544` | ✅ | ✅ |
| D8 | Driver un-verified | `routes/admin/drivers.py:1544` | ✅ | ✅ |
| D9 | Profile photo approved / rejected | `routes/admin/drivers.py:1595` | ✅ | ❌ |

D1 is deliberate for push — the app shows the next step — but there is still no
welcome or what-happens-next email. D6 fires only if the admin ticks `notify`.

### A2. Document expiry

Loop: `utils/document_expiry.py`, every 12 h, 7-day warning window, replay-safe
via a CAS on `drivers.doc_expiry_warned_at`. Covers licence, insurance, vehicle
inspection, background check, and work eligibility.

| # | Scenario | Where | Push | Email |
|---|---|---|---|---|
| D10 | Expiring in 2–7 days | `utils/document_expiry.py:225` | ✅ | ✅ |
| D11 | Expiring in 1 day | same | ✅ | ✅ |
| D12 | Expires today | same | ✅ | ✅ |
| D13 | Expired → auto-suspended, kicked offline | `utils/document_expiry.py:168` | ✅ | ✅ |
| D14 | Admin manual expiry nudge | `routes/admin/drivers.py:1259` | ✅ | ❌ |

### A3. Account status & admin actions

Policy: `utils/driver_status_notifications.py`, one sender
(`notify_driver_status_change`) shared by every call site.

| # | Scenario | Push | Email | Note |
|---|---|---|---|---|
| D15 | Approved / restored / reactivated | ✅ | ✅ | `normal` tier |
| D16 | Rejected (with reason) | ✅ | ✅ | `account` tier |
| D17 | Suspended (with reason) | ✅ | ✅ | `account` tier |
| D18 | Banned | ✅ | ✅ | `account` tier; reason withheld by design |
| D19 | `needs_review` | ✅ | ❌ | Push-only on purpose — fires on every vehicle edit and doc re-upload |
| D20 | Moved back to `pending` | ❌ | ❌ | `status_message()` returns `None` |
| D21 | **Auto-reactivation when `suspended_until` lapses** | ❌ | ❌ | `utils/suspension_reactivation.py` — driver never learns they can work again |
| D22 | User-level ban/suspend | ⚠️ | ❌ | `routes/admin/users.py:302` — different copy and tier from D17 for the same human event |
| D23 | Generic `PUT /admin/drivers/{id}` | ❌ | ❌ | Even for status-adjacent fields |
| D24 | **Stripe Connect KYC blocks payouts** | ❌ | ❌ | `routes/webhooks.py:1297` — payouts blocked, driver not told |
| D25 | Background-check status change | ❌ | ❌ | No dedicated notification |
| D26 | Service-area assignment | ❌ | ❌ | |
| D27 | Admin wallet credit / debit | ❌ | ❌ | `routes/admin/wallet.py` has zero notification calls |

### A4. Rides & earnings

| # | Scenario | Where | Push | Email |
|---|---|---|---|---|
| D28 | Ride offer | `routes/rides/matching.py:911` | ✅ | ❌ |
| D29 | **Rider cancels an assigned ride** | `routes/rides/cancellation.py:356` | ❌ | ❌ |
| D30 | Ride completed / fare earned | `routes/drivers/ride_complete.py:895` | ❌ | ❌ |
| D31 | Cancellation fee earned | `services/cancellation_service.py:206` | ✅ | ❌ |
| D32 | Payout requested / created | `routes/drivers/payouts.py:613` | ❌ | ❌ |
| D33 | `payout.paid` — money landed | `routes/webhooks.py:1615` | ❌ | ❌ |
| D34 | `payout.failed` | `routes/webhooks.py:1657` | ✅ | ❌ |
| D35 | Payout retries exhausted | `utils/payment_retry.py:183` | ⚠️ | ❌ |
| D36 | Weekly / monthly earnings statement | `utils/driver_statement_job.py:301` | ❌ | ✅ |
| D37 | T4A annual slip | `utils/t4a_annual_job.py:130` | ✅ | ✅ |
| D38 | Tax export / DSAR data export | `routes/drivers/tax_exports.py:168` | ❌ | ✅ |
| D39 | Spinr Pass invoice | `routes/drivers/subscriptions.py:1125` | ❌ | ✅ |
| D40 | Spinr Pass expiring 3 d / 24 h / expired | `routes/drivers/subscriptions.py:1742` | ✅ | ❌ |
| D41 | Quests / incentives progress | `utils/quest_tracker.py` | ❌ | ❌ |
| D42 | Referral payout | `utils/referral_payout.py` | ❌ | ❌ |
| D43 | Lost-item report assigned | `routes/admin/vehicle_fleet.py:541` | ⚠️ | ❌ |

D29 is WebSocket-only: a driver en route to pickup with a backgrounded app is
not notified that the ride was cancelled. D30 notifies the rider only — the
driver gets nothing when a ride completes. D33's `payout_processed` type is
declared in `NOTIFICATION_DEEPLINKS` but never emitted.

---

## Part B — Rider

### B1. Signup & account

| # | Scenario | Where | Push | Email |
|---|---|---|---|---|
| R1 | OTP send | `routes/auth.py:449` | — | ❌ (SMS, correct) |
| R2 | OTP verify → new account created | `routes/auth.py:1087` | ❌ | ❌ |
| R3 | Profile creation (where email is collected) | `routes/users.py:69` | ❌ | ✅ |
| R4 | Welcome email | `routes/users.py:139` | ❌ | ✅ |
| R5 | Email verification | — | ❌ | ❌ |
| R6 | Profile change | `routes/users.py:69` | ❌ | ❌ |
| R7 | **Email-address change** | `routes/users.py:143` | ❌ | ✅ |
| R8 | New-device login / logout-all | `routes/auth.py:1792` | ❌ | ❌ |
| R9 | Account deletion request (PIPEDA) | `routes/users.py:297` | ❌ | ✅ |
| R10 | **Rider DSAR data export** | `routes/users.py:152` | ❌ | ❌ |
| R11 | Admin changes rider account status | `routes/admin/users.py:302` | ✅ | ❌ |

R2 is account *creation* at OTP verify, before an email address exists — R3/R4
is the first point a rider is reachable, and that is where the welcome now
fires. R5 remains open: `users.email_verified` is set `false` and never
verified for riders, because the `verify-email-otp` endpoint belongs to the
corporate portal, not the rider app. R6 (name/gender edits) stays silent by
design; only an address change is security-relevant. R10 still records a
request with a 30-day SLA and never assembles data or sends anything — the
working export exists driver-side (`routes/drivers/tax_exports.py:668`) but the
rider app does not call it.

### B2. Ride lifecycle

| # | Scenario | Where | Push | Email |
|---|---|---|---|---|
| R12 | Ride booked / confirmed | `routes/rides/booking.py:1199` | ❌ | ❌ |
| R13 | Corporate guest booking | `services/guest_notification_service.py:167` | ✅ | ❌ |
| R14 | Driver assigned | `routes/drivers/ride_flow.py:413` | ✅ | ❌ |
| R15 | Driver en route / arriving | — | ❌ | ❌ |
| R16 | Driver arrived | `routes/drivers/ride_flow.py:623` | ✅ | ❌ |
| R17 | Ride started | `routes/drivers/ride_flow.py:690` | ✅ | ❌ |
| R18 | Ride completed (driver taps) | `routes/drivers/ride_complete.py:895` | ✅ | ❌ |
| R19 | **Ride completed (rider taps)** | `routes/rides/lifecycle.py:126` | ❌ | ❌ |
| R20 | Ride cancelled by driver | `routes/drivers/ride_cancel.py:174` | ✅ | ❌ |
| R21 | No-show fee charged | `routes/drivers/ride_cancel.py:383` | ✅ | ✅ |
| R22 | Auto-cancel, no drivers found | `routes/rides/matching.py:1347` | ✅ | ❌ |
| R23 | Driver chat message | `routes/rides/chat.py:176` | ✅ | ❌ |
| R24 | "Rate your trip" prompt | `routes/rides/rating.py:126` | ❌ | ❌ |

Push is the right channel for R14–R18 — email would be wrong for an in-ride
event. R19 is an asymmetry: the rider-initiated completion path sends the rider
nothing, while the driver-initiated one does.

### B3. Receipts & payments

| # | Scenario | Where | Push | Email |
|---|---|---|---|---|
| R25 | **Ride receipt** | `utils/email_receipt.py:442` | ❌ | ✅ |
| R26 | **Corporate guest ride receipt** | `services/payment_service.py:745` | ❌ | ✅ |
| R27 | Card declined at settlement | `services/payment_service.py:1108` | ✅ | ❌ |
| R28 | Stripe `payment_intent.payment_failed` | `routes/webhooks.py:911` | ✅ | ❌ |
| R29 | Refund processed | `routes/webhooks.py:1078` | ✅ | ✅ |
| R30 | Wallet top-up | `routes/webhooks.py:736` | ✅ | ✅ |
| R31 | Wallet debits / credits generally | `routes/wallet.py` | ❌ | ❌ |
| R32 | **Retries exhausted → rider blocked from booking** | `utils/payment_retry.py:130` | ⚠️ | ✅ |
| R33 | Promo applied / expiring / earned | `routes/promotions.py` | ❌ | ❌ |
| R34 | Loyalty tier change | `routes/loyalty.py` | ❌ | ❌ |

R25 is sent at **settlement**, not at completion, and carries HTML + a PDF +
an optional route PNG. GST and PST appear as separate line items
(`utils/email_receipt.py:132-151`, driven by the persisted `ride.tax_breakdown`),
satisfying the receipt requirement in CLAUDE.md.

R26 previously produced no receipt at all: a guest never calls
`/process-payment`, so `auto_settle_guest_corporate` was the only settlement
path with no receipt hook. A phone-only guest still has no email on file and is
skipped silently.

R27/R28 stay push-only deliberately — a declined card is a prompt to act inside
the app, and the rider is mid-session when it fires. R32 is what matters after
that: when retries are finally exhausted the rider is blocked from booking, and
that notice used to reach **admins only**.

### B4. Scheduled rides, safety, corporate

| # | Scenario | Where | Push | Email |
|---|---|---|---|---|
| R35 | Scheduled ride T-10 min reminder | `utils/scheduled_rides.py:533` | ✅ | ❌ |
| R36 | Scheduled dispatch starting / delayed / on hold | `utils/scheduled_rides.py:489` | ✅ | ❌ |
| R37 | Scheduled ride booked confirmation | — | ❌ | ❌ |
| R38 | Rider SOS | `routes/rides/safety.py:37` | ❌ | ❌ |
| R39 | Safety check-in (20 min in) | `utils/safety_checkin_loop.py:111` | ✅ | ❌ |
| R40 | Safety incident reported | `routes/safety.py:122` | ❌ | ❌ |
| R41 | Invited to a corporate account | `routes/corporate_company.py:214` | ❌ | ✅ |
| R42 | Invite claimed / membership activated | `routes/auth.py:526` | ❌ | ❌ |
| R43 | Monthly allowance reset | `utils/allowance_reset.py` | ❌ | ❌ |
| R44 | Allowance exhausted | `routes/rides/booking.py:760` | ❌ | ❌ |
| R45 | Removed from corporate account | `services/corporate_member_offboarding_service.py:136` | ⚠️ | ❌ |

T-10 min is the only scheduled-ride reminder tier — there is no T-1 h or T-24 h.
R38 fans out to emergency contacts (SMS) and the safety team (email); the rider
who triggered it gets no confirmation. R44 surfaces only as a 4xx at booking time.

---

## Part C — Fixed in the 2026-08-08 branch

**Driver:**

| ID | Was | Now |
|---|---|---|
| D5 | Document approval silent, even when it reactivated the driver | Push + email via the shared policy |
| D7/D8 | Verify/unverify sent inline, no `deleted_at` guard | Routed through the policy; push + email |
| D10–D13 | Document expiry push-only, all four tiers | Push + email, inside the existing replay claim |
| D13 | Suspension push on the default tier — an opted-out driver got nothing | `account` tier |
| D15–D18 | Approval/rejection/suspension/ban push-only | Push + email |

**Rider:**

| ID | Was | Now |
|---|---|---|
| R4 | No welcome email; nothing ever confirmed the address on file worked | Welcome on first profile completion |
| R7 | Address could be changed with no notice to either side — a silent account-takeover vector | Security notice to the **old** address |
| R9 | Deletion request confirmed nothing, and the account locks immediately so the in-app text can't be re-read | Written confirmation incl. the 7-year retention carve-out and purge date |
| R21 | No-show fee push-only — the most disputable charge with no written record | Push + email |
| R26 | Corporate guest rides settled with **no receipt at all** | Receipt, same guard as the Meta conversion hook |
| R29 | Refund push-only | Push + email |
| R30 | Wallet top-up push-only | Push + email, gated on the credit not being a dedup hit |
| R32 | Retries exhausted notified **admins only**; the blocked rider was told nothing | Rider email + the existing admin alerts |

**Cross-cutting:**

| ID | Was | Now |
|---|---|---|
| X1 | `notification_preferences.email_enabled` read by nothing | Honoured for OPTIONAL-class mail |
| Branding | No email contained the Spinr logo | `utils/email_layout.py` + `routes/branding.py` |

New modules: `utils/email_layout.py` (branded HTML + plain-text),
`utils/email_notifications.py` (channel policy), `utils/rider_emails.py` (rider
copy), `routes/branding.py` (logo URL).
Kill switch: `app_settings.lifecycle_emails_enabled` (migration 286) — covers
every email routed through the policy layer, which is all of the above except
R26 (that reuses the pre-existing receipt pipeline).

**Every new email is TRANSACTIONAL** — a financial record, a security notice, or
a regulatory confirmation. Under CASL those are implied-consent messages a
preference toggle must not suppress. That means the `OPTIONAL` class still has
**no production caller**: it is wired and tested, but nothing ships as
suppressible yet. Digests and reminders are its intended first users.

---

## Part D — Open findings

Not addressed in the 2026-08-08 branch. Tracked in `ACTION_ITEMS.md`.

| ID | Finding |
|---|---|
| X2 | Five preference columns (`sms_enabled`, `ride_updates`, `promotions`, `safety_alerts`, `earnings_summary`) are persisted, surfaced in the app, and read by nothing |
| X3 | `users.email` is nullable; any account that abandoned profile setup is permanently unreachable by email |
| X4 | `utils/receipt_email.py` is dead code with hardcoded `_GST_RATE=0.05` / `_PST_RATE=0.06` instead of reading the service area — will silently mis-tax if wired up. Confusingly named against the live `utils/email_receipt.py` |
| X5 | Most rider pushes omit `target_app="rider"`, falling back to the legacy `users.fcm_token` column (`features.py:1664`) |
| X6 | Two push call sites pass the wrong ID type and are silently dropped: `utils/payment_retry.py:183,412` passes `drivers.id`/`ride.driver_id` where `user_id` is required; `routes/admin/vehicle_fleet.py:541` passes `fcm_token` |
| R5 | Rider email addresses are never verified — `email_verified` is written `false` and nothing ever flips it. The welcome email is now a *de facto* deliverability check, but not a verification flow |
| R8 | No "new device signed in" alert on login or logout-all |
| R10 | Rider DSAR export assembles no data and sends no email — a PIPEDA obligation |
| R19 | Rider-initiated ride completion sends the rider nothing, unlike the driver-initiated path |
| R31/R33/R34 | Wallet debits, promos and loyalty tier changes have zero notification calls of any kind |
| R35 | Scheduled rides have one reminder tier (T-10 min) and no booking confirmation |
| R38 | Rider SOS sends the rider no confirmation that help was alerted |
| R43/R44 | Corporate allowance reset and exhaustion are silent; exhaustion surfaces only as a 4xx at booking |
| D24 | Stripe Connect KYC blocking payouts notifies nobody |
| D29 | Rider-cancels-assigned-ride reaches the driver by WebSocket only |
| D21 | Auto-reactivation after `suspended_until` lapses is silent |

## Branding status

| Requirement | Status |
|---|---|
| Logo in emails | ✅ everywhere, from the bundled asset or an admin-set `company_logo_url` — including the ride receipt and Spinr Pass invoice, retrofitted behind `branded_receipt_enabled` |
| Company name / address / contact | ✅ from the admin Settings page via `utils/company_details.py`, falling back to `report_branding`'s constants when unset. Covers every email **and** the receipt/invoice PDF attachments. Report PDFs (SGI, airport, compliance) still use their own constants deliberately |
| Brand red | `#FF3B30` per `.claude/context/brand-spinr.md` across every email including the retrofitted receipt and invoice. Report PDFs keep `#ee2b2b` (ADR-008) |
| Shared shell | ✅ **every email**. Directly via `render_email` / `render_from_text`, or indirectly — `features.send_email` wraps a plain-text `body` in the shell when the caller passes no `html`, which covers driver statements, corporate low-balance, tax/DSAR exports and safety-team alerts. Only marketing email is exempt, and deliberately: its CASL footer is a legal requirement with its own shape |
| Plain-text alternative | ✅ for new emails and the receipt (which carries the same GST/PST breakdown). The invoice email is still HTML-only |
| Typography | Plus Jakarta Sans leads a system stack. Webfonts are unreliable in email (Outlook ignores `@font-face`), so this degrades by design |
| Test coverage | ✅ `tests/test_email_layout.py`, `tests/test_receipt_shell_snapshot.py`, and `tests/test_all_emails_are_branded.py` — the last walks every send site in `backend/` and fails if one bypasses the layout without an argued allowlist entry, so "all emails are branded" is a check rather than a claim |

**Standing gap:** there is no automated visual or snapshot regression tooling
for email in this repo. Client rendering (Gmail, Apple Mail, Outlook) is
verified by hand or not at all.

## Retrofit — done

Every email now renders through the shared layout.

- **Receipt and Spinr Pass invoice** (2026-08-08) — behind
  `branded_receipt_enabled` (migration 288, defaults on), with the
  pre-retrofit shell kept verbatim and pinned by
  `tests/test_receipt_shell_snapshot.py`, so the flag's off-position is a real
  rollback rather than an untested branch.
- **The remaining senders** (2026-08-09) — corporate OTP, member invite, KYB
  decision, signup ops alert, admin broadcast, T4A/DSAR exports directly;
  driver statements, corporate low-balance and safety-team alerts indirectly
  through `features.send_email`. No flag: the alternative to a branded email
  here is the bare `<p>` it replaced, so there is no half-state worth having
  and `git revert` restores it exactly.

What settings do **not** drive: the product name in prose ("Open the Spinr
driver app"). `company_name` holds the legal entity, which does not read well
mid-sentence. See N17 in `ACTION_ITEMS.md`.
