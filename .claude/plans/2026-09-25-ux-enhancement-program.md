# UX enhancement program: apps, admin portal and public web (2026-09-25)

**Goal:** close the gaps in `docs/audit/2026-09-25-ux-scorecard-world-class-minimal.md` and `docs/audit/2026-09-25-ux-motion-admin-website-research.md`, one small, reviewed PR at a time. The target is world-class and minimal: fewer patterns, done consistently, accessible by default.

**Status key:** `[ ]` open · `[~]` in progress · `[x]` merged · `[H]` needs a human (cannot be done from an agent session)

## How every item ships

- **One PR at a time from `claude/spinr-animations-admin-ux-x7nl5x`.** The agent can push only this branch. Each PR merges before the next starts, and the branch is restarted from `main` after each squash-merge.
- **Small PRs.** Commits of ≤3 files and diffs of about 200 lines or less, per CLAUDE.md. Big sweeps are split into batches.
- **Verify before pushing.** Each PR states its checks up front: typecheck, lint, affected tests, and new tests proven to fail on the old code. Each also carries a Change Impact Log entry.
- **Review before merge.** The most relevant `spinr-*` reviewer runs on the diff:
  - accessibility for a11y work
  - dispatch for the offer card
  - safety for SOS
  - migration for flags
- **Feature flags.** User-visible, non-trivial changes to live-tested flows are dark-launched behind default-off flags. The flags are `settings` columns exposed via `GET /settings` (the existing pattern, e.g. migration 484). Accessibility fixes that only affect users who opted into an OS setting follow the unflagged #4607/#5830 precedent.
- **Visual evidence.**
  - Mobile has no visual tooling, so every mobile PR says "not screenshotted" and lists a manual device check `[H]`.
  - Admin has 6 merge-blocking visual baselines (`login`, `dashboard-home`, `-drivers`, `-monitoring`, `-settings`, `-rides`). A PR that intentionally changes one needs a human to re-run `update-visual-baselines.yml` `[H]`.

## Decisions (answered 2026-09-25)

| ID | Decision | Answer | Affects |
|---|---|---|---|
| D1 | Text-size cap replacing `allowFontScaling={false}` | **1.5×**, via one shared `MAX_FONT_SCALE` constant | W1.1 |
| D2 | Loading standard in the apps | **Skeletons for full-screen content loads**; spinners stay for short blocking actions. Behind a flag | W3.5 |
| D3 | Translations | **Finish French to 100%; hide es/zh until complete.** Agent-drafted French needs human review before release | W7.1 |
| D4 | Flag strategy | **One flag per feature**: each flagged item ships its own migration, `/settings` field, admin toggle and app hook | W2.3, W3.x |

## Waves

Waves run in order. The surfaces within a wave touch different files, but they still ship one PR at a time because only one branch is available.

### Wave 1: accessibility floor and quick wins (low risk)
| ID | Item | Surface | Gate | Verify |
|---|---|---|---|---|
| W1.1a `[x]` | Replace `allowFontScaling={false}` with `maxFontSizeMultiplier` (D1). Keep a hard lock only where a fixed box truly requires it, e.g. an OTP digit | rider-app (~28 sites) | unflagged a11y | grep = 0 unjustified sites; tsc; affected tests; `[H]` largest-text device pass |
| W1.1b `[x]` | Same | driver-app + shared (~56 sites) | unflagged a11y | same |
| W1.2 `[x]` | `aria-sort` on `SortableHead` and `aria-label` on 15 unlabelled search inputs. **Sticky `thead` moved to W5.5:** tables sit inside horizontal-scroll containers, which stop `position: sticky` from following the page scroll, so it needs a table-layout change | admin | none (no visible change) | tests; build; baselines unchanged |
| W1.3 `[x]` | Admin motion: a global `prefers-reduced-motion` rule (spinners exempt) and an exit animation on the alert feed. `MotionConfig` was skipped because its only consumer already uses `useReducedMotion()`; add it with the next `motion` component | admin | none | tests; build; baselines unchanged |
| W1.4 `[x]` | Public web. **Reduce Motion on `/track` needed no code:** W1.3's global rule already overrides the car marker's transition, and `/track` already shows its own "link invalid or expired" state. The remaining gap was the 404 page, which sent every visitor to the staff dashboard; it now picks its action by path (tracking link, driver sign-up, company portal, or dashboard for staff) | web | none | tests; build served and fetched |

