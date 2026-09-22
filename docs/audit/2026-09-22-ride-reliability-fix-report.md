# September 22 ride reliability fixes

## Current outcome

The confirmed defects from the combined review have been addressed in local branch `fix/live-ride-reliability`, based on `f3ecdfd2bc8ccd67f94fe49c450e2a2651ed449a`. GPT-6 Luna agents acted as Spinr architect, authentication developer, dispatch developer, mobile developer, payments developer, and independent security reviewer.

This is a code-and-test delivery, **not a production clearance**. No production migration, app release, live charge, refund, or setting change was performed. Publishing the branch was blocked by automatic approval review because explicit authorization for the GitHub destination was required. A read-only remote check confirmed that the branch was not created.

Related context: [PR #5712](https://github.com/srikumarimuddana-lab/spinrvm/pull/5712) describes the incidents; [PR #5709](https://github.com/srikumarimuddana-lab/spinrvm/pull/5709) supplied the existing refresh-contract commits incorporated here.

## Changes and boundaries

| Issue | Remediation | Boundary |
|---|---|---|
| Successful refresh rejected by driver background client; invalid foreground expiry | Backend supplies backward-compatible `expires_in`; clients validate absolute expiry and persist the rotated credentials correctly | Existing-session expiry/background-return requires device testing |
| Stale explicitly revoked token kills a newer login | Persist explicit revocation reasons; refuse old credential without cascading into fresh sessions; retain rotated-token theft protection; audit logout-all and clarify cross-app scope | Historical NULL reasons remain conservative; no speculative backfill |
| Own rider request offered to the same account's driver profile | Exclude rider-owned drivers before ranking/claiming in primary, cascade, and service candidate paths | Existing accept-time protection retained |
| Cancelled batch offer leaves driver unavailable or Period 2 open | Conditional pending-offer cancellation plus transactional ownership-aware release; protect against stale Period-2 writes and offers inserted after cancellation | Apply migration 442 before backend rollout; uncertain ownership fails closed and is logged |
| Captured-fare refund failure permits another cancellation fee | Block fresh fee charging when refund outcome is failed, raised, or unknown; preserve driver cleanup/notification | Existing unresolved refunds require reconciliation, not a guessed second charge |
| 1–49¢ tip overflow captured then silently dropped | Reject before capture, retain hold and entered tip, return exact supported amounts; rider can edit and retry | Configured `min_tip_amount` is unchanged; no rounding or automatic tip increase |
| Split fare/tip success classified as underpayment | Store exact successful PI components in aggregate ledger; verify membership, amounts, total, and settled status; defer early app-settlement webhooks | Historical rows without component proof are not automatically rewritten |
| Missing payment timestamp | Legacy finalizer and atomic settlement RPC stamp `paid_at`; preserve the latest canonical driver earnings formula | Migration 443 is required for atomic RPC behavior |
| Notifications crash on Android | Replace five-category horizontal FlatLists with wrapped accessible controls in both apps | Removes the implicated native horizontal-scroll path; exact Fabric cause and distinct driver child-removal crash still require device reproduction |

The four historical no-offer requests cannot be conclusively attributed to networking from the available evidence. The deterministic refresh and self-dispatch defects are corrected; dispatch eligibility/presence policy was not weakened. Meta CAPI's historical invalid `extinfo` report was not changed speculatively: current server code does not send that field, so a fresh failing payload/version is needed.

## Verification

Counts below describe separate test runs and must not be added blindly because some suites overlap.

| Check | Result |
|---|---|
| Integrated backend authentication, cancellation refund, card settlement, and complete webhook test files | 176 passed; 17 success-webhook cases passed again after the final metadata-contract correction |
| Integrated dispatch, insurance, cancellation, and refund interaction suites | 117 passed |
| Integrated rider notification, payment orchestration, custom-tip, and ride-completed screen suites | 102 passed |
| Integrated driver auth-store initialization/races, background auth/location, and profile suites | 163 passed |
| Driver notification screen suites | 27 passed |
| ActiveRidePanel driver test file | 33 passed after clearing leaked mock call history; same failure first reproduced on unchanged main |
| Auth developer's admin staff and MFA coverage | 51 passed; focused mocked logout/deletion cases also passed |
| Disposable PostgreSQL 18.3 (PGlite) migration execution | Auth column addition succeeds; payment timestamp/canonical earnings/component metadata/idempotency/ACL checks pass; batch release state checks pass |
| Rider production Android JavaScript/Hermes export | Passed with repository postinstall patches applied; 3,370 modules, 10 MB HBC |
| Driver production Android JavaScript/Hermes export | Passed with repository postinstall patches applied; 3,726 modules, 11 MB HBC |
| Native-Postgres harness | Collected but skipped without a native PostgreSQL DSN; fixtures updated for the new live-offer requirement |

PGlite dispatch scenarios include online/offline release, replay without extra periods, cancellation before delayed Period 2, refusal of newer/missing claim stamps, historical accepted offers, active competing offers/rides, rejection of a pending offer after cancellation, and service-role-only execution. It executes the actual migration SQL in a disposable fixture; it does **not** exercise scheduling between two native PostgreSQL sessions.

Both app postinstall scripts applied the repository's React Native, Gradle-plugin, and maps patches successfully. The rider notification suite (23 tests) and driver notification suites (27 tests) passed again against the patched dependency trees before the final exports. Exports from the earlier unpatched installation are not the final build evidence. No native APK/AAB was produced or installed on a device.

The first new rider tab assertion failed because React Native wrappers repeated accessibility props. It was corrected to count actual button components, then the integrated suites passed. The final webhook review also caught a mocked metadata field that did not match the actual Stripe producer; the test was corrected, reproduced the failure, and passed after the handler used the producer's `rider_id` field. Earlier cached-toolchain limitations in individual impact logs were superseded by the successful locked-dependency mobile runs above.

A broad logout-all test run was blocked by automatic approval review when an unmocked case attempted cloud metadata access. It was not retried through a workaround; targeted mocked logout cases and the refresh suites passed. The full repository test suite was not run. The existing Starlette test-client deprecation warning is unrelated to these changes.

A broader driver test attempt was stopped after surfacing an order-dependent `ActiveRidePanel` fixture failure. The full file reproduced the same seven stale storage calls on unchanged main with identical patched dependencies. Clearing that mock's calls between cases fixed the test isolation issue; all 33 cases passed, with no product-code change.

## Release gates

1. Review the branch and approve publication to `srikumarimuddana-lab/spinrvm`. The proposed PR text is in `docs/audit/2026-09-22-ride-reliability-pr-draft.md`. Publication is separate from merging or deploying.
2. Apply and verify migrations **441 (revocation reason), 442 (safe batch release), 443 (settlement timestamp)** using the repository migration runner in staging before backend rollout. The deployment workflow does not apply these automatically. Never replay the one-shot trigger migration manually without the runner's applied-migration checks.
3. Run the new native PostgreSQL integration cases and existing affected CI checks. The architecture review checked lock order; a disposable WASM database does not prove two-session concurrency.
4. Build signed native apps and test the exact reported Android 17 build/device class plus a supported stable Android version. Repeat bell taps, back navigation/remount, category changes, unread/deletion updates, background/foreground transitions, and large font sizing. JavaScript export is not an APK/AAB or device test.
5. Complete two consecutive staged rides with separate rider/driver accounts: cross token expiry, background the driver, interrupt/recover the network, cancel during an offer/accept race, then book again. Confirm live presence, no self-offers, availability, and correct insurance intervals.
6. In Stripe **test mode**, verify rejected 1¢/49¢ overflow does not capture or replace the hold, 50¢ and supported adjustments settle the intended total, duplicate/out-of-order webhooks produce one aggregate ledger entry, and failed/ambiguous refund responses never create another cancellation charge.

For the incident accounts, retest sign-in on both apps after release. Historical unclassified revocations retain conservative replay behavior; the migration does not infer reasons for old rows. Read-only reconciliation must precede any separately authorized historical financial or insurance correction.

## Impact and rollback records

Detailed affected-consumer lists, before/after examples, verification limits, and rollback guidance are recorded in:

- `docs/change-log/2026-09-22-refresh-token-expires-in-schema-mismatch.md`
- `docs/change-log/2026-09-22-explicit-refresh-revocation.md`
- `docs/change-log/2026-09-22-dispatch-self-offer-and-batch-cancel-period-close.md`
- `docs/change-log/2026-09-22-captured-cancel-refund-guard.md`
- `docs/change-log/2026-09-22-subminimum-tip-overflow.md`
- `docs/change-log/2026-09-22-tip-overflow-rider-recovery.md`
- `docs/change-log/2026-09-22-split-payment-reconciliation.md`
- `docs/change-log/2026-09-22-notification-horizontal-scroll-crash.md`

Do not undo real charges, ledger entries, or historical insurance intervals as a code rollback. Keep data reconciliation separate and evidence-based.
