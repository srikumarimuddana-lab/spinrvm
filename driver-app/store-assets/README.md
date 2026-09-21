# Spinr Driver — store assets

`metadata.json` holds the App Store Connect / Google Play listing copy.
`screenshots/` holds the generated Google Play phone screenshots.

## Regenerating the screenshots

```bash
python3 driver-app/store-assets/generate_screenshots.py                  # every size
python3 driver-app/store-assets/generate_screenshots.py ios-1320x2868    # just one
```

Renders the 8 marketing artboards at every store size into `screenshots/` using
headless Chromium (no npm/pip dependencies). The phone mock-ups reproduce the
real driver-app screens — copy comes from `driver-app/i18n/en.json` and the
components under `driver-app/components/`, so the listing shows what the app
actually renders. Edit `build_cards()` to change headlines or ordering.

### Sizes — which file goes in which upload slot

Artboards are keyed by **exact pixel size**, because that is what App Store
Connect validates. An inch label maps to more than one pixel size (the "6.7-inch"
label covers both 1290×2796 and 1284×2778, which belong to *different* slots), so
naming by inches is how a set ends up rejected on upload.

| Upload slot | Accepts | Use these files |
|---|---|---|
| App Store — 6.9" Display | 1320×2868 **or** 1290×2796 | `ios-1320x2868-*` (or `ios-1290x2796-*`) |
| App Store — 6.5" Display | 1242×2688 **or** 1284×2778 | `ios-1284x2778-*` |
| App Store — 5.5" Display | 1242×2208 | `ios-1242x2208-*` |
| App Store — iPad 12.9"/13" | 2064×2752, 2048×2732 | `ipad-2048x2732-*` |
| Google Play — phone | flexible | `android-1080x1920-*` |

App Store Connect also offers **"Keep using 6.9-inch Display"** on the smaller
iPhone slots — tick that and it reuses the 6.9" set, so the 6.5" and 5.5" files
become optional. They are generated anyway so either route works.

To add a size, append `(key, width, height, notch)` to `ARTBOARDS` — there is no
per-size layout code.

Layout is derived from one reference artboard (1080×1920), so every size shares
the composition: a 1:2.12 handset frame sitting fully on the canvas with its
bottom bezel and home indicator visible, the header block centred in the space
above it, and two floating callout cards overlapping the phone. Add a size by
appending to `ARTBOARDS` — no per-size layout code. iOS artboards draw an
iPhone frame (Dynamic Island, Wi-Fi glyph in the status bar); the Android
artboard draws a punch-hole camera.

**The layout is measured, not guessed.** Everything below comes from the live
rider-app listing screenshot at 1290×2796, so `ios-6.7` reproduces it and the
other sizes follow the same proportions:

| Element | Measured on the rider set | Generator value (reference units) |
|---|---|---|
| Frame | x 179–1110, y 765–2740 → 931×1975, 1:2.12 | `FRAME_ASPECT`, `FRAME_H`, `BOTTOM_GAP` |
| Logo | 204 wide | `logoW=b.px(172)` |
| Logo → headline | 63 | `logoGap=b.px(53)` |
| Headline line pitch | 116 | `h1=b.px(99)`, `line-height:0.98` |
| Headline → subtitle | 33 | `h1Gap=b.px(28)` |
| Subtitle line pitch | 55 | `sub=b.px(33)`, `line-height:1.41` |

Reference units are the measured pixels divided by that board's content scale
(1.194 at 1290×2796). Keep the frame aspect near 1:2.12 if you change it — a
taller frame reads as an elongated slab rather than a phone.

Headlines are two lines and subtitles two lines by design; the header block is
centred in the space above the frame, and on the 16:9 Android canvas that space
is tightest (~44px above the logo). A subtitle long enough to wrap to three
lines eats that margin, so keep them to roughly 90 characters.

### Callout cards

Each light card carries two floating feature cards that overlap the phone —
upper-left and lower-right, as on the rider set. They are defined per screen in
`build_cards()` as `(side, y_fraction, icon, title, subtitle)`. The `y_fraction`
is a fraction of canvas height, so a card holds its position across all five
sizes. When picking one, put it over the least information-dense band of that
screen: covering a map or a chart is fine, covering a price, a hero number or
the control the card is advertising is not.

### Screens

| File | Headline | Screen shown |
|---|---|---|
| `01-drive-canadian` | Drive Canadian. Keep 100%. | brand card + earnings |
| `02-go-online` | Go online. Earn on your terms | dashboard + GO button |
| `03-know-your-earnings` | Know what you'll earn | ride offer panel |
| `04-demand` | Drive where the demand is | demand heat map |
| `05-guided-live` | Every trip, guided live | navigation to pickup |
| `06-earnings` | Your earnings. Clearly. | earnings dashboard |
| `07-rider-pin` | The right rider, every time | PIN handshake |
| `08-quests` | Hit targets, keep the bonus | quests & bonuses |

Google Play accepts up to 8 phone screenshots. 01–06 are the core set; 07 and 08
are optional extras — drop them from `build_cards()` for a tighter listing.

### iPad caveat

The iPad set renders the **phone** UI inside a device frame, because driver-app
has no dedicated tablet layout to show. `app.config.ts` sets
`ios.supportsTablet: true`, so App Store Connect will ask for iPad screenshots.
If review wants genuine iPad captures, they have to come from a build running on
an iPad — that is not something this generator can fake honestly.

## Brand font

Headlines should render in **Plus Jakarta Sans** (see
`.claude/context/brand-spinr.md`). The generator embeds it automatically if the
`.ttf` files are present in `driver-app/assets/fonts/`:

```
PlusJakartaSans-Regular.ttf  PlusJakartaSans-Medium.ttf
PlusJakartaSans-SemiBold.ttf PlusJakartaSans-Bold.ttf
PlusJakartaSans-ExtraBold.ttf
```

They are **not** currently in the repo, so the committed PNGs fall back to the
closest installed grotesque. Drop the files in and re-run to get on-brand
typography before submitting the listing.

## Still to do before submission

- Add Plus Jakarta Sans (above) and re-run, so headlines are on-brand.
- `metadata.json` still has `TODO` values for `apple_id` and
  `service_account_key`.
- Decide whether the iPad slot is filled with these frames or with real iPad
  captures (see the iPad caveat above).
