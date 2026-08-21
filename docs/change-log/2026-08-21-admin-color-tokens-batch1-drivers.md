# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-21 |
| Author | Claude Code (session, vikas@ngitservices.com) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (see PR) |
| Related issue or gap ID | #2816 Stage 1, Batch 1 — see `docs/change-log/2026-08-21-admin-color-token-migration-plan.md` |

## 1. Issue / gap identified

`drivers/page.tsx` had 441 raw hardcoded-Tailwind-color occurrences (#2816
backlog). **Correction to this batch's own original scoping**: the plan
doc claimed this file was "uniformly broken (zero `dark:` occurrences)" —
that was wrong, from a garbled shell-output read earlier in this session.
Direct verification found the file is mostly *already* dark-mode-aware
(107 of 132 color-bearing lines pair a color with a `dark:` variant on
the same line); only **25 lines** genuinely lack any `dark:` treatment.

## 2. Root cause

Two different root causes bundled under one grep count:
- **25 lines**: genuinely missing dark-mode treatment — a light-shade
  color (e.g. `bg-gray-300`, `text-red-500`, `text-amber-600`) applied
  directly with no theme awareness, inconsistent with the rest of the
  same file's own established pattern (e.g. the same "online dot"
  pattern already uses `bg-muted-foreground/30` for its off-state two
  components over, at lines 2016/2020 — the fixed lines just hadn't
  caught up to that pattern).
- **~107 lines**: correctly excluded from this batch — self-contained
  color pairs (`bg-emerald-100 ... dark:bg-emerald-900/30 ...`) that
  already work in both themes, matching #2816's own established
  "hardcoded but fine" category from prior batches (staff, forecast,
  service-areas change-logs).

## 3. Fix / remediation

Migrated the 16 fixable instances (of the 25 broken lines) to the
semantic tokens now available (`--success`/`--warning` from PR #4325,
`--destructive`/`--muted-foreground` pre-existing):

- Online/offline status dots (5 instances) → `bg-success` / `bg-muted-foreground/40`
- "No fare configs" warning text → `text-warning`
- Required-field asterisk + validation error text (2 instances) → `text-destructive`
- Verification checkmark icon + progress bar (2 instances) → `text-success` / `bg-success`/`bg-warning`
- Profile/vehicle photo status dots (2 instances) → `bg-success`
- Stripe Connect "Connected" dot → `bg-success`
- Training completion progress bars (2 instances) → `bg-success`
- Document-expiry `neutral` status dot (in a 4-state `styles` object where
  the sibling `emerald`/`amber`/`red` entries already have `dark:` pairs
  on `bg`/`primary`/`secondary`, just not on `dot` — left those 3
  `dot` fields alone since their lines already carry `dark:` elsewhere,
  same per-line classification rule applied everywhere else in this batch) → `bg-muted-foreground/40`
- Document-card expiry text → `text-destructive`

**Deliberately left untouched** (9 of the 25 broken lines), and why:

- **Solid-fill buttons/badges with white text** (`bg-emerald-600 text-white`,
  `bg-red-600 hover:bg-red-700 text-white`, and a 3-way `bg-emerald-500 text-white`/`bg-red-500 text-white`/`bg-amber-500 text-white`
  badge ternary — 5 instances): computed the actual contrast before
  deciding, not assumed. Dark mode's `--success` (#30d158, a bright
  vibrant green) is only **2.02:1** with white text — well under WCAG
  AA's 4.5:1 — because the light/dark `--warning`/`--success` tokens were
  contrast-verified in PR #4325 as **plain text against the page
  background**, not as a solid fill behind white text (see that PR's own
  `globals.css` comments). Swapping these buttons to `bg-success text-white`
  would have been a genuine dark-mode contrast *regression*, not a fix.
  Left as raw Tailwind classes; real fix needs new `--success-foreground`/
  `--warning-foreground` tokens (or routing through the shared `<Button>`
  component's already-verified `variant="destructive"` treatment,
  `bg-destructive text-white ... dark:bg-destructive/60`) — out of scope
  here, flagged for a future token-expansion pass.
