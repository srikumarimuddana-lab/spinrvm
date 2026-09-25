# Change Impact & Risk Log: store listing matches weekly-only payouts

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-25 |
| Author | Claude Code (agent session) |
| Surface(s) | driver-app (store screenshots and generator only, no app code) |
| Domain (Sentry tag) | drivers / payments (listing copy only, no runtime code) |
| PR / commit link | branch `claude/store-listing-weekly-payouts` |
| Related issue or gap ID | Owner decision 2026-09-25 (weekly-only payouts, PR #5810); in-app disputes removed (PR #5811) |

## 1. Issue / gap identified

The committed Spinr Driver store screenshots still showed a "Cash out" button on the earnings screen (`01-drive-canadian` and `06-earnings`, 12 PNGs across 6 sizes). Spinr pays drivers only through the weekly Sunday auto-payout, so the button advertised a feature that no longer exists. On `06-earnings`, the "T4A-ready" callout card also covered the "Next payout: Every Sunday" row, which hid the weekly schedule.

## 2. Root cause

PR #5810 fixed the generator source (`generate_screenshots.py`: removed the mock button and the "Or cash out anytime" caption) and the `metadata.json` text. It did not re-render the PNGs, and it left a note that a human had to do that. The callout overlap had been there before: the callout's `y_fraction` (0.81) was chosen while the Cash out button still made the balance card taller.

## 3. Fix / remediation

- Re-ran `python3 driver-app/store-assets/generate_screenshots.py`, which renders all 48 artboards. Only `01-drive-canadian` and `06-earnings` changed (12 PNGs). The other 36 came out byte-identical to the committed files, so the render is deterministic in this container and nothing else drifted.
- `06-earnings`: moved the "T4A-ready" callout from `0.81` to `0.755` of canvas height. It now sits over the empty right half of the Available Balance card, where the Cash out button used to be, and "Next payout: Every Sunday" is fully visible. I checked this visually on android-1080x1920, ios-1290x2796, ios-1320x2868 and ipad-2048x2732.
- Text sweep: no change was needed. I checked every store listing field in `driver-app/store-assets/metadata.json` (description, full_description, short_description, subtitle, promotional_text, release_notes, review_notes) and `rider-app/store-assets/metadata.json`. PR #5810 had already changed both driver descriptions and both release notes to "Weekly automatic payouts". Neither file claims instant payouts or an in-app dispute feature. The driver copy's "Keep 100% of every fare" is unchanged.

Sweep patterns (case-insensitive): `instant pay`, `instantly pay`, `cash out` / `cashout`, `paid instantly`, `get paid instantly`, `payout on demand`, `on-demand payout`, `instant transfer`, `same-day payout`, `dispute`.

Paths swept: `driver-app/store-assets/**`, `rider-app/store-assets/**`, `driver-app/app.config.ts`, `rider-app/app.config.ts`, `driver-app/eas.json`, `rider-app/eas.json` (no store description or release-notes fields there), `.github/workflows/submit-play-store.yml` (no listing text), `docs/growth/**`, `docs/comms/**`, `docs/runbooks/app-store-review.md`. The repo has no fastlane metadata directory and no website/marketing copy directory.

Reviewed and kept on purpose:
- `08-quests` callout "Straight to wallet / The moment you hit it", and its subtitle "land straight in your wallet". Quest rewards are credited to the in-app wallet balance (`backend/routes/quests.py`, `reward_type` `wallet_credit`). That describes a wallet credit, not a bank payout, so it does not conflict with weekly payouts.
- `02-go-online` "Go live instantly" and the description line "Go online instantly". These are about going online, not about payouts.

## 4. Risk & impact on existing functionality

Blast radius: **isolated**. The generator is a standalone script. Nothing imports it, and no runtime code reads the PNGs. The live app, backend and admin-dashboard are unaffected. The committed PNGs only reach users when someone uploads them to App Store Connect or Play Console.

The only other consumer of the screenshots is `driver-app/store-assets/README.md`, which describes the screens and upload slots. Its screen table ("06-earnings | Your earnings. Clearly.") is still accurate, so I did not edit it.

Alternative considered: regenerate only the two affected screens by hand-filtering the output. Rejected because the generator has no per-screen flag (only per-artboard). A full render also shows that the other 36 PNGs have not drifted, and that check is worth more than a narrower run.

## 5. User-experience effect

- **Drivers and prospective drivers** browsing the store listing will stop seeing a Cash out button, and will see "Every Sunday" as the next payout. They see this only after a human uploads the new PNGs (see section 8a). Until then the live listing is unchanged.
- The running app does not change, so nobody sees a mid-session difference.
- Headlines, subtitles and captions are unchanged. The only copy change is the one PR #5810 already made ("Automatic weekly deposits").

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `driver-app/store-assets/generate_screenshots.py` | `06-earnings` T4A callout `y_fraction` 0.81 → 0.755 | Stop the callout hiding "Next payout: Every Sunday" |
| `driver-app/store-assets/screenshots/*-01-drive-canadian.png` (6 files) | Re-rendered | Remove the stale Cash out button |
| `driver-app/store-assets/screenshots/*-06-earnings.png` (6 files) | Re-rendered | Remove the Cash out button, show "Automatic weekly deposits", show the Sunday schedule |
| `docs/change-log/2026-09-25-store-listing-weekly-payouts.md` | New | This log |

## 7. Before / after

```
# Before (committed PNG, rendered from the pre-#5810 generator)
AVAILABLE BALANCE  $318.64                 [ Cash out ]
Next payout                         [T4A-ready card covering "Every Sunday"]
Callout: "Paid every Sunday / Or cash out anytime"
```

```
# After
AVAILABLE BALANCE  $318.64          [T4A-ready card in the empty right half]
Next payout                                    Every Sunday
Callout: "Paid every Sunday / Automatic weekly deposits"
```

```python
# generate_screenshots.py, build_cards() 06-earnings
- ("right", 0.81, ic("chart"), "T4A-ready", "Tax docs at year end"),
+ ("right", 0.755, ic("chart"), "T4A-ready", "Tax docs at year end"),
```

## 8. Rollback plan

Repo-side: no live data is involved, so reverting the commit is enough. It restores the previous PNGs and the callout position.

Store-side: if the new screenshots are already uploaded and have to come back down, re-upload the previous PNGs from git history (`git show <prev-sha>:driver-app/store-assets/screenshots/<file>`) in the same console screens listed below. Screenshot changes on both stores go out with the next listing/version review, so no app redeploy is needed.

### 8a. Human step: uploading to the stores

The agent cannot upload. A person with console access must do it:

- **App Store Connect:** My Apps → Spinr Driver → App Store tab → select the version → **Screenshots** (6.9" slot: `ios-1320x2868-*`, 6.5": `ios-1284x2778-*`, 5.5": `ios-1242x2208-*`, iPad 13": `ipad-2048x2732-*`). If any live **Promotional text** or **Description** differs from `metadata.json`, update it on the same page.
- **Google Play Console:** Spinr Driver → Grow → Store presence → Main store listing → Phone screenshots (`android-1080x1920-*`). Check that the full description matches `metadata.json` (it should say "Weekly automatic payouts to your bank").
- Replace at least the `01` and `06` screenshots in every slot. The other screenshots did not change.

## 9. Verification performed

- [x] Generator run in this container: headless Chromium from `/opt/pw-browsers` (the generator's first candidate path), 48/48 rendered, and every PNG's pixel size asserted by the generator itself.
- [x] `git status` after the render: only the 12 PNGs for `01` and `06` changed; the other 36 are byte-identical.
- [x] Opened and visually inspected `android-1080x1920-06-earnings`, `ios-1290x2796-06-earnings`, `ios-1320x2868-06-earnings`, `ipad-2048x2732-06-earnings` and `ios-1320x2868-01-drive-canadian`. None has a Cash out button. The callout reads "Paid every Sunday / Automatic weekly deposits", and "Next payout: Every Sunday" is fully visible on each `06` file.
- [x] Blast-radius grep: patterns and paths as listed in section 3.
- [x] Driver classification: the new and moved copy adds no control-of-work language.
- [ ] No automated tests exist for the store-asset generator. No production app build is relevant because no app code changed.

## What was NOT verified

- The live App Store Connect and Play Console listings were not inspected. I cannot see what is currently published, so I cannot confirm it matches `metadata.json`.
- I did not open `ios-1242x2208-*` and `ios-1284x2778-*` visually. They use the same layout formula as the sizes I checked, so the callout position was reasoned about for them, not seen.
- Headlines still use the fallback grotesque font, not Plus Jakarta Sans. That was already true (see the README's "Brand font"), and this change does not affect it.
- The rider-app has no screenshot generator and no committed screenshots, so nothing was regenerated for it.

## Follow-ups outside this change's ownership (not edited)

| Location | What it says | Suggested fix |
|---|---|---|
| `backend/migrations/212_seed_saskatchewan_driver_faqs.sql` / `325_faq_answers_drop_ai_voice.sql` (DB-seeded FAQ), mirrored in `docs/driver-faqs-saskatchewan.md:179-181` | "When and how do I get paid?" gives no schedule | Now that weekly-only is policy, a new migration could state "paid out automatically every week (Sunday) to your bank account" |
| `docs/runbooks/saskatoon-launch.md:274` | "within 24 h (instant payout if enabled)" | Drop the instant clause |
| `.agents/roles/business-analyst.md:117`, `.agents/roles/manager.md:44` | List "Instant cashout" / "Instant payout / cash out" as a product feature | Mark as not offered (weekly only) |
| `driver-app/app/driver/payout.tsx:549` | Code comment "able to cash out" | Comment-only wording |
