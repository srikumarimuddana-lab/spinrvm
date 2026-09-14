# Test-ride recovery implementation plan

Goal: validate the six findings for SPR-5BNURH and SPR-BCPJPV, fix reproducible defects, document residual risks, and open a PR against main.
Architecture: retain the shared mobile auth store and the existing insurance transition RPC. Preserve settlement policy and historical audit rows. No production writes or deployment are part of this PR task.
Evidence: operator's local mid-ride process-death report; read-only production queries; source at base 6c21b7929.

TodoWrite is unavailable in this harness; this checklist tracks sequential subtasks, each committed before the next starts. Each implementation commit includes its impact entry (three files maximum).

- [x] Investigate report and create branch from current origin/main in the existing isolated worktree.
- [x] Auth read recovery: shared/store/authStore.ts, driver-app/__tests__/store/authStore.initialize.test.ts, docs/change-log/2026-09-13-auth-storage-read-recovery.md. Reproduce SecureStore read rejection; distinguish unavailable storage from absent credentials; preserve credentials and use existing recoverable-session flow. Verify iOS/Android rejection, retry, missing token, revoked token, and foreground refresh races in Jest; commit.
- [x] Auth write durability: same store and test file, docs/change-log/2026-09-13-auth-storage-write-failure.md. Reproduce rejected refresh-token persistence; reject setTokens before publishing the new access token; test successful persistence and failed writes; commit.
- [x] Insurance attribution: migration now 421; original fix committed in de3d5b047, sequencing/test portability fixed in 6ffb39705. Seven real-Postgres regression tests plus manual SQL repro; full direct-pool suite 36 passed, 1 skipped.
- [x] Findings report saved as docs/audit/2026-09-13-last-two-test-rides-findings-and-plan.md; review corrections tracked below.
- [x] PR #5348 opened by the other agent. Original implementation's Android/iOS JS exports passed CI for both apps. Physical-device/native validation remains a release follow-up.

## PR #5348 review follow-up

The other agent committed the insurance migration, CI integration tests, startup isolation,
pre-initialization 401 handling, and logout marker handling. Review continues on this PR.

- [x] Subtask 1 (test + impact log + this checklist): cover partial token writes, unavailable recovery rereads, and logout callbacks; focused Jest passed (31 driver + 20 shared/rider tests).
- [x] Subtask 2 (two layouts + shared auth store): corrected comments about defensive initialization handling in d24bb9595.
- [ ] Subtask 3 (findings report + startup impact log + original report): record independent review/test results and correct stale completion/fare-policy claims; commit.
- [ ] Publish review commits to PR #5348, replace stale PR description with completed required fields, and verify required-fields CI. Keep PR open; no deployment or historical data changes.
- [x] Migration sequencing follow-up: fetched main, renamed unapplied 419 to free prefix 421, corrected fixture/test references and timezone assertion, committed as 6ffb39705. Manual SQL/impact log references corrected in the subsequent documentation commit.
