# Change Impact & Risk Log: text on red (error toasts, red buttons) is white and readable

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-26 |
| Author | Claude Code session (claude.ai/code) |
| Surface(s) | admin-dashboard (global stylesheet), including the company portal |
| Domain (Sentry tag) | admin (display only) |
| PR / commit link | UX program W2.2a — branch `claude/spinr-animations-admin-ux-x7nl5x` |
| Related issue or gap ID | Found during W2.1's accessibility review; the user chose "own small PR first" on 2026-09-26 |

## 1. Issue / gap identified

Every error toast (245 `variant: "destructive"` call sites) and the red buttons and badges in 23 components ask for `text-destructive-foreground`, but that colour did not exist. In light mode their text rendered near-black on red: `#111827` on `#dc2626` is **3.67:1**, below WCAG 2.1 AA's 4.5:1 for normal text. In dark mode it was 4.63:1.

## 2. Root cause

The stylesheet defined `--destructive` and mapped `--color-destructive`, but never defined `--destructive-foreground` or `--color-destructive-foreground`. Tailwind v4 generates no rule for a colour utility with no theme value, so the class did nothing and the text inherited the body's `text-foreground`.

## 3. Fix / remediation

- `globals.css` defines `--destructive-foreground: #ffffff` in `:root` and `.dark`.
- `globals.css` maps `--color-destructive-foreground: var(--destructive-foreground)` in `@theme inline`.
- The `.theme-v2` block doesn't override `--destructive`, so it inherits the token.
- White on `#dc2626` is 4.83:1 in both themes, the same pairing the Button's own destructive variant already uses (`text-white`).

**Alternatives considered:**
- **Change the 23 components and the toast** to `text-white`. That's 24 edits instead of one token, and the next component written with the shadcn convention would hit the same gap.
- **Fold this into W2.2.** Rejected at the user's choice, so the colour change stays separately reviewable and revertable.

## 4. Risk & impact on existing functionality

- **Blast radius:** every element with `text-destructive-foreground`:
  - `components/ui/toast.tsx` (every error toast, and its close button's `/70` variant)
  - cloud-messaging
  - corporate-accounts (list, detail, members, KYB queue)
  - drivers (page, document reviewer, action bar, detail sheet, notes)
  - monitoring (alert feed, Redis)
  - promotions
  - rides (detail modal, flag form)
  - staff
  - support (complaints, flags, lost-and-found, tickets)
  - users
  - vehicle-types
- **What changes:** only the text colour on those red elements, from near-black to white in light mode and from `#fafafa` to white in dark mode. No layout or size change.
- **Visual baselines:** two baselined pages contain an affected element, but neither shows it in its captured state:
  - On `dashboard-drivers`, the red button sits inside the document-review dialog, which is closed.
  - On `dashboard-monitoring`, the unread-events badge only renders when live events have arrived.

  CI's visual-regression job confirms; if it does flag a diff, it is this intended colour change, and the baseline needs a human re-capture `[H]`.

## 5. User-experience effect

- **Admins and company-portal users:** error toasts and red buttons/badges show white text on red instead of near-black, and are easier to read, especially in light mode.
- **Mid-session:** applies on the next page load after deploy.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/globals.css` | `--destructive-foreground` in `:root` and `.dark`; `--color-destructive-foreground` in `@theme inline` | The class now resolves to a real, readable colour |
| `admin-dashboard/src/app/destructive-foreground.test.ts` | New, 3 tests | The token is mapped and reaches ≥4.5:1 on `--destructive` in both themes |

## 7. Before / after

```css
/* Before */
--color-destructive: var(--destructive);
--destructive: #dc2626;
/* After */
--color-destructive: var(--destructive);
--color-destructive-foreground: var(--destructive-foreground);
--destructive: #dc2626;
--destructive-foreground: #ffffff;
```

## 8. Rollback plan

**No feature flag:** a CSS token for an accessibility contrast fix. **Rollback:** revert the PR; admin redeploys through Vercel. No data is touched.

## 9. Verification performed

- [x] **Guard test:** 3 tests. All fail on the old stylesheet and pass on the new.
- [x] **Production build:** `npm run build` succeeds, and the emitted CSS now contains `.text-destructive-foreground{color:var(--destructive-foreground)}` and `--destructive-foreground:#fff`. Before, it had no such rule.
- [x] **Full admin suite:** 94 files, 781 tests pass.
- [x] **Contrast computed** with the WCAG formula:
  - `#111827` on `#dc2626` is 3.67:1
  - `#1c1c1a` (theme v2) on `#dc2626` is 3.53:1
  - `#fafafa` on `#dc2626` is 4.63:1
  - `#ffffff` on `#dc2626` is 4.83:1

## 10. What was NOT verified

- **Rendered pages:** none were screenshotted. The baseline reasoning above is from reading the components; CI's visual job is the real check.
- **Hover states:** `hover:bg-destructive/90` lightens the red slightly under white text. That's the same trade-off the existing Button destructive variant already makes, and it wasn't measured.
- **No screen-reader or real-display pass.**
