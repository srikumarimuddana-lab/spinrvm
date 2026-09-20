# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-19 |
| Author | Claude (spinr-notification-ux-reviewer finding, applied same session) |
| Surface(s) | backend |
| Domain (Sentry tag) | admin |
| PR / commit link | srikumarimuddana-lab/spinrvm#5508 |
| Related issue or gap ID | Same anti-pattern class as ACTION_ITEMS.md C112/C113 |

## 1. Issue / gap identified

`POST /notifications/debug-ride-offer` (admin-only) built its FCM data payload
with `rider_name` and precise pickup/dropoff lat/lng included raw, despite its
own docstring/comment explicitly claiming "spatial fields are excluded there
too" — matching `routes/rides/matching.py`'s live offer path, which does
exclude them via its own `_FCM_EXCLUDE` set. Found by the new
`spinr-notification-ux-reviewer` agent's first real run against this file.

## 2. Root cause

`notifications.py` implements its own local `_stringify_fcm()` helper instead
of importing/reusing `matching.py`'s exclusion logic. The two payload builders
drifted: `matching.py` added `_FCM_EXCLUDE` (rider_name, rider_profile_image,
service_area_polygon, planned_route_polyline, plus lat/lng fields behind a
flag) at some point after `notifications.py`'s debug endpoint was written, and
nothing kept the two in sync. The docstring was written assuming parity that
was never actually implemented in this file.

## 3. Fix / remediation

Added a local `_DEBUG_FCM_EXCLUDE` set (`rider_name`, `pickup_lat`,
`pickup_lng`, `dropoff_lat`, `dropoff_lng`) and an `exclude` parameter on
`_stringify_fcm()`, applied at the `admin_debug_ride_offer` call site.
Corrected the misleading comment to state what the code now actually does
and point at this finding so a future reader doesn't trust stale prose again.

## 4. Risk & impact on existing functionality

- Blast radius: **isolated** — one admin-only, `Depends(get_admin_user)`-gated
  debug endpoint (`/notifications/debug-ride-offer`). Grepped for other
  callers of `_stringify_fcm`: none outside this file. Grepped for other
  reads of `offer_payload`/`fcm_data` in this function: none downstream of
  the `send_push_notification` call — the excluded keys were never read back
  for the HTTP response.
- No interaction with the ride state machine, background loops, or money/
  wallet deltas — this endpoint dispatches only a synthetic `debug-` ride ID
  that resolves to a harmless 404 on Accept/Decline, never a real ride.
- Does not touch `routes/rides/matching.py`'s live dispatch path or its
  `_FCM_EXCLUDE` set at all — deliberately kept the fix local rather than
  refactoring that file's inline variable into a shared import, to avoid
  widening this fix's blast radius into a live-tested dispatch surface for
  a debug-endpoint-only bug.
- Today's actual exposure was low: the removed fields were hardcoded synthetic
  values (`"Debug Rider"`, fixed Saskatoon test coordinates), not live user
  PII, since the endpoint has no code path that pulls a real rider's data in.
  The gap was the missing exclusion mechanism itself — the next natural
  extension of this endpoint (making it pull a real rider's name/location to
  be more "realistic") would have shipped a live PII leak with zero warning.

## 5. User-experience effect

Nobody — admin-only debug/diagnostic endpoint, not reachable by riders or
drivers, and its response shape/behavior for the admin caller is unchanged
(same success/failure fields; only the FCM data payload sent to the test
device's push tray changed, dropping fields no admin was relying on reading
back from the API response).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/notifications.py` | Added `_DEBUG_FCM_EXCLUDE`, `exclude` param on `_stringify_fcm`, applied it at the `debug-ride-offer` call site, corrected the misleading docstring comment | Stop `rider_name`/precise lat-lng from reaching the FCM data payload |
| `backend/tests/test_p3_push_notifications.py` | Added `test_fcm_payload_excludes_rider_name_and_precise_location` to `TestDebugRideOffer` | Regression coverage; confirmed it fails against the pre-fix code and passes against the fix |

## 7. Before / after

```python
# Before
def _stringify_fcm(payload: Dict[str, Any]) -> Dict[str, str]:
    return {
        k: json.dumps(v) if isinstance(v, (dict, list)) else (str(v) if v is not None else "")
        for k, v in payload.items()
    }
...
fcm_data = _stringify_fcm(offer_payload)
```

```python
# After
_DEBUG_FCM_EXCLUDE = {"rider_name", "pickup_lat", "pickup_lng", "dropoff_lat", "dropoff_lng"}

def _stringify_fcm(payload: Dict[str, Any], exclude: Optional[set] = None) -> Dict[str, str]:
    exclude = exclude or set()
    return {
        k: json.dumps(v) if isinstance(v, (dict, list)) else (str(v) if v is not None else "")
        for k, v in payload.items()
        if k not in exclude
    }
...
fcm_data = _stringify_fcm(offer_payload, exclude=_DEBUG_FCM_EXCLUDE)
```

## 8. Rollback plan

Plain `git revert` is sufficient — this is a code-only change to an
admin-debug endpoint's outbound FCM payload, no data was written or migrated,
and no live data was ever exposed by the pre-fix code path (see §4).

## 9. Verification performed

- [x] Automated tests run: `pytest backend/tests/test_p3_push_notifications.py` (48 passed) and the specific new test in isolation
- [x] Confirmed the new test fails against the pre-fix code (`git stash` the fix, re-run — failed with `rider_name` present) and passes with the fix restored
- [x] `ruff check` clean on both changed files
- [ ] Manual repro steps followed in staging — **not verified**; this is a debug-only admin endpoint with no staging-specific behavior, reasoned about via test + code read rather than a live staging call
- [x] Blast-radius grep performed: no other callers of `_stringify_fcm`; no downstream reads of the excluded keys in this function
- [x] Reviewed against relevant CLAUDE.md convention: PIPEDA (no PII in FCM/log payloads)
- [x] Not feature-flagged — not user-visible/non-trivial in the sense the flag rule targets (admin-only debug tool, no rider/driver-facing behavior change)

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain revert)
- [x] Blast radius is stated: isolated to this one debug endpoint
- [x] No silent behavior change to an already-shipped flow — the admin caller's response shape is unchanged; only the outbound FCM payload's field set changed, which is the fix itself
