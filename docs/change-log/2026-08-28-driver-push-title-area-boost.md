# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-28 |
| Author | Claude Code (mkkreddy52@gmail.com) |
| Surface(s) | backend |
| Domain (Sentry tag) | dispatch |
| PR / commit link | branch `claude/driver-notification-area-boost-usaor4` |
| Related issue or gap ID | Driver report: push notification fare ≠ in-app offer fare |

## 1. Issue / gap identified

Both halves of the ride-offer push a driver receives — the title
(`New ride · $X.XX`) and the BigPicture fare banner rendered behind it —
showed only the base ride earnings, excluding any active area boost / ride
incentive. The in-app offer panel — and the driver-app's own locally-rendered
Notifee notification — show fare **+** bonus, so a boosted ride advertised a
lower number in the push tray than the app displayed on tap.

## 2. Root cause

Two separate misses, same shape — a money surface built from
`driver_earnings` alone while the boost lived in a field the caller ignored:

1. **Title.** `backend/routes/rides/matching.py`'s dispatch notify loop builds
   the title from `ride["driver_earnings"]`, even though the incentive total is
   computed in the same function (`_fetch_incentives()` → `_total_bonus`) and
   shipped in the WS/FCM payload as `total_bonus`. The client combines the two
   (`notifeeService.ts`: `offer.fare + (offer.total_bonus || 0)`); the
   server-built title never did.
2. **Banner.** `backend/utils/offer_card.render_offer_card` has always accepted
   a `total_bonus` argument and drawn a boost pill for it, but
   `backend/routes/offer_card.py` never passed one — so the pill was dead code
   in production and the banner drew the base fare as its headline. The
   renderer's own unit test passed `total_bonus=2.50`, which is why the gap
   looked covered.

## 3. Fix / remediation

1. Push title now uses `driver_earnings + _total_bonus`, matching the client's
   `totalEarnings` formula exactly.
2. The banner endpoint now looks up the ride's active incentives and passes the
   total to the renderer, and the renderer's headline is now `fare + boost`
   with the boost itemised in the pill — the same shape as the in-app offer
   panel (big total, breakdown underneath).
3. Pill copy changed from `+$5.00 BONUS` to `INCL. $5.00 BOOST`, because the
   headline beside it is now the total: a `+$X` pill next to a total reads as
   money on top of that number.
4. Pill now fits itself to the space left by the headline (30 → 26 → 22 px,
   dropped if none fits). Found by rendering: headline and pill share one row
   and neither is fixed-width, so a 3-digit total (`$105.00`) overprinted the
   pill. Pre-existing latent bug, made more reachable by the summed headline.

Net effect: title, banner, and offer panel now all show one identical number.
Display-only; nothing about what the driver is actually paid changed.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated.** The only consumer of `earnings_label` is the FCM
  notification title built ~10 lines below it in the same function. It is not
  written to the DB, not part of `dispatch_payload`, and not read by any other
  route, loop, or client field. `_total_bonus` was already computed
  unconditionally on this path (same `asyncio.gather`) — no new query, no added
  dispatch latency, so the < 2 s dispatch-offer SLA is unaffected.
- **No money-path change.** `driver_earnings`, `ride_incentives`, wallet
  deltas, and payout logic are untouched; this only formats a string.
- **No ride-state / insurance-period / WS-event change.**
- **Banner blast radius: one caller.** `render_offer_card` is called from
  exactly one place (`routes/offer_card.py`); `earnings_labels` is new. Nothing
  else renders or reads the banner.
