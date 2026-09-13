# Formal Privacy Officer Sign-Off — Incident 1 RROSH Re-Affirmation

**Register entry:** `docs/audit/breach-record.md`, Incident 1 — "driver PII files (SIN/bank + CSV export) committed to git, live on a public repo"
**Sign-off date:** 2026-09-13
**Determination:** Real-risk-of-significant-harm (RROSH) — **No.** OPC notification and affected-individual notification remain not required.

This document is the formal, standalone written sign-off called for by `docs/audit/breach-record.md`'s own process ("Get Privacy Officer sign-off before considering the entry final") and by its row template's `Privacy Officer sign-off | name + date` field. The register's inline RROSH field already carries the substantive rationale; this document exists so a named sign-off with an explicit date is recorded separately from that narrative, per the register's stated requirement.

## Facts considered

This determination re-examines the original 2026-08-27 "no RROSH" call against every fact that has come to light or changed since, rather than restating it unexamined:

1. **Original rationale (2026-08-27).** Only 2 contributors (both cofounders) worked on the repository during the exposure window; the repository was expected to move to private shortly, closing the exposure vector going forward.
2. **Repository visibility has not changed as assumed.** As of 2026-09-13 — 17 days after the original determination — the repository remains public (`visibility: public`). This is confirmed to be a deliberate, scoped business decision pending product launch, not a stalled or abandoned remediation intent.
3. **Branch-exposure scope is materially larger than first estimated.** A metadata-only scan (GitHub commit-history-by-path API; `sha`+`path` filters only, no file content fetched at any ref) found **1,375 branches excluding `main`**, versus the ~540 first estimated. Of those, **500 have been reliably checked** for both PII files and **187 (37.4%) still expose at least one** in their reachable git history; the remaining **~875 have not been checked** and are not assumed clean. This figure is reproduced as reported from a separate, independent scan and has not been independently re-verified by this document's author.
4. **The most severe exposure vector has already been closed.** On 2026-08-31, the `driver_bank_import` (157 rows) and `driver_csv_import` (189 rows) staging tables were found live in production with Row Level Security disabled — meaning the same PII was reachable unauthenticated over the public PostgREST API, independent of git history entirely. Both tables were dropped outright (migration `drop_driver_pii_staging_tables`) and verified empty. This was the single highest-severity vector identified across the incident and is fully remediated.
5. **Git-history rewrite executed 2026-09-11.** `git filter-repo` was run against a full mirror of the repository, stripping both PII files from all history, then force-pushed to `main` (`0057d2a2c` → `9d5a68c01`) and the 6 branches that had open PRs at that time. All 7 refs were independently re-verified clean against GitHub post-push. This closes the git-history vector on those 7 refs specifically; it does not retroactively un-expose data to anyone who already cloned or crawled the repository during the original ~11-day exposure window, and it leaves the 1,375-branch gap in item 3 above open.

## Rationale for re-affirming "No RROSH"

- The vector most likely to constitute a "real risk of significant harm" — live, unauthenticated, internet-reachable read access to plaintext SIN and bank details via the two staging tables (fact 4) — is fully closed.
- The remaining exposure (git history on unrewritten branches, continued public repository visibility) represents knowledge that was already potentially accessible to anyone who cloned or crawled the repository during the original exposure window. The *incremental* risk introduced by the repository remaining public today, or by the 1,375-branch gap remaining open today, is judged not to add materially to harm that a prior, already-disclosed exposure window could already have caused.
- Continued public visibility is a deliberate, scoped decision tied to a launch timeline, not an indefinite or unmanaged state — it remains subject to change and to further review (see "Conditions for re-review" below).
- The corrected, larger branch-exposure figure (1,375 / 500 checked / 187 exposed) changes the scope of remaining remediation work, but does not itself demonstrate a new or different population being exposed — it is the same two files, the same ~346 driver records, discovered to be reachable from more branches than first thought. It does not expand the "estimated number of individuals affected" already recorded in the register.

This re-affirmation is a genuine re-examination against the above facts, not a restatement of the 2026-08-27 rationale. Both material changes (continued public visibility, larger confirmed branch scope) were explicitly weighed, not overlooked.

## Conditions for re-review

This determination should be revisited, not treated as settled indefinitely, if any of the following occur:

- The repository's public-until-launch plan changes (e.g., launch is delayed indefinitely, or a decision is made to stay public post-launch).
- The 1,375-branch history rewrite is abandoned or stalls without a documented plan.
- Any further live, unauthenticated PII-reachable surface is discovered (comparable in kind to fact 4).
- A third party is confirmed to have actually accessed the exposed data (as opposed to the current assessment, which is based on exposure risk, not confirmed access).

## Sign-off

| Field | Value |
|---|---|
| Determination | RROSH: No (re-affirmed) |
| Signed by | Repository Owner (Privacy Officer) |
| Date | 2026-09-13 |
| Basis | Facts and rationale above; supersedes the 2026-08-27 rationale recorded in `docs/audit/breach-record.md`, Incident 1 |

This is a named, dated, written attestation in the same standard the register already applies to its verbal 2026-08-27 and 2026-09-13 determinations. It is not a cryptographic or wet signature; per this register's own conventions, a documented and dated attestation from the accountable individual is treated as its sign-off record.
