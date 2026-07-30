# Change Impact & Risk — rotating pseudonyms for pre-match driver markers (T3b)

**Date:** 2026-07-30 · **Branch:** `claude/critical-security-pipeda-breach-pn67ww`
**Surface:** backend (rider-visible payload) · **Risk:** medium — changes a field two rider-app screens render
**Related:** T3 (`b23a9092`), PIPEDA data minimisation

---

## Issue / gap identified

T3 coarsened pre-match driver positions to 500 m and capped the search radius, closing
the launch-gate no-go. It deliberately left one exposure open and said so in the code:

> ```
> # The driver row id is retained: the rider app uses it only as a map
> # marker key, and rotating it would remount markers mid-session. It does
> # still allow following one (coarsened) vehicle over time, which is
> # hardening beyond this gate — tracked separately, not silently assumed
> # to be covered here.
> ```

That stable `drivers.id` is enough to defeat the coarsening. Any authenticated rider
can poll `GET /drivers/nearby` on a timer and stitch one driver's coarse positions
into a movement trace. A single 500 m sample says little; a week of samples keyed to a
known id shows where that contractor starts and ends their day — the exact inference
PIPEDA data minimisation exists to prevent, requiring no precise coordinates at all.

Coarsening the *position* while leaving a permanent *identifier* attached to it only
lowers the resolution of the trace, it does not prevent the trace.

## Root cause

The projection was designed around "what fields may a rider see", and `id` reads as
metadata rather than as personal information. It is not: an identifier that is stable
across observations is what converts a series of individually-harmless positions into
a pattern of life. The privacy property needed is unlinkability across observations,
which no per-field allowlist expresses.

## Fix / remediation

The pre-match payload now carries a rotating HMAC pseudonym instead of the row id.
Three properties, each load-bearing:

| Property | Why |
|---|---|
| **Rotating** (15 min) | Caps any trace at one period, however long a client polls. |
| **Per-viewer** | The viewer's user id is in the HMAC input, so two riders — or two accounts held by one person — see different tokens for the same driver and cannot pool observations. Without this, rotation only slows a determined collector down. |
| **Staggered per driver** | The rotation boundary is offset by a value derived from the driver id, so markers do not all churn on the same tick. |

The stagger is what makes rotation affordable, and it is a direct answer to T3's
objection. The rider app uses this value as a React marker key (`key={driver.id}` in
`ride-options.tsx:624` and `(tabs)/index.tsx:484`), so a synchronised rotation would
remount every marker at once. Staggered, at most one marker re-keys at a time, which
renders as a single approximate car icon re-mounting in place.

Key material is a **derived subkey**, `HMAC(JWT_SECRET, "spinr-prematch-driver-pseudonym-v1")`,
not `JWT_SECRET` itself — a token forged from one must not be interchangeable with
anything on the auth path.

## Risk & impact on existing functionality

**Blast radius — verified before changing anything, because this field crosses to the
client:**

- **Both call sites already funnel through one projection.** `GET /drivers/nearby`
  (`routes/drivers/location.py:313`) and the `get_nearby_drivers` WS message
  (`routes/websocket.py:1185`) both call `prematch_driver_list`, so one change covers
  both and they cannot drift. Both now pass `viewer_id`.
- **The id is display-only.** Grepped the rider app: the only uses are
  `key={driver.id}` and `identifier={driver.id}` / `identifier={`nearby-${driver.id}`}`
  — a React key and a marker identifier. Nothing stores it, sends it back, or joins on
  it.
- **No endpoint accepts a client-supplied driver id** on the rider path (grepped for
  `preferred_driver`, `requested_driver`, `driver_id` request bodies in
  `routes/rides/` — no matches), so the pseudonym cannot break booking or dispatch.
  Dispatch assigns drivers server-side from the real row.
- **The assigned-ride tracking path is untouched.** It does not use this projection;
  once a ride is assigned, the rider legitimately sees the real driver and exact
  position.
- **Admin surfaces are untouched** — they read `is_online` and full driver rows
  through separate admin routes.

**What could regress:** a marker remount is visible as a brief icon re-render. With
15-minute staggered rotation, a rider with the map open for a typical booking session
(a few minutes) usually sees none; someone idling on the home map sees at most one
marker re-key at a time. There is **no visual regression tooling for this surface**, so
this was reasoned about from the marker-key semantics, not observed — see below.

## User experience effect

**Rider-facing but effectively invisible.** Cars still appear at the same coarse
positions with the same icons, headings, and vehicle types. The only change is the
opaque key behind each marker, which no UI displays.

**Driver-facing: an improvement, not visible in-app.** A driver's coarse movements can
no longer be followed across a 15-minute boundary by a rider polling the map.

No corporate-admin or internal-admin change.

## Files modified

| File | What changed | Why |
|---|---|---|
| `backend/utils/driver_map_visibility.py` | Added `prematch_driver_pseudonym()` + derived subkey; payload emits it instead of `driver.id`; `viewer_id` threaded through both projection helpers | The fix |
| `backend/routes/drivers/location.py` | Passes `viewer_id=current_user["id"]` | Scope pseudonyms to the caller |
| `backend/routes/websocket.py` | Passes `viewer_id=user["id"]` | Same, for the WS path |
| `backend/tests/test_prematch_driver_location_privacy.py` | 4 existing assertions updated to the new contract; +10 tests | Pin the new properties |
| `docs/change-log/2026-07-30-prematch-driver-marker-pseudonym.md` | New — this file | Required by CLAUDE.md |

