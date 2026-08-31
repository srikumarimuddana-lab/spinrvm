# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-30 |
| Author | Claude Code (session), on behalf of vikas@ngitservices.com |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | branch `claude/migration-batch-readiness-wicr1d` |
| Related issue or gap ID | Operator-reported: garbled text ("Type" separated from "IMPORT to enable") on Legacy Booking Import's commit-confirmation panel, screenshot attached live |

## 1. Issue / gap identified

The commit-confirmation text on `LegacyBookingImport.tsx` ("This writes N ride(s)... Type IMPORT to enable.") rendered with broken wrapping/alignment, making it hard to read what the operator was being asked to type.

## 2. Root cause

`components/ui/label.tsx`'s `<Label>` renders as a **flex container** (`flex items-center gap-2`) by design — appropriate for a short label paired with one inline element (e.g. "Type `IMPORT` to enable:"). This component wrapped a long, multi-sentence disclaimer paragraph (four lines including an inline `<span>` for the mono-styled confirm phrase) inside that same `<Label>`. Flexbox lays out its children — including bare text nodes — as flex items in a row rather than wrapping them like normal block/prose text, so the sentence didn't wrap the way a `<p>` would; it fragmented instead.

## 3. Fix / remediation

Split the content: the long disclaimer sentence now renders as a plain `<p>` (normal block wrapping), and only the short "Type `IMPORT` to enable:" phrase stays inside the `<Label>`, which is exactly the short-flex-content case the component is designed for. The `Input`/`Button` row got `flex-wrap` so it wraps cleanly on narrow admin-dashboard columns too.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to this one confirmation panel.** No other `<Label>` usage in this file wraps a multi-sentence paragraph — grepped the file to confirm.
- **Purely visual/markup restructuring** — the confirm-phrase logic (`CONFIRM_PHRASE`, `confirmText` state, `canCommit` gating) is completely unchanged; still requires typing `IMPORT` to enable Commit.
- `npx tsc --noEmit` clean; `npm run build` — real production build, succeeded.

## 5. User-experience effect

- **Internal admin only.** Before: the commit-confirmation instructions were visually garbled, making the required action unclear right before a live, non-undoable write. After: reads as a normal paragraph followed by a clear "Type IMPORT to enable:" label next to the input.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/dashboard/bulk-operations/_components/LegacyBookingImport.tsx` | Split the commit-confirmation `<Label>` into a `<p>` (long disclaimer) + a short `<Label>` (confirm phrase only); added `flex-wrap` to the input/button row | Fix broken text layout caused by wrapping a long paragraph in a flex-container `<Label>` |

## 7. Before / after

```tsx
// Before — long paragraph inside a flex-container Label
<Label htmlFor="booking-import-confirm" className="text-xs">
    This writes {c.total_rides_planned} ride(s) ... Type{" "}
    <span className="font-mono">{CONFIRM_PHRASE}</span> to enable.
</Label>
```

```tsx
// After — long text as a normal paragraph, short label stays in the Label
<p className="text-xs text-muted-foreground">
    This writes {c.total_rides_planned} ride(s) ...
</p>
<div className="flex flex-wrap items-center gap-2">
    <Label htmlFor="booking-import-confirm" className="whitespace-nowrap text-xs">
        Type <span className="font-mono">{CONFIRM_PHRASE}</span> to enable:
    </Label>
    ...
</div>
```

## 8. Rollback plan

`git-revert-safe` — pure markup/CSS restructuring, no logic or data change.

## 9. Verification performed

- [x] Root-caused against `components/ui/label.tsx`'s actual flex-container styling, not guessed from the screenshot alone.
- [x] Grepped the file for other `<Label>` usages — none wrap multi-sentence content, confirming this was the only instance of the pattern.
- [x] `npx tsc --noEmit` — clean.
- [x] `npm run build` — real production build, succeeded.

## What was NOT verified

- Not screenshotted post-fix in this session (no browser/rendering tool available) — the fix is reasoned from the component's actual CSS (`flex` vs. block) rather than visually confirmed; the operator's next view of this panel will be the real-world check.
- No automated visual-regression tooling exists for admin-dashboard (per CLAUDE.md's standing gap) — noted per the standing exception rather than re-discovered.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (`git revert`)
- [x] Blast radius is stated, not assumed (one confirmation panel, no other Label misuse found)
- [x] No silent behavior change — confirm-phrase gating logic is unchanged, only markup/layout
