# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-21 |
| Author | Claude Code (session, vikas@ngitservices.com) |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (see PR) |
| Related issue or gap ID | #2816 Stage 1, Batch 3 — see `docs/change-log/2026-08-21-admin-color-token-migration-plan.md` |

## 1. Issue / gap identified

`service-areas/page.tsx` (2,886 lines, largest single file in the #2816
backlog after `drivers/page.tsx`) had 84 lines lacking any `dark:`
treatment, despite 2 prior #2816 batches on this file (per change-log
history) — those covered the general tab and page shell, not the Airport
Zone and Incentive sub-panels touched here.

## 2. Root cause / findings

Per-line classification of all 84 lines settled into three groups:

1. **22 real semantic-token fixes** — text/icon colors genuinely
   describing a success/warning/destructive state (toggle-on indicators,
   "points defined" success text, the surge-justification regulatory-risk
   warning, a required-field asterisk, delete links, "up to date"
   status) → migrated to `text-success`/`text-warning`/`text-destructive`
   (and `bg-success/15` for the two tinted-background pairs).
2. **6 neutral/inactive/optional badges** — genuinely mappable to the
   existing `bg-muted`/`text-muted-foreground` tokens (INACTIVE, Optional,
   No Expiry, and a generic type-label badge) — these aren't really
   "warning-adjacent," they're the neutral/off state, which already has a
   real token.
3. **56 house-convention `dark:` pairings, not token migrations** — two
   large, previously-unmigrated sub-panels (the blue-themed "Airport Zone"
   create/edit form, the amber-themed "Incentive" create/edit form) plus
   several categorical pastel badges (AIRPORT, airport-zone-count,
   Required, Has Expiry, Both Sides) use blue/violet/amber/emerald as a
   **panel or category brand color**, not a semantic app-wide state — no
   3-token system can express "this form is about airports." These got
   the exact `dark:bg-X-900/NN dark:text-X-NNN` pairing already used
   dozens of times elsewhere in this same codebase (verified via grep
   before applying, not invented), bringing them to dark-mode parity
   without misusing the semantic tokens for a non-semantic purpose.

**Deliberately left untouched (8 lines)**: 4 solid-fill buttons with
white text (`bg-blue-500 text-white`, `bg-amber-500/600 text-white`, a
`bg-green-500 text-white` save-confirmation state) — same unverified
white-on-solid-fill contrast risk documented in Batch 1, not our
`--success` token here so no regression risk from leaving them, but not
converted either; 1 decorative icon in an empty state (`text-amber-300`
Car icon); 1 low-stakes `hover:bg-green-50` background (the base text
color was migrated to `text-success`, the hover background was not —
rarely visible, not worth the same rigor as a base state color).

## 3. Fix / remediation

See §2. Applied programmatically via a verified line-by-line substitution
script (each substitution checked against the actual line content before
writing, aborting on any mismatch) rather than manual find-replace, given
the volume — every substitution traces to one of the three categories
above, not a blanket regex.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to one file**, 77 lines changed (154 diff
  lines) — all string-literal className changes, zero logic/prop/state
  changes. Reviewed the full diff before committing.
- The 6 gray→muted-token conversions change the exact rendered color in
  **light mode too** (Tailwind's static `gray-100`/`text-gray-500` vs.
  the app's `--muted`/`--muted-foreground` tokens are visually similar
  neutrals but not byte-identical) — flagging this explicitly since it's
  the one category in this batch where light-mode appearance shifts
  slightly, unlike the `dark:`-pairing additions which are purely
  additive (light mode renders identically, only dark mode changes).
- Every `dark:` pairing reused an exact combination already present
  elsewhere in the same file or codebase (grepped before applying) —
  not a novel color choice.
- Repo-wide lint warning count stayed under the `--max-warnings` ratchet
  (confirmed via `npm run lint`, exit 0).

## 5. User-experience effect

**Internal admin only.** Two previously-100%-light-mode-only sub-panels
(Airport Zone creation, Incentive creation — both real, actively-used
admin forms, not decorative) now render correctly in dark mode instead of
showing light-pastel-on-near-black. Several status badges gain dark-mode
contrast for the first time. Light-mode appearance is unchanged except
for the 6 gray→muted-token conversions, which are a very close visual
match (both are neutral grays tuned for the same UI role).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/service-areas/page.tsx` | 22 semantic-token fixes, 6 neutral-token fixes, 56 house-convention `dark:` pairings, 8 deliberate non-fixes | #2816 Batch 3 |

## 7. Before / after (representative)

```tsx
// Semantic token fix
- <p className="text-xs text-green-600 mt-1">{count} points defined</p>
+ <p className="text-xs text-success mt-1">{count} points defined</p>

// Neutral/inactive -> real muted token
- {!area.is_active && <span className="... bg-gray-100 text-gray-500 ...">INACTIVE</span>}
+ {!area.is_active && <span className="... bg-muted text-muted-foreground ...">INACTIVE</span>}

// House-convention dark: pairing (panel brand color, not semantic)
- <div className="bg-blue-50 border border-blue-200 rounded-xl p-5 mb-5">
+ <div className="bg-blue-50 border border-blue-200 dark:bg-blue-900/20 dark:border-blue-800 rounded-xl p-5 mb-5">
```

## 8. Rollback plan

`git-revert-safe` — single file, string-literal className changes only,
no data/API/schema change.

## 9. Verification performed

- [x] Real production build (`npm run build`) — succeeded.
- [x] `npx tsc --noEmit` — clean.
- [x] `npx vitest run` — 339/339 passed.
- [x] `npm run lint` (the exact CI command) — exit 0, well under the
  `--max-warnings` ratchet.
- [x] `npx eslint` on the touched file directly — 0 errors.
- [x] Every programmatic substitution was checked against the actual
  line content before writing (script aborts on mismatch) — no blind
  regex applied across the file.
- [x] Read the full diff (154 lines) before committing, not just trusted
  the script output.
- [ ] Not manually click-tested/screenshotted in dark mode — same
  sandbox limitation as every prior UI change-log this session; visual-
  regression baseline still not seeded (tracked as a blocking
  prerequisite in the migration-plan doc).

## 10. Sign-off

- [x] Rollback plan is concrete and testable.
- [x] Blast radius is stated, not assumed.
- [x] No silent behavior change — flagged the one category (gray→muted-token) where light-mode rendering shifts slightly; everything else is additive dark-mode-only.
