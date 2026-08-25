# Spinr store screenshots

Source for the Spinr **rider** App Store screenshot set. Each `*.dc.html` file is
one artboard, laid out by `canvas.json` and published as a design canvas.

## The set

| File | Slot | Story |
|---|---|---|
| `Main.dc.html` | 1 | Your fare stays home |
| `Rider02.dc.html` | 2 | See the price before you tap |
| `Rider03.dc.html` | 3 | Watch your driver arrive |
| `Rider04.dc.html` | 4 | Spinr's cut: $0.00 |
| `Rider05.dc.html` | 5 | Help is one tap away |

All five are **1290 × 2796** (App Store, iPhone 6.7"). Rendered PNGs at that exact
size live in `png/`.

Order follows the real ride flow — book, price, track, pay, safety. Apple only
surfaces the first two or three in search results, so those carry the pitch.

## How it's built

`_build.py` is the generator: one template plus five per-frame configs, so the
design system stays consistent across the set. It composes the phone screens
from `_screens/*.html`, which are extracted from (and match) the real app.

```bash
python3 _build.py        # regenerate the five .dc.html artboards
./_rebuild.sh            # build standalone render pages into _render/
node _measure.mjs        # assert nothing overflows its artboard
node _shoot.mjs          # render _render/*.html -> png/ at exact store size
```

Rendering needs Chromium (`/opt/pw-browsers/chromium-1194/chrome-linux/chrome`)
and Playwright, plus `_fonts.css` — a base64 Plus Jakarta Sans **variable** face
(`font-weight: 200 800`) fetched from Google Fonts. Four fixed-weight faces do
not work: Google serves one variable file for all four weights, so fixed faces
all render at the same weight.

### The continuous route line

The red line crossing each frame is continuous across the set: frame N exits its
right edge at the same height frame N+1 enters its left edge (`ye` / `yx` in
`_build.py`). Reorder the frames and those hand-off heights must be reordered too.

### Gotcha that bit once

`.sp * { box-sizing: border-box }` styles descendants, **not** `.sp` itself. The
root artboard kept `content-box`, so its padding was added on top of its declared
height and every frame overflowed by exactly its padding. The selector must be
`.sp, .sp *`.

## Brand sources

Colors and type are lifted from the live app, not invented — see
`.claude/context/brand-spinr.md` and `shared/theme/index.ts`. Phone content
mirrors the real screens (`rider-app/app/(tabs)/index.tsx`,
`rider-app/app/ride-options.tsx`) including exact radii, control sizes and copy,
scaled ~2.13× from the 390pt design width.

`spinr-logo.png` is copied verbatim from `rider-app/assets/images/`.
`spinr-logo-light.png` is the same asset with its palette remapped for dark
backgrounds (charcoal → `#F2F2F7`, brand red preserved) — regenerate it rather
than hand-editing if the source logo changes.

## Sample data

Fares, addresses, "Sam R.", plate, card last-4, ratings and driver counts are
**placeholder sample data**. The $22.99 fare and its line items are internally
consistent (base + distance + time + booking fee, then GST 5% and PST 6%), and
no screen claims a discount the total does not reflect — keep it that way if you
edit the numbers. Vehicle tiers (`Standard` / `XL` / `Accessible`) are
placeholders; the real ones are admin-managed `vehicle_types` rows.

Replace all of it, and give the marketing claims a legal/marketing pass, before
submitting.

## Not built yet

- **Google Play** needs its own **1080 × 1920** set. Play rejects anything taller
  than 9:16, so these 2796-tall images cannot be reused there.
- **Spinr Driver** is a separate store listing and needs its own set.
  `Driver01-03.dc.html` hold the driver story and copy from an earlier pass, but
  predate this design system and need rebuilding through `_build.py`.
