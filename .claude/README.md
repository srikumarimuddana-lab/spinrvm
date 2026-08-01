# `.claude/` — Spinr Claude Code setup

This folder configures how Claude Code works with the Spinr monorepo. Everything here is checked in **except** `settings.local.json` (machine-local, gitignored) and anything with secrets.

PR hygiene is owned jointly by this folder and `.github/`:

```
.github/
├── pull_request_template.md         # Tier 1–4 template; Tier 5–7 expanded by workflow
├── labeler.yml                      # surface:* / area:* / risk:* path-based labels
└── workflows/
    ├── pr-checks.yml                # auto-label, size-advisory, required-fields,
    │                                # merge-conflict-detect, expand-sections, auto-summary
    └── claude-review.yml            # spinr-* agent review with Impact cross-check
```

The `/pr` command auto-fills the template from the diff; the `pr-checks` workflow then validates, expands conditional sections, and posts an advisory summary. `claude-review.yml` would run the deep agent audit on top, but **it is disabled by design** — see below.

## Layout

```
.claude/
├── settings.json             # Project-wide: model, permissions, hooks, plugins
├── settings.local.json       # Machine-local (gitignored) — your API keys, MCP servers
├── launch.json               # Dev server launch configs
├── mcp.example.json          # Template for MCP servers — copy to settings.local.json
├── README.md                 # You are here
│
├── context/                  # On-demand context, imported via @.claude/context/*.md
│   ├── sprint-current.md     # Active sprint state (update every sprint)
│   ├── domain-dispatch.md    # Matching, offer timeout, WS events
│   ├── domain-payments.md    # Fare math, Stripe, corporate billing
│   ├── domain-safety.md      # SOS, insurance periods, night rides
│   └── regulatory-sk.md      # SGI, retention, accessibility, tax
│
├── agents/                   # Specialised subagents (Agent tool)
│   ├── spinr-security-auditor.md
│   ├── spinr-money-auditor.md
│   └── spinr-migration-reviewer.md
│
├── commands/                 # Slash commands (`/name`)
│   ├── commit.md             # /commit
│   ├── pr.md                 # /pr
│   ├── review.md             # /review
│   ├── start.md              # /start
│   ├── status.md             # /status
│   ├── fare-audit.md         # /fare-audit        → money auditor
│   ├── migration-check.md    # /migration-check   → migration reviewer
│   ├── incident.md           # /incident          → P0/P1 runbook
│   └── adr.md                # /adr               → decision record
│
└── hooks/                    # Harness-executed scripts
    ├── pre-commit            # Installed into .git/hooks by scripts/setup-claude.sh — secret scanning
    ├── session-start.sh      # Prints sprint header on new session
    ├── pre-migration-write.sh # Warns before writing to backend/migrations/
    └── post-python-write.sh  # ruff format + ruff check --fix on backend *.py writes
```

Bootstrap script lives outside this folder at `scripts/setup-claude.sh`.

## First-time setup

Run the bootstrap script once per clone:

```
./scripts/setup-claude.sh
```

It is idempotent and does:
1. Installs `.claude/hooks/pre-commit` → `.git/hooks/pre-commit` (secret scan).
2. Scaffolds `.claude/settings.local.json` from `mcp.example.json` if missing — never overwrites.
3. Verifies `jq` and `ruff` are on PATH (hooks degrade gracefully if not).
4. Prints manual follow-ups it cannot automate.

Then, manually:

- **Configure MCP credentials** in `.claude/settings.local.json` (gitignored):
  - Supabase token: use a **read-only** access token scoped to non-PII tables. Never put the service role key there — it bypasses RLS.
  - Context7 API key: free tier is fine.
  - Delete the file if you don't want MCP servers.
- **`ANTHROPIC_API_KEY` is deliberately NOT set** — the AI PR review in
  `.github/workflows/claude-review.yml` is off, a cost decision taken
  2026-08-01 (tracked as `ACTION_ITEMS.md` C7). The workflow stays in the repo
  and skips cleanly with a notice rather than failing. Setting the secret is
  all that is needed to turn it on; nothing else has to change. Until then,
  the `spinr-*` audit agents are available **on demand** — invoke them via the
  Agent tool in a session, or comment `/claude review` on a PR (that path also
  needs the secret). Do not rely on an automatic deep audit landing on a PR.
