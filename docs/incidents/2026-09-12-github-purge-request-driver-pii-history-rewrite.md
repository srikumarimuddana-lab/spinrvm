# GitHub purge request — driver PII history rewrite

**Status: DRAFT, NOT SUBMITTED.** Needs a human to fill in the commit-SHA list below and submit
at https://support.github.com/ — no tool available to this session can file a GitHub support
ticket. See `docs/audit/breach-record.md` Incident 1, still-open item 1, and
`docs/runbooks/driver-pii-history-rewrite-plan.md` (step 5) for full context.

## What happened, as verified from this session

`main` was force-pushed sometime between **2026-09-11T22:10:06Z** (last fetch before the
rewrite, tip `b4accffb7`) and **2026-09-11T23:21:46Z** (first fetch showing the forced update,
new tip `b711f1b2d`). This matches `docs/runbooks/driver-pii-history-rewrite-plan.md` being
executed: `git filter-repo --invert-paths` against `driver_bank_sin_migration.sql` and
`driver_csv_migration.sql`.

Verified directly (not assumed from the force-push alone):
```
git log origin/main --full-history --oneline -- driver_bank_sin_migration.sql driver_csv_migration.sql
```
returns **empty** against the current `main` tip — neither file appears in any commit reachable
from `main` today. That matches the runbook's own step-3 success criterion.

## What this draft is missing — fill in before submitting

**The authoritative list of rewritten (pre-rewrite) commit SHAs.** `git filter-repo` prints
this itself during the rewrite (and keeps a full record in the rewrite clone's
`.git/filter-repo/commit-map`). This session's clone is shallow and was fetched *after* the
rewrite already happened, so it cannot reproduce that list — whoever ran the rewrite needs to
pull it from their own filter-repo output or the mirror clone's commit-map before submitting
this request. Do not submit without it; GitHub needs specific SHAs to know what to purge, not
just file names.

(For reference only, **not verified as complete** — five commits touching
`driver_bank_sin_migration.sql` are reachable from this session's last pre-rewrite object,
`b4accffb7`: `16d3d8ea6`, `9379917dd`, `b1edab4ec`, `b6c3e2c1f`, `949a4cba1`. This list does not
include the runbook's own cited introduction/removal commits — `2d5f54276`, `1d6d329a9`,
`41356340d`, `44183d3`, `41cee45` — which are not present as objects in this shallow clone at
all. Treat this parenthetical as a lead, not a substitute for the real commit-map.)

## Request to submit (fill in the bracketed section, then send via https://support.github.com/)

> Subject: Request to purge cached/orphaned commit data after a history rewrite (PII removal)
>
> Repository: srikumarimuddana-lab/spinrvm
>
> We force-pushed a rewritten history to this repository on 2026-09-11 (between approximately
> 22:10 and 23:21 UTC) to remove two files that contained real personal information (committed
> in error) from every commit. The commits below no longer exist on any branch or tag, but since
> GitHub can retain references to force-pushed-over commits (via caches, PR diff views, or
> internal object storage) for some time after the push, we're requesting they be purged rather
> than left to expire naturally, given the sensitivity of the data involved.
>
> Removed commit SHAs (pre-rewrite): **[fill in — see "What this draft is missing" above]**
>
> Files removed from history: `driver_bank_sin_migration.sql`, `driver_csv_migration.sql`
>
> Please confirm once any cached/orphaned copies of these commits have been purged from GitHub's
> systems.

## Before submitting, also worth noting to GitHub support (optional but relevant)

The repository was, and as of 2026-09-12 still is, **public** (`"private": false`, confirmed via
the GitHub API). If GitHub's purge process treats public-repo exposure differently (e.g. content
already indexed/crawled), it may be worth flagging that explicitly rather than assuming the
standard force-push cache-expiry process applies the same way it would to a private repo.

## After GitHub responds

Update `docs/audit/breach-record.md` Incident 1 with GitHub's confirmation (or lack of one) —
this closes out the runbook's step 5, the last piece of the git-history remediation. It does not
close the incident overall: repository visibility (still-open item 2 in the breach record) is a
separate, unresolved action.
