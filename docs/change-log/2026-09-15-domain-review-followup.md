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
