# PR 5722 review remediation plan

> Execute sequential, independently tested commits of at most three owned files per slice. The ten findings in the September 23 review are the specification. No production writes or deployment.

Goal: fix refund accounting/recovery/reporting, driver dispute handling, SOS cancellation, session displacement and pending offers, offline completion, offer timers and no-show timing.

Architecture: preserve existing atomic payment projection; extend its transaction for capped attributable refund adjustments behind an explicit rollout flag. Use existing supported safety resolution states. Bind displaced sessions durably. Do not claim offline trip completion until the server acknowledges it. Keep new interfaces additive.

Global constraints: Decimal money; service-role SQL with pinned search_path; append-only migrations; preserve driver/rider ownership and insurance periods; fail visibly; do not touch outer checkout's rider edits. Each implementation slice has red regression, minimal fix, green targeted suite, specialist review, named-file commit before its next slice. TodoWrite is unavailable; this checklist and commit ledger are the tracker.

## Tasks / file boundaries

- [ ] M1: offline completion: driver-app/store/driverStore.ts, hooks/useDriverDashboard.ts, store/__tests__/driverStore.test.ts. Keep active ride and explicit error; remove automatic delayed GPS replay. Verify transport failure cannot create terminal success.
- [ ] M2: offer hold: store and store tests. Bind hold to the offer lifecycle; reset/new offer/decline and empty recovery cannot freeze offers.
- [ ] A1: auth binding schema/helper/tests (choose exact existing refresh helper after inspection). Persist session binding, reject displaced refresh including races; test legacy and new devices.
- [ ] A2: auth login/refresh integration and tests (auth.py + at most two tests). Preserve scope and simultaneous-login semantics; fail closed on revocation failures.
- [ ] A3: pending offer cleanup: auth.py + tests + SQL only if needed. Atomic guards preserve accepted trips/insurance and release assigned/pending claims without user-decline penalties.
- [ ] M3: server-driven logout local teardown: hook + hook test. Old device must not offline or revoke the new session.
- [ ] S1: safety.py + safety test + shared SOS client if needed. Persist supported closed/resolution semantics, preserve ownership and 60s window, show failures instead of clearing UI.
- [ ] S2: shared SOS test + remaining shared client change (if S1 needs backend-only test slice). Verify failed cancellation remains visible and retryable.
- [ ] N1: driver ride_reads.py + backend no-show test. Return authoritative eligible_at and server_now in active ride payload using mutation's exact area/settings policy.
- [ ] N2: ActiveRidePanel.tsx + panel test. Countdown from authoritative deadline, including non-300s waits.
- [ ] P1: new atomic hold SQL helper/schema + native SQL regression tests (at most three files). Attribute holds to ride; cumulative cap; replay/concurrency; historical refund safety; default-off rollout.
- [ ] P2: append-only refund projection replacement + native tests (at most three files). Same transaction as ledger; all callers use it, failed hold rolls back accounting. Update refund metadata consistently.
- [ ] P3: webhooks.py + hold tests. Remove separate unsafe writer. Verify refund.updated, charge.refunded and worker all reach shared atomic projection.
- [ ] P4: disputes.py + dispute tests. Driver claims stay open for manual earnings review; never refund passenger or fabricate driver compensation. Rider refund path preserved; correct notification audience.
- [ ] P5: earnings.py + balance tests. Holds reduce payable, not transferred total; expose adjustment total separately.
- [ ] P6: driver_statement.py + statement tests. Label refund adjustments, exclude from cash transfer totals; inspect other reporting consumers.
- [ ] Final: complete change-impact log and PR body; run affected backend/mobile suites and available production build check; final money/security/migration review, push PR branch and verify remote SHA.

## Decisions and verification boundaries

- Removing unsafe queued completion is preferred over inventing client-controlled billing timestamps. Offline completion stays pending on screen; no delayed automatic position/time substitution.
- Driver earnings compensation has no general existing adjustment writer. Keep the claim open with an explicit manual-review response rather than reuse rider refunds or invent bonuses.
- Prefer extending existing locked refund RPC over a Python read/sum/write, which is neither atomic nor replay-safe.
- Preserve completed adjustment audit rows; rollback uses feature flags and compensating accounting, never deletion of financial history.
- Review race cases: refund replay/order, concurrent partial refunds, login vs refresh, accept vs session cleanup, offline completion vs server success/lost response.
- No live Stripe/Supabase/OTP calls. Native SQL test availability and mobile build limitations must be stated.

## Progress / rulings

Initial PR head: 07e2cc99e59945fdff7f1837158072d2f54d54ea. Independent checkout protects concurrent outer work. Specialists own disjoint code; root alone coordinates commits and final push.
