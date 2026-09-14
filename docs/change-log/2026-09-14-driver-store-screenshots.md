# Change Impact & Risk Log — driver-app Play Store screenshots

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-14 |
| Author | Claude Code (session) |
| Surface(s) | driver-app (store assets only — no app code) |
| Scope | Google Play phone set, then extended to the iOS + iPad sizes |
| Domain (Sentry tag) | n/a (no runtime code) |
| PR / commit link | branch `claude/gifted-bohr-oq2pe7` |
| Related issue or gap ID | `driver-app/store-assets/metadata.json` → `screenshots.screens_to_capture` (previously unfilled) |

## 1. Issue / gap identified

`driver-app/store-assets/` listed 8 screens to capture for the Play Store but contained no
screenshots and no way to produce them; the rider-app set exists only as external image files
with no generator in the repo.

## 2. Root cause

The screenshots were never produced. `metadata.json` recorded the intent (`screens_to_capture`)
but nothing in the repo could render them, so the listing had copy and no imagery.

## 3. Fix / remediation

Added `driver-app/store-assets/generate_screenshots.py`, a dependency-free generator that renders
the 8 marketing artboards with headless Chromium at every store size (1080×1920 Play Store phone,
1320×2868 / 1290×2796 / 1242×2208 iPhone, 2048×2732 iPad Pro), and committed the resulting 40 PNGs.
Layout is derived from one reference artboard so all sizes share a composition: the header block is
centred in the space above the device frame, and the frame is sized to run off the bottom edge. iOS
artboards draw an iPhone frame (correctly proportioned Dynamic Island, Wi-Fi glyph in the status
bar); the Android artboard keeps a punch-hole camera.
Phone mock-ups reproduce the real driver-app screens: copy is taken from `driver-app/i18n/en.json`
(`home.go`/`home.stop`, `dashboard.youreOnline`, `activeRide.*`, `rideOffer.*`, `earnings.*`) and
layout from `components/dashboard/*`, `components/panels/RideOfferPanel.tsx` and
`app/driver/quests.tsx`. Colours are the `shared/theme` brand tokens via
`.claude/context/brand-spinr.md`.

## 4. Risk & impact on existing functionality

**Blast radius: isolated.** Grepped the repo for `store-assets` — every hit is prose or the
metadata files themselves (`reports/audits/2026-07-22-*.md`, `rider-app/store-assets/metadata.json`,
`driver-app/store-assets/metadata.json`). Nothing imports, bundles, or builds from this directory:

- `driver-app/app.config.ts` does not reference `store-assets/`; app icons/splash come from
  `driver-app/assets/`, which is untouched.
- No workflow reads it — `submit-play-store.yml` contains no reference to `store-assets` or to
  screenshots at all.
- No test, lint rule, or migration touches it.

Pre-existing gap noticed, not changed: both apps' `metadata.json` cite
`docs/deploy/04-mobile-eas.md` as the submission runbook and that file still does not exist
(already recorded in `reports/audits/2026-07-22-implementation-certification-plan-v1.md`).

Only pre-existing file changed is `metadata.json`, and only inside its `screenshots` block
(the manifest of which screens exist). Listing copy, package name, and review notes are unchanged.

## 5. User-experience effect

No in-app change. Nobody using the app — rider, driver, corporate admin, or internal admin — sees
any difference, mid-session or otherwise. These files are consumed only by a human uploading a
Play Store listing. No app copy, notification text, or validation rule changed.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/store-assets/generate_screenshots.py` | New — artboard generator | Makes the screenshots reproducible and editable in-repo |
| `driver-app/store-assets/screenshots/*.png` | New — 40 PNGs (8 screens × 5 store sizes) | The deliverable for both listings |
| `driver-app/store-assets/README.md` | New — how to regenerate, size table, font and iPad caveats, remaining gaps | The generator is useless if nobody knows it exists |
| `driver-app/store-assets/metadata.json` | `screenshots` block: replaced the aspirational `screens_to_capture` list with the 8 generated names + a note that iOS/tablet sizes are still missing | Leaving the old list would misdescribe what was actually produced |

## 7. Before / after

Additive; `metadata.json`'s manifest block is the only edit to existing content.

```
# Before
"screens_to_capture": [ "01-driver-dashboard-online", ... "08-tax-documents" ]   # none existed

# After
"android_phone_generated": [ "01-drive-canadian", ... "08-quests" ]              # all 8 committed
"_generated_note": "... iOS sizes and tablet sizes are not yet produced ..."
```

## 8. Rollback plan

`git rm` the four new paths and revert the `metadata.json` block. Nothing is deployed, cached, or
applied to live data by this change — no build, migration, or Stripe/wallet state is involved, so a
revert is complete on its own. If a screenshot is already uploaded to the Play Console, replacing it
there is a console action independent of this repo.

## 9. Verification performed

- Ran the generator; all 8 PNGs re-render deterministically from a clean `screenshots/` directory.
- Asserted every output matches its artboard's exact pixel size (the generator fails loudly if not).
- Re-rendered the Android set after the multi-size refactor and confirmed no visual regression.
- Inspected all 8 rendered PNGs visually, plus pixel-level checks (a small PNG decoder) on the
  bottom edge of each artboard to confirm no content is cut.
- Found and fixed a real rendering bug this exposed: headless Chromium reserves 87px of window
  chrome, so the captured viewport was 1833px tall while the PNG was 1920px — the bottom of every
  artboard was being cropped and back-filled with the page background. The generator now measures
  that inset at runtime, renders into a correspondingly taller window, and crops the PNG back to
  1080×1920.
- No app build was run: **this change touches no app code**, so `npm run build` / `tsc` are not
  applicable. No backend tests are affected.

## 10. What was NOT verified

- **Brand font not applied.** Plus Jakarta Sans could not be fetched (npm, PyPI and
  fonts.googleapis.com are all blocked by this environment's proxy) and is not vendored in the repo,
  so the committed PNGs render headlines in a fallback grotesque. The generator embeds the real font
  automatically once the `.ttf` files are dropped into `driver-app/assets/fonts/` — **re-run it
  before submitting the listing**.
- **Brand red not colour-matched to the rider-app set.** These use the canonical `#FF3B30`
  (`shared/theme` / `brand-spinr.md`). The existing rider screenshots appear very slightly deeper;
  they were not compared on a calibrated display. `RED` is a single constant at the top of the
  generator if an exact match is wanted.
- **No visual-regression tooling exists for driver-app** (admin-dashboard's Playwright job covers
  only its own 6 pages), so these renders were reasoned about and eyeballed, not diffed against a
  baseline.
- **The iPad set shows the phone UI in a device frame.** driver-app has no dedicated tablet
  layout, so there is no real iPad UI to render. `app.config.ts` sets `ios.supportsTablet: true`, so
  App Store Connect will ask for iPad screenshots; if Apple review wants genuine iPad captures they
  must come from a build running on an iPad. Flagged rather than faked.
- No screenshot was tested against App Store Connect or the Play Console upload validators — pixel
  dimensions were verified locally against Apple's and Google's published sizes, not by uploading.
- Screenshot content is a designed mock-up, not a device capture of a running build; it was matched
  against the app's source, not against a screen recording.
