# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-21 |
| Author | Claude (closing ACTION_ITEMS.md C113) |
| Surface(s) | backend |
| Domain (Sentry tag) | admin |
| PR / commit link | srikumarimuddana-lab/spinrvm (this PR) |
| Related issue or gap ID | ACTION_ITEMS.md C113 |

## 1. Issue / gap identified

`backend/routes/notifications.py`'s `admin_debug_ride_offer` (admin-only diagnostic
endpoint) builds its FCM `offer_payload` next to a comment claiming parity with the
live ride-offer dispatch path(s) — but the comment was inaccurate about what those
live paths actually exclude today.

## 2. Root cause

Two things had drifted apart from the comment's claim:

1. **The comment predates this task was already partially stale.** ACTION_ITEMS.md's
   own C113 write-up (filed during the 2026-09-14 C112 audit) described this endpoint
   as having *no* `rider_name` exclusion at all. That was true when C113 was filed, but
   a separate, unrelated fix (PR #5508, 2026-09-19, `spinr-notification-ux-reviewer`
   finding, see `docs/change-log/2026-09-19-debug-ride-offer-fcm-pii-exclusion.md`)
   already added a `_DEBUG_FCM_EXCLUDE` set (`rider_name`, `pickup_lat`, `pickup_lng`,
   `dropoff_lat`, `dropoff_lng`) and rewrote the comment before this task started.
   ACTION_ITEMS.md's C113 status line was never updated to reflect that, so it kept
   describing a state that no longer existed.
2. **The 2026-09-19 comment was itself still inaccurate**, just in a subtler way: it
   claimed `routes/rides/matching.py`'s live offer path "enforces... no rider name or
   precise lat/lng" via its own `_FCM_EXCLUDE`, as if both were unconditionally
   excluded. In fact, reading `matching.py`'s current code: `_FCM_EXCLUDE`
   unconditionally excludes only `rider_name` (plus `service_area_polygon`,
   `planned_route_polyline`, `rider_profile_image` — fields this debug payload never
   carries anyway); precise pickup/dropoff coordinates and `rider_rating` are excluded
   there **only** when the `minimal_fcm_offer_payload_enabled` app_settings flag is on
   (migration 424, default `FALSE` — so live traffic today still sends raw lat/lng).
   Separately, `routes/admin/rides.py`'s `admin_create_ride` (direct-assignment) path
   has its own `_ADMIN_FCM_EXCLUDE = {"rider_name"}` with **no** coordinate filtering
   at all, flagged or otherwise — a third, different exclusion set the old comment
   didn't distinguish from `matching.py`'s.

## 3. Fix / remediation

Comment-only change (no executable code touched — `_DEBUG_FCM_EXCLUDE`'s contents,
`_stringify_fcm`, and `offer_payload` are byte-identical before/after this PR):

- Rewrote the comment above `_DEBUG_FCM_EXCLUDE` to state, accurately, what each of
  the two real dispatch paths excludes today (unconditional vs. flag-gated), that they
  differ from each other, and that this debug endpoint is deliberately stricter than
  both (always drops `rider_name` + coordinates, flag or no flag).
- Rewrote the second comment at the `offer_payload` construction site to stop
  re-asserting the same "mirrors matching.py's `_FCM_EXCLUDE`" framing for both fields
  and instead point back at the corrected comment above.
