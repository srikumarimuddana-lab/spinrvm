# /spinr-swarm — Run One Autonomous Improvement Cycle

Execute one full cycle of the swarm protocol (`docs/framework/08-swarm-protocol.md`): pick the highest-value safe improvement, take it through brainstorm → debate → implement → adversarial test → validate → document, and end with a ranked next recommendation. This is the standing answer to "make the app better" with no narrower task given.

## Usage

```
/spinr-swarm                       # discovery mode: pick from ACTION_ITEMS.md, else sweep
/spinr-swarm <area or file>        # cycle scoped to an area (e.g. dispatch, rider-app booking)
/spinr-swarm audit                 # discovery sweep only — report + backlog entries, no implementation
/spinr-swarm charter <A-H>         # work an Enhancement Charter track (docs/framework/09-enhancement-charter.md)
/spinr-swarm ux-ideate <surface>   # UX/interaction-design research — proposals only, never implements (see §1b)
```

Charter mode: the first invocation on a track is an **assessment cycle** — re-verify the track's "exists today" column against current code, score the gaps, file chosen items into `ACTION_ITEMS.md`, and recommend the first implementation cycle; no implementation. Later invocations implement one filed item per cycle under the full loop. Honor disposition labels (Research → ADR, Process → human-ready draft, never code). Open P0/P1 backlog items always preempt charter work. When a charter cycle adds UI surface, it must also state what it removed or simplified — the anti-overwhelm rule.

## 1 · Observe & pick (discovery mode)

1. Read `ACTION_ITEMS.md` open `[ ]` items. Highest band first (P0 → P4); within a band, rank by `(user × business × reliability × security impact × confidence) / effort`.
2. Skip items marked as needing a human with dashboard access (most P2 operational items) — report them as blocked-on-human instead of simulating them.
3. Only if no open item fits the scope: run a discovery sweep (`/full-audit` on the area, or targeted reading) and file new findings into `ACTION_ITEMS.md` before working one.
4. State the pick and why in one paragraph, with the value ranking.

## 1b · UX ideation mode (`ux-ideate`)

```
/spinr-swarm ux-ideate <surface>   # rider-app | driver-app | admin-dashboard | all
```

Research and propose only — **never implements, never opens a PR**. For "what
should this surface's interaction design do next" (buttons, transitions,
loading/empty/error states, micro-interactions), not "fix this known bug" —
use plain discovery mode for that. Ends with dated entries filed into
`docs/ux-ideas.md`, never code.

