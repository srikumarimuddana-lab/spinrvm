# /design-review — Whole-Screen Design Critique

Delegate to the `spinr-ui-ux-critic` agent for a holistic visual/UX critique of
a screen or flow in **any of the three surfaces** — admin-dashboard, rider-app,
or driver-app — judged against that surface's own current design direction
(admin-dashboard's **Quiet Console**; rider-app/driver-app's warmer,
per-app-distinct direction in `spinr-rider-driver-design-system`) — not a
mechanical token/a11y audit (those are `spinr-design-consistency-reviewer`'s
and `spinr-accessibility-reviewer`'s jobs, dispatched alongside it here for a
full picture, and they already cover all three surfaces). Unlike `/review`
and the other `/*-check` commands, this is meant to run against a shipped
screen or flow on demand, not just a diff — a design review usually wants to
judge the whole screen, not only what recently changed.

## Usage

```
/design-review dashboard/staff                                # an admin-dashboard route under admin-dashboard/src/app/
/design-review admin-dashboard/src/components/ui/badge.tsx    # a specific admin-dashboard component
/design-review rider-app/app/wallet.tsx                        # a rider-app screen
/design-review driver-app/app/driver/(tabs)/index.tsx          # a driver-app screen
/design-review                                                  # falls back to staged + unstaged diff, grouped per surface touched
/design-review PR 123                                           # a GitHub PR's diff, grouped per surface touched
```

## What it does

1. Determines which surface(s) the scope touches from the path(s) — one of
   `admin-dashboard/`, `rider-app/`, `driver-app/`. Loads the matching skill
   doc up front for each surface in scope — `.claude/skills/spinr-admin-design-system/SKILL.md`
   for admin-dashboard, `.claude/skills/spinr-rider-driver-design-system/SKILL.md`
   for rider-app/driver-app. The critic agent also loads its own copy, but
   loading it here first lets this command fail fast with a clear message if
   a skill is missing, rather than the agent silently guessing at the design
   direction. A scope spanning more than one surface runs each surface as its
   own tracked review below — never blend the two directions into one verdict.
2. Scopes the review:
   - An admin-dashboard route (e.g. `dashboard/staff`) → resolve to
     `admin-dashboard/src/app/<route>/page.tsx` plus any co-located
     components it imports directly (one level, not the whole transitive
     tree — the critic reads what a reviewer would actually open)
   - A rider-app/driver-app screen path (Expo Router file under `app/`, e.g.
     `rider-app/app/wallet.tsx` or `driver-app/app/driver/(tabs)/index.tsx`)
     → resolve to that screen file plus any components it imports directly
     from `components/` (one level), same "what a reviewer would actually
     open" principle
   - A file path (any surface) → that file plus its direct siblings in the
     same directory, for the "consistency with sibling screens" check
   - No args → `git diff --cached` + `git diff`, grouped by which of
     `admin-dashboard/**`, `rider-app/**`, `driver-app/**` each changed file
     falls under; run one critic pass per surface with changes, skip surfaces
     with none
   - `PR N` → the PR's diff via the GitHub MCP tools, grouped the same way
3. Dispatches **three agents in parallel** per surface in scope — independent
   critiques, not a sequential pass:
   - `spinr-ui-ux-critic` — holistic critique (this command's primary purpose)
   - `spinr-design-consistency-reviewer` — brand/token/four-states mechanical audit
   - `spinr-accessibility-reviewer` — WCAG 2.1 AA
4. Presents all reports under their own headings, grouped by surface first
   and then by agent — never merge or paraphrase them into one narrative; a
   reviewer wants to see where the three perspectives agree and where they
   don't (e.g. a screen can be technically token-compliant and WCAG-clean
   while still reading as visually noisy — that gap between "compliant" and
   "good" is exactly what `spinr-ui-ux-critic` exists to surface), and where
   a multi-surface scope is in play, wants each surface judged against its
   own direction rather than one blended verdict.

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

- Before flipping `admin_theme_v2_enabled` on for any admin-dashboard route
  (Stage 4 of the Quiet Console rollout is an explicit human decision — this
  command is how to gather the evidence for that decision, not a substitute
  for it)
- After building a new screen on any of the three surfaces, before it ships
- Whenever a human reviewer asks "does this look right" and wants more than
  a compliance checkbox
- When considering a rider-app/driver-app change against the warmer,
  per-app-distinct direction in `spinr-rider-driver-design-system` — e.g.
  checking a new driver-app screen actually matches driver-app's
  punchier/faster tone rather than defaulting to rider-app's calmer pace
- **Not** a substitute for `/review`'s automatic dispatch on every PR — this
  is an on-demand, whole-screen tool a human reaches for, not a CI gate

## Do NOT

- Cross-apply one surface's direction to another — admin-dashboard's Quiet
  Console ("stay neutral, color is for signal only") does not apply to
  rider-app/driver-app, and their warmer/energetic expectation does not
  apply back to admin-dashboard. `spinr-ui-ux-critic` guards against this
  itself, but scope requests to one surface where possible rather than
  relying on that as the only backstop
- Treat rider-app and driver-app as one shared visual identity when
  reviewing both — they're related (same brand palette, same typeface) but
  deliberately different in tone; don't flag driver-app for being "louder"
  than rider-app in a combined review, that's the design
- Treat `spinr-ui-ux-critic`'s findings as blocking the way
  `spinr-security-auditor`'s are — this is design judgment for a human to
  weigh, not a pass/fail gate
- Auto-fix findings — all three agents report, humans decide the fix
- Ask `spinr-ui-ux-critic` to judge something it's already disclosed it
  can't (pixel-level visual quality) — take those findings to an actual
  screenshot/Playwright pass instead (rider-app and driver-app have no
  automated visual-regression tooling at all; admin-dashboard's seeded
  Playwright visual-regression job covers only its 6 baselined pages — see
  CLAUDE.md's release-gate §6 before assuming a diff is spurious)