- **Two decorative icon accents** (`text-amber-500`/`text-blue-500` on a
  rating star and a location pin) — not semantically a warning/success/
  destructive state, no token maps to them; correctly excluded.
- **One self-contained white overlay button** (`bg-white/95 text-gray-900`
  on an image-preview control) — deliberately theme-invariant (stays
  visible over any photo, in either theme); correctly excluded.
- **`text-gray-800` inside a `bg-white/90` circle overlay icon** — same
  reasoning, self-contained white circle regardless of page theme.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to `drivers/page.tsx`** — 16 lines changed
  (32 diff lines), all string-literal className substitutions, no
  logic/prop/state changes.
- Every token used (`bg-success`, `text-success`, `bg-warning`,
  `text-warning`, `text-destructive`, `bg-muted-foreground/40`,
  `border-success/40`) is either pre-existing and already used
  extensively elsewhere in this same file, or (`success`/`warning`) was
  contrast-verified as plain-text/non-text-UI-element color against the
  page background in PR #4325 — a solid `<span>`/`<div>` status dot
  clears the laxer 3:1 non-text-UI threshold if the same color already
  clears 4.5:1 as text against the same background, so reusing it for
  dots (not buttons) is safe without new contrast math.
- No behavior change for any state where `driver.is_online`/verification/
  expiry status was already true or false before this change — only the
  *color* rendered for each state changes, not which state renders.

## 5. User-experience effect

- **Internal admin only.** Visually: 16 spots that previously used a
  hardcoded green/amber/red/gray now render via the theme's actual
  success/warning/destructive/muted tokens. In light mode these are
  visually near-identical to before (the tokens were chosen to match).
  In dark mode, the 5 "online/offline dot" and 2 "photo status dot"
  instances gain real dark-mode contrast for the first time (previously
  `bg-gray-300`/`bg-gray-400` on a near-black page background).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/drivers/page.tsx` | 16 raw-color→semantic-token substitutions (see §3) | #2816 Batch 1 |

## 7. Before / after (representative sample — full list in §3)

```tsx
// Before
<span className={`... ${driver.is_online ? "bg-emerald-500" : "bg-gray-300"}`} />
<p className="text-[10px] text-amber-600 mt-1">...</p>
<span className="text-red-500">*</span>

// After
<span className={`... ${driver.is_online ? "bg-success" : "bg-muted-foreground/40"}`} />
<p className="text-[10px] text-warning mt-1">...</p>
<span className="text-destructive">*</span>
```

## 8. Rollback plan

`git-revert-safe` — single file, string-literal className changes only,
no data/API/schema change.

## 9. Verification performed

- [x] Real production build (`npm run build`) — succeeded.
- [x] `npx tsc --noEmit` — clean.
- [x] `npx vitest run` — 339/339 passed.
- [x] Computed real WCAG contrast math (relative-luminance formula, not
  eyeballed) for `--success`'s dark-mode value against white text before
  deciding to leave the solid-fill buttons untouched — 2.02:1, fails AA —
  this is why those 5 instances were deliberately NOT migrated.
- [x] Classified every one of the 25 candidate lines individually (fixed
  vs. deliberately excluded, with a stated reason each) rather than a
  blind mechanical substitution — matches the established #2816
  methodology from prior batches.
- [ ] **Not manually click-tested/screenshotted in dark mode** — this
  sandbox has no way to render the app visually with real theme
  switching (same limitation noted in every UI change-log this session);
  the visual-regression baseline this migration should ideally be
  checked against is not yet seeded (see the migration-plan doc's
  prerequisite section) — flagged, not silently skipped.

## 10. Sign-off

- [x] Rollback plan is concrete and testable.
- [x] Blast radius is stated, not assumed — single file, 16 lines, each individually classified.
- [x] No silent behavior change — visual-only, and the risky subset (solid-fill white-text buttons) was explicitly identified and left alone rather than guessed at.
