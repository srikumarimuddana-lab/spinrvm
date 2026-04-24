# `.claude/` — Spinr Claude Code setup

This folder configures how Claude Code works with the Spinr monorepo. Everything here is checked in **except** `settings.local.json` (machine-local, gitignored) and anything with secrets.

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
    ├── pre-commit            # Installed into .git/hooks — secret scanning
    ├── session-start.sh      # Prints sprint header on new session
    ├── pre-migration-write.sh # Warns before writing to backend/migrations/
    └── post-python-write.sh  # ruff format + ruff check --fix on backend *.py writes
```

## First-time setup

1. **Install the pre-commit hook** (once per clone):
   ```
   cp .claude/hooks/pre-commit .git/hooks/pre-commit
   chmod +x .git/hooks/pre-commit
   ```

2. **Configure MCP servers** (optional, improves Claude's reach):
   ```
   cp .claude/mcp.example.json .claude/settings.local.json
   # edit settings.local.json: paste real keys under mcpServers
   ```
   - Supabase token: use a **read-only** access token scoped to non-PII tables. Never put the service role key there — it bypasses RLS.
   - Context7 API key: free tier is fine.

3. **Update the sprint file** at the start of every sprint:
   ```
   # edit .claude/context/sprint-current.md
   # fill in Sprint goal, In-flight table, Recently shipped
   ```
   The SessionStart hook surfaces the Sprint goal automatically in every new session.

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
| PR opened on GitHub | `.github/workflows/claude-review.yml` auto-triggers |

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
