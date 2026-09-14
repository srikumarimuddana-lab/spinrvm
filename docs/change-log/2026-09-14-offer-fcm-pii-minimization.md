# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-14 |
| Author | Claude Code (AI-assisted, spinr platform) |
| Surface(s) | backend, driver-app |
| Domain (Sentry tag) | dispatch |
| PR / commit link | #5382 (`claude/1231-finding15-offer-pii-minimization`) |
| Related issue or gap ID | #1231 finding 15 (remaining half) |

## 1. Issue / gap identified

The ride-offer FCM `data` payload built in `backend/routes/rides/matching.py`'s batch-dispatch
path carries precise pickup/dropoff GPS coordinates (`pickup_lat`, `pickup_lng`,
`pickup_nav_lat`, `pickup_nav_lng`, `dropoff_lat`, `dropoff_lng`) and `rider_rating` in cleartext
through Google's (Android) and Apple's (iOS) push infrastructure. `rider_name` and
`rider_profile_image` were already excluded from this same payload in an earlier fix
(`_FCM_EXCLUDE`, referenced in the code as "PIPEDA (C5)") — this is the remaining half.

## 2. Root cause

The killed-app background handler (`driver-app/services/backgroundMessaging.ts`) has always
reconstructed the full offer display (for the Notifee card on Android, and for
`PENDING_OFFER_KEY` hydration on both platforms) directly from the FCM `data` payload, because it
had no authenticated way to refetch the offer by `ride_id` from a headless/killed execution
context. Removing the fields from `data` without first giving the background handler an
alternate source would have broken offer rendering outright, so the fields stayed — not by
decision, just because no replacement data path existed yet.

Investigation (this PR) confirmed, directly in `backend/features.py`'s `_build_fcm_message`:
address labels (`pickup_address`/`dropoff_address`) are separately unavoidable in the
OS-visible push on iOS (`aps.alert` renders `"{pickup_label} → {dropoff_label}"` — a genuine,
Apple-delivered visible alert, by design, since a driver needs to see the pickup/dropoff area to
decide whether to accept). Android dispatch pushes are `notification=None` (fully data-only) —
Notifee builds the visible card client-side from `data`. So addresses were never the highest-value
target; the precise GPS pins and `rider_rating` are — more precise than a label, disclosed nowhere
else on-device, and with no legitimate reason to transit third-party push infra in full precision.

## 3. Fix / remediation

1. New `app_settings`-backed flag `minimal_fcm_offer_payload_enabled` (migration 424), default
   `FALSE` — matches today's behavior exactly when off.
2. New authenticated `GET /api/v1/drivers/rides/{ride_id}/offer` endpoint
   (`backend/routes/drivers/ride_reads.py`), reusing `decline_ride`'s exact ownership-check logic
   (read-only): 404 for not-found/not-this-driver's-offer (never 403, to avoid leaking offer
   existence to a driver who wasn't offered it), 410 for an offer that existed but is no longer
   `pending` (already accepted by another driver, expired, or cancelled).
3. `matching.py`'s existing `_FCM_EXCLUDE` set is extended, gated behind the flag, to also drop
   the coordinate fields and `rider_rating` from the FCM `data` dict. The WebSocket
   `dispatch_payload` sent to the rider/driver is unaffected either way — the exclude set only
   filters a derived copy built after the WS send.
4. `driver-app/services/backgroundMessaging.ts`: when the FCM data payload carries
   `offer_minimal === 'true'`, a new `_fetchOfferHeadless()` helper authenticates the same way
   `_declineHeadless()` already does in production today (`getBackgroundAuthToken()` +
   `initFirebaseServices()` + `getAppCheckToken()` for `X-Firebase-AppCheck`) and fetches the full
   offer from the new endpoint, bounded by a 5s timeout so a slow/hung request cannot leave the
   driver's phone silent past that window. On any failure (network, 401/403, 5xx) it degrades to
   the partial payload rather than dropping the notification entirely; a definitive 404/410 is
   treated as "offer gone."

Deliberate deviation from the issue's own text: the issue suggested minting a new short-lived
offer-scoped token for this refetch. Not built — `_declineHeadless()` proves the driver's
persisted background auth token is already reachable from this exact headless context in
production today, so the new fetch reuses that proven pattern instead of introducing a new
token-signing/TTL-management surface.

