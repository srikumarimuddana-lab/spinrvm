# Contributor aliasing policy

**Status: proposed 2026-09-25, awaiting owner confirmation on the attribution-footer question below before any commit/PR footer changes.** This file is the single source of truth for how real identities are referred to in *written, forward-facing* Spinr content (reports, docs, templates, comments). It does **not** change any git commit history, GitHub account username, or CODEOWNERS enforcement — see "What this does not do" below for why.

## Alias table

| Real identity (GitHub / git / email) | Role (from CODEOWNERS, confirmed 2026-08-28) | Alias to use in new/updated docs, reports, comments |
|---|---|---|
| `srikumarimuddana-lab` / `srikumarimuddana@gmail.com` | Admin / account owner | **Spinrmk** |
| `ittalenthireca-sketch` / `ittalenthire.ca@gmail.com` | Write access; authors most PRs | **Spinrmv** |
| Both, collectively, as an organization label | — | **TeamSpinr** |
| Generic outbound/no-reply contact used in docs | — | **noreply@spinr.ca** (replaces any ad hoc address; note: `info@spinr.ca` was searched for repo-wide on 2026-09-25 and found nowhere, so there was nothing to replace there) |

Use these consistently in every new audit report, change-log entry, ADR, runbook, and comment from this point forward. Do not silently drift to a different spelling or shorten "TeamSpinr" to something else — that reintroduces the inconsistency this file exists to remove.

## What this does not do, and why

- **Does not rewrite git commit author name/email.** This repo already went through one `git filter-repo` history rewrite (2026-09-11, PII removal) whose GitHub-side cache purge is still an open, unresolved request (`docs/incidents/2026-09-12-github-purge-request-driver-pii-history-rewrite.md`). A second rewrite — to relabel authors rather than remove data — would re-trigger the same force-push/cache-purge exposure for a purely cosmetic reason, invalidate every existing PR/issue commit reference, and require re-syncing CODEOWNERS/branch protection (which match real GitHub usernames, not display aliases). Not worth the risk for a naming preference.
- **Does not rename the GitHub accounts.** Only the account owners can do that, in GitHub account settings — no tool available to this session can do it, and CODEOWNERS/branch protection would need a matching update the moment either handle changes.
- **CODEOWNERS keeps the real `@srikumarimuddana-lab` / `@ittalenthireca-sketch` handles.** GitHub can only resolve real usernames for review-routing enforcement; an alias there would silently break required-review enforcement on every money/schema/auth/dispatch/safety path it currently protects. The file's existing header comment is updated to point here for the human-readable alias, nothing else.

## Open decision: commit/PR attribution footer (needs your explicit answer)

Every commit and PR this session (and future Claude Code sessions) creates currently ends with:
```
Co-developed with Claude Code (claude-sonnet-5)
Co-Authored-By: Claude <noreply@anthropic.com>
Claude-Session: <url>
```
You asked to replace this with `noreply@spinr.ca` / `TeamSpinr`. I can do that for footers I write **going forward** (the harness lets a user's explicit instruction override its default attribution line) — but doing so removes the one place a reviewer can tell a change was AI-authored, on a repo whose own `CLAUDE.md` documents an AI-driven engineering process (25+ automated reviewer agents, mandatory Change Impact Logs, `AI-assisted — spinr platform (Claude Code)` on PR bodies) in detail. Silently dropping the disclosure while the process stays fully documented elsewhere is an inconsistency a future auditor, acquirer, or regulator could read as concealment, not polish — on a product already carrying two open regulatory/compliance threads (SK PST, PIPEDA retention).

**Recommendation:** keep a neutral, non-personal disclosure marker instead of removing disclosure entirely, e.g.:
```
Co-developed-by: TeamSpinr Automation <noreply@spinr.ca>
```
This satisfies "no personal Anthropic/gmail identity in the footer" (your actual professionalism goal) without losing AI-authorship traceability. I have **not** made this change yet — tell me which of the three you want (keep Anthropic disclosure as-is / adopt the neutral marker above / drop disclosure entirely) and I'll apply it to every future commit and PR consistently from that point on. Existing, already-merged commit/PR footers are not retroactively editable without the same history-rewrite risk described above, so this only ever applies forward.
