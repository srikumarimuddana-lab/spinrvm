# Spinr brand identity

Reference this whenever producing anything customer-facing that isn't application
code — marketing copy, ad creative, video scripts, one-off HTML/artifact mockups,
decks, social assets. Application UI already pulls these values live from
`shared/theme/index.ts`; this file exists for the surfaces that don't import that
module (ad platforms, artifact previews, external design tools).

**Do not invent a palette or font for Spinr materials.** If a task needs colors
beyond what's listed here (e.g. a data-viz categorical palette), derive them from
this palette rather than picking an unrelated one.

## Color

Canonical source: `shared/theme/index.ts` (`lightColors` / `darkColors`), ported
to admin-dashboard's `globals.css` per `docs/change-log/2026-07-29-admin-dashboard-brand-token-port.md`.

| Token | Light | Dark | Notes |
|---|---|---|---|
| Primary / brand red | `#FF3B30` | `#FF453A` | The brand color. Use for CTAs, accents, the wordmark treatment. |
| Primary (contrast-safe) | `#D32F2F` | `#D32F2F` | `primaryDark` — use for text/buttons needing WCAG AA contrast on white, per the admin-dashboard port. |
| Background | `#FFFFFF` | `#000000` | True black in dark mode (OLED), not near-black. |
| Surface | `#FFFFFF` | `#1C1C1E` | |
| Text | `#1A1A1A` | `#F2F2F7` | |
| Text secondary | `#6B7280` | `#8E8E93` | |
| Border | `#E5E7EB` | `#38383A` | |
| Success | `#34C759` | `#30D158` | |
| Warning | `#d97706` | `#F59E0B` | |
| Info | `#3B82F6` | `#0A84FF` | |
| Danger/error | `#DC2626` | `#FF453A` | |
| Orange accent | `#FF9500` | `#FF9F0A` | |
| Gold | `#FFD700` | `#FFD700` | |

Spinr is **not** a teal/green or amber brand — earlier ad/video drafts in this
session used an invented teal+amber palette because no brand reference existed
yet; treat those as superseded.

## Typography

**Plus Jakarta Sans** — loaded via `@expo-google-fonts/plus-jakarta-sans` in
rider-app/driver-app (`driver-app/app/_layout.tsx:20`), referenced as the base
font in `shared/config/spinr.config.ts:132`. Use weights 400 (regular), 500
(medium), 600 (semibold), 700 (bold).

Known gap: admin-dashboard still ships Geist, not Plus Jakarta Sans — an
unresolved follow-up tracked as Phase-0 epic #2785. Don't copy admin-dashboard's
current font choice into new marketing materials; use Plus Jakarta Sans.

## Logo

Real asset, not a recreation: bullseye mark + "spinr" wordmark, transparent
background.

- `driver-app/assets/images/spinr-logo.png` / `rider-app/assets/images/spinr-logo.png` — source, used live in both mobile apps
- `backend/static/branding/spinr_logo.png` — 768×312px, 2x upscaled for print/PDF (report headers). See `backend/static/branding/README.md`.

If the source logo is ever updated, re-run the same resize into the backend copy
rather than hand-editing it.

## No formal brand guideline doc (yet)

There is no dedicated style-guide/brand-guideline document in `docs/` — these
values are assembled from `shared/theme/index.ts`, `docs/adr/008-report-branding-fixed-vs-branded.md`,
and `docs/change-log/2026-07-29-admin-dashboard-brand-token-port.md`. If a real
brand guideline PDF/Figma exists outside this repo, prefer it over this file and
update this file to match.