- Confirmed the `rider_name` exclusion this item asked for is **already present**
  (added by PR #5508, 2026-09-19) — no functional gap remained to fix. No new
  exclusion code was added; adding a second, redundant exclusion mechanism would
  violate CLAUDE.md's simplicity-first principle for no behavioral gain.

**Alternative approach considered (CLAUDE.md gate 10):** extract a shared, importable
exclusion set/helper that `matching.py`, `admin/rides.py`, and `notifications.py` could
all import, instead of three independent local sets. Rejected: both `matching.py`'s
`_FCM_EXCLUDE` and `admin/rides.py`'s `_ADMIN_FCM_EXCLUDE` are **local variables**
scoped inside their own request-handler function bodies (the batch-dispatch loop and
`admin_create_ride` respectively), not module-level constants — there is nothing
importable to reuse today without refactoring a live, high-traffic dispatch path and a
ride-assignment admin endpoint purely to serve a debug-only diagnostic tool. That fails
the blast-radius-first gate for a fix this narrow, and would be the opposite of the
"touch only what the task requires" rule. Keeping `notifications.py`'s own local
`_DEBUG_FCM_EXCLUDE` (already in place since PR #5508) wins on cost, effort, and
consistency with the precedent the C112 fix itself set (`_ADMIN_FCM_EXCLUDE` is also
intentionally local rather than shared).

## 4. Risk & impact on existing functionality

- **Blast radius: isolated, comment-only.** Zero executable-code lines changed. Grepped
  for every caller of `admin_debug_ride_offer` (route `POST /notifications/debug-ride-offer`):
  only test file `backend/tests/test_p3_push_notifications.py` and this repo's own docs
  reference it — no other backend/admin-dashboard/rider-app/driver-app code calls it.
- **Grepped for every other `new_ride_assignment`-shaped FCM payload builder** (the
  blast radius C112's audit had already mapped): confirmed exactly three sites build
  such a payload — `routes/rides/matching.py` (batch auto-dispatch), `routes/admin/rides.py`
  (`admin_create_ride`, direct assignment), and `routes/notifications.py`
  (`admin_debug_ride_offer`, this file). `backend/features.py` and
  `backend/utils/push_retry.py` only *read* `data["type"] == "new_ride_assignment"` on
  an already-built payload (routing/logging decisions) — they don't construct one, so
  they're consumers, not additional sites needing the same fix. `notifications.py` is
  the "third site" C112's write-up refers to; no fourth site exists.
- Not listed in `docs/known-forks.md` — grepped for `notifications.py`/
  `admin_debug_ride_offer`/`_FCM_EXCLUDE`, no match, so no sibling-file check applies.
- No interaction with the ride state machine, background loops, dispatch, or money/
  wallet deltas. The endpoint dispatches only a synthetic `debug-` ride ID.

## 5. User-experience effect

None. Admin-only debug/diagnostic endpoint (`Depends(get_admin_user)`), not reachable
by riders or drivers. The response shape and the outbound FCM payload's field set are
both unchanged by this PR — only source comments changed.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/notifications.py` | Corrected two comments near `_DEBUG_FCM_EXCLUDE` and the `offer_payload` construction to state the true, current exclusion behaviour of `matching.py`'s and `admin/rides.py`'s live paths (unconditional vs. `minimal_fcm_offer_payload_enabled`-flag-gated), instead of a false blanket parity claim | Close ACTION_ITEMS.md C113: stop a misleading comment from making it easy to silently reintroduce a real PII leak if this endpoint is ever made to accept a caller-supplied rider name |
| `ACTION_ITEMS.md` | Marked C113 CLOSED with today's date and a summary | Backlog hygiene |

## 7. Before / after

```python
# Before
# Same invariant routes/rides/matching.py's live offer path enforces via its
# own _FCM_EXCLUDE: no rider name or precise lat/lng may ride in an FCM data
# payload (cleartext in the device tray, transits Google/Apple push infra).
# Kept as a local, debug-endpoint-scoped set rather than importing
# matching.py's — that set is a local variable inside a live dispatch code
# path, not a shared constant, and this fix should not touch that file.
_DEBUG_FCM_EXCLUDE = {
    "rider_name",
    "pickup_lat",
    "pickup_lng",
    "dropoff_lat",
    "dropoff_lng",
}
```

```python
# After
# Defensive PII exclusion for a diagnostic/debug endpoint. Corrected 2026-09-21
# (ACTION_ITEMS.md C113) — the previous version of this comment claimed
# parity with routes/rides/matching.py's live offer path that doesn't
# actually exist. As of today, the two real dispatch paths differ from each
# other and from this endpoint:
#   - matching.py's batch-dispatch path unconditionally excludes rider_name
#     (plus service_area_polygon/planned_route_polyline/rider_profile_image,
#     none of which this payload carries) via its own _FCM_EXCLUDE, but only
#     drops precise pickup/dropoff coordinates and rider_rating when the
#     minimal_fcm_offer_payload_enabled app_settings flag is on (migration
#     424; default False today, so live traffic currently still sends raw
#     lat/lng in the FCM data payload).
#   - admin/rides.py's admin_create_ride (direct-assignment) path excludes
#     only rider_name via its own _ADMIN_FCM_EXCLUDE, with no coordinate
#     filtering at all, flagged or otherwise.
# This debug endpoint is deliberately stricter than both: it always drops
# rider_name AND coordinates, since a diagnostic tool has no legitimate
# reason to carry either in cleartext through Google/Apple push infra.
# Kept as its own local, debug-endpoint-scoped set rather than importing
# either sibling's set: both _FCM_EXCLUDE and _ADMIN_FCM_EXCLUDE are local
# variables inside their own request-handler bodies, not shared module-level
# constants, so there is nothing importable to reuse without refactoring a
# live dispatch/admin path for a debug-only fix — same reasoning
# admin/rides.py's own _ADMIN_FCM_EXCLUDE (C112) already applied.
_DEBUG_FCM_EXCLUDE = {
    "rider_name",
    "pickup_lat",
    "pickup_lng",
    "dropoff_lat",
    "dropoff_lng",
}
```

(The `_DEBUG_FCM_EXCLUDE` set contents themselves are unchanged — this is a comment
correction, not a behavior change.)

## 8. Rollback plan

Plain `git revert` is sufficient. This changes source comments only — no executable
code, no migration, no feature flag, no live data (Stripe charges, wallet deltas, ride
state) was touched or is at risk.

## 9. Verification performed

- [x] Automated tests run: `pytest backend/tests/test_p3_push_notifications.py` (48
      passed, including the pre-existing `TestDebugRideOffer::
      test_fcm_payload_excludes_rider_name_and_precise_location` regression test added
      by PR #5508 — still green, confirming the `rider_name`/coordinate exclusion this
      item asked for is genuinely already in place); also ran
      `test_loguru_call_conventions.py` (8 passed) since `notifications.py` uses stdlib
      `logging`, not loguru — no impact expected or found.
- [x] `ruff check backend/routes/notifications.py` and
      `ruff format --check backend/routes/notifications.py`: both clean.
- [x] Blast-radius grep performed (see §4): every caller of `admin_debug_ride_offer`;
      every other `new_ride_assignment` FCM-payload builder; `docs/known-forks.md` for a
      sibling-file obligation (none).
- [x] `/code-review` (medium effort) run against the actual diff before committing —
      zero findings (pure comment diff; every factual claim in the new comment was
      cross-checked against `matching.py`/`admin/rides.py`'s current code).
- [x] Reviewed against relevant CLAUDE.md conventions: PIPEDA (no PII in FCM payloads),
      dual-import pattern (untouched), simplicity-first (no redundant exclusion
      mechanism added since one already existed).
- [x] Not feature-flagged — not applicable; no behavior changed, admin-only debug tool.

## 10. What was NOT verified

- No real device push was sent as part of this change (this PR touches only comments;
  the previous PR #5508 that added the actual `_DEBUG_FCM_EXCLUDE` behavior was
  verified by its own test suite, not a live device send either — see that PR's change
  log for its own verification boundary).
- Did not re-verify `matching.py`'s `minimal_fcm_offer_payload_enabled` flag's real
  device behavior (out of scope — this PR doesn't touch that flag or its code path,
  only documents its current default/effect accurately in a comment).
- No visual/UI surface touched — not applicable (backend-only comment change).

## 11. Sign-off

- [x] Rollback plan is concrete and testable (plain revert)
- [x] Blast radius is stated: isolated to one debug endpoint's comments; confirmed via
      grep that no fourth `new_ride_assignment` payload-building site exists
- [x] No silent behavior change to an already-shipped flow — zero executable code
      changed; the admin caller's response and the outbound FCM payload are unchanged