### Wave 2: one feedback system per surface
| ID | Item | Surface | Gate | Verify |
|---|---|---|---|---|
| W2.0 `[x]` | **Added 2026-09-26:** `AlertDialog` came from a separate Radix package with its own focus and layer registries, so a confirmation opened inside a side sheet never took focus and Escape closed the sheet too. Import it from `radix-ui` like the other wrappers (user chose this fix) | admin | none | regression test; Chromium check; full suite; baselines unchanged |
| W2.1 `[x]` | Replace 12 browser `confirm()` calls with an in-app dialog via a `useConfirm()` hook (destructive styling). The 5 `alert()` error messages move after W2.2, so they become persistent error toasts rather than auto-dismissing ones | admin | none; staff-only UX note | tests per page |
| W2.2a `[x]` | **Added 2026-09-26:** `text-destructive-foreground` (every error toast, red buttons in 23 files) was never defined, so text on red inherited near-black (3.67:1). Define the token as white (4.83:1). User chose "own small PR first" | admin | none | static contrast guard; build emits the class; baselines unchanged |
| W2.2 `[x]` | Toast policy: limit 3, errors persist until dismissed, `role=status`/`role=alert` | admin | none | unit test on reducer |
| W2.3 `[~]` | Unify driver toasts onto the shared toast, by swapping the implementation behind `driver-app/hooks/useToast.ts` so the 25 callers are unchanged. **Decided 2026-09-26:** a driver-app copy of the rider banner; nothing moves to `shared/`. | driver-app | flag `driver_unified_toast_enabled` | tests; `[H]` device check |

### Wave 3: feel (flagged)
| ID | Item | Surface | Gate | Verify |
|---|---|---|---|---|
| W3.1 | *(Folded into each item under D4: every flagged item carries its own migration, `/settings` field, admin toggle and app hook, reviewed by `spinr-migration-reviewer`.)* | — | — | — |
| W3.2 | Haptics vocabulary via `expo-haptics`: assign, arrive, pay, errors, go-online. SOS hold gets haptic ticks, after which its pulse can respect Reduce Motion | apps + shared | flag `ux_haptics_enabled`; respects driver prefs, adds a rider pref | tests; `spinr-safety-sos-reviewer`; `[H]` device |
| W3.3 | Driver offer card v2: countdown on the UI thread (`scaleX`, anchored to server expiry), urgency in the last 5 s with a text equivalent, accept/expire feedback | driver-app | flag `driver_offer_card_v2_enabled` | tests; `spinr-dispatch-reviewer`; `[H]` device |
| W3.4 | "Driver found" reveal, and crossfades on ETA/fare changes. The fare shows the server's Decimal-formatted value; only the transition animates | rider-app | flag `rider_motion_v2_enabled` | tests; `[H]` device |
| W3.5 | Skeletons for full-screen content loads (D2): driver activity/earnings, rider history/receipts/wallet | apps | flag `app_skeletons_enabled` | tests |

### Wave 4: smooth maps on the web
| ID | Item | Surface | Gate | Verify |
|---|---|---|---|---|
| W4.1 `[~]` | Marker interpolation util (lerp position + bearing, snap on large jumps, off under Reduce Motion) for monitoring and the live-ride map | admin | none | unit tests on util; monitoring baseline unchanged (tiles stubbed) |
| W4.2 `[~]` | Same util on `/track`, plus brand tokens and a clear "arrived / trip ended" state. Adds no PII beyond today's driver name and plate | web | none | tests; `spinr-regulatory-compliance-checker` (PIPEDA) |

### Wave 5: admin efficiency
| ID | Item | Surface | Gate | Verify |
|---|---|---|---|---|
| W5.1 `[~]` | Command palette routes derived from the sidebar config (one source), plus a `?` shortcut sheet | admin | existing `admin_command_palette_enabled` (flip `[H]`) | tests |
| W5.2 | Saved filter views (per admin, browser-local) on Rides and Drivers | admin | none | tests |
| W5.3 | Shared `EmptyState` component; pollers pause while the tab is hidden and refresh on focus | admin | none | tests |
| W5.5 | Sticky table headers. Needs the table scroll container restructured (or `thead` sticky inside a vertically scrolling table wrapper) without changing baselined pages | admin | none | baselines, which may need re-capture `[H]` |
| W5.4 | Bulk actions on Drivers. **Needs scoping:** backend bulk endpoints plus RBAC review. Proposal first, not code | admin + backend | proposal | `spinr-admin-rbac-reviewer` |

