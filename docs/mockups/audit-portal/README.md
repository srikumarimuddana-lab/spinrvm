# Audit Logs page — design mockup

Non-functional design mockup for the admin-dashboard Audit Logs page
(`admin-dashboard/src/app/dashboard/audit-logs/page.tsx`), reviewed and
iterated against the real sidebar, topbar, and button/badge components in
`admin-dashboard/src/components/`.

**Live preview (interactive):** https://claude.ai/code/artifact/a9f44e21-84bf-4fa4-a4fa-28ae59a4ffea

This is design reference only — no application code changes. Nothing here
is wired to real data or the backend.

## Contents

Four artboards, all covering the audit-logs page (no other product surface):

| File | What it covers |
|---|---|
| `Main.dc.html` | Desktop (1440px) — primary layout |
| `Tablet.dc.html` | Tablet width (834px), collapsed sidebar |
| `Mobile.dc.html` | Phone width (390px), card list layout |
| `Settings.dc.html` | Audit-log settings/retention tab |

`canvas.json` is the layout manifest (Claude Design canvas format) tying the
four `.dc.html` artboards together on one canvas; `spinr-logo-*.png` are the
Spinr wordmark assets used in the topbar, copied from
`admin-dashboard/public/` for visual fidelity.

## Review history

Reviewed in four passes against the real components before this mockup was
finalized:

1. **Sidebar** — nav group labels corrected to match the real
   `NAV_GROUPS` in `sidebar.tsx` (`Operate`→`Operations`, `Trust`→`Support`);
   added the real collapse toggle (240px↔68px, matching `topbar.tsx`).
2. **Badges** — audit-event-type badges made theme-aware (separate text
   colors for dark/light mode) — the original fixed dark-mode shade failed
   WCAG AA contrast (~2.7:1) against the light-mode card background.
3. **Buttons** — added `:disabled` styling (`opacity:0.5`,
   `pointer-events:none`, `cursor:not-allowed`) to the pagination
   prev/next buttons on all four artboards; none had it before.

Scope was intentionally trimmed from an earlier, broader exploration (driver/
rider/corporate-portal variants, print/export views, empty/loading states)
back down to just these four audit-log-page artboards — the other variants
didn't serve the stated goal of auditing the audit-logs page itself.
