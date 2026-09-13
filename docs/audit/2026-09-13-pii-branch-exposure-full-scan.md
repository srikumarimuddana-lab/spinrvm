# PII branch-exposure scan — complete, full-repo result (2026-09-13)

**Purpose:** supersede the partial 500-branch GitHub-API sample recorded in `docs/audit/breach-record.md` Incident 1, item 1 (1,375 branches excluding `main`, 500 checked, 187/37.4% exposed, ~875 unchecked) with a complete, every-branch result, and record the methodology pitfall found along the way so it isn't repeated.

**Scope of this document:** investigation only. No branch was deleted, no history was rewritten, no PII file content was ever read, fetched, or displayed at any commit/ref — every step below is either a commit-metadata listing (`git log --format=%H`, no `-p`/`--stat`/diff) or a branch-containment check (`git branch -r --contains`).

## Methodology

Unlike the earlier sample (GitHub's commit-history-by-path API, `sha`+`path` filters, checked branch-by-branch), this scan used a full local clone of the repository:

1. `git fetch origin` to pull every branch ref that exists on GitHub.
2. `git log --all --format=%H -- <path>` for each of the two PII files, to find every commit anywhere in the repo's history that ever touched that path.
3. For each such commit, `git branch -r --contains <sha>` to list every branch that has it as an ancestor.
4. Union of step 3 across both files = the complete exposed-branch set. Every other branch is clean.

This is exhaustive by construction — it does not sample or estimate; every branch that existed on GitHub at scan time was checked.

## Methodology pitfall found and corrected: shallow-clone false positives

The first run of this scan used this session's default clone, which turned out to be **shallow** (`git rev-parse --is-shallow-repository` → `true`). A shallow clone truncates history at a boundary; `git log`/`git diff` have no parent object for a boundary commit and treat it as if it were a root commit with no history, which can spuriously make it look like that commit "touched" every path in its own tree — including a PII file, even when the boundary commit's actual content for that path is the already-blanked, safe version.

This is very likely the same failure mode referenced elsewhere in `docs/audit/breach-record.md` Incident 1 as "this session's own earlier, discarded attempt at a git-diff-based safety check, which produced an unusable 100%-false-positive result on this repo's branch topology."

Caught and corrected before anything was written to any document:

- The first (shallow) pass returned **11** candidate "bad" commits. Three of them — `1d69834b5`, `2ce565c96`, `81d7c424f` (2026-09-07/08, all unrelated CI/test-fix commits with no plausible connection to a driver-data migration script) — were exactly the three commits listed in `.git/shallow`, i.e. the shallow boundary itself.
- Verified directly (blob presence + size, never content): all three carry `driver_bank_sin_migration.sql` at blob `8b137891...`, **1 byte** — identical to the blob the real blanking commit (`44183d3`) writes, not the original 63,570–63,622-byte PII blob. Confirmed false positives.
- Ran `git fetch --unshallow` to convert to a full clone, then re-ran the entire scan from scratch. The corrected run found **8** genuine bad commits (the 3 artifacts gone) and, in this case, an unchanged 544/850 split — every branch that had one of the 3 artifact commits as an ancestor also had a genuine PII-introducing commit as an ancestor anyway, since the artifacts are chronologically downstream of the real introduction commits on the same lineage. The result did not change, but this was verified, not assumed.
- Sanity-checked against the 7 refs `breach-record.md` already records as individually rewritten on 2026-09-11 (`main` + `docs/ai-security-assessment-2026-09-08` + 5 `dependabot/*` branches): all 7 correctly come back clean in this scan.

**Takeaway for whoever runs a git-history check like this on this repo again: confirm `git rev-parse --is-shallow-repository` is `false` first**, or `git fetch --unshallow` before trusting any result.

## Root-cause commits (8, verified against full history)

| SHA | Date | Message | File |
|---|---|---|---|
| `2d5f5427686ca99b3b2f6964f715a3d6c6096095` | 2026-08-13 | Add bank/SIN migration script from old system CSV | `driver_bank_sin_migration.sql` |
| `1d6d329a9dca5e4108bce39adbaad7426ea73cf2` | 2026-08-14 | Migrate driver bank/SIN data and drop unused GST column (#3918) | `driver_bank_sin_migration.sql` |
| `db576d41fcae1bc12bfdfacc0cf3bad6bf65e474` | 2026-08-14 | Drop dead gst_hst_number column, fix bank migration to use gst_bn | `driver_bank_sin_migration.sql` |
| `44183d328ddebac794d5f33310802f4d173ce64f` | 2026-08-26 | Update fmt.Println message from 'Hello' to 'Goodbye' (blanks the file — working-tree only, history unaffected) | `driver_bank_sin_migration.sql` |
| `41356340d586fc39ed2097ccf3912f3f79e5cf59` | 2026-08-13 | Add driver CSV migration script for importing approved drivers from old system | `driver_csv_migration.sql` |
| `5bc2717ffbc6101f95905d4929dbb85c5181af2d` | 2026-08-13 | Fix UUID cast in driver CSV migration matching step | `driver_csv_migration.sql` |
| `41cee45ae4fb196b1933391e23923304a4d51fb2` | 2026-08-30 | security(pii): remove live driver_csv_migration.sql + record breach entry (#4596) (#4731) | `driver_csv_migration.sql` |
| `9856108ab840c9fbe3bd3a68d315abef68b1cc07` | 2026-08-31 | security(pii): remove driver_csv_migration.sql (live plaintext PII, #4596) + breach record | `driver_csv_migration.sql` |

Excluded as shallow-clone artifacts (do not re-add if this scan is repeated): `1d69834b535b58337939d1aca4f2c516ab5f46ef`, `2ce565c9643763700285c8d6fb634975c2e7afcd`, `81d7c424f1933f8e58786bc349f137d13900bb0b`.

## Result

| | Count | % |
|---|---|---|
| Total branches (2026-09-13) | 1,394 | 100% |
| Exposed (either PII commit reachable) | **544** | 39.0% |
| Clean | 850 | 61.0% |

`main` is clean (rewrite executed 2026-09-11, per `breach-record.md`). None of the 544 exposed branches is `main`.

## Cross-check against the earlier 500-branch sample

Comparing this complete result against the earlier partial sample (`docs/audit/breach-record.md` item 1's "187/500, 37.4%" figure) found **83 of those 500 branches were misclassified** by the sample:
- 60 branches the sample called "exposed" are actually clean.
- 23 branches the sample called "clean" were actually still exposed (missed).

That's a ~16.6% error rate in the sample — its 37.4% figure should be treated as **superseded**, not merely supplemented, even though the complete rate (39.0%) turned out close to it. The sample's own report already disclosed it had gaps (~875 branches never checked, some results discarded due to a mapping failure at scale); this scan closes that gap completely rather than continuing to check the remainder piecemeal.

## Exposed branches (544, full list)

```
a29-tax-history-audit-table
a34-referral-legacy-guard
a40-payment-cascade-doc-correction
a40-rls-role-level-test-coverage
action-items-corrections-2026-09-11
ai14-tracking-close
b14-driver-self-serve-license
b25-auto-label-run-maestro
b30/no-float-cast-shared-fare-fields
b31/exclude-legacy-rides-promo-eligibility
b34/insurance-period-corrections-table
b35/file-booking-float-cast-followup
b35/no-float-cast-authorized-and-promo-fields
b36/fare-service-recalc-grand-total-float-fix
b40-saved-addresses-rls
c22-migration-drift-audit
c24-mark-closed-already-fixed
c43-rls-enable-migration-prep
chore/admin-color-token-lint-rule
claude/2816-destructive-dark-mode-sweep
claude/2826-a11y-real-data-coverage
claude/a11y-keyboard-touch-target-fixes
claude/a30-legacy-import-commit-note
claude/a31-driver-balance-legacy-total-rides
claude/a34-rider-provenance-code-gap-stale-note
claude/a35-guard-monitor-verified-live
claude/a37-guard-trigger-ddl-audit
claude/a38-step-h-driver-rides-guard
claude/a39-migrate-py-reconcile-delete
claude/a40-connect-webhook-crosscheck
claude/action-items-b31-driver-earnings-close
claude/action-items-c63-c64-status-fix
claude/add-tip-late-charge-guard
claude/admin-audit-logs-ui-5jgk0s
claude/admin-dashboard-analytics-review-xsjyuk
claude/admin-documents-rbac-followup
claude/admin-monitoring-map-legibility
claude/admin-monitoring-sheet-conversion-recovery
claude/admin-pagination-gaps
claude/admin-portal-batch7-sub70
claude/admin-portal-batch7-sub72
claude/admin-portal-batch7-sub76
claude/admin-portal-duplicates-s00b4r
claude/admin-portal-heatmaps-audit-gm8fbn
claude/admin-portal-ui-issues-nbxjes
claude/admin-quiet-console-stage1
claude/admin-theme-flag-copy-fix
claude/adr-014-distributed-tracing-deferred
claude/ai-chat-postal-code-bug-nx9tun
claude/android-auto-earnings-privacy-2nzgpp
claude/android-auto-react-native-izvv6c
claude/android-auto-screens-2katir
claude/android-sha1-fingerprint-mismatch-q2olb0
claude/api-db-query-optimization-90pz28
claude/app-store-screenshot-design-qvy7ej
claude/apply-migrations-400-405-production
claude/archify-spinr-install-w91cuq
claude/attached-run-0rol3p
claude/b13-driver-regulatory-authority-write-paths
claude/b13-round3-guard-tightening
claude/b14-require-license-at-document-approve
claude/b15c-status-correction
claude/b25-dispatch-attempt-confirmed-blocked
claude/b27-dispute-closed-webhook-fixes
claude/b28-payouts-amount-numeric
claude/b29-booking-import-rides-numeric
claude/b37-admin-coverage-gate-wired
claude/b37-coverage-ratchet-plan
claude/b38-dispatch-attempt-confirmed-blocked
claude/b39-admin-allowance-zod-step4
claude/b39-admin-policy-timewindow-zod
claude/b39-driver-payout-zod-step3
claude/b39-driver-signup-zod
claude/b39-step5-profile-setup
claude/b39-step6-login
claude/b39-wallet-topup-zod-step2
claude/b39-work-allowance-zod-pilot
claude/b39-zod-migration-adr
claude/b7-service-areas-city-verified
claude/backend-ipv6-dualstack-bind
claude/backend-test-failures-opb5p1
claude/backend-unreachable-diagnosis-99nvcj
claude/breakup-drivers-page
claude/breakup-earnings-page
claude/breakup-service-areas-page
claude/bulk-import-checklist-navigation
claude/bulk-import-commit-flow-polish
claude/bulk-import-ux-batch-a
claude/bulk-import-ux-batch-b
claude/bulk-import-ux-batch-c
claude/bulk-import-ux-batch-d
claude/bulk-import-ux-batch-e
claude/bulk-import-ux-batch-f
claude/bulk-import-ux-batch-g
claude/bulk-import-ux-batch-h
claude/bulk-operations-copy-summary-confirm-rollout
claude/bump-expo-github-action-v9
claude/c13-workflows-verified-firing
claude/c18b-pin-flyctl-actions-sha
claude/c20-lint-round4-safety-work-legal
claude/c21-2026-09-04-self-merge-evidence
claude/c22-2026-09-04-followup-verification
claude/c22-close-remaining-5-untracked
claude/c22-migration-override-marker-note
claude/c22-purge-pii-retention-column-fixes
claude/c23-chargebacks-admin-tab
claude/c23-dispute-evidence-due-by
claude/c23-dispute-evidence-pack
claude/c23-dispute-evidence-reminder-loop
claude/c23-dispute-pack-a11y-fix
claude/c23-dispute-pack-ui
claude/c40-fix-pr-checks-duplicate-append
claude/c40-pr-checks-duplicate-tier5-finding
claude/c41-safetysheet-settingswav-leak-fix
claude/c50-phase0-t3-dispatch-timing-metrics
claude/c55-insurance-period-alerting-reconciler
claude/c63-go-online-experience-recheck
claude/c64-ws-location-batch-plausibility
claude/c69-loguru-extra-fix
claude/c70-matching-py-latent-bugs
claude/c95-post-merge-note
claude/c96-resolved-2026-09-10
claude/c96-status-update-2026-09-09
claude/c98-fly-railway-cli-gap
claude/c98-sandbox-verification-2026-09-11
claude/c99-firebase-auth-blocked
claude/chat-faq-display-issues-ufzgec
claude/ci-concurrency-c96-mitigation
claude/ci-error-audit-rootcause
claude/ci-token-permissions-c93-c94
claude/close-c44-dry-run-verification
claude/close-token-lint-gaps
claude/codex-review-pr-4616-uvxz83
claude/coverage-become-driver
claude/coverage-driver-tabs-index
claude/coverage-ride-options
claude/coverage-sweep-2026-08-24-next-tier
claude/cr-3295-adr010-metrics-config
claude/cr-3295-metrics-mvp-option-b
claude/cr-3718-image-size-accept-risk
claude/cr-3764-3765-yarn-resolutions
claude/cr-3864-postgres-healthcheck
claude/cr-4104-dual-run-driver-hold-guard
claude/cr-4105-rider-import-promo-fix
claude/cr-4106-legacy-id-crosswalk
claude/cr-4108-tax-amount-relabel
claude/cr-4112-ci-audit-dedup
claude/cr-4138-verify-email-flaky-test-fix
claude/cr-4187-migration-prefix-collision-hardfail
claude/dark-mode-ambient-assessment-only
claude/demand-legend-swatch-contrast
claude/dependabot-ignore-eslint-major-4823
claude/dependabot-prs-issues-analysis-sdwx3p
claude/deploy-rider-driver-playstore-rexq9r
claude/design-review-assets
claude/dev-test-env-platforms-9pj0tl
claude/doc-imports-and-plans-redirect
claude/driver-app-car-orientation-srtdi0
claude/driver-app-carmarker-test-image-import-fix
claude/driver-app-crash-update-gax81x
claude/driver-app-heatmap-planning-o7v5ic
claude/driver-app-token-migration-round1
claude/driver-count-mismatch-legacy-dgw9xw
claude/driver-csv-migration-8187tv
claude/driver-dormancy-flagging
claude/driver-earnings-tip-underpayment-fix
claude/driver-notification-area-boost-usaor4
claude/driver-rider-emails-messages-pdf-bio6az
claude/driver-signup-upload-format-f0hdxa
claude/driver-welcome-letter-pdf-3bbrrm
claude/e1-staging-scaffolding
claude/e4-synthetic-monitoring-scaffolding
claude/e6-dast-scaffolding
claude/e7-backup-restore-scaffolding
claude/enable-feature-dev-plugin
claude/expo-ios-build-failure-c1drlv
claude/extract-driver-detail-slideout
claude/extract-keyboard-row-shared-component
claude/faq-legal-sections-coverage-7ky777
claude/file-ci-audit-fingerprint-item
claude/fill-loading-empty-error-gaps
claude/financial-events-zero-row-investigation
claude/financial-migration-auditor-skill
claude/fix-4441-insurance-period2-doc
claude/fix-admin-a11y-color-contrast
claude/fix-b23-rider-identity-doc
claude/fix-backend-test-ai-chat-stale-tests
claude/fix-confirm-payment-ownership-test
claude/fix-decals-500-truncation
claude/fix-driver-heatmap-loading-shimmer
claude/fix-driverapp-tsc-regression
claude/fix-eas-mobile-update-node-version
claude/fix-gitleaks-allowlist-blindspot-4216
claude/fix-heatmap-k-anonymity-floor
claude/fix-legal-documents-root-mount
claude/fix-metrics-agent-dockerfile-apt
claude/fix-migrations-skip-list-test-path
claude/fix-public-faq-tests
claude/fix-sentry-lockfile-regression
claude/fix-sentry-replay-resolution
claude/fix-stripe-events-dual-import-parity
claude/fix-supportscreen-tsc-regression
claude/focused-babbage-562iiw
claude/followup-driver-earnings-doc-fixes
claude/g5-new-ride-requests-kill-switch
claude/gps-pings-redis-wh6h8r
claude/heap-map-display-comparison-2o8mv9
claude/heatmap-airports-enable
claude/heatmap-regina-enable
claude/heatmap-saskatoon-canary
claude/imported-rides-map-generation-bwqjxm
claude/imported-rides-missing-names-6in5c8
claude/ios-car-marker-orientation-c1wi5i
claude/issue-backlog-analysis-st2t10
claude/iternio-android-auto-upgrade-nfwrfr
claude/kill-dormant-theme-v2-css
claude/label-run-maestro-contents-perm
claude/late-tip-absorption-monitoring
claude/legacy-import-gst-preservation
claude/legacy-payout-correction-dry-run
claude/legacy-payout-correction-writepath
claude/legacy-tax-id-csv-builder
claude/lifecycle-improvements
claude/live-monitoring-map-fallback
claude/lost-found-chat-bugs-n4qodp
claude/maestro-local-readme
claude/main-branch-guard-workflow
claude/map-nav-investigation-round10
claude/map-padding-null-map-guard
claude/map-vehicle-tracking-animation-3e85y2
claude/metrics-agent-bootstrap-workflows
claude/metrics-agent-email-alert-contact
claude/migration-batch-readiness-wicr1d
claude/migration-checklist-highlight-completed
claude/migration-checklist-progress
claude/migration-checklist-status-panel
claude/migration-status-oversized-in-fix
claude/min-touch-lint-enforcement
claude/mongo-legacy-gst-audit
claude/mongo-supabase-migration-audit-4nnrgl
claude/n1-query-batching
claude/npm-yarn-audit-fails-rngo5s
claude/p1-admin-query-optimization
claude/p2-dispatch-loop-optimization
claude/partial-refund-payment-status
claude/pgbouncer-pool-migration-plan-jp69bh
claude/pickup-otp-payment-fixes-5a8dnk
claude/pii-rewrite-permission-finding
claude/pin-alloy-image-digest-3295
claude/portal-otp-bypass-testing-60bqjz
claude/post-migration-data-audit-dtbeg8
claude/pr-4645-review-l8v5ok
claude/pr-4873-codex-review-r0ag6j
claude/pr-4892-implementation-oc3d67
claude/pr-4894-merge-conflict-ff22h3
claude/pr-5079-analysis-plan-55dus9
claude/pr-5085-5079-hardening-5a2aj7
claude/pr-5085-5079-hardening-b11-rg-determination
claude/pr-5085-5079-hardening-c72-fingerprint
claude/pr-5085-5079-hardening-c86-stripe-loop
claude/pr-5085-5079-hardening-c88-close
claude/pr-5085-5079-hardening-c88-migration
claude/pr-5085-5079-hardening-c89-prod-correction
claude/pr-5085-5079-hardening-c91-monitoring-baseline
claude/pr-5085-5079-hardening-c92-visual-regression-gate
claude/pr-5138-implementation-27zn2l
claude/pr-5142-review-5dagsf
claude/pr-review-action-items-g5d7aw
claude/project-health-gf4enc
claude/railways-backup-server-oyo1t2
claude/refund-ledger-cumulative-delta
claude/regenerate-tools-batch-limit-fix
claude/remaining-tasks-bwrxh6
claude/remove-broken-codecov-upload
claude/remove-regina-update-faq-k94mmm
claude/remove-vercel-auto-deploy-3drmfi
claude/repo-hygiene-audit-2d1ehp
claude/repo-issues-triage-vr091k
claude/repo-issues-triage-vr091k-2
claude/reseed-visual-baselines
claude/retention-purge-column-error-9ggb7k
claude/ride-location-gap-status-constraint-63yvzu
claude/ride-offer-alert-ownership
claude/ride-offer-ringtone-edges-wl1w4c
claude/ride-payment-tip-strategy-ue6q15
claude/rider-account-coverage
claude/rider-activity-coverage
claude/rider-ai-assistant-coverage
claude/rider-app-button-card-input-round1
claude/rider-brand-splash-premium-n7l85n
claude/rider-chat-driver-coverage
claude/rider-driver-arrived-coverage
claude/rider-driver-arriving-coverage
claude/rider-emergency-contacts-coverage
claude/rider-lost-found-chat-coverage
claude/rider-loyalty-coverage
claude/rider-manage-cards-coverage
claude/rider-notifications-coverage
claude/rider-otp-coverage
claude/rider-payment-confirm-coverage
claude/rider-pick-on-map-coverage
claude/rider-privacy-settings-coverage
claude/rider-promotions-coverage
claude/rider-reactivate-account-coverage
claude/rider-referral-coverage
claude/rider-ride-completed-coverage
claude/rider-ride-details-coverage
claude/rider-ride-in-progress-coverage
claude/rider-ride-options-coverage
claude/rider-ride-status-coverage
claude/rider-scheduled-rides-coverage
claude/rider-search-destination-coverage
claude/rider-settings-coverage
claude/rider-tabs-index-coverage
claude/rider-textbox-visibility-d4w9lv
claude/rider-verify-email-coverage
claude/rider-wallet-coverage
claude/rider-wallet-coverage-round2
claude/rider-work-profile-coverage
claude/rideshare-code-review-2pzhgv
claude/rideshare-code-review-rvm37m
claude/rideshare-code-review-xewaez
claude/rideshare-team-roles-w8wazs
claude/rollback-understand-anything-marketplace
claude/route-regen-preview-mode
claude/route-snapshot-receipt-text-yk8cd5
claude/saskatoon-city-monthly-report-7xwtco
claude/scope-consent-checkbox-to-first-login
claude/security-gates-js-yaml-sharp-bump
claude/service-areas-pagination
claude/shared-button-card-input-primitives
claude/sk-pst-revert-gst-only
claude/skia-crash-incident-closure-verified
claude/skia-crash-incident-note
claude/skia-heatmap-crash-killswitch
claude/sos-options-analysis-t060ph
claude/spacing-font-touch-target-lint
claude/spinr-android-auto-maps-sc35h4
claude/spinr-app-all-surfaces-de596c
claude/spinr-backend-error-7d8dzt
claude/spinr-coverage-check-kiq0vv
claude/spinr-coverage-driver-dashboard
claude/spinr-coverage-lowcov-screens
claude/spinr-coverage-sweep-2026-08-24
claude/spinr-db-calls-rejected-mzusr7
claude/spinr-docs-audit-oh0309
claude/spinr-faq-review-uodytp
claude/spinr-legal-docs-review-t842up
claude/spinr-migration-batch-readiness-dwghtm
claude/spinr-mongodb-migration-u9y6iz
claude/spinr-plugin-evaluation-pioxpj
claude/spinr-rideshare-framework-u5xdnl
claude/spinr-scheduled-rides-review-azf7ob
claude/spinrvm-faq-legal-api-ioiryg
claude/spinrvm-issues-crs-m2ar9u
claude/spr-pe7ttb-missing-route-3qj3kw
claude/staff-faqs-pagination
claude/standby-parity-flyctl-fix
claude/stripe-booking-error-551scp
claude/stripe-payouts-schedule-f4v9o8
claude/stripe-webhook-signature-cd3qxj
claude/subscription-bandwidth-optimization-i3uwqz
claude/supabase-pgbouncer-validation-x8p35d
claude/tax-id-backfill-ux-pilot
claude/test-account-deletion-sql-53f4th
claude/test-dev-canary-setup-5h07cp
claude/three-ledger-reconciliation
claude/token-lint-color-duplicate-r9ihf1
claude/topology-remediation-plan-80gnou
claude/topology-remediation-plan-g516e0
claude/trusting-cori-als79k
claude/uber-lyft-payment-tips-uyrv75
claude/update-claude-md-principles-a43moo
claude/wallet-corporate-late-tip-debit
claude/weekly-payout-audit-tsdnxg
claude/weekly-payout-spinner-fix
claude/welcome-email-review-mwrjn6
claude/zoho-pii-scrub-gap
cr-4081-insurance-periods-backfill
cr-4216-gitleaks-manifest-scoping
dependabot/pip/backend/python-docx-1.2.0
design/audit-portal-mockup
docs-close-c57-rls-conftest
docs-correct-a40-fleet-audit-status
docs/ai17-status-correction
docs/app-store-review-creds
docs/c49-ci-wiring-correction
docs/close-migration-359-owner-to-gap
docs/driver-heatmap-redesign-ios-android-auto-20260908
docs/driver-notification-audit-c97
docs/driver-pii-history-rewrite-plan
docs/engineering-review-hardening-2026-09-07
docs/legal-audit-fixes-terms-checklist
docs/ux2-rider-app-consolidation
docs/ux2-round2-action-items-consolidation
driver-app/close-test-coverage-gaps
driver-app/map-camera-heatmap-improvements
driver-app/marker-tracking-fixes
feat/admin-color-tokens-batch1-drivers
feat/admin-color-tokens-batch2-rides
feat/admin-color-tokens-batch3-service-areas
feat/admin-color-tokens-batch4-driver-components
feat/admin-color-tokens-batch5-earnings
feat/admin-color-tokens-batch6-audit-logs
feat/admin-color-tokens-batch7-scoping
feat/admin-color-tokens-batch7-sub10
feat/admin-color-tokens-batch7-sub11
feat/admin-color-tokens-batch7-sub12
feat/admin-color-tokens-batch7-sub13
feat/admin-color-tokens-batch7-sub14
feat/admin-color-tokens-batch7-sub15
feat/admin-color-tokens-batch7-sub16
feat/admin-color-tokens-batch7-sub17
feat/admin-color-tokens-batch7-sub18
feat/admin-color-tokens-batch7-sub19
feat/admin-color-tokens-batch7-sub2
feat/admin-color-tokens-batch7-sub20
feat/admin-color-tokens-batch7-sub21
feat/admin-color-tokens-batch7-sub22
feat/admin-color-tokens-batch7-sub23
feat/admin-color-tokens-batch7-sub24
feat/admin-color-tokens-batch7-sub25
feat/admin-color-tokens-batch7-sub26
feat/admin-color-tokens-batch7-sub27
feat/admin-color-tokens-batch7-sub28
feat/admin-color-tokens-batch7-sub29
feat/admin-color-tokens-batch7-sub3
feat/admin-color-tokens-batch7-sub30
feat/admin-color-tokens-batch7-sub31
feat/admin-color-tokens-batch7-sub32
feat/admin-color-tokens-batch7-sub33
feat/admin-color-tokens-batch7-sub34
feat/admin-color-tokens-batch7-sub35
feat/admin-color-tokens-batch7-sub36
feat/admin-color-tokens-batch7-sub37
feat/admin-color-tokens-batch7-sub38
feat/admin-color-tokens-batch7-sub39
feat/admin-color-tokens-batch7-sub4
feat/admin-color-tokens-batch7-sub40
feat/admin-color-tokens-batch7-sub41
feat/admin-color-tokens-batch7-sub42
feat/admin-color-tokens-batch7-sub43
feat/admin-color-tokens-batch7-sub44
feat/admin-color-tokens-batch7-sub45
feat/admin-color-tokens-batch7-sub46
feat/admin-color-tokens-batch7-sub47
feat/admin-color-tokens-batch7-sub48
feat/admin-color-tokens-batch7-sub49
feat/admin-color-tokens-batch7-sub5
feat/admin-color-tokens-batch7-sub50
feat/admin-color-tokens-batch7-sub51
feat/admin-color-tokens-batch7-sub52
feat/admin-color-tokens-batch7-sub53
feat/admin-color-tokens-batch7-sub54
feat/admin-color-tokens-batch7-sub55
feat/admin-color-tokens-batch7-sub56
feat/admin-color-tokens-batch7-sub57
feat/admin-color-tokens-batch7-sub58
feat/admin-color-tokens-batch7-sub59
feat/admin-color-tokens-batch7-sub6
feat/admin-color-tokens-batch7-sub60
feat/admin-color-tokens-batch7-sub61
feat/admin-color-tokens-batch7-sub62
feat/admin-color-tokens-batch7-sub63
feat/admin-color-tokens-batch7-sub64
feat/admin-color-tokens-batch7-sub65
feat/admin-color-tokens-batch7-sub66
feat/admin-color-tokens-batch7-sub67
feat/admin-color-tokens-batch7-sub68
feat/admin-color-tokens-batch7-sub7
feat/admin-color-tokens-batch7-sub8
feat/admin-color-tokens-batch7-sub9
feat/c50-phase0-dispatch-metrics
feat/corporate-rider-statement-export
feat/driver-profile-completeness
feature/rider-driver-design-system
feature/spinr-swarm-ux-ideate-mode
fix-admin-rpc-rollup-test-mocks
fix-b42-payment-failed-webhook-and-remediation
fix-background-check-consent-policy
fix-claim-driver-atomic-retry-policy
fix-compliance-parse-iso-utc-dual-import
fix-dispatch-claim-loop-orphan-release
fix-gitleaks-cloudflare-d1-fp
fix-pypdf-cve-bump
fix-ride-completed-actual-route-processing
fix/admin-compliance-e2e-module-gate
fix/admin-crash-sparse-data
fix/admin-settings-write-allowlist-gaps
fix/ai17-f3-error-codes
fix/c50-phase2-direct-pool-review-fixes
fix/c97-driver-app-fallback-notification
fix/c97-firebase-init-loud-logging
fix/claude-md-period-2-wording
fix/driver-toast-theme-colors
fix/error-handling-guards-opt-exception
fix/gitleaksignore-billing-monitor-fingerprint
fix/gps-teleportation-spike-filter
fix/insurance-period-mid-trip-guards
fix/legacy-consent-notice-admin-allowlist
fix/noshow-period1-guard-finding4
fix/p0-db-query-optimizations
fix/pre-existing-e2e-test-flakes
fix/records-tab-stale-state
fix/semantic-status-tokens
fix/ux1-rider-app-themed-text
fix/ux2-driver-app-round2-batch1
fix/ux2-driver-app-round2-batch2
fix/ux2-driver-app-round2-batch3
fix/ux2-driver-app-round2-batch4
fix/ux2-driver-app-round3-cleanup
fix/ux2-driver-app-spacing-font-adoption
fix/ux2-rider-app-batch1
fix/ux2-rider-app-batch2
fix/ux2-rider-app-batch3
fix/ux2-rider-app-batch4
fix/ux2-rider-app-batch5
fix/ux3-driver-app-button-adoption
fix/ux4-shared-motion-timing
flyio-new-files
g1-stripe-payout-cashflow-model
g2-migration-skip-list
g8-support-sla-tracking
mvapps/affectionate-edison-g41yvo
mvapps/blissful-thompson-u14frt
mvapps/focused-carson-npl22j
mvapps/modest-lamport-x3zph5
mvapps/nifty-ramanujan-sqrdgn
mvapps/relaxed-shannon-63wlk6
mvapps/sleepy-galileo-hqp2xn
n11c-legacy-shell-investigation
n12-email-render-plan
n9-dead-toggle-removal
preview/theme-v2-on
publish-driver-deactivation-policy
ravi/dispatch-geo-provider-admin-ui
revert/phase3-preview-flag-override
security/remove-driver-csv-pii-file
security/widen-gitleaks-driver-csv-pii
staging
test/admin-settings-write-allowlist-drift-guard
ux1-driver-app-text-wrapper-rollout
ux1-rider-app-text-wrapper-rollout-batch2
ux1-rider-app-text-wrapper-rollout-round3
```

Note: this session's own designated working branch, `mvapps/affectionate-edison-g41yvo`, appears in the list above — forked from `main` before the 2026-09-11 rewrite and never rebased onto the rewritten history since. No PII file was created, modified, or read on this branch as part of this scan or the doc updates it produced; the branch is listed here for transparency, not as a special case.

## Recommendation

`main`'s rewrite already proved the mechanism works (`git filter-repo --invert-paths` + force-push, executed manually by the repo owner on 2026-09-11 per `docs/audit/breach-record.md`). The 544 branches above are the complete remaining scope — extend the same process to them rather than resuming branch-by-branch enumeration, which this scan has now made unnecessary. See `docs/runbooks/driver-pii-history-rewrite-plan.md` for the updated plan and commands.

No remediation was performed as part of this scan — investigation only, per explicit instruction.
