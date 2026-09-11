# Feature-Dev Fusion — /spinr-feature spec rationale (2026-09-11)

Full comparison (Anthropic's `/feature-dev` plugin's 7 phases vs. Spinr's own
`/plan` and `/spinr-swarm`) was published as an Artifact rather than checked
into the repo, since it's a comparison narrative rather than something that
needs versioning: `Feature-Dev Fusion` (see the session that produced it for
the link, or ask for it to be regenerated — it is not a repo file). This doc
exists only to record the resulting decision and the exact rationale behind
`.claude/commands/spinr-feature.md`, so neither lives solely in chat history.

## What was compared

`anthropics/claude-plugins-official`'s `feature-dev` plugin (`plugins/feature-dev/commands/feature-dev.md`,
verified at commit `3ea32df`) ships 7 phases: Discovery, Codebase Exploration,
Clarifying Questions, Architecture Design, Implementation, Quality Review,
Summary — each read in full, not paraphrased, along with its three subagents
(`code-explorer`, `code-architect`, `code-reviewer`).

Against Spinr's own `.claude/commands/plan.md` and `.claude/commands/spinr-swarm.md`,
read the same way. Verdict: spinr-swarm is structurally the same 7-phase
shape done again with real teeth on 5 of 7 phases (domain-reviewer debate
instead of generic architecture opinions, CLAUDE.md's actual hard rules
during implementation instead of "follow conventions," an explicit
adversarial test pass, a mandatory Change Impact Log with confidence
labeling instead of a plain summary) — because it assumes the work is
*already scoped*, sourced from `ACTION_ITEMS.md` or a named area.

## The two genuine gaps that motivated `/spinr-feature`

1. **No mandatory clarifying-questions gate for a fresh, unscoped request.**
   CLAUDE.md's "Think before coding" principle covers this in spirit but
   isn't a structured, un-skippable phase the way feature-dev's Phase 3 is
   — including its specific discipline that "whatever you think is best"
   still requires an explicit confirmation, not a silently-assumed yes.
2. **No commissioned parallel exploration for an area with no existing
   `domain-*.md`.** spinr-swarm's "understand before touching" is a
   checklist that assumes a domain doc already exists to read. Nothing in
   Spinr's tooling builds that context from scratch the way feature-dev's
   parallel multi-angle explorers do.

## Why `/spinr-feature` is shaped the way it is

- **Phases 1–3 (Intake, conditional Explore, mandatory Clarify) are new** —
  borrowed from feature-dev's Phases 1–3, adapted: Explore is conditional
  on no domain doc existing yet (cheaper path preferred when available),
  and Clarify is verbatim-equivalent to feature-dev's mandatory gate.
- **Phases 4 onward are not reimplemented** — the command hands off directly
  into `spinr-swarm.md`'s own §2–§7 (Understand before touching through
  Report), explicitly skipping spinr-swarm's own §1 Observe & pick (nothing
  to pick from a backlog; the item is whatever `/spinr-feature` just
  finished scoping). This avoids the drift risk of two copies of the same
  rules — one command is the single source of truth for the back half.
- **One shared command, not one per surface.** Neither `/spinr-swarm` nor
  `/review` has per-surface variants — they dispatch the relevant subset of
  the 24 `spinr-*` reviewers dynamically based on what the diff touches.
  `/spinr-feature` follows the same architecture: the intake discipline
  (ask, explore, clarify) is identical regardless of which surface the
  feature lands in; only the reviewer roster dispatched during
  spinr-swarm's §3 debate and §5 test-then-attack changes, and that
  dispatch logic already exists.

## Not part of this change

feature-dev's own Phases 5–7 (Implementation, Quality Review, Summary) were
deliberately not ported anywhere — spinr-swarm's §4–§7 already cover the
same ground with more rigor (CLAUDE.md's hard rules, an adversarial pass,
a mandatory Change Impact Log). Porting them would have meant maintaining
two weaker copies of rules Spinr already enforces better.
