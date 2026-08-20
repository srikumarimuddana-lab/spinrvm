# The pipeline: how a piece of work moves from idea to running in production

This is the "graph" referred to in `RESEARCH.md` — a chain of stages a piece of work
(a "surface") passes through, with one stage able to send work backward to an earlier
one instead of always moving forward. It's implemented as a Claude Code `Workflow`
script, not a new service — see `agents/pipeline.workflow.js`.

## The ten stages

```
 1. IDEATION        →  2. REQUIREMENTS   →  3. PLAN & DESIGN   →  4. ARCHITECTURE
        ▲                                                                │
        │                                                                ▼
10. OPERATIONS  ←  9. RELEASE  ←  8. CHANGE REVIEW  ←  7. SECURITY  ←  6. QA  ←  5. DEVELOPMENT
        │                              │                    │            │
        └──────────── incident/────────┴──── finding sends ─┴── back to ─┘
                       regression           work back to
                       feeds next           Development (loop,
                       Ideation             not a dead end)
```

Plain-language version of that diagram: the work moves forward through ten stages.
Three of those stages — QA, Security, and Change Review — are allowed to reject and
send the work back to Development instead of passing it on. That's the "graph, not a
straight line" property borrowed from LangGraph's design (see `RESEARCH.md`). Nothing
skips a stage to save time; a stage that has nothing to add still runs and says so —
that's what makes the paper trail trustworthy later.

| # | Stage | Who runs it (see `agents/roles/`) | Input | Output | Can send work backward? |
|---|---|---|---|---|---|
| 1 | Ideation | Leadership + Product roles, brainstorming together | A one-line problem statement | 2-4 candidate approaches, one recommended | No — this stage *is* the start |
| 2 | Requirements | Product role | Chosen approach from Ideation | What must be true when this is done (acceptance criteria), explicitly scoped out-of-bounds items | No |
| 3 | Plan & Design | Product + Engineering roles | Requirements | UX/data flow, what's additive vs. destructive, feature-flag decision | No |
| 4 | Architecture | Engineering role | Design | Which files/modules change, blast-radius list (every other caller of anything touched) | No |
| 5 | Development | Engineering role | Architecture plan | Actual diff | — |
| 6 | QA | Engineering role (test-focused) | Diff | Tests run + result, coverage note | **Yes** — back to Development on failure |
| 7 | Security | Trust/Safety/Security role | Diff + QA result | Pass, or specific findings | **Yes** — back to Development on a real finding |
| 8 | Change Review | Finance/Legal/People role (compliance angle) + a human when the surface is live-tested | Everything above | Change Impact & Risk Log entry (CLAUDE.md's required template) | **Yes** — back to Development if rollback plan is missing/weak |
| 9 | Release | Engineering role | Approved change | PR opened (draft), CI status | No — a red CI here is a bug in an earlier stage, not this stage's problem to route around |
| 10 | Operations | Operations & Support role | Live/merged change | What to watch, for how long, what "this broke" looks like | Feeds back into Ideation if it *does* break |

## Why these ten and not fewer

Anthropic's own guidance (see `RESEARCH.md`) warns against giving every agent every
tool. Splitting Development from QA from Security from Change Review means each
stage's agent only needs the tools that stage requires — a QA-stage agent doesn't need
GitHub write access, a Security-stage agent doesn't need to edit fare code. That's
"capability narrowing" applied to a pipeline instead of a single agent.

## Why a stage can loop backward, not just fail

A pipeline that only fails forward (QA fails → whole run fails → a human restarts it
from scratch) throws away everything Ideation through Development already got right.
Looping back to Development *only* — keeping the earlier decisions — is cheaper and
matches how a real engineering team actually handles a failed review: nobody re-does
the requirements doc because a test failed.

## Scaling the pipeline to the size of the work

Not every "surface" deserves all ten stages run as separate heavyweight agent calls.
A two-file documentation cleanup and a new Stripe webhook handler are not the same
size of problem. The pipeline script scales itself:

- **Chore / cleanup** (no live-tested surface touched): stages 1-4 collapse into one
  combined "scope it" pass, stages 5-7 run for real, stage 8's Change Impact Log is
  written but marked not-required (per CLAUDE.md, only mandatory for live-tested
  surfaces), stage 10 is a one-line "nothing to watch" note.
- **Small feature, no live-tested surface**: all ten stages run, but Ideation only
  generates 2 candidates instead of 4, and Architecture's blast-radius check is a
  single grep pass, not a fan-out.
- **Anything touching rides/dispatch/payments/auth/corporate/safety**: all ten stages
  run at full depth, Change Review is mandatory and blocking, and a human is looped in
  at Change Review before Release regardless of how confident the agents are — this is
  the one place the pipeline does not run fully autonomously, by design (see
  `GUARDRAILS.md`).

## What every stage produces, in the same three documents

Every run of the pipeline writes to the same three files under `agents/runs/<date>-<slug>/`,
regardless of which stages ran:

### 1. `progress-report.md`
A running log, one entry per stage, written **as the stage finishes**, not
reconstructed afterward. Each entry: what stage, what it did, how long it took, what
it decided, what (if anything) it sent back and why.

### 2. `decisions.md`
Every non-obvious call, one entry each, in this shape:

```
## Decision: <short title>
**Stage:** <which stage made this call>
**What was decided:** <the decision, one or two sentences>
**Why:** <the actual reasoning — not "best practice", the specific tradeoff>
**Alternative(s) considered:** <what else was on the table, and why it lost>
**Reversible?** <yes/no, and how, if something later shows this was wrong>
```

### 3. `challenges-and-issues.md`
Anything that didn't go smoothly — a stage that had to loop back, a blast-radius
grep that turned up a surprising caller, a place the agent wasn't confident and said
so instead of guessing. This file existing and being non-empty is a *good* sign, not
a failure — a run that reports zero challenges on anything beyond a trivial change is
more likely to have missed something than to have had a genuinely clean run.

These three files, plus the PR itself, are the full record of a run — written in
plain language on purpose, so a non-engineer on the team (or a future audit) can read
what happened without translating agent jargon first.

## Where this lives

- `agents/pipeline.workflow.js` — the actual Workflow script (the "graph engine")
- `agents/roles/*.md` — one file per department, read by the relevant stage's agent
  as its system context. Each department also has an `agents/roles/<department>/
  *.md` subdirectory of individual role docs (41 total, one per role on the org
  chart) — these are for humans browsing the org, not read by the pipeline itself.
- `agents/GUARDRAILS.md` — the standing "can't do" list every stage must respect
- `agents/runs/<date>-<slug>/` — one folder per pipeline run, holding the three
  documents above
