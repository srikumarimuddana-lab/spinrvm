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

- The first (shallow) pass returned **11** candidate "bad" commits. Three of them (2026-09-07/08, all unrelated CI/test-fix commits with no plausible connection to a driver-data migration script — SHAs redacted, see note below) were exactly the three commits listed in `.git/shallow`, i.e. the shallow boundary itself.
- Verified directly (blob presence + size, never content): all three carry `driver_bank_sin_migration.sql` at blob `8b137891...`, **1 byte** — identical to the blob the real blanking commit writes, not the original 63,570–63,622-byte PII blob. Confirmed false positives.
- Ran `git fetch --unshallow` to convert to a full clone, then re-ran the entire scan from scratch. The corrected run found **8** genuine bad commits (the 3 artifacts gone) and, in this case, an unchanged 544/850 split — every branch that had one of the 3 artifact commits as an ancestor also had a genuine PII-introducing commit as an ancestor anyway, since the artifacts are chronologically downstream of the real introduction commits on the same lineage. The result did not change, but this was verified, not assumed.
- Sanity-checked against the 7 refs `breach-record.md` already records as individually rewritten on 2026-09-11 (`main` + `docs/ai-security-assessment-2026-09-08` + 5 `dependabot/*` branches): all 7 correctly come back clean in this scan.

**Takeaway for whoever runs a git-history check like this on this repo again: confirm `git rev-parse --is-shallow-repository` is `false` first**, or `git fetch --unshallow` before trusting any result.

## Root-cause commits (8, verified against full history)

**SHAs redacted 2026-09-19, same reasoning as the branch-name redaction below:** GitHub serves
a commit by its SHA (`git show <sha>` or the `/commits/{sha}` API) regardless of whether any
branch currently points to it, as long as the object still exists in the repo's packfiles. On a
still-public repo with remediation not yet done, publishing these 8 SHAs is the same class of
"here's exactly where to look" index the branch-name list would have been — arguably a more
precise one, since a SHA goes straight to the object with no branch search needed. Date,
message, and file are kept (already-public information — the earlier partial GitHub-API sample
and the commit messages themselves were never secret); only the SHA column is withheld.

| # | Date | Message | File |
|---|---|---|---|
| 1 | 2026-08-13 | Add bank/SIN migration script from old system CSV | `driver_bank_sin_migration.sql` |
| 2 | 2026-08-14 | Migrate driver bank/SIN data and drop unused GST column (#3918) | `driver_bank_sin_migration.sql` |
| 3 | 2026-08-14 | Drop dead gst_hst_number column, fix bank migration to use gst_bn | `driver_bank_sin_migration.sql` |
| 4 | 2026-08-26 | Update fmt.Println message from 'Hello' to 'Goodbye' (blanks the file — working-tree only, history unaffected) | `driver_bank_sin_migration.sql` |
| 5 | 2026-08-13 | Add driver CSV migration script for importing approved drivers from old system | `driver_csv_migration.sql` |
| 6 | 2026-08-13 | Fix UUID cast in driver CSV migration matching step | `driver_csv_migration.sql` |
| 7 | 2026-08-30 | security(pii): remove live driver_csv_migration.sql + record breach entry (#4596) (#4731) | `driver_csv_migration.sql` |
| 8 | 2026-08-31 | security(pii): remove driver_csv_migration.sql (live plaintext PII, #4596) + breach record | `driver_csv_migration.sql` |

Excluded as shallow-clone artifacts (do not re-add if this scan is repeated): 3 commits,
2026-09-07/08, unrelated CI/test-fix commits — SHAs redacted for the same reason as the table
above. Reproducible via this doc's own Methodology section against a non-shallow clone (they're
exactly the 3 that fall out of the 11-vs-8 comparison described in the pitfall section above).

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

## Exposed branches (544, count only — full list redacted from this public repo)

**Redacted 2026-09-19, before merge, per manual review of this PR.** This section originally
inlined all 544 branch names. This repo is still `visibility: public` (verified 2026-09-19)
and remediation (rewriting those 544 branches) has not happened yet — publishing the exact
list of which refs still carry driver SIN/bank/CSV PII in reachable history would hand anyone
browsing the repo a ready-made index of exactly what to `git fetch`, meaningfully lowering the
effort to find data this document's own purpose is to help contain. The count (544/1,394,
39.0%), methodology, and root-cause commit table above are unaffected and stay public — they
don't materially help someone locate the PII faster than the already-public partial sample did.

**The full 544-branch list, and the 8+3 redacted SHAs above, are not lost** — all are fully
reproducible from this document's own "Methodology" section (`git fetch origin`, `git log --all
--format=%H -- <path>` for both files, `git branch -r --contains <sha>` per commit, unioned)
against a **non-shallow** clone. The repository owner or whoever executes the remaining rewrite
should re-run that.

**Un-redact both the branch list and the commit-table SHAs** once the 544 branches have been
rewritten (at which point they describe remediated history, not a live exposure map) — at that
point they're a closed-out record like the 7 already-rewritten refs, not a live target list.

Note: this session's own designated working branch, `mvapps/affectionate-edison-g41yvo`, is among the 544 exposed branches — forked from `main` before the 2026-09-11 rewrite and never rebased onto the rewritten history since. No PII file was created, modified, or read on this branch as part of this scan or the doc updates it produced; it's named here for transparency, not as a special case.

## Recommendation

`main`'s rewrite already proved the mechanism works (`git filter-repo --invert-paths` + force-push, executed manually by the repo owner on 2026-09-11 per `docs/audit/breach-record.md`). The 544 branches identified above (full list held privately — see the redaction note) are the complete remaining scope — extend the same process to them rather than resuming branch-by-branch enumeration, which this scan has now made unnecessary. See `docs/runbooks/driver-pii-history-rewrite-plan.md` for the updated plan and commands.

No remediation was performed as part of this scan — investigation only, per explicit instruction.
