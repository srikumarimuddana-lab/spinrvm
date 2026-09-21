# Admin clean-install repair during export investigation

| Field | Value |
|---|---|
| Surface / domain | admin-dashboard / admin |
| Issue | CI cannot install the dashboard dependencies after the filtered-export merge. |
| Root cause | The committed lockfile omits Vitest's nested magic-string 1.4.1 dependency. CI fails with EUSAGE before tests/build. |
| Fix | Regenerate lock metadata, adding only the missing dev-dependency entry. |
| Risk / blast radius | All dashboard clean installs; no runtime source, dependency constraints, auth, database, mobile or backend changes. |
| UX effect | None directly. Restores a prerequisite for testing/building releases; does not prove the reported export is corrected in a user's session. |
| Alternative | Replacing npm ci with npm install would weaken reproducibility; repairing the lock preserves the existing gate. |
| Rollback | Revert the lock entry for future builds. No deployed state or data needs rollback; reverting reintroduces the known install failure. |

## Files

| File | Change | Reason |
|---|---|---|
| admin-dashboard/package-lock.json | Add nested magic-string entry | Satisfy the existing Vitest requirement |
| This record | Record evidence and verification boundaries | Avoid equating a build repair with a verified live export |

## Evidence and boundaries

- GitHub Actions admin-test for merge commit 5bbe095 fails at npm ci with `Missing: magic-string@1.4.1 from lock file`.
- The failure reproduces locally before the repair. The generated diff is ten added lines with no other dependency changes.
- The CI deploy-admin job is manual-only (`workflow_dispatch`); its skipped status on a push alone does not establish a deployment failure.
- The live API schema accepts status and service_area_id. The current admin domain's public API bundle serializes export filters. Neither observation verifies the signed-in page callback, the backend reached by that dashboard, or the resulting CSV.
- Root cause of the user's continued unfiltered download remains unconfirmed. The build repair must not be presented as proof that issue is resolved.
- Verification: normal `npm ci --no-audit --no-fund` passed (1080 packages); offline dry-run validation passed; the 16 existing export page-component/API tests passed; `git diff --check` passed. Independent review found no blockers.
- A fresh `npm run build` was attempted after clean installation but failed because this environment could not fetch Plus Jakarta Sans from Google Fonts. This is not a successful production build. An earlier offline install attempt also lacked a cached zod-to-json-schema archive; the subsequent normal install succeeded.
- No production deployment or authenticated production download performed. The existing page-component test stubs the table; it does not verify the real dropdown UI or a deployed CSV. The full dashboard suite and Playwright suite were not rerun for this lock-only change.
