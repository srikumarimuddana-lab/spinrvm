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
| W1.1a `[~]` | Replace `allowFontScaling={false}` with `maxFontSizeMultiplier` (D1). Keep a hard lock only where a fixed box truly requires it, e.g. an OTP digit | rider-app (~28 sites) | unflagged a11y | grep = 0 unjustified sites; tsc; affected tests; `[H]` largest-text device pass |
| W1.1b | Same | driver-app + shared (~56 sites) | unflagged a11y | same |
| W1.2 | `aria-sort` on `SortableHead`, sticky `thead`, `aria-label` on 14 unlabelled search inputs | admin | none (no visible change at rest) | tests; axe; baselines unchanged |
| W1.3 | Admin motion: `MotionConfig reducedMotion="user"`, a global `prefers-reduced-motion` rule, and an exit animation on the alert feed | admin | none | tests; baselines unchanged |
| W1.4 | `/track`: honour Reduce Motion, plus a public "link expired or invalid" state instead of "Go to Dashboard" | web | none | tests; manual check |

### Wave 2: one feedback system per surface
| ID | Item | Surface | Gate | Verify |
|---|---|---|---|---|
| W2.1 | Replace 10 browser `confirm()`/`alert()` calls with `AlertDialog` (destructive styling) | admin | none; staff-only UX note | tests per page |
| W2.2 | Toast policy: limit 3, errors persist until dismissed, `role=status`/`role=alert` | admin | none | unit test on reducer |
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
| W7.2 | Correct stale counts in the rider/driver design skill | docs | — | — |
| W7.3 | Remove unused `ErrorScreen`, `FormScreen` and `shared/validators`. **Deletion: ask first** | shared | approval | tests |

## Cannot be done from an agent session `[H]`

- **Production settings:** flipping any flag in the production `settings` row.
- **Visual baselines:** re-capturing them via `update-visual-baselines.yml`.
- **Real devices:** device checks (largest text size, Reduce Motion, haptics) and EAS/native builds.
- **Marketing site:** www.spinr.ca is not in this repo, so it needs its repo attached or its owner.

## Progress log

- 2026-09-25: #5829 research doc, #5830 Reduce Motion for loops, #5832 test time-bomb fix, #5836 scorecard doc. Plan written.
- 2026-09-25: #5836 merged (scorecard and plan). W1.1a opened: rider-app text follows the OS text size up to 1.5×.
