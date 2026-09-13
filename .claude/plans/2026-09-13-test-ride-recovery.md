# Test-ride recovery implementation plan

Goal: validate the six findings for SPR-5BNURH and SPR-BCPJPV, fix reproducible defects, document residual risks, and open a PR against main.
Architecture: retain the shared mobile auth store and the existing insurance transition RPC. Preserve settlement policy and historical audit rows. No production writes or deployment are part of this PR task.
Evidence: operator's local mid-ride process-death report; read-only production queries; source at base 6c21b7929.

TodoWrite is unavailable in this harness; this checklist tracks sequential subtasks, each committed before the next starts. Each implementation commit includes its impact entry (three files maximum).

- [x] Investigate report and create branch from current origin/main in the existing isolated worktree.
- [ ] Auth read recovery: shared/store/authStore.ts, driver-app/__tests__/store/authStore.initialize.test.ts, docs/change-log/2026-09-13-auth-storage-read-recovery.md. Reproduce SecureStore read rejection; distinguish unavailable storage from absent credentials; preserve credentials and use existing recoverable-session flow. Verify iOS/Android rejection, retry, missing token, revoked token, and foreground refresh races in Jest; commit.
- [ ] Auth write durability: same store and test file, docs/change-log/2026-09-13-auth-storage-write-failure.md. Reproduce rejected refresh-token persistence; reject setTokens before publishing the new access token; test successful persistence and failed writes; commit.
- [ ] Insurance attribution: backend/migrations/419_insurance_period_ride_identity.sql, backend/tests/sql/insurance_period_ride_identity.sql, docs/change-log/2026-09-13-insurance-period-ride-identity.md. Run synthetic PostgreSQL assertions against the old function (must fail on A-to-B Period 2), then new function (must pass). Compare period plus NULL-safe ride identity. Preserve grants, signature, close/open semantics, original closed rows, and migration 253. Review migration; commit.
- [ ] Findings report: docs/audit/2026-09-13-test-ride-code-review.md. Explain corrections to F1/F2 claims; trace F3 distance fallback, F5 accuracy ingestion, F6 audience policy. Document native symbolication/device validation, historical correction and fare-policy follow-ups. Verify all source references; commit.
- [ ] Run focused regression checks and mobile production JS exports; record exact limits. Review final diff, push branch, create PR with risks and remaining gates. Do not merge.
