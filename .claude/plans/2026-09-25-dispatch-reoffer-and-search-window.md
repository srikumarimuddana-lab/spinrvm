# Dispatch: re-offer decliners, shorter search, honest "no drivers" ending

Status: **plan only — nothing implemented.** Written 2026-09-25 for the
Saskatoon launch, where a ride may have ~10 drivers in range and nobody
accepts on the first pass.

## Problem

Today, with 10 drivers in range and `max_simultaneous_offers = 3`:

| Time | What happens |
|---|---|
| 0–60 s | Offers go 3 → 3 → 3 → 1. Every driver has now been asked once. |
| 60–300 s | Every driver holds a 300 s `spinr:offer_skip` key for this ride. Only newly-online drivers can get it. The rider watches "searching" with nothing happening. |
| 300 s | Ride auto-cancels (`matching.py:2177` timer + `stuck_ride_sweeper.py` backstop). Rider app jumps to home with no explanation (`rider-app/app/_layout.tsx:185-189`). |

Uber gives up after about **2 minutes** and offers another product or
"notify me" ([Uber Developers FAQ](https://developer.uber.com/docs/riders/faq)).
Lyft re-matches unfilled requests on every dispatch cycle.

## Decisions already made (user, 2026-09-25)

1. **Re-ask drivers who *declined*.** They were at the phone and looked at
   the trip. A second ask a minute later can convert.
2. **Do not re-ask drivers who *ignored* the offer.** Ignoring means "not at
   the phone." Re-asking wastes a round. Three misses in a row takes them
   offline (`auto_offline_miss_threshold`), and that is fine and intended.
3. Shorter search than 5 minutes, then a clear ending for the rider.

## Blocker found while planning — ignored offers are recorded as declines

`driver-app/store/driverStore.ts:576-584`: when the countdown reaches 0 with
the app in the foreground, the app calls `declineRide()` itself, with no
reason. The backend (`routes/drivers/ride_flow.py:653+`) then:

- records it as a **decline** (`ride_offers.status = 'declined'`, `ride_declined` audit row), and
- calls **`reset_miss_streak`** (`ride_flow.py:804`).

So a driver who leaves the app open and walks away:

- never reaches the 3-miss auto-offline — the streak resets on every expiry.
  Ghost-driver protection only works for backgrounded or killed apps today;
- would be classed as a "decliner" under decision 1 and re-asked — the exact
  group decision 2 says to skip.

**Phase 0 must ship before the re-offer phase.** Without it, decision 1 and
decision 2 cannot be told apart.

(Real declines, which Phase 0 leaves alone: the offer card's Decline button,
the notification Decline action in `services/backgroundMessaging.ts:443`,
and the Android Auto Decline button in `lib/androidAuto/register.ts:557`.)

## Founder view — ranked by impact on a thin-supply launch

Dispatch logic cannot create drivers. It can stop wasting the ones we have
and stop making riders wait for nothing. In order of impact:

1. **Stop ghost drivers (Phase 0).** In a 10-driver market, one ghost is 10%
   of supply. Each ghost wastes a 15 s round per ride and makes the map
   show a driver who won't come. This is a bug fix and the biggest win.
2. **Honest ending for the rider (Phases 2–3).** 3 minutes, then "No drivers
   right now" with **Try again** and **Schedule**. This beats a silent
   5-minute cancel.
3. **Wider second pass (Phase 4, needs your call).** A driver 12 km away beats
   a cancelled ride. The radius is fixed today; `domain-dispatch.md`'s
   "2 → 5 → 10 km" is not what the code does.
4. **Re-ask decliners (Phase 1).** Cheap, but expect low conversion: most
   declines are about distance or destination, and those haven't changed a
   minute later. Worth having; not the headline.
5. **Launch settings, no code.** For Saskatoon: `max_simultaneous_offers` 3 → 5
   (the first pass finishes in ~30 s instead of ~60 s) and
   `ride_offer_timeout_seconds` 15 → 20 (drivers who are driving get time to
   glance). Both are already per-area admin settings.

Beyond code: at launch, supply is an ops problem — driver recruitment and
opt-in "riders are requesting near you" alerts. Those must stay
informational, never mandatory, because of contractor classification.
Tracked here as a follow-up, not built.

### Alternatives considered

- **Repeat the whole list, including ignorers, every 20 s** (the first
  idea): rejected. It re-pings ghost drivers, and each one wastes a round.
- **Broadcast to everyone in range, or an open-trips board like Uber's
  Trip Radar:** bigger driver-app build and constant "ride taken" noise for
  drivers. Revisit when supply grows.
- **Keep 5 minutes and only improve the searching copy:** rejected. It leaves
  ~4 minutes of dead waiting.

## Target behaviour — all phases on, Saskatoon settings

10 drivers in range, batch 3, 15 s offers. Four drivers decline, six ignore.

| Time | What happens |
|---|---|
| 0–60 s | Pass 1: 3 → 3 → 3 → 1. Ignorers get a skip for the whole search, plus a miss on their streak. Decliners get a 45 s cooldown. |
| ~60–90 s | Decliners' cooldowns end, and the existing 10 s retry loop re-offers them. This is pass 2 — no new loop code. With Phase 4 on, the wider radius adds new drivers here too. |
| 90–180 s | Retry every 10 s picks up drivers who come online or finish a trip. |
| 180 s | Search ends. Rider sees "No drivers available right now" → **Try again** (fresh fare quote) / **Schedule for later**. |

Each driver gets at most **2 offers per ride**. A second decline does **not**
lower their acceptance rate again — we asked twice, and they shouldn't pay
twice for it.

## Phases

Each phase is one PR. Each is ≤ 3 files where possible, has its own flag,
and has its own Change Impact Log in `docs/change-log/`. Flags default
**off** and are flipped per environment in the `settings` table (the
`app_settings` pattern, rollback without a redeploy). Dispatch is a
live-tested surface: each backend PR gets a `mock_supabase_client` dry run
and a `spinr-dispatch-reviewer` pass before merge; Phases 0 and 1 also get
`spinr-insurance-period-auditor`, because they touch offer release.

### Phase 0 — Tell "ignored" apart from "declined" (prerequisite, bug fix)

| # | Change | Verify |
|---|---|---|
| 0.1 | Migration `466_settings_offer_expiry_as_miss.sql`: `settings.offer_expired_decline_as_miss_enabled BOOL DEFAULT false`, plus the field in `routes/admin/settings.py`. Check the next free number with `ls backend/migrations \| sort -V \| tail -1` first. | `migration-check` / `spinr-migration-reviewer`; `run_migrations --dry-run` |
| 0.2 | `driver-app/store/driverStore.ts` `setCountdown`: auto-decline sends `declineRide(id, 'offer_expired')`. The button path is unchanged. | Extend `store/__tests__/driverStore.test.ts`: countdown-to-0 posts `{reason: 'offer_expired'}`; tapping Decline still posts no body. |
| 0.3 | `routes/drivers/ride_flow.py` `decline_ride`: when `reason == 'offer_expired'` and the flag is on, run the **same path as a server timeout** — `process_expired_offer(ride_id, driver_id, miss_threshold)` (legacy) or `expire_offer_v2` (v2). No `reset_miss_streak`, no `ride_declined` audit row. Return 200. The pending→expired claim already makes this idempotent against the server timer and the reaper. | New tests: expired-reason decline increments the streak, triggers auto-offline at the threshold, writes no decline audit row, and racing the batch-timeout handler double-counts nothing. Existing decline tests still pass. |

Blast radius: `reset_miss_streak` callers (`ride_flow.py:446` accept,
`:804` decline, `status.py:203/1350` go-online/offline) — only `:804` changes,
and only for this reason value. `process_expired_offer` is reused, not
modified.

Driver-visible effect: a driver who leaves the app open and misses 3 offers
in a row is now taken offline and gets the existing "You're now offline"
push. Only new driver-app builds send the reason. Older builds keep today's
behaviour until they update (needs an EAS `[build]`).

Rollback: flag off → backend treats `offer_expired` like any other decline
(today's behaviour). The app change is harmless under flag-off.

### Phase 1 — Re-offer decliners once; never re-offer ignorers

| # | Change | Verify |
|---|---|---|
| 1.1 | Migration `467_settings_dispatch_reoffer.sql`: `dispatch_reoffer_enabled BOOL DEFAULT false`, `dispatch_decline_reoffer_after_seconds INT DEFAULT 45 CHECK 15–120`, `dispatch_max_offers_per_driver_per_ride INT DEFAULT 2 CHECK 1–3`, plus fields in `routes/admin/settings.py`. | Migration review; admin settings tests |
| 1.2 | One helper, `utils/offer_skip.py`, owning the `spinr:offer_skip:*` key that is written in 5 places today (`matching.py:1914`, `:2101`, `driver_offer_service.py:52`, `ride_flow.py:848`): `mark_ignored(ride, driver)` → skip TTL = search window + 5 min margin; `mark_declined(ride, driver)` → `INCR spinr:offer_declines:{ride}:{driver}`; below the max → skip TTL = `decline_reoffer_after_seconds`, at the max → long skip. Flag off → both keep today's 300 s. The dispatch-side filter (`matching.py:661`, `:929`) is unchanged — it already skips any live key. | Unit tests for the helper: flag on/off, 1st vs 2nd decline, Redis error path (logs at error level, never raises into dispatch — today's fail-open behaviour preserved). |
| 1.3 | Switch the 5 call sites to the helper. The 2nd decline on the same ride skips `update_acceptance_rate(False)`. | `test_ride_state_machine.py` untouched (no new transition); new dispatch test with the 10-driver scenario on `mock_supabase_client`: decliners re-offered once after the cooldown, ignorers never, 3rd offer never. |

Blast radius: every writer of `spinr:offer_skip` (the 5 above), both readers
(`matching.py:661`, `:929`), and `update_acceptance_rate` — acceptance rate
feeds ranking (`rank_by_eta_with_acceptance`), so the 2nd-decline exemption
slightly helps those drivers' ranking. No DB schema change for rides, no
state-machine change, no money path.

Insurance: a re-offer runs the normal claim path, so Period 2 opens at claim
and closes on decline/expiry exactly as today
(`release_driver_and_close_period`). Needs an explicit
`spinr-insurance-period-auditor` pass, because a driver can now open and
close Period 2 twice for one ride.

Rollback: `dispatch_reoffer_enabled = false` → 300 s skip for everyone
(today's behaviour). Keys already written expire on their own within 10 min.

### Phase 2 — Configurable search window (default stays 300 s)

| # | Change | Verify |
|---|---|---|
| 2.1 | Migration `468_settings_ride_search_timeout.sql`: `ride_search_timeout_seconds INT DEFAULT 300 CHECK 90–600` + admin field. | Migration review |
| 2.2 | Read it in: `booking.py:1601` (spawn `ride_search_timeout`), `stuck_ride_sweeper.py:42/55` (cutoff, so the durable backstop matches the timer), and `_MAX_DISPATCH_ATTEMPTS` in `matching.py:117` (derived: window ÷ 10 s). Scheduled rides keep `scheduled_search_deadline` unchanged. | Extend `test_p0_ship_blockers.py::TestNoDriversAvailableTimeout` + sweeper tests for 180 s; fall back to 300 on a settings read error, logged at error level. |
| 2.3 | Fix `.claude/context/domain-dispatch.md` (single offer, 2/5/10 km expansion, and "never re-offer" are all stale). | Doc review |

Rollback: set it back to 300. Pure config.

Rider-visible: at 180 s a searching rider gets the existing cancel ~2 min
sooner. Ship **with or after** Phase 3, so the shorter wait ends on a real
screen, not a jump to home.

### Phase 3 — Rider "No drivers right now" ending

| # | Change | Verify |
|---|---|---|
| 3.1 | Backend: make sure the `ride_cancelled` WS + push for this case carry `cancellation_type: "no_drivers_found"` (the DB row already does, at `matching.py:2245`; check the payload at `:2331` and in the sweeper). | Payload test |
| 3.2 | Rider app: on `ride_cancelled` with `no_drivers_found` for the ride on screen, show a sheet — "No drivers available right now" + **Try again** + **Schedule for later** — instead of `router.replace('/(tabs)')`. **Try again** returns to vehicle/fare selection with the same addresses and fetches a **new quote**. It must never auto-rebook at the old price: surge must be visible before booking (CLAUDE.md). Builds on the 2026-09-23 rebook fix (`resetBookingDraft`, cancel latch) — do not regress it. | Jest: sheet renders only for the on-screen ride; Try again refetches the fare; a late cancel for an older ride still does nothing (existing cancel-latch tests). |
| 3.3 | Searching copy: in batch mode the per-round `driver_timeout` toast ("The driver did not respond in time…") fires every 15 s and reads as failure. Show a steady "Contacting drivers near you…" instead. en-CA + fr-CA strings. | i18n coverage test (`errorI18nCoverage.test.ts`) |

rider-app has **no** visual-regression tooling. These screens will be
checked by reasoning and Jest only, and each PR must say so. Needs
`spinr-accessibility-reviewer` (WCAG AA) and `spinr-design-consistency-reviewer`.

Stripe: the auth hold is already released on auto-cancel. Try again creates
a new ride with a new hold — no new money path, but say it explicitly in the
impact log.

### Phase 4 — Wider second pass (recommended; needs your go-ahead)

When the normal-radius pool is empty (pass 1 exhausted), search again at
`search_radius_km × dispatch_expanded_radius_multiplier` (e.g. 1.5, capped).
The offer card must show the longer pickup clearly (verify it already shows
pickup distance/time), and the rider's ETA must update to the real longer
pickup.

Open question before building it: at 0% commission, long unpaid pickups hurt
drivers. Do we pay a long-pickup fee? That is a fare/receipt change (a new
disclosed line item) and needs `spinr-money-auditor` and your sign-off.
Without it, expect far drivers to decline, which Phase 1 then re-asks once.

## Not in scope

- Surge, fares, the Stripe flow (except Phase 4's open question).
- Per-service-area overrides for the new settings (global first; add a
  `service_areas` column later if Regina and Saskatoon need different values).
- Driver-side demand alerts and the open-trips board (follow-ups above).

## Open questions for you

1. Search window for launch: **180 s**? (Uber is ~120 s.)
2. Phase 4 wider radius: build it now, and with or without a long-pickup fee?
3. Should we force a minimum driver-app version at launch, so every driver
   has the Phase 0 fix? Otherwise older builds keep resetting their miss streak.

## What this plan did not verify

- Uber and Lyft behaviour comes from web search snippets; the sandbox
  blocked direct fetches of their engineering pages. Whether either one
  re-offers a trip to a driver who declined it could not be confirmed.
- The v2 availability path (`driver_availability_v2_enabled`) runs expiry
  inside the `resolve_driver_offer` RPC. Phase 0.3's v2 branch assumes
  `expire_offer_v2` can be called from the decline route; confirm before
  building.
- The `driver_timeout` toast frequency in 3.3 is inferred from the batch
  handler (`matching.py:2161`), not observed on a device.