## Before / after

```python
# BEFORE — utils/driver_map_visibility.py
payload = {
    "id": driver.get("id"),          # stable row id → a trace key
    "lat": coarse[0], "lng": coarse[1],
    "precision": "approximate",
}

# AFTER
payload = {
    "id": prematch_driver_pseudonym(driver.get("id"), viewer_id),
    "lat": coarse[0], "lng": coarse[1],
    "precision": "approximate",
}
```

```
# On the wire, same driver, two riders, same instant:
  rider_1 sees  {"id": "491971db64656a4d", "lat": 52.135, "lng": -106.668, …}
  rider_2 sees  {"id": "c0a4e2f7b1d38a95", "lat": 52.135, "lng": -106.668, …}
# and both differ again after the driver's staggered 15-minute boundary.
```

Four existing T3 tests asserted `d["id"] == "drv_near"`. They were updated to compare
against `prematch_driver_pseudonym(...)` rather than relaxed — each still asserts
exactly which drivers are on the map, and one gained an explicit
`assert "drv_a" not in {d["id"] for d in out}`.

## Rollback plan

`git revert` is safe and immediate — this is a pure projection change. No migration, no
stored data, no money path. Reverting restores the row id in the payload (and the
tracking exposure); nothing else changes, because nothing persists the pseudonym.

There is deliberately **no feature flag**. The value is consumed only as an opaque
marker key, so there is no client contract to stage: an old client receiving a
pseudonym behaves identically to one receiving a row id. `PSEUDONYM_PERIOD_S` is the
tuning knob if rotation proves too visible — raising it weakens the privacy bound
without any code change, and it is a single constant.

Note the pseudonyms are **not stable across a `JWT_SECRET` rotation**, since the
subkey derives from it. That is a one-off marker re-key at the moment of rotation, not
a correctness problem, but it is worth knowing before rotating that secret.

## Verification performed

- **33 tests pass** in `test_prematch_driver_location_privacy.py` (23 before; +10).
- **Mutation-verified — six mutations, all caught:**

  | Mutation | Failing tests |
  |---|---:|
  | Emit the raw row id again (restore the exposure) | 8 |
  | Drop the viewer from the HMAC (colluding accounts pool traces) | 3 |
  | Never rotate (bucket pinned to 0) | 2 |
  | Remove the per-driver stagger (all markers remount together) | 1 |
  | Route stops passing `viewer_id` | 3 |
  | Token varies per call (marker churn on every poll) | 7 |

- **Both directions asserted**: the row id never appears anywhere in the payload
  (`"drv_secret" not in str(out)`), *and* distinct drivers stay distinguishable, *and*
  the token is stable within a period — because a token that changed per request would
  remount every marker on every poll, which mutation 6 confirms is caught.
- **Fail-safe direction tested**: a caller that forgets `viewer_id` still gets a
  pseudonym, never the row id.
- **Opacity**: token length is constant (16 hex) regardless of input length, so it
  leaks nothing about what it replaces.
- **Blast radius verified by grep, not assumed** — rider-app usages of the id, and the
  absence of any rider endpoint accepting a client driver id.
- **Full suite:** `pytest -m "not slow"` → **5916 passed, 8 skipped, 1 xfailed,
  1 failed** (5906 before; +10). Same pre-existing `test_compliance_reports.py`
  failure, unrelated (proven in the T1 log).
- **Lint:** `ruff check` clean on all four changed files.

## What was NOT verified

- **No rider-app change was made, and no rider-app test was run.** The claim that a
  staggered re-key is visually a non-event rests on reading the marker code
  (`key={driver.id}` → React remounts that one marker) rather than on observing it.
  **There is no visual or snapshot regression tooling for the rider map** — a standing
  gap already recorded in `ACTION_ITEMS.md`, not something this change can close.
- **`CarMarker`'s behaviour on a key change is inferred from React semantics**, not
  from the component's source or from a device run. If it animates position
  transitions internally, a re-key restarts that animation — plausible, unverified,
  and the most likely place a user-visible artefact would appear.
- **Rotation cadence is a judgement call, not a measurement.** 15 minutes trades trace
  length against marker churn. No user testing informed it, and no telemetry exists
  yet to say how long riders keep the map open.
- **Not tested against a live driver moving in real time.** All tests use fixed
  coordinates and an injected clock.
- **The WS path's `viewer_id` wiring is covered only by the shared projection tests.**
  There is a REST endpoint test asserting per-caller scoping
  (`test_rest_endpoint_scopes_pseudonyms_to_the_authenticated_caller`) but no
  equivalent driving the WS handler end-to-end, so mutation 5 was run against the REST
  route only. The WS call site was verified by reading it.
- **No production build applies** — backend-only.
- **CI has not run this.** Local `pytest` + `ruff` only.
