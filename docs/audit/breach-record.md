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
| Breach start (estimated, UTC) | 2026-08-16T17:25:24Z — both files' first appearance in git history, commit `3c336ff` (merge of PR #3978) |
| Breach end (confirmed, UTC) | `driver_bank_sin_migration.sql`: working-tree content blanked 2026-08-26T19:22:45Z (commit `44183d3`). `driver_csv_migration.sql`: working-tree removal, 2026-08-27 (this PR). **Neither is fully closed** — history has not been rewritten for either file; full plaintext remains retrievable from any commit before each file's respective removal. |
| Data categories exposed | `driver_bank_sin_migration.sql` (157 drivers): SIN (plaintext), bank account/transit/institution numbers, GST/BN, date of birth, home address, Stripe Connect account ID. `driver_csv_migration.sql` (189 drivers): name, email, phone, **driver license number**, latitude, longitude. |
| Estimated number of individuals affected | Up to 346 driver records (157 + 189); overlap between the two files is plausible but not yet confirmed/deduplicated. |
| Real-risk-of-significant-harm (RROSH) determination | **no — owner's determination, 2026-08-27.** Rationale given: only 2 contributors (both cofounders) worked on the repository during the exposure window; the repository is being moved to private to close the exposure vector going forward. **Caveat recorded per this register's purpose of proving a complete history, not overriding the owner's determination:** the repository was confirmed **public** (`visibility: public`) for the full ~11-day window between breach start and this record. "2 contributors" describes who committed code, not who could have viewed a public repository — search-engine crawlers, GitHub's own indexing, and any third party with the URL are all outside what commit-author history can rule out. The owner weighed this and proceeded on their own determination. |
| OPC notified | No — per the RROSH determination above. |
| SGI notified (only if driver licence, insurance, or CRC/VSC data involved) | No. **Applicable** — `driver_csv_migration.sql` exposed driver license numbers directly; this field should not be skipped as "not applicable" on a re-review. |
| Affected individuals notified | No — per the RROSH determination above. |
| Root cause | Two one-off data-migration scripts (legacy MongoDB → Supabase driver migration) were committed to the repository root with live plaintext PII instead of being run-and-discarded locally, or committed only with synthetic/redacted data. `backend/migrations/CLAUDE.md`'s "One-off data-migration scripts" section names both files as the known incident, but only `driver_bank_sin_migration.sql` was remediated in the first pass (#4547); `driver_csv_migration.sql` went unnoticed until a later audit (#4596) because the CI backstop added after the first remediation (`spinr-sin-bank-pii` gitleaks rule) only matches SIN/bank column keywords, not this second file's column shape (name/email/phone/license_number/latitude/longitude). A separate, unrelated orphan git commit (`007ef80a`, see #4603) silently reverted several other files' content in roughly the same window but is not implicated in this specific incident. |
| Fix deployed | Working-tree removal only so far, no PR number assigned yet at time of writing this entry (branch `security/remove-driver-csv-pii-file`). `driver_bank_sin_migration.sql` blanked in commit `44183d3` (2026-08-26). `driver_csv_migration.sql` removed in this branch (2026-08-27). **Git-history rewrite for both files is a separate, still-open action** requiring explicit owner authorization and coordination across every existing clone (2 cofounders) before executing — not performed as part of this fix. |
| Post-mortem link | Not yet written. Should cover: why the CI gitleaks backstop's column-keyword matching missed the second file's shape (a CR on widening the rule is referenced in #4596 but not yet filed as a standalone numbered CR here); why the repository was public for the exposure window in the first place. |
| Incident Commander | Not formally assigned — handled ad hoc via GitHub issues #4547/#4596/#4603 and an AI-assisted session (2026-08-27). |
| Privacy Officer sign-off | Repository owner (verbal RROSH determination recorded above, 2026-08-27) — not a formal written sign-off per this register's stated process; the substantive determination is documented here pending a formal one. |

**Still-open items, not closed by this entry:**
1. Git history rewrite for both files (`git filter-repo` or BFG) — needs explicit owner authorization + coordination with all existing clones before executing.
2. Repository visibility (public → private) — owner stated intent; not independently re-verified as complete as of this entry.
3. CI gitleaks rule widening to catch the CSV file's column shape, not just SIN/bank keywords.
