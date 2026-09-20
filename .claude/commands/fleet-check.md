# /fleet-check — Agent-Fleet Coverage & Research Audit

Runs `spinr-agent-fleet-strategist` against the current `spinr-*` reviewer
roster and Spinr's own surfaces — builds a coverage matrix (surface →
owning agent or NONE), flags new-code-path gaps, mines `docs/audit/*.md` and
`ACTION_ITEMS.md` for recurring findings with no standing owner, and —
optionally — researches current agentic-review industry practice against
what Spinr's fleet already does.

This is the one check that audits the review fleet itself rather than the
app. Nothing else in this repo tracks whether a new surface has a reviewer
or whether the fleet's own design is falling behind practice — confirmed gap
from the 2026-09-19 inventory sweep.

## Usage

```
/fleet-check                 # coverage matrix + gap scan only
/fleet-check backend/routes/newthing.py   # also checks this path for an owning agent
/fleet-check research        # also runs the industry-technique research section
```

## When to run it

- After adding a new backend route file, service, background loop, or
  frontend screen — confirm something in the fleet actually claims it before
  assuming a future `/review` or `/full-audit` pass would catch a regression
  there
- Periodically (recommended: monthly, via a Routine) as the codebase and
  roster both grow — coverage drift is silent until something breaks and no
  agent flagged it
- When evaluating whether to add a new `spinr-*` agent — run this first so
  the decision is based on an actual gap, not a guess
- With `research`, when you want a grounded, cited recommendation on a new
  agentic-QA technique before deciding to adopt it — not more than
  monthly, since industry practice doesn't shift week to week and each run
  costs a web-search budget

## What it is not

- Not a substitute for `/full-audit` or `/review` — this command never
  looks at application-code correctness, only whether the fleet covers a
  surface
- Not authorization to build anything — its output is a recommendation.
  Building a new agent, widening an existing one, or adopting a new
  technique still goes through this repo's normal escalation
  (`AskUserQuestion` for anything structural or ambiguous, per CLAUDE.md's
  pre-merge release gates)

See `.claude/agents/spinr-agent-fleet-strategist.md` for exactly what it
checks and its output format.
