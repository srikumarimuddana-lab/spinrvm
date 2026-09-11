# 2026-09-11 — Understand-Anything plugin: enablement decision

## What this is

[`Egonex-AI/Understand-Anything`](https://github.com/Egonex-AI/Understand-Anything) (MIT,
commit `a8cdbf4` at time of enablement) is a Claude Code plugin that runs a multi-agent
pipeline (`project-scanner`, `file-analyzer`, `architecture-analyzer`, `tour-builder`,
`graph-reviewer`, plus `domain-analyzer`/`article-analyzer` for optional commands) over a
codebase and produces an interactive knowledge graph (`.ua/knowledge-graph.json`) —
structural extraction via tree-sitter, semantic summaries via LLM agents.

## Why it's a plausible fit for spinrvm

Five surfaces (`backend`, `rider-app`, `driver-app`, `admin-dashboard`, `shared`), ~53k
tracked statements in `backend` alone, ~25 routers, 66 DB helpers, 41 background loops.
Nothing in this repo currently maintains a real cross-file/cross-surface dependency graph —
the closest thing is the hand-written `domain-*.md` context files, which document business
logic, not call/import structure.

## Verification performed before enabling

- **Language coverage**: read `understand-anything-plugin/agents/project-scanner.md` in full.
  The bundled `extract-import-map.mjs` script (tree-sitter-backed) natively resolves imports
  for TypeScript, JavaScript, Python, Go, Rust, Java, Kotlin, Scala, C#, Ruby, PHP, C, C++ —
  covers `backend` (Python) and every TS/TSX surface (`rider-app`, `driver-app`,
  `admin-dashboard`, `shared`).
- **Hook safety**: read both hooks in `understand-anything-plugin/hooks/`.
  - `PostToolUse` (`post-tool-use-auto-update.mjs`) only fires on a Bash command matching
    `git (commit|merge|cherry-pick|rebase)`, and even then no-ops unless the project's own
    `.ua/config.json` has `autoUpdate: true` (set only by explicitly running
    `/understand --auto-update`) AND a knowledge graph already exists. Does **not** touch git
    hooks — it's a Claude Code session-level hook, so it cannot collide with
    `.claude/hooks/pre-commit`'s 10-check pipeline.
  - `SessionStart` hook is similarly gated behind `autoUpdate: true` in `.ua/config.json`.
  - **Conclusion**: both are inert by default; nothing runs until the graph is generated and
    `--auto-update` is explicitly opted into for this repo. Not enabled as part of this pilot.

## Risk found and how it's being handled

**`extraKnownMarketplaces` entries for third-party (non-Anthropic) repos have already broken
this exact mechanism once in this repo.** PR #5164 (2026-09-09) added
`karpathy-skills` → `forrestchang/andrej-karpathy-skills` (a personal fork). PR #5176
(2026-09-10, one day later) had to remove it: that repo sat outside this environment's
GitHub-proxy allowlist for the actual plugin/marketplace **bootstrap sync** step (a different,
stricter network path than the ad-hoc `git clone` a session can do interactively) — it 403'd
at session start and **broke marketplace sync for every plugin, including `feature-dev`**,
until the entry was removed.

`Egonex-AI/Understand-Anything` is in the same category (third-party org, not
`anthropics/claude-plugins-official`). This session could clone it fine via the interactive
git proxy, but that does **not** prove the bootstrap-sync path can reach it — that's the exact
gap that bit `karpathy-skills`. There's no way to test the bootstrap-sync path from inside a
running session; it only surfaces at the next fresh session start.

**Decision (made explicitly with the user, not unilaterally):** enable it anyway, following
the same pattern as `feature-dev` (PR #5164), accepting the risk — but with the rollback
pre-documented here so it's a one-line fix if history repeats:

### Rollback plan

If a fresh session reports a marketplace-sync failure (e.g. "Edit your environment's setup
script and start a new session," or `feature-dev`/`/spinr-feature` becoming unavailable):

1. Remove the two added entries from `.claude/settings.json`:
   ```diff
   -    "understand-anything": {
   -      "source": {
   -        "source": "github",
   -        "repo": "Egonex-AI/Understand-Anything"
   -      }
   -    }
   ```
   ```diff
   -    "understand-anything@understand-anything@a8cdbf4": true
   ```
2. Commit and push — exactly what PR #5176 did for `karpathy-skills`.
3. No other files are affected; this pilot touches nothing else.

## What was NOT verified

- Whether the actual bootstrap-sync network path can reach `Egonex-AI/Understand-Anything`
  (the core open risk above — only a fresh session start will show this).
- The plugin has not been run (`/understand`) against any spinrvm surface yet. Given the
  README's own warning that a first run "can consume a significant number of tokens on large
  projects," running it is a separate, deliberate decision for whoever starts the next fresh
  session — not bundled into this enablement change.
- No visual/functional test of the dashboard, chat, or diff-impact commands.

## Recommended next step (for whoever picks this up in a fresh session)

1. Confirm the marketplace synced cleanly (no bootstrap error, `feature-dev` still available).
2. Pilot scoped to one surface first: `/understand backend` (highest LOC, most tribal
   knowledge, most onboarding value) rather than the whole monorepo in one run.
3. Leave `--auto-update` off until the generated graph has been reviewed once.
