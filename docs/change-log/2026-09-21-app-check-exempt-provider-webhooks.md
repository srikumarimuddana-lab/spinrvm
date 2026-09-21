# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-21 |
| Author | agent |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | (uncommitted at write time) |
| Related issue or gap ID | Stripe / SES / Twilio webhooks rejected by App Check since 2026-09-17 |

## 1. Issue / gap identified

Production has rejected every provider webhook since App Check enforcement came on: `POST /api/v1/webhooks/stripe` returns `401 {"detail":"App Check token required"}`. `stripe_events` shows the **last received webhook at 2026-09-17 03:15:29 UTC**, and 0 in the last 2 days, against a normal 80–100/day. SES bounce/complaint and Twilio inbound webhooks share the router and are blocked the same way.

## 2. Root cause

`FirebaseAppCheckMiddleware` requires `X-Firebase-AppCheck` on every path not in `_APP_CHECK_EXEMPT_PREFIXES`. The webhook router (`routes/webhooks.py`, mounted at `/api/v1/webhooks`) was never added. Stripe, SNS and Twilio call from their own servers and can never attach an App Check token. The gap was latent until enforcement came on: `ENV=production` on 2026-09-17, then the kill switch was removed around 2026-09-19.

## 3. Fix / remediation

Add `/api/v1/webhooks/stripe` and `/api/v1/webhooks/twilio-inbound` to `_APP_CHECK_EXEMPT_PREFIXES`. Each authenticates its provider:
- **Stripe:** `stripe.Webhook.construct_event` with the webhook secret; 400 on a bad signature, 500 if the secret is unset (fails closed).
- **Twilio inbound:** `RequestValidator`. It is enforced only while `settings.twilio_auth_token` is set, which a read-only check confirmed is true in production on 2026-09-21.

**`/api/v1/webhooks/ses` is deliberately left enforced.** Its SNS signature check proves the message came from *some* SNS topic, and its topic allowlist fails open while `settings.aws_ses_sns_topic_arn` is blank, which it is in production (same check). Exempting it now would let anyone subscribe our URL to their own topic and post forged bounces or complaints, suppressing email to arbitrary addresses. Exempt it in a one-line follow-up once the ARN is configured. The user chose this scope on 2026-09-21.

Alternatives considered:
- `APP_CHECK_ENFORCEMENT=off`. Rejected: it removes mobile attestation everywhere to fix three server-to-server routes.
- The whole `/api/v1/webhooks/` prefix. Rejected: it would include SES, whose topic check is open in production (see above).

## 4. Risk & impact on existing functionality

- **Blast radius:** only `/api/v1/webhooks/stripe` and `/api/v1/webhooks/twilio-inbound`. SES and every app path keep App Check; `test_ses_and_app_endpoints_stay_enforced` pins both.
- **On deploy:** Stripe resumes delivery, including retries still inside its ~3-day window. Handlers claim each event through `claim_stripe_event` before processing, so retried or resent events are idempotent.
- **A burst of backlogged events** will be processed on deploy. They are money-moving handlers (payment success, refunds, disputes, Connect account updates), and they are processed as they would have been when first delivered. Monitor `spinr_payment_settlement_total` and Sentry `domain:payments` after deploy.

## 5. User-experience effect

- Rider/driver: inbound SMS replies (STOP/START) resume. Payment-state changes driven by webhooks (e.g. 3-D Secure completion, refunds, disputes, Connect onboarding status) resume. Anything stuck since 2026-09-17 may update when the backlog lands.
- Admin: the Stripe events screen starts filling again.
- Visible mid-session: yes, as backlogged events are processed.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/core/middleware.py` | `/api/v1/webhooks/stripe` and `/twilio-inbound` added to `_APP_CHECK_EXEMPT_PREFIXES` | Providers can't send App Check tokens |
| `backend/tests/test_appcheck_webhooks_exempt.py` | New | Real middleware with enforcement on: Stripe and Twilio reach the handler; SES and app paths still 401 |

## 7. Before / after

```python
# Before
    "/api/v1/auth/send-otp",
    "/api/v1/auth/verify-otp",
)
# After
    "/api/v1/auth/send-otp",
    "/api/v1/auth/verify-otp",
    "/api/v1/webhooks/stripe",
    "/api/v1/webhooks/twilio-inbound",
)
```

## 8. Rollback plan

Revert the two exemption lines (redeploy). This restores the broken state: webhooks blocked again. Nothing is written by the change itself. The events processed after deploy are legitimate provider events and are not reverted.

## 9. Verification performed

- [x] Live probe before the fix: `POST https://api-spinr.spinr.ca/api/v1/webhooks/stripe` returned `App Check token required`. Fly logs showed repeated Stripe deliveries at 401 (17:24–17:25 UTC).
- [x] `stripe_events` read-only query: last `received_at` 2026-09-17 03:15:29 UTC; per-day counts 09-13..09-17, then none.
- [x] `pytest tests/test_appcheck_webhooks_exempt.py tests/test_appcheck_portal_exempt.py`: all pass (see PR).
- [x] Red check: with the exemption removed, the webhook tests fail. The enforced-path tests still pass.
- [x] Production settings (read-only, booleans only): `twilio_auth_token` set = true; `aws_ses_sns_topic_arn` set = false. That is why SES is excluded.
- [x] `spinr-security-auditor` review: SHIP. It flagged the SES topic fail-open and the Twilio blank-token skip as pre-existing gaps. SES is now excluded; Twilio's token is confirmed set.
- [x] `ruff check` and `ruff format --check` clean.

## 10. What was NOT verified / required follow-up

- **Not verified in production yet.** After deploy, re-probe: an unsigned POST should now get `400 Invalid signature` / `Missing signature`, not the App Check 401. Then confirm new `stripe_events` rows arrive.
- **Missed events (manual, in the Stripe Dashboard):** Developers → Webhooks → the production endpoint.
  1. If Stripe has **disabled** the endpoint after the repeated failures, re-enable it.
  2. **Resend** every failed event since 2026-09-17 03:15 UTC. Events older than Stripe's automatic retry window (~3 days) will not be retried on their own. Resending is safe: `claim_stripe_event` de-duplicates.
- **SES follow-up:** set `aws_ses_sns_topic_arn` in admin settings, then exempt `/api/v1/webhooks/ses`. Bounce/complaint feedback stays unprocessed until then, which risks SES sender reputation. The AWS SNS subscription may also have backed off during the gap.
- **Hardening follow-up (ticket):** make the Twilio handler and the SNS topic check fail closed when their setting is blank in production.
- Twilio inbound messages during the gap were not delivered.
- Not load-tested for the backlog burst.