- **One added DB read**, on the banner-render path only — a signed-URL image
  GET the device makes after the push lands, explicitly off the dispatch hot
  path (see that module's docstring), so the < 2 s offer→phone SLA is
  untouched. The read is the same `ride_incentives` query dispatch already
  runs; it fails open to "no pill" so a boost-lookup error can never cost the
  driver the banner (or a 503 on an image the OS is fetching).
- The incentive query is **duplicated** from `matching.py`'s `_fetch_incentives`
  rather than shared: that one runs against `_deps`-injected Supabase handles
  the dispatch tests patch, and rerouting it through a shared helper would have
  changed what those tests intercept. Noted as a small, deliberate duplication.
- Failure mode is unchanged: a non-numeric earnings value still falls back to
  the `"New fare"` label (the `+ float(_total_bonus or 0)` is inside the same
  `try`).

## 5. User-experience effect

- **Driver-facing, visible immediately** to any driver receiving an offer push
  on a ride in a boosted area. Both the tray title and the banner now show what
  they'll actually earn, matching the in-app panel instead of under-stating it.
  For rides with no active incentive both are byte-identical to before.
- **Copy change (driver-visible):** the banner's boost pill now reads
  `INCL. $5.00 BOOST` instead of `+$5.00 BONUS` — required by the headline
  change; a `+$X` pill beside a total would over-state earnings, which is worse
  than the bug being fixed. Not feature-flagged: the flag mechanism here would
  itself be new, and the un-flagged state (today's build) is the wrong number.
- Riders, corporate admins, and internal admins see nothing.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/rides/matching.py` | Push title earnings label now adds `_total_bonus` to `driver_earnings` | Match the in-app offer panel and the driver-app's local notification |
| `backend/tests/test_dispatch_notify_loop_branches.py` | Two regression tests: title with an active incentive, and title with none | Lock the two branches so the title can't silently drift from the payload again |
| `backend/routes/offer_card.py` | Looks up active `ride_incentives` for the ride and passes `total_bonus` to the renderer; fails open | The banner never received the boost, so its pill was dead code |
| `backend/utils/offer_card.py` | New `earnings_labels()`; headline is now fare + boost; pill copy `INCL. $X BOOST`; pill font steps down / drops to avoid overprinting a wide headline | One number for actual earnings, matching the offer panel and title; keep the row legible at 3-digit totals |
| `backend/tests/test_offer_card_route.py` | Tests: boost reaches the renderer, wrong-vehicle incentive skipped, lookup failure still renders | Cover the new query and its fail-open path |
| `backend/tests/test_offer_card.py` | Unit tests for `earnings_labels` (sum, no-pill, missing fare) | Money copy testable without Pillow |

## 7. Before / after

```python
# Before
earnings_label = f"${float(ride.get('driver_earnings') or 0):.2f}"
# ride with driver_earnings=12.50 + "Area Boost" $5.00 → "New ride · $12.50"
```

```python
# After
_offer_total = float(ride.get("driver_earnings") or 0) + float(_total_bonus or 0)
earnings_label = f"${_offer_total:.2f}"
# same ride → "New ride · $17.50"  (matches the offer panel)
```

```python
# Before — banner: caller never passed the boost, so the pill never drew
render_offer_card(fare=_num(ride.get("driver_earnings")) or 0.0, ...)
# headline "$12.50", no pill
```

```python
# After — banner
render_offer_card(fare=..., total_bonus=_total_bonus or None, ...)
# earnings_labels(12.50, 5.00) -> ("$17.50", "INCL. $5.00 BOOST")
```

## 8. Rollback plan

`git revert` of the commits is a complete rollback here. No migration, no
persisted state: both values are computed per offer at send/render time, so the
next dispatch after a redeploy uses whichever formula is live. Nothing already
delivered needs correcting — no money, wallet, or ride row was written
differently. Partial rollback is possible too (reverting the banner change
alone leaves the corrected title in place), though it reintroduces the
title/banner mismatch and is not recommended.

## 9. Verification performed

- Static: `python3 -m py_compile` on both changed files — passes.
- Reviewed every consumer of `earnings_label` (single call site) and of
  `_total_bonus` (WS payload, FCM data payload) to confirm no other reader
  changes behavior.
- Cross-checked the new formula against the client's:
  `driver-app/services/notifeeService.ts:222` (`offer.fare + (offer.total_bonus || 0)`),
  `driver-app/components/panels/RideOfferPanel.tsx:150` (`totalEarnings = baseFare + totalBonus`),
  and `driver-app/lib/androidAuto/carCard.ts` (`totalEarningsLabel`).
- Executed `earnings_labels` standalone (extracted, no backend imports):
  `(12.50, 5.00) → ("$17.50", "INCL. $5.00 BOOST")`; `(12.50, 0/None) →
  ("$12.50", None)`; `(None, None) → ("$0.00", None)`.
- Confirmed `render_offer_card` has exactly one production caller.

## 10. What was NOT verified

- **The new tests were not executed.** This environment's network policy blocks
  PyPI (`pip install` → 403 via the agent proxy), so pytest and the backend
  dependencies could not be installed. The two added tests are written against
  the existing `test_dispatch_notify_loop_branches.py` harness and are unrun —
  CI is the first execution.
- Not tested against a real device/FCM delivery, nor against live Supabase; the
  incentive rows in the tests are mocked.
- No end-to-end check that a real boosted ride's tray title now matches the
  in-app panel on a physical driver device.
- **The banner was previewed via a replica, not by Pillow.** Pillow can't be
  installed in this environment (PyPI blocked), so the layout was reproduced
  in HTML at the same 1024×512 geometry, loading the same bundled DejaVu TTFs
  at the same px sizes, and screenshotted with the pre-installed Chromium.
  That preview is what surfaced the headline/pill collision. Glyph advances
  match (same fonts, same em sizes) but Chromium's text baseline and rounding
  are not byte-identical to Pillow's, so this is a close approximation, not
  the production image — the fit thresholds were separately confirmed by
  reading advance widths straight out of the TTF `hmtx` table, which is what
  Pillow's `textlength()` sums.
- The `$99.75` + `$25.00` case clears the pill by only ~2 px at full size.
  It passes, but it is the tightest passing case — worth knowing if the pill
  copy is ever lengthened again.
- No test asserts pixels; the added test asserts the fit *rule* (which size is
  chosen), not the rendered output. There is still no visual-regression
  tooling for this surface, per CLAUDE.md.
- The duplicated incentive query in `routes/offer_card.py` was not measured
  against a real Supabase; its latency contribution to banner render is
  assumed-small, not benchmarked.