1. **Load the real design-system truth first, before generating a single idea.**
   Skipping this is how admin-dashboard's `#2785` "futuristic" misstep happened,
   corrected 2026-08-31 to "Quiet Console" (see the `spinr-admin-design-system`
   skill — load it in full for admin-dashboard work, every time, not from memory).
   - `admin-dashboard` → `spinr-admin-design-system` skill + a live read of
     `admin-dashboard/src/app/globals.css` for the actual current token values.
   - `rider-app` / `driver-app` → `.claude/context/brand-spinr.md`,
     `shared/theme/index.ts`, and that app's existing button/transition/
     state-handling components (grep for them — read what's actually there,
     don't assume a pattern exists).
   - `all` → run this step once per surface; never blend their design languages
     into one set of ideas.
2. **Explore in parallel, different angles** — the same multi-agent "map the
   existing patterns before proposing anything" move a generic feature-dev
   workflow uses for novel scope, applied here to interaction design instead of
   code architecture (routine backlog items are usually well-specified enough
   not to need this — reserve it for open-ended ideation and true discovery
   sweeps):
   - Agent A: catalog the surface's current interaction vocabulary — every
     button variant, transition/animation (note duration/easing where
     discoverable from code, and which transitions look ad-hoc — no easing,
     inconsistent durations across visually-similar actions — versus
     deliberate), and loading/empty/error-state pattern already in use, with
     file:line. This is the one motion-*craft* angle no other reviewer
     checks: `spinr-design-consistency-reviewer` only checks reduced-motion
     respect, `spinr-ui-ux-critic` only checks whether motion reads as
     decorative noise — neither judges whether a transition's timing/easing
     is actually well-made. Ideas must build on this catalog, not duplicate it.
   - Agent B: friction as raw material for ideas, not a compliance pass.
     `spinr-design-consistency-reviewer` already owns the mechanical "does
     every async action have all four states" finding — run that agent for
     the audit; don't restate its findings here. Instead look for: abrupt
     (non-animated) state changes a user would feel as jarring, button
     treatments inconsistent across visually-similar actions, and existing
     loading/empty/error states that are functionally present but feel
     low-effort (a bare spinner where a skeleton would read calmer, a plain
     "no results" where empty-state guidance would help). Each finding needs
     a concrete file:line + what a user actually sees, framed as "here's an
     upgrade idea," not "here's a violation."
   - Agent C: precedent — does a comparable pattern already exist elsewhere in
     this surface (or `shared/`) that a new idea should reuse instead of
     introducing a fourth way to do the same thing?
3. **Respect hard constraints; don't rediscover known gaps.** Cross-check every
   idea against the design system's own "known, tracked gaps" and "never
   propose" lists (e.g. admin-dashboard's OLED-true-black dark background,
   Quiet Console's "calmer, not busier" direction) before including it. A
   violation of one of these isn't a creative idea — it's a bug against the
   design system. Drop it, or reframe it as a plain bug report instead.
4. **Score with the same 10-dimension table** the rest of the swarm protocol
   already uses (`docs/framework/08-swarm-protocol.md`) — reuse it rather than
   inventing a separate ad-hoc "good taste" scale. Every entry needs the score,
   the file:line evidence from step 2, and which existing pattern (if any) it
   reuses or replaces.
5. **File into `docs/ux-ideas.md`** (create if absent), one dated entry per
   idea, grouped by surface, ranked highest-score first. State plainly in the
   report: this is a proposal list, not a commitment — nothing here gets built
   until a human picks an entry, at which point it re-enters the normal
   `/spinr-swarm <item>` cycle (options, specialist review, tests, feature
   flag, Change Impact Log) like any other backlog item.

## 2 · Understand before touching

- Read the relevant domain contract (`.claude/context/domain-*.md`), any ADR or decision write-up covering the area, and the actual code.
- **Blast radius first**: grep every caller/reader/importer of what will change; write the list down.
- Check the deliberate-decisions list (fare-estimate wait, retention model, dual-import, unredacted export, …) — if the "problem" is a recorded decision, stop and report instead of fixing.
- For genuinely novel/undiscovered scope (no existing `ACTION_ITEMS.md` entry, no domain doc covers it) — use the parallel-explorer-agent pattern from `ux-ideate` step 2 instead of one linear read; a well-specified backlog item usually doesn't need this.

## 3 · Brainstorm & debate

- Produce **Option A (minimal) / B (moderate) / C (strategic)** with the 10-dimension score table from the swarm protocol.
- Run the debate: dispatch the relevant `spinr-*` reviewer agents on the *proposal* (not just the diff later). Contested → resolve by evidence, testing competing hypotheses where cheap.
- Genuinely user-owned trade-off → `AskUserQuestion`. Otherwise decide and record the decision.

## 4 · Implement under the constitution

All of `CLAUDE.md` applies unchanged: ≤3-file subtasks committed one at a time, ~200-line diffs, surgical changes, Decimal money, state-machine guards, required scaffolding never "simplified" away, feature flags via `app_settings` for user-visible changes, additive over destructive.

## 5 · Test, then attack

1. Repo's own checks for every touched surface: `ruff check` + `pytest` (backend), `jest` (rider/driver), `vitest` (admin) — plus a real production build for frontend changes.
2. Regression test for the fixed behavior (reproduce first, then pass).
3. **Adversary pass** on the change itself: races, partial failure, dependency outage, malicious/stale clients, 10× load reasoning. Fix what breaks; retest.
4. Route the final diff through `/review` (or `/full-audit` for cross-cutting changes).

## 6 · Document & close

- Behavior-changing → full Change Impact Log (`docs/templates/CHANGE_IMPACT_LOG.md`), including "What was NOT verified" and a live-data rollback plan.
- Update `ACTION_ITEMS.md` (mark the item, or file follow-ups discovered mid-cycle).
- Commit per subtask; push; PR per `/pr` conventions.

## 7 · Report

End every cycle with the protocol's report shape — Discovery · Impact · Root cause · Options+scores · Adversarial review · Decision · Implementation · Validation (commands + counts) · Results · Remaining risks · **Next recommendation** — with every claim labeled KNOWN / INFERRED / TESTED / UNTESTED / UNKNOWN and confidence HIGH/MEDIUM/LOW. No "definitely safe", no "production ready" without evidence.

## Operating-agreement caps (charter §Operating agreement — check before starting any cycle)

- ≤ 2 charter tracks in flight; ≤ 1 open PR touching a regulated surface (money, rides/dispatch, auth, insurance periods, corporate billing, safety); ≤ 3 open swarm PRs total. At a cap: work review feedback, docs, or Process drafts instead of opening new work.
- 24-hour soak after a regulated-surface merge before the next regulated cycle (docs/tests/dark-flagged changes exempt).
- Transparency test at brainstorm time: *can a rider or driver see what this does and why, before it affects them?* Fail → redesign or drop. Driver-facing incentives are legal-review-required before the implementation cycle starts.
- Every cycle names the KPI it moves and what the user will see; can't answer both → don't run the cycle.

## Hard bounds

- Production is immutable without explicit human approval: no prod DB writes, credential rotation, irreversible migrations, mass user changes, payment-config changes, or disabling of any security control/gate/hook — ever, from this command.
- Never weaken a safety mechanism to go faster; gate decay gets a `[CR]` issue.
- One cycle per invocation: finish (or cleanly hand off) the picked item before proposing the next.
