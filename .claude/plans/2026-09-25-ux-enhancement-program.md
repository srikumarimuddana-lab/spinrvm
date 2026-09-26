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
| W2.3 | Unify driver toasts onto the shared toast, by swapping the implementation behind `driver-app/hooks/useToast.ts` so the 25 callers are unchanged | driver-app | flag `driver_unified_toast_enabled` | tests; `[H]` device check |

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
| W4.1 | Marker interpolation util (lerp position + bearing, snap on large jumps, off under Reduce Motion) for monitoring and the live-ride map | admin | none | unit tests on util; monitoring baseline unchanged (tiles stubbed) |
| W4.2 | Same util on `/track`, plus brand tokens and a clear "arrived / trip ended" state. Adds no PII beyond today's driver name and plate | web | none | tests; `spinr-regulatory-compliance-checker` (PIPEDA) |

### Wave 5: admin efficiency
| ID | Item | Surface | Gate | Verify |
|---|---|---|---|---|
| W5.1 | Command palette routes derived from the sidebar config (one source), plus a `?` shortcut sheet | admin | existing `admin_command_palette_enabled` (flip `[H]`) | tests |
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
| W7.1 | Translations per D3 | apps | — | key-parity test en vs fr |
| W7.2 `[~]` | Correct stale counts in the rider/driver design skill | docs | — | — |
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
