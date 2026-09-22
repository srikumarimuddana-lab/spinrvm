# Proposed title

Fix ride reliability across session refresh, dispatch, payments, and notifications

# Proposed description

The September 22 rides exposed a deterministic refresh-contract failure, unsuccessful matching, an Android notification crash, and payment/insurance edge cases. These affect a driver's ability to stay available and a rider's ability to complete consecutive rides safely.

This branch incorporates the refresh fixes from #5709 and implements the confirmed findings discussed in #5712:

- Validate and persist refreshed credentials correctly; keep compatibility with installed clients.
- Distinguish explicit logout from suspicious refresh replay, preserving theft detection and adding logout-all audit/scope clarity.
- Exclude a rider's own driver profile before offers are claimed.
- Cancel only pending offers and release availability/insurance transactionally when the cancelled offer still owns the driver. Prevent late pending offers and stale Period-2 writes after cancellation.
- Block a second cancellation charge while a captured-hold refund is unresolved.
- Reject unchargeable 1–49¢ tip overflow before capture and let riders explicitly edit it.
- Record exact split PaymentIntent components, defer early settlement webhooks, and stamp `paid_at` without regressing canonical driver earnings.
- Remove the implicated horizontal notification-tab lists from both apps while retaining filtering and accessible controls.

## Verification

- Backend integration runs: 176 authentication/payment/webhook tests; 117 dispatch/insurance/cancellation tests. The final metadata-contract correction passed all 17 success-webhook tests.
- Rider: 102 affected tests. Driver: 163 auth/profile tests, 27 notification tests, and 33 ActiveRidePanel tests after a baseline-confirmed mock isolation repair.
- Actual migrations 441–443 executed together in disposable PGlite PostgreSQL 18.3; ownership, state, idempotency, earnings, ledger component, and service-role ACL checks passed.
- Both integrated Android JavaScript/Hermes exports passed after the repository's production dependency patches were applied. Notification suites also passed against patched dependencies.
- GPT-6 Luna architecture and independent security reviews completed; all identified blockers addressed.

## Draft release gates

This PR must remain draft until staging/native validation is complete. No production deployment, database write, real charge, refund, or setting change was performed for this work.

1. Apply and verify migrations 441, 442, and 443 with the migration runner before backend rollout. The deployment workflow does not run them automatically.
2. Run the native PostgreSQL integration tests; local PGlite checks do not establish two-session lock scheduling.
3. Build signed apps and reproduce the notification flow on the reported Android device/build class. A JavaScript export does not establish the native crash is resolved.
4. Run two consecutive staged rides across token expiry, background/resume, network interruption, and cancellation/accept races. Validate payments in Stripe test mode.

Historical unknown revocations and ledger/insurance rows are not backfilled. Current source does not establish the historical Meta `extinfo` failure or explain every no-offer decision. See `docs/audit/2026-09-22-ride-reliability-fix-report.md` for evidence limits and the per-domain change-impact/rollback records.
