# Spinr store screenshots

Source for the App Store and Google Play screenshot sets (rider + driver).

Each `*.dc.html` file is one artboard. They are laid out by `canvas.json` and
assembled into a single published design canvas that renders every artboard and
exports them as PNG/PDF.

| File | Store | Size | Slot |
|---|---|---|---|
| `Main.dc.html` | App Store · Rider | 1290×2796 | 1 · Hero |
| `Rider02.dc.html` | App Store · Rider | 1290×2796 | 2 · Fare transparency |
| `Rider03.dc.html` | App Store · Rider | 1290×2796 | 3 · Safety |
| `Rider04.dc.html` | App Store · Rider | 1290×2796 | 4 · Upfront pricing |
| `Rider05.dc.html` | App Store · Rider | 1290×2796 | 5 · Plan ahead |
| `Driver01.dc.html` | App Store · Driver | 1290×2796 | 1 · Keep 100% |
| `Driver02.dc.html` | App Store · Driver | 1290×2796 | 2 · Fare before accept |
| `Driver03.dc.html` | App Store · Driver | 1290×2796 | 3 · Weekly payouts |
| `Play01.dc.html` | Google Play | 1080×1920 | Rider hero |
| `Play02.dc.html` | Google Play | 1080×1920 | Driver hero |
| `DirectionB/C/D.dc.html` | — | 1290×2796 | Alternate design directions (low-fi) |

**Sizes are not interchangeable.** Google Play rejects images taller than 9:16,
so the 1290×2796 App Store images cannot be reused for Play. `Play0*.dc.html`
reuse the same phone markup at `scale(0.75)` inside a 1080×1920 frame.

## Brand sources

Colors and type are lifted from the live app, not invented — see
`.claude/context/brand-spinr.md` and `shared/theme/index.ts`. Phone content
mirrors the real screens (`rider-app/app/(tabs)/index.tsx`,
`rider-app/app/ride-options.tsx`, `driver-app/components/**`) including exact
radii, control sizes and copy, scaled ~2.13× from the 390pt design width.

`spinr-logo.png` is copied verbatim from `rider-app/assets/images/`.
`spinr-logo-light.png` is the same asset with its palette remapped for dark
backgrounds (charcoal → `#F2F2F7`, brand red preserved) — regenerate it rather
than hand-editing if the source logo changes.

## Sample data

Fares, addresses, driver name, plate, bank last-4, trip counts and balances are
**placeholder sample data**. Vehicle tier names (`Standard`, `XL`,
`Accessible`) are placeholders too — real tiers are admin-managed rows in
`vehicle_types`. Replace all of it with real or clearly fictional values, and
give the marketing claims a legal/marketing pass, before submitting to either
store.

## Rebuilding

```bash
cd design/app-store-screenshots
node "<claude design skill dir>/seed-canvas.mjs" \
  --template "<claude design skill dir>/payload.template.html" \
  --out spinr-app-store-screenshots.html \
  --title "Spinr Store Screenshots" \
  --artboard Main.dc.html --artboard Rider02.dc.html ... \
  --image spinr-logo.png --image spinr-logo-light.png \
  --canvas canvas.json
```

Then republish `spinr-app-store-screenshots.html` to the same artifact URL.
