# Spinr Driver — store assets

`metadata.json` holds the App Store Connect / Google Play listing copy.
`screenshots/` holds the generated Google Play phone screenshots.

## Regenerating the screenshots

```bash
python3 driver-app/store-assets/generate_screenshots.py            # every size
python3 driver-app/store-assets/generate_screenshots.py ios-6.9    # just one size
```

Renders the 8 marketing artboards at every store size into `screenshots/` using
headless Chromium (no npm/pip dependencies). The phone mock-ups reproduce the
real driver-app screens — copy comes from `driver-app/i18n/en.json` and the
components under `driver-app/components/`, so the listing shows what the app
actually renders. Edit `build_cards()` to change headlines or ordering.

### Sizes

| Key | Pixels | Store slot |
|---|---|---|
| `android-phone` | 1080×1920 | Google Play phone |
| `ios-6.9` | 1320×2868 | iPhone 16 Pro Max class |
| `ios-6.7` | 1290×2796 | iPhone 15/14 Pro Max class |
| `ios-5.5` | 1242×2208 | legacy iPhone 8 Plus slot |
| `ipad-12.9` | 2048×2732 | iPad Pro 12.9" |

Layout is derived from one reference artboard (1080×1920), so every size shares
the composition: the header block is centred in the space above the device frame
and the frame is sized to run off the bottom edge. Add a size by appending to
`ARTBOARDS` — no per-size layout code. iOS artboards draw an iPhone frame
(Dynamic Island, Wi-Fi glyph in the status bar); the Android artboard draws a
punch-hole camera.

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
