# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-20 |
| Author | Tooling (Claude Code) |
| Surface(s) | `.claude/agents/`, `.claude/commands/` — dev tooling only, no app code |
| Domain (Sentry tag) | n/a (not a runtime surface) |
| PR / commit link | see PR on branch `claude/spinr-faq-review-uodytp` |
| Related issue or gap ID | Follow-up from the Support-section (FAQ/Legal) content audit — turning the manual readiness-check method into a repeatable subagent |

## 1. Issue / gap identified

The last two sessions' legal-document readiness checks (which drafts are close to publication, which gating conditions are actually closed in code vs. still genuinely open) were done by hand: reading each draft's header, grepping the codebase for the cited constants/flows, and reasoning about which conditions were code-checkable vs. required a human decision. This is exactly the kind of repeatable audit this repo already has a pattern for (`spinr-migration-reviewer`, `spinr-security-auditor`, etc.) but no agent existed for the legal-content-readiness case specifically.

## 2. Root cause

No `spinr-*-reviewer` agent existed for `docs/legal/` drafts, so each readiness check was re-derived from scratch rather than run as a scoped, repeatable audit.

## 3. Fix / remediation

Added, following the exact convention of the existing `spinr-migration-reviewer` / `/migration-check` pair:

- `.claude/agents/spinr-legal-readiness-reviewer.md` — a read-only auditor subagent. Its core contribution is a **three-way classification** of every gating condition in a draft's header/pre-publication notes or its `legal-text-publication-checklist.md` row:
  1. **Code-checkable** — resolve directly (grep the cited constant/flow, cite file:line)
  2. **Live-data** — flag as requiring a DB-connected session, never guessed
  3. **Human-decision** (counsel review, an uncommitted business number, an unprovisioned vendor/inbox) — flag with exactly who needs to supply what, never invented
- `.claude/commands/legal-check.md` — the `/legal-check` slash command that dispatches the agent, scoped to either all Draft-status files or a single path argument.

The agent is explicitly read-only (reports findings; does not edit drafts or the checklist) — applying a finding is a separate, reviewed step, consistent with CLAUDE.md's Change Impact & Risk Log requirement for anything that changes checklist state.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to two new files under `.claude/agents/` and `.claude/commands/`.** Dev-tooling configuration only — not loaded by, or reachable from, any runtime surface (backend, rider-app, driver-app, admin-dashboard). No existing agent, command, or skill is modified.
- **No other consumers** — these are new files with no prior callers; nothing else references them yet.
- Grepped `.claude/agents/` and `.claude/commands/` for any existing file with a conflicting name (`spinr-legal-readiness-reviewer`, `legal-check`) — none found, no collision.

## 5. User-experience effect

None — internal Claude Code tooling, not user-facing in any app surface.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `.claude/agents/spinr-legal-readiness-reviewer.md` | New subagent definition | Repeatable, scoped audit of legal-draft gating conditions |
| `.claude/commands/legal-check.md` | New slash command | `/legal-check` entry point, matching the `/migration-check` pattern |
| `docs/change-log/2026-08-20-legal-readiness-reviewer-agent.md` | New change-log entry | Standard practice for this repo |

## 7. Before / after

Purely additive — no existing behavior changed. No before/after snippet applicable (not a behavior-changing diff).

## 8. Rollback plan

`git-revert-safe` — two new markdown config files, no schema, no data, no code path, no live surface touched. Deleting the two files (or reverting the commit) fully removes the capability with no other effect.

## 9. Verification performed

- Read the existing `spinr-migration-reviewer.md` / `migration-check.md` pair in full and matched their structure (frontmatter shape, Scope section, numbered How-to-review steps, fenced Output format, Anti-patterns section) so the new agent/command are consistent with the repo's established convention.
- Confirmed no naming collision with any existing agent or command file.
- Did not invoke the new agent against a real file in this change — it has not yet been exercised end-to-end; first real run will be the next `/legal-check` invocation.

**What was NOT verified**: the agent's actual output quality/accuracy on a live run — this change only adds the definition, it does not include a first execution transcript. Also not verified: whether the agent's `tools: Read, Grep, Glob, Bash` grant is sufficient for every future draft (a future draft citing a fact only discoverable via an MCP tool, e.g. a live Supabase query, would need the agent's live-data classification to correctly defer to a DB-connected session, which is by design but untested against a real case yet).

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed — two new isolated config files, no other consumers
- [x] No silent behavior change to an already-shipped flow: purely additive tooling