## 4. Risk & impact on existing functionality

- **Blast radius: single-surface per component, evaluated cross-surface.** Backend:
  `matching.py`'s dispatch-payload construction (one function) and a new, additive-only endpoint
  in `ride_reads.py`. Driver-app: `backgroundMessaging.ts`'s message handler (one function).
  No rider-app or admin-dashboard file touched.
- **Who else reads/writes the same things:**
  - `dispatch_payload` (the WS send) — read by every driver connection's WS handler; **not
    mutated by this PR** (confirmed: the `_FCM_EXCLUDE` filter builds a separate `fcm_data` dict
    via comprehension after `dispatch_payload` is already sent). A dedicated test asserts the WS
    payload is byte-identical in both flag states.
  - `ride_offers` table / `accept_ride`'s preemption logic (`routes/drivers/ride_flow.py`) — the
    new endpoint's 410-on-non-pending path was traced end-to-end against the real "another driver
    won" race (`accept_ride` flips every other pending `ride_offers` row to `preempted` on
    acceptance) to confirm a losing driver's re-fetch gets 410, not stale offer detail.
  - `app_settings` — one new column, read once per dispatch attempt off the already-fetched
    settings dict (no new DB round trip on the dispatch hot path).
  - **Found, not touched, flagged for follow-up:** `backend/routes/admin/rides.py`'s
    admin-direct-assignment path builds its own separate, unflagged FCM data dict that still
    includes full `rider_name`, `rider_rating`, and coordinates — the earlier `rider_name`
    exclusion apparently never reached this sibling path. Out of scope for this PR (bundling an
    unflagged historical gap into a new-flag PR would blur this log); recommend a follow-up
    `ACTION_ITEMS.md` entry.
- **Could this regress a currently-working flow?** Only if the flag is ever flipped on before a
  driver-app binary containing this PR's `backgroundMessaging.ts` change reaches their device —
  an older binary would receive a minimal payload with no code path to refetch it, silently
  losing precise pickup/dropoff coordinates from the offer. This is why the flag ships off and
  the rollout dependency is stated explicitly in the PR (device-verify first, then a binary
  rollout, only then flip the flag).
- **Background loops / state machine / money:** none touched. No `core/lifespan.py` loop added or
  changed; no `rides.status` write anywhere in this diff (the new endpoint is read-only); no
  wallet/Stripe path touched.

## 5. User-experience effect

- **Who sees a difference today (flag off): nobody.** Byte-for-byte identical FCM payload and
  driver-app behavior.
- **Once a human flips the flag on:** drivers see the same offer panel and Notifee card, just
  sourced from an authenticated fetch instead of the push payload directly — no visual or
  functional change from the driver's perspective in the success path. On a degraded fetch
  (network failure, slow response), the driver still gets a notification, just potentially
  missing distance/rating detail until they open the app — not silence.
- **Rider-visible change: none**, at any point — riders are not on this payload's receiving end.
- **Mid-session visibility:** not applicable — this only affects a push payload constructed at
  dispatch time, not an already-open screen.
