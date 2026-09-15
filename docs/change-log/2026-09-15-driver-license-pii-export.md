# Full licence numbers in authorized driver exports

| Field | Value |
|---|---|
| Surface / domain | Admin dashboard and driver export API / admin |
| Issue and cause | Show PII is not sent to export; the backend always masks decrypted licence numbers. |
| Fix | Explicit show_pii option, super-admin authorization, successful audit before decrypt, no-store responses, conditional licence output and CSV heading. |
| Alternative | Browser-only unmasking cannot recover server-masked data or enforce authorization; the backend option preserves the masked default. |
| Risk / blast radius | Drivers page is the sole production exportDrivers caller (via api.ts re-export). Shared list selection, filters, pagination, encrypted storage, VIN masking and SIN handling are unchanged. Authorized CSVs intentionally contain full licence data. |
| UX | Super-admin + Show PII exports full licence numbers. Other exports retain last four. This explicit user-requested option defaults off; no layout change or separate feature flag. |
| Rollback | Deploy previous dashboard then previous backend. No schema/data mutations. Downloaded CSVs cannot be recalled. |

## Files

| File | Purpose |
|---|---|
| backend/routes/admin/rides.py | Authorize, audit and return the requested licence representation |
| backend/tests/test_admin_rides_read_endpoints_coverage.py | HTTP endpoint tests for both modes, permissions, audit/decrypt failures |
| admin-dashboard/src/lib/api/content-area.ts | Type the export-only option |
| admin-dashboard/src/app/dashboard/drivers/page.tsx | Pass authorized toggle state and choose CSV heading |
| admin-dashboard/src/app/dashboard/drivers/page.export.test.tsx | Verify on/off and role behavior |

Before: `license_no = mask(decrypted)` for all exports.

After: `license_no = decrypted if show_pii else mask(decrypted)` after super-admin authorization and successful intent audit. Audit exceptions or missing saved rows prevent disclosure. Failed decrypts return blank, never ciphertext. Audit records contain mode/counts, not full licence values.

## Verification and rollout

Restored the previously reviewed implementation after workspace replacement. Fresh verification:

- 28 backend export endpoint tests passed, including full/masked mode, regular-admin denial, audit failure/absent-row failure, decrypt failure, selected filters and pagination.
- 24 client/page-component/CSV tests passed. All three page regressions failed before wiring the option. Backend missing opt-in/authorization/audit behavior was demonstrated by failing tests in the prior workspace.
- `npm run build` passed after the final frontend changes. Targeted ESLint: zero errors, 13 existing warnings. Ruff and `git diff --check` passed.
- Independent backend security and frontend reviews found no remaining blockers. Fixed safe logging was added for audit failures; no licence values enter logs.
- Dependencies were copied from an existing same-repository checkout for local tests/build, with a test-only socksio dependency added outside the repository. No manifests or lockfiles changed by this feature.
- Not verified: authenticated production download, live Vault decryption, browser/visual regression or full application suites. Page tests stub the table and verify the real page callback; they do not prove live browser behavior. Existing dashboard visual regression tooling remains applicable; layout is unchanged.

Deploy backend before dashboard. An old backend ignores show_pii and continues masking. No merge/deployment or live PII export is part of this PR. The earlier live filtering report remains separate and has not been verified in the user's browser.
