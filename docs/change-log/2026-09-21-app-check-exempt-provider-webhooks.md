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

Add `/api/v1/webhooks/` to `_APP_CHECK_EXEMPT_PREFIXES`. Each handler already authenticates its provider:
- **Stripe:** `stripe.Webhook.construct_event` with the webhook secret; 400 on a bad signature
- **SES via SNS:** RSA signature verification plus a trusted-host check
- **Twilio inbound:** `RequestValidator`, which fails closed when the auth token is set

Alternatives considered:
- `APP_CHECK_ENFORCEMENT=off`. Rejected: it removes mobile attestation everywhere to fix three server-to-server routes.
- Exempting only `/api/v1/webhooks/stripe`. Rejected: SES and Twilio are equally unable to send the header, and both are signature-authenticated.

## 4. Risk & impact on existing functionality

- **Blast radius:** only requests whose path starts with `/api/v1/webhooks/`; no other path's App Check behaviour changes. This is covered by `test_app_endpoints_stay_enforced` and `test_exemption_is_scoped_to_the_webhooks_prefix`.
- **On deploy:** Stripe resumes delivery, including retries still inside its ~3-day window. Handlers claim each event through `claim_stripe_event` before processing, so retried or resent events are idempotent.
- **A burst of backlogged events** will be processed on deploy. They are money-moving handlers (payment success, refunds, disputes, Connect account updates), and they are processed as they would have been when first delivered. Monitor `spinr_payment_settlement_total` and Sentry `domain:payments` after deploy.

## 5. User-experience effect

- Rider/driver: payment-state changes driven by webhooks (e.g. 3-D Secure completion, refunds, disputes, Connect onboarding status) resume. Anything stuck since 2026-09-17 may update when the backlog lands.
- Admin: the Stripe events screen starts filling again.
- Visible mid-session: yes, as backlogged events are processed.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/core/middleware.py` | `/api/v1/webhooks/` added to `_APP_CHECK_EXEMPT_PREFIXES` | Providers can't send App Check tokens |
| `backend/tests/test_appcheck_webhooks_exempt.py` | New | Real middleware with enforcement on: webhook paths reach the handler; app paths still 401 |

## 7. Before / after

```python
# Before
    "/api/v1/auth/send-otp",
    "/api/v1/auth/verify-otp",
)
# After
    "/api/v1/auth/send-otp",
    "/api/v1/auth/verify-otp",
    "/api/v1/webhooks/",
)
```

## 8. Rollback plan

Revert the one-line exemption (redeploy). This restores the broken state: webhooks blocked again. Nothing is written by the change itself. The events processed after deploy are legitimate provider events and are not reverted.

## 9. Verification performed

- [x] Live probe before the fix: `POST https://api-spinr.spinr.ca/api/v1/webhooks/stripe` returned `App Check token required`. Fly logs showed repeated Stripe deliveries at 401 (17:24–17:25 UTC).
- [x] `stripe_events` read-only query: last `received_at` 2026-09-17 03:15:29 UTC; per-day counts 09-13..09-17, then none.
- [x] `pytest tests/test_appcheck_webhooks_exempt.py tests/test_appcheck_portal_exempt.py`: 8 passed.
- [x] Red check: with the exemption removed, the 3 webhook tests and the prefix test fail. The app-path test still passes, since it doesn't depend on the entry.
- [x] `ruff check` and `ruff format --check` clean.

## 10. What was NOT verified / required follow-up

- **Not verified in production yet.** After deploy, re-probe: an unsigned POST should now get `400 Invalid signature` / `Missing signature`, not the App Check 401. Then confirm new `stripe_events` rows arrive.
- **Missed events (manual, in the Stripe Dashboard):** Developers → Webhooks → the production endpoint.
  1. If Stripe has **disabled** the endpoint after the repeated failures, re-enable it.
  2. **Resend** every failed event since 2026-09-17 03:15 UTC. Events older than Stripe's automatic retry window (~3 days) will not be retried on their own. Resending is safe: `claim_stripe_event` de-duplicates.
- Same review for SES (the AWS SNS subscription may have backed off) and Twilio (inbound messages during the gap were not delivered).
- Not load-tested for the backlog burst.
