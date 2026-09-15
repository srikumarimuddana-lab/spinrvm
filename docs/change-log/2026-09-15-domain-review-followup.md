# PR #5423 domain review follow-up

## Store destinations

- Issue: store URLs were labelled unconfirmed after a host-only replacement.
- Root cause: the original change did not inspect the website repository or retrieve its pages.
- Fix: both support URLs now target the canonical `https://www.spinr.ca/help`; metadata comments record verification of privacy, help, and marketing pages.
- Alternative: retain `/support`, which the website redirects to `/help`; the direct route avoids that unnecessary redirect.
- Blast radius: both store metadata files; references in store-assets README and mobile submission documentation. No mobile runtime logic changes.
- User effect: store support links lead directly to the help centre after store metadata is submitted.
- Files: `rider-app/store-assets/metadata.json`, `driver-app/store-assets/metadata.json`, this log.
- Before: `support_url: https://www.spinr.ca/support`. After: `support_url: https://www.spinr.ca/help`.
- Verification: parsed both JSON files; checked all three website URL fields; retrieved privacy, terms, help, home and drive pages through web access on September 14; checked website route files and redirect configuration.
- Limits: web retrieval may use cached pages. No App Store/Play Console submission, native build, or visual regression run. Mobile apps have no active visual regression tooling.
- Rollback: revert this metadata commit; no database or deployed state changes.

## Rate-limit documentation destination

- Issue: the replacement `https://spinr.ca/docs/rate-limits` still has no corresponding website route.
- Root cause: changing the host alone preserved an invented path.
- Fix: link the existing public GitHub runbook, and keep the sample response in that runbook consistent.
- Alternative: build a documentation page in the website; linking the existing runbook is the smallest complete correction.
- Blast radius: `rate_limit_exceeded_handler` responses and their documented sample. `documentation_url` remains a string; status, error code, retry timing, rate-limit headers, auth and enforcement are unchanged.
- User effect: anyone following the diagnostic documentation link gets the runbook. No screen layout changes.
- Files: `backend/utils/rate_limiter.py`, `docs/runbooks/rate-limits.md`, this log.
- Before: `https://spinr.ca/docs/rate-limits`. After: `https://github.com/srikumarimuddana-lab/spinrvm/blob/main/docs/runbooks/rate-limits.md`.
- Verification: Python syntax parse; extracted handler executed with lightweight logging/metrics/response adapters for a 3-per-minute limit; confirmed 429, unchanged retry fields/headers, and exact runbook destination.
- Limits: full pytest invocation could not start because pytest is unavailable in the resumed runtime. No backend integration suite or mobile production build was run.
- Rollback: revert this URL-only commit; no database state changes.

## Remaining release checks

The original PR's native/app test and deployment gates remain open. Deployed `ALLOWED_ORIGINS` must be checked separately: deleting hardcoded origins does not revoke an origin still configured in that environment variable. This review did not access deployment settings.