- **Update the sprint file** at the start of every sprint — edit `.claude/context/sprint-current.md` (goal, in-flight, recently shipped). The SessionStart hook surfaces the goal automatically in every new session.

## What the hooks do

| Hook | Trigger | Behaviour |
|---|---|---|
| `session-start.sh` | New Claude session | Prints active sprint goal if `sprint-current.md` is filled in |
| `pre-migration-write.sh` | Before any Write/Edit | If path is `backend/migrations/*.sql`, prints the migration checklist to stderr. Never blocks. |
| `post-python-write.sh` | After any Write/Edit | If path is `backend/**/*.py`, runs `ruff format` + `ruff check --fix`. Silent on success. |

All hooks exit 0. None block Claude's tool calls — they advise and auto-format only.

## When to use which agent / command

| Situation | Reach for |
|---|---|
| About to commit money-touching code | `/fare-audit` |
| Just added a SQL migration | `/migration-check` |
| Production incident | `/incident` |
| Non-trivial design decision | `/adr` |
| Generic pre-commit scan | `/review` (existing) |
| Broad security sweep | `spinr-security-auditor` via Agent tool |
| Opening a PR | `/pr` — fills the tiered template from the diff |
| PR opened on GitHub | `.github/workflows/pr-checks.yml` validates + expands. `claude-review.yml` is **off** (no `ANTHROPIC_API_KEY`) — run the audit agents yourself if the diff warrants one |

## PR pipeline (what fires when a PR opens)

| Job (in `pr-checks.yml`) | Effect | Blocks merge? |
|---|---|---|
| `auto-label` | Applies `surface:*` / `area:*` / `risk:*` labels from changed paths | no |
| `size-advisory` | Posts a single comment if the PR exceeds 15 files or 500 lines; cleans up if it shrinks | no |
| `required-fields` | Verifies Tier 1 (always) and Tier 2/4 (when `Type ≠ trivial`) are filled — no `<placeholders>` | **yes** |
| `merge-conflict-detect` | If `git log --merges base..head` is non-empty, applies `debug:merge-conflict` and prompts for a Tier 7 note | no |
| `expand-sections` | Appends Tier 5–7 subsection templates (migration / money / UI / auth / bg-loop / RLS / safety / bug-fix / high-risk) based on what the diff touches; idempotent | no |
| `auto-summary` | Posts/updates a comment summarising declared type/risk/audience and flagging declaration-vs-diff mismatches | no |
| `claude-review.yml` | **Disabled — does not run.** `ANTHROPIC_API_KEY` is intentionally unset (C7), so the job skips with a notice. When enabled it runs `spinr-security-auditor` + (conditionally) `spinr-money-auditor` and `spinr-migration-reviewer`, including each agent's IMPACT MISMATCHES cross-check against the PR body. Also skips markdown/`docs/`/`.claude/` diffs and no longer re-runs on every push | no |

`Type: trivial` (formatting / typo / comment / lockfile-only) skips Tier 2–4 enforcement and the section expander. The `scope contract` checkbox is the author's attestation that the diff matches the declared type — abusing `trivial` is a review-time finding.

## Permissions model

`settings.json` allow-list is narrow by design:
- Claude can read anywhere
- Claude can write only inside product folders + `.claude/` subfolders + `CLAUDE.md` + `.github/workflows/claude*.yml`
- `settings.local.json`, `.env*`, `*.pem`, `*.key`, and non-claude CI workflows are **denied** — even if you ask for it, Claude can't write them

To broaden permissions, prefer adding to `settings.local.json` (per-machine) over loosening `settings.json` (affects everyone).

## Updating this config

- Conventions change → update `CLAUDE.md` + relevant `context/*.md` in the same commit
- New domain with deep rules → add a new `context/domain-*.md` and reference it from `CLAUDE.md`
- New audit pattern → add an agent under `agents/` and a wrapper command under `commands/`
- Never add a hook that blocks by default (exit non-zero) without discussing with the team — blocking hooks create hidden dependencies

## Non-goals

- This folder does **not** encode business logic that belongs in code
- This folder is **not** a substitute for product documentation — that lives in `docs/`
- Claude is **not** authorised to push to `main`/`develop`, force-push, or run destructive git commands; those are denied at the permission layer
