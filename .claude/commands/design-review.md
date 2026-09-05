# /design-review — Whole-Screen Admin-Dashboard Design Critique

Delegate to the `spinr-ui-ux-critic` agent for a holistic visual/UX critique of
an admin-dashboard screen or flow, judged against Spinr's current **Quiet
Console** direction — not a mechanical token/a11y audit (those are
`spinr-design-consistency-reviewer`'s and `spinr-accessibility-reviewer`'s
jobs, dispatched alongside it here for a full picture). Unlike `/review` and
the other `/*-check` commands, this is meant to run against a shipped page or
flow on demand, not just a diff — a design review usually wants to judge the
whole screen, not only what recently changed.

## Usage

```
/design-review dashboard/staff              # a route under admin-dashboard/src/app/
/design-review admin-dashboard/src/components/ui/badge.tsx   # a specific component
/design-review                               # falls back to staged + unstaged diff, admin-dashboard-scoped only
/design-review PR 123                        # a GitHub PR's admin-dashboard diff
```

## What it does

1. Loads `.claude/skills/spinr-admin-design-system/SKILL.md` up front — the
   critic agent also loads it itself, but loading it here first lets this
   command fail fast with a clear message if the skill is missing, rather
   than the agent silently guessing at the design direction.
2. Scopes the review:
   - A route (e.g. `dashboard/staff`) → resolve to
     `admin-dashboard/src/app/<route>/page.tsx` plus any co-located
     components it imports directly (one level, not the whole transitive
     tree — the critic reads what a reviewer would actually open)
   - A file path → that file plus its direct siblings in the same directory,
     for the "consistency with sibling screens" check
   - No args → `git diff --cached` + `git diff`, filtered to
     `admin-dashboard/**` only (this command has no opinion on rider-app/
     driver-app visual direction — that's a different, unwritten design
     language)
   - `PR N` → the PR's diff via the GitHub MCP tools, same admin-dashboard filter
3. Dispatches **three agents in parallel** over the same scope — independent
   critiques, not a sequential pass:
   - `spinr-ui-ux-critic` — holistic critique (this command's primary purpose)
   - `spinr-design-consistency-reviewer` — brand/token/four-states mechanical audit
   - `spinr-accessibility-reviewer` — WCAG 2.1 AA
4. Presents all three reports under their own headings, in that order — never
   merge or paraphrase them into one narrative; a reviewer wants to see where
   the three perspectives agree and where they don't (e.g. a screen can be
   technically token-compliant and WCAG-clean while still reading as visually
   noisy — that gap between "compliant" and "good" is exactly what
   `spinr-ui-ux-critic` exists to surface)

## Output

`spinr-ui-ux-critic`'s report first:

```
SPINR DESIGN CRITIQUE — <scope>
================================
Reviewed against: Quiet Console (docs/change-log/2026-08-31-quiet-console-stage-1-3.md)
Limitation: code-reasoned, not screenshot-verified — see notes below for anything needing a visual pass.
WHAT WORKS ...
WHAT READS AS NOISY / INCONSISTENT ...
NEEDS A HUMAN VISUAL PASS ...
VERDICT: ON-DIRECTION / NEEDS DESIGN PASS / NOT YET ENGAGING WITH QUIET CONSOLE
```

Then `spinr-design-consistency-reviewer`'s report:

```
SPINR DESIGN CONSISTENCY AUDIT — <scope>
==========================================
BLOCKERS ...
WARNINGS ...
INFO ...
VERDICT: ON-BRAND & COMPLETE / FIX BLOCKERS / NEEDS DESIGN REVIEW
```

Then `spinr-accessibility-reviewer`'s report, in whatever format that agent
already uses.

There is no single combined verdict for this command — the three agents
answer different questions (does this look good / is this token-compliant /
is this accessible), and collapsing them loses exactly the information a
design reviewer needs.

## When to run

- Before flipping `admin_theme_v2_enabled` on for any route (Stage 4 of the
  Quiet Console rollout is an explicit human decision — this command is how
  to gather the evidence for that decision, not a substitute for it)
- After building a new admin-dashboard screen, before it ships
- Whenever a human reviewer asks "does this look right" and wants more than
  a compliance checkbox
- **Not** a substitute for `/review`'s automatic dispatch on every PR — this
  is an on-demand, whole-screen tool a human reaches for, not a CI gate

## Do NOT

- Run this against rider-app or driver-app — there is no documented,
  current design-direction doc for either surface the way Quiet Console
  exists for admin-dashboard; `spinr-ui-ux-critic` explicitly requires the
  skill doc and refuses to guess
- Treat `spinr-ui-ux-critic`'s findings as blocking the way
  `spinr-security-auditor`'s are — this is design judgment for a human to
  weigh, not a pass/fail gate
- Auto-fix findings — all three agents report, humans decide the fix
- Ask `spinr-ui-ux-critic` to judge something it's already disclosed it
  can't (pixel-level visual quality) — take those findings to an actual
  screenshot/Playwright pass instead
