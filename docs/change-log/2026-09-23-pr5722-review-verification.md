# PR 5722 review fixes and verification

## Scope and review coverage

All fourteen review findings were checked against the code and addressed. Changes
remain on the PR branch; no deployment, merge, production data mutation, or rollout
flag activation was performed. Domain work and independent review used GPT-6 Luna.

| Review comment | Correction |
|---|---|
| 4083700733 | Resolve a false-alarm SOS using the schema-supported resolved status and preserve resolution attribution. |
| 4083700739 | Revoke displaced mobile refresh credentials atomically with the driver session generation, including alternate login flows. |
| 4083700750 | Clear the SOS UI only after confirmed server acknowledgement; preserve retry state on failure in both apps. |
| 4083700760 | Accept eligible legacy NULL-generation refresh rows while rollout is off; retain logout-all watermark and revocation checks. |
| 4083700773 | Use the server no-show deadline and server clock offset. |
| 4083700785 | Only reset an offered ride after winning the pending-offer compare-and-swap; recheck ride obligations before going offline. |
| 4083700788 | Permit legitimate declines on service-animal trips; reject the explicit discriminatory refusal reason and authorize the offer first. |
| 4083700796 | Persist ordered stop completion, reject stale stop snapshots, route all providers to the next stop, and guard final completion against concurrent edits. |
| 4083700800 | Separate refund deductions from cash totals in balances, statements, driver history, admin summaries, windows, and period snapshots. |
| 4083700807 | Scope duplicate disputes to the authenticated claimant. |
| 4083700813 | Fail closed on malformed eligibility dates and model years when the recheck flag is enabled. |
| 4083700912 | Collect and validate eligibility dates in registration and both driver profile flows. |
| 4083700921 | Apply cumulative refund holds after completed auto, instant, and standard cash payouts; exclude adjustment rows and unsuccessful/future payouts. |
| 4083700933 | Remove stop coordinates from minimal FCM offers while retaining the authenticated WebSocket payload. |

Alternatives and file-level explanations are recorded in the accompanying scoped
change logs. In particular, stop progression uses an additive client capability:
new clients enforce every pending stop, and persisted progress retains that guard;
untouched legacy stop arrays remain compatible with older installed clients.

## Verification performed

The following focused runs passed. Counts describe individual runs and overlap;
they are not a claim that the full repository suite was run.

| Area | Evidence |
|---|---|
| Authentication and refresh | Final integrated six-suite run: 98 passed, including session cleanup, alternate login, generation binding, refresh lifecycle, and session identity. |
| Eligibility and decline behavior | Integrated focused backend run: 153 passed. |
| Payout accounting and statements | Integrated focused backend run: 239 passed; migration reporting contract suite: 4 passed. |
| Driver navigation | Integrated dashboard, active-ride panel, and auto-navigation Jest run: 109 passed. |
| Rider safety and stop refresh | Integrated SOS and rider socket Jest run: 18 passed. |
| Driver eligibility forms | Profile setup/schema: 31 passed; profile editing: 24 passed; become-driver flow: 42 passed. |
| Driver payout history | Integrated history Jest run: 14 passed. |
| Disputes, minimal push, SOS, stop APIs | Integrated core run had 167 passing tests and one obsolete refresh-flag fixture failure; that fixture was corrected and passed in the final 98-test auth run. |
| SQL execution | Exact migration 451 and 454 statements executed in local PGlite with synthetic tables/data. Verified auto/instant/standard partial refunds, replay protection, deduction caps, unsuccessful payout exclusion, flag-off behavior, transaction rollback/retry, cash stats/window/period snapshot totals and IDs, and RPC privileges. |
| Static checks | Changed Python files passed Ruff E9/F63/F7/F82; git diff whitespace check passed. Existing migration safety and RPC conflict checks passed locally without CI rule changes. |
| Independent review | Security, money, and navigation review of the actual changes; follow-up fixes included alternate auth flows, stale role flags, payout period snapshots, route fallbacks, stop snapshot races, and direct completion-call compatibility. |

## Limits and pre-merge checks

- Automatic approval review stopped a broader ride test run because it attempted
  cloud metadata endpoint access. That parent run is unverified and was not retried.
  It is not included as a passing completion-suite result above.
- PGlite is a local PostgreSQL execution check, not a live Supabase or concurrent
  multi-connection integration run. Direct-pool tests requiring a database were
  skipped. Supabase checks in this session were read-only schema/migration checks.
- Real Stripe refunds, two-phone displacement, physical iOS/Android accessibility,
  visual checks, native EAS builds, and production performance were not tested.
  Mobile visual changes were reasoned about, not screenshotted. Admin build/tests
  were unavailable in this checkout; the changed payout label was reviewed in code.

## Deployment and rollback

Apply migrations 451, 452, and 454 before deploying the new backend; migration 452's
RPC must exist even while its flag is off. Keep refund holds disabled until every
backend replica has removed the former Python per-event hold writer. Keep single
session enabled only after every replica preserves refresh generation bindings.
Enable eligibility enforcement only after validating and collecting fleet data.
New mobile stop progression requires the new backend endpoint.

Rollback is coordinated: disable rollout flags first; preserve refund adjustment
and session-revocation audit records. Do not restore the former Python refund hold
writer after enabling the SQL path, or revive revoked credentials. An old backend
can tolerate additive columns, but removing the new stop endpoint would strand new
mobile clients. Retain the endpoint or coordinate the mobile rollback. Incorrect
financial deductions require audited compensating entries, never deletion.
