# /tooling-check — Claude Code Config Drift Audit

Runs `spinr-tooling-hygiene-reviewer` against this repo's own Claude Code
config surface — `.mcp.json`, `.claude/mcp.example.json`, `.claude/hooks/*`,
`.husky/*`, and CLAUDE.md's "Claude-Adjacent Directories" table. This is the
layer none of the other 23 `spinr-*` agents watch, and the one that produced
the dual-pre-commit-hook and duplicate-MCP-server findings in the 2026-09-07
"Spinr Control Plane" audit.

## Usage

```
/tooling-check
```

No arguments — it always audits the whole config surface, not a diff.

## When to run it

- After any change to `.mcp.json`, `.claude/mcp.example.json`,
  `.claude/hooks/*`, `.husky/*`, or `CLAUDE.md`'s directory table
- Periodically as a health check — nothing else in this repo's CI or agent
  fleet catches this class of drift, so it won't surface on its own the way
  an application-code regression would via `ci.yml`
- Before believing a claim like "the pre-commit hook conflict is fixed" —
  verify it instead of assuming a past fix is still in place

## What it is not

- Not a substitute for `/review` or the domain `spinr-*` agents — this
  command only looks at the tooling/config layer, never application code
- Not a blocking CI gate (yet) — it's Agent-tool-invoked, on demand, same as
  the other audit agents while `claude-review.yml` stays disabled (see
  `.claude/README.md` for why)

See `.claude/agents/spinr-tooling-hygiene-reviewer.md` for exactly what it checks.
