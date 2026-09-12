# PIPEDA Breach Record Register

**Owner:** Privacy Officer
**Canonical location:** this file (`docs/audit/breach-record.md`) — the file path referenced by `docs/runbooks/data-breach.md`. `docs/dpa-register.md` previously referenced a second, different path (`reports/compliance/breach-register.md`); that reference should be updated to point here so there is exactly one canonical register, not two.
**Retention:** entries are never deleted. Retain indefinitely; 24 months is the PIPEDA-driven floor, not a ceiling — do not purge an entry at 24 months.
**Purpose:** PIPEDA requires organizations to keep a record of every breach of security safeguards involving personal information under their control, whether or not it met the "real risk of significant harm" (RROSH) bar that triggers mandatory notification to the Office of the Privacy Commissioner of Canada (OPC). This register is that record. Populate one row per incident using the template in `docs/runbooks/data-breach.md` (§ "Breach record register").

---

## How to add an entry

1. Follow `docs/runbooks/data-breach.md` end to end for the incident itself (triage, containment, RROSH assessment, notifications).
2. Once the incident is closed, copy the row template below and fill it in completely — including incidents where the RROSH assessment concluded "no" and no external notification was required. This register exists to prove a complete history, not just a list of reportable breaches.
3. Get Privacy Officer sign-off before considering the entry final.
4. Link the post-mortem (`docs/runbooks/data-breach.md`'s 5-business-day SLA) once published.

## Row template

```
### Incident [N] — [YYYY-MM-DD discovery date] — [one-line description]

| Field | Value |
|---|---|
| Discovery timestamp (UTC) | |
| Breach start (estimated, UTC) | |
| Breach end (confirmed, UTC) | |
| Data categories exposed | |
| Estimated number of individuals affected | |
| Real-risk-of-significant-harm (RROSH) determination | yes / no — rationale: |
| OPC notified | yes (date/time) / no (rationale) |
| SGI notified (only if driver licence, insurance, or CRC/VSC data involved) | yes (date/time) / no / not applicable |
| Affected individuals notified | yes (date) / no (rationale) |
| Root cause | |
| Fix deployed | PR # + date |
| Post-mortem link | |
| Incident Commander | |
| Privacy Officer sign-off | name + date |
```

---

## Register

### Incident 1 — 2026-08-25 — driver PII files (SIN/bank + CSV export) committed to git, live on a public repo

| Field | Value |
|---|---|
| Discovery timestamp (UTC) | 2026-08-25T14:59:11Z (`driver_bank_sin_migration.sql`, issue #4547) / 2026-08-27T04:01:36Z (`driver_csv_migration.sql`, issue #4596 — same incident, second file named in the original inventory but missed in the first remediation pass) |
| Breach start (confirmed, UTC) | **Correction (2026-08-31):** the prior entry here cited commit `3c336ff` (merge of PR #3978, "android-auto-react-native", 2026-08-16) as both files' first appearance — verified directly against `git show 3c336ff --stat` and `git show dea8ea5 --stat` (the other candidate hash cited in #4596): **neither commit touches either PII file.** The actual first-appearance commits, per `git log --all --diff-filter=A -- <file>`: `driver_csv_migration.sql` → commit `41356340d`, **2026-08-13T13:46:40Z**. `driver_bank_sin_migration.sql` → commit `2d5f54276`, squashed into merged PR #3918 (commit `1d6d329a9`, **2026-08-14T06:35:06Z**). This moves breach start ~2–3 days earlier than previously recorded — the exposure window was longer, not shorter, than this register stated. |
| Breach end (confirmed, UTC) | `driver_bank_sin_migration.sql`: working-tree content blanked 2026-08-26T19:22:45Z (commit `44183d3`). `driver_csv_migration.sql`: working-tree removal, 2026-08-27 (PR #4731). **Git-history rewrite (`git filter-repo --invert-paths`) executed between 2026-09-11T22:10:06Z and 2026-09-11T23:21:46Z** — see still-open item 1 below for verification and remaining caveats (this closes the git-history vector specifically; it does not by itself close the incident — repo visibility, item 2, is still open as of this writing). |
| Data categories exposed | `driver_bank_sin_migration.sql` (157 drivers): SIN (plaintext), bank account/transit/institution numbers, GST/BN, date of birth, home address, Stripe Connect account ID. `driver_csv_migration.sql` (189 drivers): name, email, phone, **driver license number**, latitude, longitude. |
| Estimated number of individuals affected | Up to 346 driver records (157 + 189); overlap between the two files is plausible but not yet confirmed/deduplicated. |
| Real-risk-of-significant-harm (RROSH) determination | **no — owner's determination, 2026-08-27.** Rationale given: only 2 contributors (both cofounders) worked on the repository during the exposure window; the repository is being moved to private to close the exposure vector going forward. **Caveat recorded per this register's purpose of proving a complete history, not overriding the owner's determination:** the repository was confirmed **public** (`visibility: public`) for the full ~11-day window between breach start and this record. "2 contributors" describes who committed code, not who could have viewed a public repository — search-engine crawlers, GitHub's own indexing, and any third party with the URL are all outside what commit-author history can rule out. **Second caveat, added 2026-08-31 — see still-open item 4 below:** the production staging tables backing this data had Row Level Security disabled, meaning this data was also reachable live over the public API independent of git/repo exposure. This determination was made before that fact was known and should be re-examined against it. The owner weighed the above and proceeded on their own determination. |
| OPC notified | No — per the RROSH determination above. |
| SGI notified (only if driver licence, insurance, or CRC/VSC data involved) | No. **Applicable** — `driver_csv_migration.sql` exposed driver license numbers directly; this field should not be skipped as "not applicable" on a re-review. |
| Affected individuals notified | No — per the RROSH determination above. |
| Root cause | Two one-off data-migration scripts (legacy MongoDB → Supabase driver migration) were committed to the repository root with live plaintext PII instead of being run-and-discarded locally, or committed only with synthetic/redacted data. `backend/migrations/CLAUDE.md`'s "One-off data-migration scripts" section names both files as the known incident, but only `driver_bank_sin_migration.sql` was remediated in the first pass (#4547); `driver_csv_migration.sql` went unnoticed until a later audit (#4596) because the CI backstop added after the first remediation (`spinr-sin-bank-pii` gitleaks rule) only matches SIN/bank column keywords, not this second file's column shape (name/email/phone/license_number/latitude/longitude). A separate, unrelated orphan git commit (`007ef80a`, see #4603) silently reverted several other files' content in roughly the same window but is not implicated in this specific incident. |
| Fix deployed | `driver_bank_sin_migration.sql` blanked in commit `44183d3` (2026-08-26). `driver_csv_migration.sql` removed via PR #4731 (2026-08-27). **Git-history rewrite for both files (see item 1 below) executed 2026-09-11**, per `docs/runbooks/driver-pii-history-rewrite-plan.md`. |
| Post-mortem link | Not yet written. Should cover: why the CI gitleaks backstop's column-keyword matching missed the second file's shape (a CR on widening the rule is referenced in #4596 but not yet filed as a standalone numbered CR here); why the repository was public for the exposure window in the first place. |
| Incident Commander | Not formally assigned — handled ad hoc via GitHub issues #4547/#4596/#4603 and an AI-assisted session (2026-08-27). |
| Privacy Officer sign-off | Repository owner (verbal RROSH determination recorded above, 2026-08-27) — not a formal written sign-off per this register's stated process; the substantive determination is documented here pending a formal one. |

**Still-open items, not closed by this entry:**
1. ~~Git history rewrite for both files (`git filter-repo` or BFG) — needs explicit owner authorization + coordination with all existing clones before executing.~~ **Closed 2026-09-12, execution detected/verified same day.** `main` was force-pushed between **2026-09-11T22:10:06Z** (last confirmed pre-rewrite fetch, tip `b4accffb7`) **and 2026-09-11T23:21:46Z** (first fetch showing `forced-update`, new tip `b711f1b2d`) — consistent with `docs/runbooks/driver-pii-history-rewrite-plan.md` finally being executed. Verified directly, not assumed from the force-push alone: `git log origin/main --full-history --oneline -- driver_bank_sin_migration.sql driver_csv_migration.sql` returns **empty** against current `main` — neither file appears in any commit reachable from `main` today, matching the runbook's own step-3 "must print nothing" success criterion. **Not independently confirmed:** who executed it (this session's tools have no GitHub org audit-log access; it was not run by any Claude Code session in this account's history — `.claude/settings.json`'s permission allowlist has no entry for `git filter-repo` or a force-push to `main`, so any session attempting it would be denied outright, consistent with the runbook's own note that step 4 had been blocked this way before); whether every existing local clone (both cofounders') has been reconciled per the runbook's step 2 coordination requirement; and the **authoritative list of rewritten commit SHAs** for the GitHub purge request (this session's clone is shallow and already past the rewrite, so it cannot reproduce `git filter-repo`'s own commit-map — that must come from whoever ran the rewrite, from their local `.git/filter-repo/commit-map` or terminal output). A draft GitHub support request to purge cached pre-rewrite views is at `docs/incidents/2026-09-12-github-purge-request-driver-pii-history-rewrite.md`, pending that SHA list and a human to submit it. **This closes the git-history exposure vector specifically — it does not close the incident overall**: item 2 below (public repo visibility) is unrelated and still open, and per the runbook itself, rewriting history on a still-public repo does not retroactively un-expose the data to anyone who already cloned/crawled it during the ~11-day pre-rewrite window.
2. **Repository visibility (public → private) — re-verified 2026-09-12 via GitHub API: still `"private": false`.** Unchanged since first flagged 2026-08-31 — now over two weeks after the 2026-08-27 RROSH determination that rested explicitly on this visibility change happening. This is not a formality: the RROSH determination above rests explicitly on "the repository is being moved to private" as part of its rationale for not notifying OPC/affected drivers. Four days after that determination (2026-08-27 → 2026-08-31) the repo remains fully public, meaning the exposure vector the determination assumed would be closed is still open today. This should go back to the Privacy Officer/owner promptly — either the visibility change happens, or the RROSH determination should be explicitly re-affirmed knowing the repo is still public, not left resting on a stated intent that hasn't materialized.
3. ~~CI gitleaks rule widening to catch the CSV file's column shape, not just SIN/bank keywords.~~ **Closed 2026-08-31**: `spinr-driver-export-pii` rule added to `.gitleaks.toml`, gated on the CSV file's actual column identifiers (`license_number`, `driver_csv_import`, `old_driver_id`, `matched_driver_id`) plus a quoted-email-literal pattern.
4. ~~Confirmation that the production `driver_bank_import`/`driver_csv_import` staging tables have actually been dropped, per each script's own final step.~~ **Closed 2026-08-31, and the finding is worse than "not yet dropped":** checked directly via the Supabase connector (project `soavhtdhefowwvforzwb`, `ca-central-1`). Both tables were still present with their full original row counts (`driver_bank_import`: 157 rows; `driver_csv_import`: 189 rows) — **and both had Row Level Security disabled.** In this schema's convention (see e.g. `driver_crc_consents`, `email_send_log` — "Service-role-only, RLS enabled, no policies"), RLS-with-no-policies is how a table is locked to backend-only access; these two tables had no such lock, meaning the anon API key shipped inside the published apps could read every row directly over PostgREST with no authentication at all. This was flagged by Supabase's own security advisor. **This is a materially different fact than what the 2026-08-27 RROSH determination above was made against** — that determination weighed git-history/contributor-count exposure, not a live, unauthenticated, internet-reachable API endpoint serving plaintext SIN and bank details. Recommend the Privacy Officer/owner re-examine the RROSH call in light of this before treating it as settled. Remediation applied: both tables dropped outright (`DROP TABLE`, migration `drop_driver_pii_staging_tables`) rather than just enabling RLS, since the migration they supported is complete and nothing in the app reads them — verified empty via `information_schema.tables` immediately after.