### Wave 6: Quiet Console and a consistent public brand
| ID | Item | Surface | Gate | Verify |
|---|---|---|---|---|
| W6.1 | Hardcoded colours → tokens (~90 files) in batches of about 10, with baseline pages last | admin | resolves to the same colours under the current theme, so no visible change | baselines unchanged per batch |
| W6.2 | Public brand shell: real logo on `/login` (replacing the "S" placeholder), brand tokens on `/company-*` and `/register/driver` | admin/web | none | `[H]` re-baseline `login` |
| W6.3 | Switch `admin_theme_v2_enabled` on | admin | `[H]` settings flip plus re-baseline all 6 pages | — |
| W6.4 | Admin font Geist → Plus Jakarta Sans (epic #2785) | admin | `[H]` re-baseline all 6 pages | — |

### Wave 7: content and housekeeping
| ID | Item | Surface | Gate | Verify |
|---|---|---|---|---|
| W7.1 `[x]` | Translations per D3 | apps | — | key-parity test en vs fr |
| W7.2 `[x]` | Correct stale counts in the rider/driver design skill | docs | — | — |
| W7.3 | Remove unused `ErrorScreen`, `FormScreen` and `shared/validators`. **Deletion: ask first** | shared | approval | tests |

## Cannot be done from an agent session `[H]`

- **Production settings:** flipping any flag in the production `settings` row.
- **Visual baselines:** re-capturing them via `update-visual-baselines.yml`.
- **Real devices:** device checks (largest text size, Reduce Motion, haptics) and EAS/native builds.
- **Marketing site:** www.spinr.ca is not in this repo, so it needs its repo attached or its owner.

## Progress log

- 2026-09-25: #5829 research doc, #5830 Reduce Motion for loops, #5832 test time-bomb fix, #5836 scorecard doc. Plan written.
- 2026-09-25: #5836 merged (scorecard and plan). W1.1a opened: rider-app text follows the OS text size up to 1.5×.
- 2026-09-25: W1.1a merged (#5837, rider). W1.1b split into three PRs, since the driver panels overlay the map and animate between fixed heights:
  - #5838 merged: trip panels.
  - #5840 merged: top bar and HUD.
  - W1.1b-3 opened: offer card, turn banner, justified locks, driver guard. It also fixes SOS being partly hidden under the turn banner, using the placement you chose ("move SOS below banner").
- Follow-up found in #5840: `shared/store/locationStore.ts:121` (`createJSONStorage(() => Platform.OS === 'web' ? localStorage : AsyncStorage)`) type-checks differently depending on which files are in the driver-app program. Adding one new test file turned it into a TS error. An explicit storage type would remove the fragility. Not yet scheduled.
- 2026-09-25: W1.1b complete (#5838, #5840, #5841), so text-size scaling is done across both apps with guards in each. W1.2 opened: admin `aria-sort` and search-input labels.
- 2026-09-25: W1.2 merged (#5843). W1.3 opened: admin Reduce Motion and the alert-feed exit.
- 2026-09-25: W1.3 merged (#5855). W1.4 opened: the 404 page sends public visitors to their own entry point.
- Follow-up found in W1.4, not scheduled: on `track.spinr.ca`, middleware answers the root and multi-segment paths with a plain-text `Not found`. A single segment already reaches the tracking page's own error state, so only malformed URLs see it. Changing it touches host routing and the tracking CSP.
- Follow-up from the W1.4 review, not scheduled: middleware treats only `/register/` (with a slash) as public, so a driver applicant who types bare `/register` is redirected to the staff login. Fixing it means changing the auth-routing allowlist in `src/middleware.ts`.
- 2026-09-26: W1.4 merged (#5857), so Wave 1 is complete. W2.0 opened.
- 2026-09-26: W2.0 added and prepared. Building W2.1 showed that confirmation dialogs inside a side sheet misbehave (no focus; Escape closes the sheet), confirmed in Chromium. W2.1 (12 call sites, in two PRs) is ready locally behind it. Follow-ups: remove the now-unused `@radix-ui/react-alert-dialog` dependency; check toasts over an open sheet (`react-toast` also bundles its own layer).
- 2026-09-26: W2.0 merged (#5869). W2.1 opened.
- 2026-09-26: W2.1 prepared locally in two parts: staff dashboard (10 sites) and company portal (W2.1b, 2 sites). Security and accessibility reviews found no blockers; their should-fixes are done. Follow-up found: 24 files use `bg-destructive text-destructive-foreground`, but `--color-destructive-foreground` isn't defined, so those red buttons lose their white text. The central fix changes rendered colour on baselined pages, so it needs a baseline re-capture `[H]`.
- 2026-09-26: W2.1 merged (#5870). W2.1b opened: company-portal booking cancel and section archive.
- 2026-09-26: W2.1b merged (#5871). W2.2a opened.
- 2026-09-26: W2.2a prepared: define the missing `--destructive-foreground` token (error toasts and red buttons fail contrast in light mode). Replaces the "24 files" follow-up from W2.1.
- Correction, 2026-09-26: W1.3's change log and PR (#5855), and later logs, cited WCAG 2.1 SC 2.3.3 as if it were required. 2.3.3 (animation from interactions) is Level AAA, not AA, so CLAUDE.md's "WCAG 2.1 AA" rule doesn't require it. Honouring Reduce Motion is good practice, not a compliance gate.
- 2026-09-26: W2.2 prepared: error toasts persist and are announced assertively, others are polite; up to 3 at once; the close button is named, visible on errors and full-strength white. Next is W2.1c: the 5 browser `alert()` error messages become persistent error toasts.
- 2026-09-26: W2.2a merged (#5872). W2.2 opened.
- 2026-09-26: W2.2 merged (#5873). W2.1c opened.
- 2026-09-26: W2.1c prepared: the 5 browser `alert()` errors become error toasts, except the ride force-cancel failure, which shows inside its dialog (a modal Dialog hides toasts from screen readers). Follow-ups, not scheduled:
  - The surge form's on/off toggle, multiplier and justification textarea have no accessible names.
  - Check with a screen reader whether error toasts fired while other modal dialogs are open get announced. It's app-wide and pre-existing.
- 2026-09-26: W2.1c merged (#5877). W7.2 opened: the rider/driver design skill's counts, rechecked on `main` (about 143 `showToast` calls in rider-app; 5 driver-app files import the shared Button; UX4 resolved by `shared/utils/motion.ts`).
- 2026-09-26: W7.2 merged (#5880). Driver SOS toast fix opened: while designing W2.3 we found that every driver toast (full width, 60 pt from the top, 3.5 s) covers the dashboard's top-right SOS and blocks taps on it. Toasts now end 76 pt from the right (`SOS_COLUMN_CLEARANCE`). User chose "narrow both toasts": this live fix ships unflagged like #5841, and W2.3's flagged banner uses the same constant. The safety review found no blockers; a rider-app fix follows as its own PR.
- Follow-up found while reviewing the SOS fix, not scheduled: white text on the toast status colours fails AA contrast in both apps (success about 2.5:1, warning about 2.1:1). It's a theme-level change in `shared/theme`, so it needs its own decision.
- 2026-09-26: Driver SOS toast fix merged (#5885). Rider SOS toast fix opened: the safety review of the driver fix found that the rider toast (full width, `insets.top + 8`, 4 s) covers the floating SOS on ride-in-progress, driver-arriving and driver-arrived. User chose its own small safety PR: rider toasts end at the same 76 pt clearance.
- Follow-up from the rider SOS review, not scheduled: on tablets (768 pt or wider), ride-in-progress places its floating SOS in the map column, so the toast still covers it. This is pre-existing. The side panel's own SOS stays reachable below the toast band. Fixing it means moving that floating SOS or making the toast aware of SOS position; the user was offered both.
- 2026-09-26: Rider SOS toast fix merged (#5886). Document reviewer fix opened. While reviewing W5.1 we found the driver document reviewer's A/R/J/K shortcuts could approve a pending document by accident, in three ways:
  - Ctrl/Cmd+A twice (select all), or holding A;
  - letters typed in the reject-reason picker;
  - a quick third A resending the review.
  Documents that need an expiry date were protected. Fixed client-side, ahead of W4.1 because it's a live accidental-approval path on driver eligibility.
- Follow-up, not scheduled: `admin_review_driver_document` (`backend/routes/admin/documents.py`) has no `status = 'pending'` condition, so two racing reviews (for example two tabs) both apply, with duplicate audit rows and driver notifications. It needs a backend "0 rows means already reviewed" guard in its own PR.
- 2026-09-26: Document reviewer fix merged (#5887). W4.1 opened: the marker interpolation util (position and heading glide between polls; snaps on large jumps, stale feeds and Reduce Motion) and its use on the monitoring map. The live-ride map (`rides/live/[id]/live-map.tsx`) isn't wired yet. W4.2 (`/track`) is built on this util and queued behind it.
- W4.1 follow-ups from its review, not scheduled:
  - wire the util into the live-ride map;
  - ignore a poll position older than the one already shown;
  - profile the animation loop at fleet scale (hundreds of markers).
- 2026-09-26: W4.1 merged (#5888). Review follow-ups from W4.2, W5.1, W7.1 and the document reviewer fix are now in `ACTION_ITEMS.md`, each re-checked against `main`. Priorities are proposed, not decided:
  - B44 (P1): the public `/track` link keeps exact pickup and drop-off addresses for up to 24 h after the trip ends.
  - C137 (P3): four sidebar links are gated on a different module from their data, including a decision on who may flush Redis prefixes.
  - C138 (P3): the backend guard against two racing document reviews, the follow-up above. Re-checking showed it must be a compare-and-set, not a plain "still pending" filter, because editing a rejection's reason is a supported re-review.
  - C139 (P3): rider-app's two translation-key sets.
  - The `/track` page's own older issues stay in W4.2's change log, which ships with W4.2: the ETA label, a blank map after one failed poll, the `scheduled` label, the developer-facing no-key message, and polling after the trip ends.
- 2026-09-26: Follow-ups merged (#5889). W2.3 backend half opened. Migration 490 adds the default-off `driver_unified_toast_enabled` flag, and `/drivers/config` serves it.
  - It was built as 489 and renumbered, because `main` gained `489_monitoring_connection_summary.sql` first.
  - The migration's comments were corrected before merge; they had called the new toast a `shared/` component.
  - The driver-app half follows in its own PR. Its accessibility review found nothing new: the contrast failure is the theme follow-up already logged, and the 1.5× text cap follows D1.
- 2026-09-26: W2.3 backend merged (#5890). W2.3 driver-app half opened, behind that flag:
  - a driver-app toast store and host modelled on the rider banner;
  - `showToast` routes to it when the flag is on;
  - the host is mounted in `_layout.tsx`, and the flag is read from `/drivers/config`.
  - Flag off is byte-identical to today. Turning the flag on needs a device check `[H]` first, because driver-app has no visual tooling.
- 2026-09-26: W2.3 driver-app half merged (#5891), so W2.3's code is complete. Before the flag can be turned on:
  - migration 490 must be applied in production;
  - a device check `[H]` is needed.
- 2026-09-26: W4.2 opened: the public `/track` page glides the car with W4.1's util, uses the admin tokens (fixed light), and shows clear "The driver has arrived" and trip-ended states. Its privacy review was clean; the older address exposure it found is B44.
- 2026-09-26: W4.2 merged (#5892). W7.1 part 1 of 2 opened: French is complete in both apps (key parity with English, pinned by a test), and Spanish and Chinese are hidden from both pickers (decision D3). Part 2 follows: the picker title, restoring the saved language at start-up, and the load-vs-pick race fix.
- 2026-09-26: W7.1 part 1 merged (#5893). Part 2 opened:
  - the rider picker title is translated;
  - both apps restore the saved language at start-up;
  - a language pick always beats a slow restore (the race the edge-case review found);
  - the stores refuse hidden language codes.
  Still open for W7.1: a fluent speaker's review of the French `[H]`, and C139 (rider-app's two key sets).
- 2026-09-26: W7.1 part 2 merged (#5894), so W7.1 is done in code; the French review `[H]` remains. W5.1 is split into three PRs:
  - A: the admin nav as one source for the sidebar and the palette (no visible sidebar change; the palette catches up with the sidebar);
  - B: palette fixes (Enter opens the highlighted row; focus returns on close);
  - C: the "?" shortcut sheet with its per-admin off switch (WCAG 2.1.4), the Ctrl/Cmd+K dialog guard, and the change log.
  Part A opened.
- 2026-09-26: W5.1 part A merged (#5895). The sidebar was byte-identical and its 5 visual baselines passed in CI. Part B opened: palette Enter opens the highlighted row when matches span sections, and the palette returns focus on close.
- 2026-09-26: W5.1 part B merged (#5896): palette Enter opens the highlighted row, and focus returns on close. Part C opened: the "?" shortcut sheet with its per-admin single-key off switch (WCAG 2.1.4), the palette's "Keyboard shortcuts" entry and footer hints, the Ctrl/Cmd+K dialog guard, and the W5.1 change log covering all three parts. W5.1 stays `[~]` until part C merges; everything stays behind `admin_command_palette_enabled` (flip `[H]`).
