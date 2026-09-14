# Spinr Driver — store assets

`metadata.json` holds the App Store Connect / Google Play listing copy.
`screenshots/` holds the generated Google Play phone screenshots.

## Regenerating the screenshots

```bash
python3 driver-app/store-assets/generate_screenshots.py
```

Renders 8 artboards at **1080×1920** into `screenshots/` using headless Chromium
(no npm/pip dependencies). The phone mock-ups reproduce the real driver-app
screens — copy comes from `driver-app/i18n/en.json` and the components under
`driver-app/components/`, so the listing shows what the app actually renders.
Edit the `cards` list in `main()` to change headlines or ordering.

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
are optional extras — drop them from `cards` if you want a tighter listing.

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

- iOS sizes (6.7", 5.5", 12.9") and Android tablet sizes are not generated yet —
  add the artboard sizes to the generator.
- `metadata.json` still has `TODO` values for `apple_id` and
  `service_account_key`.
