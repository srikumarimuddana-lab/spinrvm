# Spinr store screenshots

Source for the Spinr **rider** App Store screenshot set. Each `*.dc.html` file is
one artboard, laid out by `canvas.json` and published as a design canvas.

## The set (v4 — faithful to the shipped app)

Layout follows the dominant pattern across top App Store / Play listings
(Uber, Lyft, DoorDash, Duolingo): centered headline, straight-on centered
device, solid brand-red hero frame then warm-paper frames, floating feature
chips overlapping the device edges.

| File | Slot | Story |
|---|---|---|
| `Main.dc.html` | 1 | Saskatchewan's own ride app (red hero, home map) |
| `Rider02.dc.html` | 2 | Know the price before you ride (real tiers) |
| `Rider03.dc.html` | 3 | Track every ride, live (gradient route) |
| `Rider04.dc.html` | 4 | Spinr Assistant (real welcome + prompts) |
| `Rider05.dc.html` | 5 | Help & Support (real tabs + production FAQs) |

Fidelity contract (v4):
- Device is an iPhone: titanium rim, thin bezel, Dynamic Island, 112px screen radius.
- Maps are default-Google-style (`_gmap.py`): warm land, white roads, amber
  highway, blue water, Google-blue location dot.
- The route is RouteLine's real gradient — `#FF9500` → `#EE2B2B`
  (`shared/constants/routeMapStyle.ts`), 4pt at screen scale, round caps,
  orange at the start and red at the destination.
- Pins follow RoutePins: green `#10B981` disc + white ring + white dot pickup.
- Car markers are the actual `shared/assets/car_marker@3x.png` sprites.
- Vehicle tiers come from production Supabase `vehicle_types`: Economy / Van /
  XL with real names, descriptions and capacities. The hosted illustrations
  are network-blocked in this environment, so `_screens.py` draws side-view
  stand-ins in the app's own car-art palette — drop the real files in as
  `veh-sedan.webp` / `veh-van.webp` / `veh-xl.webp` and rebuild to swap them in.
- AI and Help screens use the real copy from `rider-app/app/ai-assistant.tsx`
  and `shared/components/SupportScreen.tsx`, plus production FAQ questions.
- The receipt/payment frame and the mocked SOS screen are gone (both on
  request).

Chip rules: each floating chip carries a fact **not** already on the screen
beside it, and sits over a quiet zone — never over a price, plate, or button.

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

### Logo variants

Three palette remaps of the same shipped asset: `spinr-logo.png` (original,
for light frames), `spinr-logo-light.png` (light wordmark, for dark frames),
`spinr-logo-onred.png` (white wordmark + charcoal bullseye, for the red hero —
the red mark vanishes on a red background otherwise). New image files must
also be added to `_rebuild.sh`'s inline list or they render as broken images.

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