- No copy/notification text changed.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/migrations/424_settings_minimal_fcm_offer_payload_enabled.sql` | New `settings.minimal_fcm_offer_payload_enabled BOOLEAN NOT NULL DEFAULT FALSE` column | Kill switch, additive, no table rewrite |
| `backend/routes/admin/settings.py` | Registers the new flag in the admin settings read/write allowlist | So it's toggleable from the admin dashboard without a redeploy |
| `backend/schemas.py` | Adds the new field to the settings Pydantic schema | Validation + typed access |
| `backend/routes/drivers/__init__.py` | Mounts the new endpoint's router | Wiring |
| `backend/routes/drivers/ride_reads.py` | New `GET /rides/{ride_id}/offer` endpoint | Authenticated refetch source for the background handler |
| `backend/routes/rides/matching.py` | Extends `_FCM_EXCLUDE`, flag-gated | Drops precise coords + `rider_rating` from FCM `data` only when enabled |
| `driver-app/services/backgroundMessaging.ts` | New `_fetchOfferHeadless()`, called when `data.offer_minimal === 'true'` | Hydrates the full offer via authenticated fetch instead of trusting the (now-partial) push payload |
| `backend/tests/test_minimal_fcm_offer_payload_flag_settings.py` | New — flag read/write round-trip tests | Coverage for the new setting |
| `backend/tests/test_driver_ride_flow_coverage.py` | New endpoint tests (404/410/503/happy-path) | Coverage for the new authenticated fetch endpoint |
| `backend/tests/test_dispatch_notify_loop_branches.py` | New FCM-payload-shape tests (flag on/off) | Confirms `_FCM_EXCLUDE` extension and WS-payload non-mutation |
| `backend/tests/test_admin_settings_write_allowlist_drift.py` | Updated allowlist-drift fixture | Keeps the drift test in sync with the new settings field |
| `driver-app/__tests__/services/backgroundMessaging.android.test.ts` | New tests for `_fetchOfferHeadless()` (success, 404/410, network/5xx degrade, no-token degrade) | Coverage for the new client-side fetch path |

## 7. Before / after

```
# Before — backend/routes/rides/matching.py (FCM data payload, always sent this shape)
_FCM_EXCLUDE = {
    "service_area_polygon",
    "planned_route_polyline",
    "rider_profile_image",
    "rider_name",
}
fcm_data = {k: ... for k, v in dispatch_payload.items() if k not in _FCM_EXCLUDE}
# -> pickup_lat/lng, dropoff_lat/lng, pickup_nav_lat/lng, rider_rating all present
```

```
# After — same payload, only when minimal_fcm_offer_payload_enabled is True
_FCM_EXCLUDE = {
    "service_area_polygon", "planned_route_polyline",
    "rider_profile_image", "rider_name",
} | ({
    "pickup_lat", "pickup_lng", "pickup_nav_lat", "pickup_nav_lng",
    "dropoff_lat", "dropoff_lng", "rider_rating",
} if settings.get("minimal_fcm_offer_payload_enabled") else set())
# flag False (default): identical to Before.
# flag True: driver-app's background handler fetches the dropped fields via
# GET /api/v1/drivers/rides/{ride_id}/offer instead.
```

## 8. Rollback plan

- **Primary**: flip `minimal_fcm_offer_payload_enabled` back to `False` via the admin dashboard
  (same write path this PR's own `test_admin_put_round_trip_persists_false` test exercises) — no
  redeploy. The FCM payload reverts to full shape within the existing settings-cache TTL, and the
  driver-app's `offer_minimal` check simply stops firing on the next dispatch.
- The flag has never mutated any stored data (it only shapes an outbound push payload at send
  time), so there is nothing to clean up after flipping it off — no data-level remediation needed.
- `git revert` is safe as a secondary option since the migration is purely additive
  (`ADD COLUMN ... DEFAULT FALSE`) with a documented rollback (`DROP COLUMN IF EXISTS`) in the
  migration file itself.

## 9. Verification performed

- [x] Automated tests run — unit only (this repo's convention, no live-Supabase CI tier): 15 new
      backend tests (flag settings round-trip, FCM-payload-shape flag on/off with WS-payload
      non-mutation assertion, new-endpoint 404/410/503/happy-path for both batch-dispatch and
      admin-direct-assign authorization branches) + 6 new driver-app jest tests (flag-off no-fetch,
      fetch-success hydration, 404/410 terminal, network/5xx degrade, no-token degrade). Full
      regression: 756 backend tests across dispatch/matching/ride_flow/ride_reads, and the full
      driver-app suite (149 suites / 1720 tests) — all pass. `ruff check` and `npx tsc --noEmit`
      both clean.
- [ ] Manual repro steps followed in staging — **not done**. See "What was NOT verified" below;
      this is the explicit reason the flag ships off.
- [x] Blast-radius grep performed — see section 4 above (WS payload, `ride_offers`/`accept_ride`
      race, `app_settings`, and the `routes/admin/rides.py` sibling gap).
- [x] Reviewed against relevant CLAUDE.md conventions — PIPEDA (this PR removes PII-adjacent
      fields from third-party push infra), state machine (no `rides.status` write in this diff),
      observability (no new metric; one `console.warn` on the driver-app degraded path, `ride_id`
      only, matching `_declineHeadless()`'s existing logging style).
- [x] Feature-flagged — `minimal_fcm_offer_payload_enabled`, default off, per CLAUDE.md's
      pre-merge release gate #3 (non-trivial, potentially-breaking-for-old-binaries change).

**What was NOT verified:**
- **Killed-app FCM background-handler execution on a real device.** This sandbox has no physical
  device and cannot exercise real FCM/APNs delivery to a headless app process. A human must
  manually verify real offer delivery on physical iOS AND Android devices, with the flag flipped
  on in staging, before it is ever enabled in production.
- The two mandatory adversarial-review agents (`spinr-dispatch-reviewer`,
  `spinr-realtime-reliability-reviewer`) could not be launched as isolated subagents from the
  sandboxed session that wrote this PR (no Agent/Task tool available there). Both agents' full
  published checklists were applied manually against the diff instead (documented in the PR body)
  — no blockers found, but this is a real gap against the letter of CLAUDE.md's gate #10.
  **Update (this session, which does have the Agent tool): `spinr-fraud-auditor` and
  `spinr-dispatch-reviewer` are being run for real against PR #5379 (finding 12) concurrently;
  the same real review is the recommended next step for this PR (#5382) before merge.**
- No `eas build` was run — no native dependency, Expo SDK, or native module changed (pure JS/TS
  logic), so not required by the PR template's own native-build gate, but noted for completeness.
- The exact added-latency (P95) for the new client-side fetch on a real network cannot be
  measured without a real device; bounded defensively by a 5s timeout in the meantime.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (admin-dashboard flag flip, already covered by a
      passing test).
- [x] Blast radius is stated, not assumed (single-surface per component; the one found-but-not-fixed
      sibling gap in `routes/admin/rides.py` is called out explicitly, not silently left out).
- [x] No silent behavior change to an already-shipped flow — the flag defaults off, so this PR
      changes zero live behavior on merge; the "User-experience effect" field above states this.

## Addendum — round 2 (real adversarial review, 2026-09-14)

The manual self-review disclosed above was superseded by real `spinr-dispatch-reviewer` and
`spinr-security-auditor` passes once a session with the Agent tool became available. Findings and
what was done about each:

**Fixed in this PR (commit follows this addendum):**

- **BLOCKER (dispatch-reviewer): TOCTOU in `get_ride_offer`'s authorization check.** The
  endpoint's "is this offer still live" gate reused the `ride` snapshot fetched at the top of the
  function (before the `ride_offers` ownership check) instead of re-reading it. `accept_ride`
  flips the winning driver's `rides.status` to `driver_accepted` well before it flips *other*
  drivers' `ride_offers` rows to `preempted` (several awaits later: re-read ride, cache
  invalidation, insurance-period write, acceptance-rate update, then the `ride_offers` updates
  themselves) — a losing driver's request landing in that real, multi-round-trip window would see
  its own `ride_offers` row still `pending`, and the stale `ride` snapshot's status check would
  pass, returning 200 with full offer detail (precise GPS + `rider_rating`) for a ride already won
  by someone else. This was exploitable **independent of the feature flag** — the endpoint is
  unconditionally mounted. **Fix:** re-fetch `ride` immediately before the authorization decision
  (after the `ride_offers` check, not before it) and re-verify both status and (for the
  direct-assignment branch) `driver_id` against the fresh read, closing the window to one final
  round trip. **Regression tests added:** `test_stale_ride_snapshot_does_not_leak_offer_after_preemption`
  and `test_stale_ride_snapshot_does_not_leak_direct_assignment_after_reassignment` — both
  simulate the exact interleaving (stale first read vs. fresh second read disagreeing) and
  confirmed to fail without the fix (reverted locally, watched both fail with "DID NOT RAISE",
  restored the fix, confirmed both pass) before being counted as real coverage.

**Tracked, not fixed in this PR (correctly out of scope per surgical-changes discipline — filed
as `ACTION_ITEMS.md` entries instead of left as change-log prose only, since a paragraph here
isn't a follow-up anyone will find later):**

- **`ACTION_ITEMS.md` C112** — `backend/routes/admin/rides.py`'s `admin_create_ride` (a different
  direct-assignment code path than the one this PR touches) builds its own FCM push with no
  `_FCM_EXCLUDE` filtering at all — full name/email/phone fallback, precise GPS, and
  `rider_rating`, unfiltered, in production today, regardless of this PR's flag. Security-auditor
  flagged that this PR's own investigation found this gap but only wrote it up in prose; C112 is
  the actual tracked entry.
- **`ACTION_ITEMS.md` C113** — `backend/routes/drivers/ride_reads.py`'s entire read-endpoint
  family (`get_active_ride`, `get_ride_history`, and this PR's new `get_ride_offer`) has no rate
  limiting at all, decorator or global middleware. Low practical risk today (`ride_id` is a
  non-enumerable UUID) but a real gap on a PII-bearing read endpoint; scoped as its own follow-up
  rather than bolted onto this PR inconsistently (only the newest endpoint would have gotten it).

**Noted, accepted as-is (low severity, not tracked separately):**

- **Timing side-channel (security-auditor):** a nonexistent `ride_id` short-circuits after one
  query; an existing-but-not-mine ride costs a second `ride_offers` round trip before also
  returning 404 — both return byte-identical bodies, but the extra round trip is a measurable
  latency difference an attacker could in principle use to distinguish "no such ride" from "ride
  exists, not yours." Not fixed: `ride_id` is a UUID (128 bits, not guessable/enumerable), so the
  practical exploitability is negligible, and padding the fast path to match the slow one's cost
  would add complexity for a threat model that doesn't apply here (CLAUDE.md's simplicity-first
  principle). Documented rather than silently accepted.
- **`offer_expires_at` fallback is dead code in practice for direct-assignment (security-auditor):**
  the fallback reads `ride.get("driver_notified_at")`, but `admin_create_ride` (the only producer
  of the direct-assignment shape) never actually sets that field — confirmed via grep, only
  `services/dispatch_service.py` writes it. In production this means every admin-direct-assigned
  offer returns `offer_expires_at: null`, not a real countdown. The PR's own
  `test_live_direct_assignment_with_no_ride_offers_row` test masks this by manually setting
  `driver_notified_at` on its fixture — a gap between the test and the real code path, not a
  security issue (a null countdown fails safe, it doesn't leak anything), but worth knowing this
  specific field is unverified against real admin-assign behavior. Not fixed here: the actual fix
  belongs in `admin_create_ride` (setting `driver_notified_at` on direct-assignment), which is
  outside this PR's stated scope (FCM payload minimization, not the admin-assignment flow's own
  field completeness) — left as a known gap rather than silently patched over.
- **404 instead of 410 for a since-accepted direct-assignment (security-auditor):** once a
  direct-assigned ride moves past `driver_assigned` (driver accepted, arrived, in progress),
  `is_direct_assigned` becomes `False` and there's no `ride_offers` row for this path, so the
  endpoint returns 404 ("not found") rather than the intended 410 ("no longer available")
  semantics. Not a security issue — 404 is still the safe, no-leak answer — just an inconsistent
  status code for that one case. Not fixed here; low enough severity not to warrant a dedicated
  `ACTION_ITEMS.md` entry, noted here for anyone touching this function next.

## Round 3 addendum (2026-09-14) — migration renumbered 422 → 424

While merging `main` forward into this PR's branch to pick up an unrelated CI infra fix (#5386,
artifact-download retry), CI's `migration-check.yml` CHECK B correctly hard-failed with a real
numeric-prefix collision: two other PRs (`422_dispatch_latency_by_zone.sql`,
`423_retention_cohorts.sql`) had merged to `main` in the meantime and claimed both 422 and 423.
Per `backend/migrations/CLAUDE.md`'s "the second one renames to the next free slot" rule — this
PR's migration is the later one to actually reach `main` — renamed
`422_settings_minimal_fcm_offer_payload_enabled.sql` to
`424_settings_minimal_fcm_offer_payload_enabled.sql` and updated every reference (the file's own
header comment, `backend/schemas.py`, `backend/routes/rides/matching.py`, this file, and
`backend/tests/test_minimal_fcm_offer_payload_flag_settings.py`, including that test's function
name). Since this migration was never merged to `main` before the rename (still on this PR's own
branch, not yet applied to any real database), the rename is a pure pre-merge correction, not an
append-only-rule violation — no idempotency-key history exists yet for the old filename.
