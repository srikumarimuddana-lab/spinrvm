# /spinr-feature — Guided Intake for a Fresh, Unscoped Feature Request

The front door for "I have an idea, nothing is scoped yet." `/plan` assumes the task is already well-specified; `/spinr-swarm`'s discovery mode assumes the work is already sitting in `ACTION_ITEMS.md`. Neither forces the two disciplines a genuinely fresh, ambiguous request needs: a mandatory clarifying-questions gate, and — for an area with no existing domain context — commissioned parallel exploration before anyone designs anything. See `docs/audit/2026-09-11-feature-dev-fusion.md` for the full comparison this command is built from.

Once the request is understood, explored, and clarified, this command hands off into `/spinr-swarm`'s own Brainstorm-&-debate-through-Report backbone unchanged — that half is already more rigorous than any generic alternative (domain-reviewer debate before code exists, CLAUDE.md's hard rules during implementation, an adversarial test pass, a mandatory Change Impact Log). This command does not reimplement any of that; it only fills the gap in front of it.

## Usage

```
/spinr-feature <rough description of what you want>
/spinr-feature                       # no description — Phase 1 asks for one
```

## When to use this vs. `/plan` vs. `/spinr-swarm`

| Situation | Use |
|---|---|
| You already know the files and the shape of the change | `/plan` |
| Autonomous "make it better," sourced from the backlog or a named area | `/spinr-swarm` |
| A person has a fresh feature idea that isn't scoped, isn't in `ACTION_ITEMS.md`, and might touch an area with no `domain-*.md` yet | **`/spinr-feature`** |

## Phase 1 · Intake

**Goal**: Understand what's being asked before touching anything.

1. Create the todo list for all phases below.
2. If the request is vague, ask: what problem is this solving, what should it actually do, any known constraints or non-negotiables?
3. Summarize your understanding in one paragraph and confirm it's right before moving on.

## Phase 2 · Explore (conditional)

**Goal**: Build missing context — but only pay for it when it's actually missing.

1. Check whether `.claude/context/domain-*.md` already covers this feature's area. If yes, **skip this phase** — Phase 4 (below) reads that file directly, which is cheaper and already-vetted.
2. If no domain doc covers it, launch 2–3 exploration agents in parallel via the Agent tool (`Explore` type, or `general-purpose` for anything needing web context), each covering a different angle:
   - Similar existing features and how they're implemented
   - Architecture/abstraction layers this feature would sit in
   - UX/UI patterns already established for comparable flows (if user-facing)
   - Testing conventions this area already follows
   Each should return the 5–10 files most essential to understanding the area, not just prose.
3. Read the files they identify. Present a short summary of what you now know before moving on — don't silently carry it forward unstated.

## Phase 3 · Clarify (mandatory — do not skip)

**Goal**: Resolve every ambiguity before anyone designs anything.

1. From the request plus whatever Phase 2 surfaced, list every underspecified point: edge cases, error handling, integration points, scope boundaries, backward compatibility, performance expectations.
2. Present the full list to the user at once, organized, not trickled out one at a time.
3. **Wait for actual answers before proceeding.** If the user says "whatever you think is best," give your specific recommendation and get an explicit yes — a shrug is not a confirmation.

## Phase 4 onward · Hand off to `/spinr-swarm`

With the feature now understood, explored, and clarified, proceed directly into `/spinr-swarm`'s own sections **§2 through §7** (`.claude/commands/spinr-swarm.md`), unchanged:

- **§2 Understand before touching** — blast-radius grep, the relevant `domain-*.md`, and the deliberate-decisions check (if the "problem" is actually a recorded decision, stop and report instead of redesigning it).
- **§3 Brainstorm & debate** — Option A/B/C scored on the 10-dimension table, debated by whichever `spinr-*` reviewers this feature's surface(s) actually implicate.
- **§4 Implement under the constitution** — all of CLAUDE.md, unchanged: ≤3-file subtasks, Decimal money, state-machine guards, feature flags for anything user-visible.
- **§5 Test, then attack** — real suites, a regression test, an adversarial pass, then `/review`.
- **§6 Document & close** — Change Impact Log for anything behavior-changing, `ACTION_ITEMS.md` updated if this surfaced follow-up work.
- **§7 Report** — the same structured report shape, same KNOWN/INFERRED/TESTED/UNTESTED/UNKNOWN labeling discipline.

Skip spinr-swarm's own **§1 Observe & pick** — there's nothing to pick from a backlog; the item is the feature this command just finished scoping. Its operating-agreement caps and hard bounds (regulated-surface soak time, production-immutability, never weakening a safety mechanism) apply exactly as written there.
