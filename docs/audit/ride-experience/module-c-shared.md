# Module C — Shared Library Audit (Ride Experience Industry Benchmark)

**Scope:** `shared/components/{AppMap,CarMarker,RouteLine,RoutePins}.tsx`,
`shared/utils/{markerPlayback,gpsSmoothing,vehicleTracking}.ts`,
`shared/hooks/usePlacesAutocomplete.ts`, diffed at a feature level against
`driver-app/components/CarMarker.tsx`.

**Research date:** 2026-09-12 (WebSearch). Pricing/technique claims decay — see
Dimension 24's own caveat; the technique comparisons below are timestamped
accordingly, not sourced from model memory.

---

## Summary table

| Feature area | Maturity (1–5) | Gap | One-line why |
|---|---|---|---|
| GPS smoothing (Kalman filter, `gpsSmoothing.ts`) | 4 | GREEN | Matches the standard consumer-GPS Kalman-smoothing pattern; Uber's own bleeding edge (particle filters) solves a different problem (phone-side raw positioning), not display smoothing |
| Playback-delay buffering + Catmull-Rom interpolation (`markerPlayback.ts`) | 4 | GREEN | Matches the documented buffer-then-interpolate pattern used across ride-hailing marker-animation implementations; spline (not linear) interpolation is above the median tutorial-level approach |
| Route-snapping (`vehicleTracking.ts:snapToRoute`) | 4 | GREEN | Same category as Google Roads API / Grab's map-matching / Lyft's own published real-time map-matching work — continuity-hint segment biasing is a refinement most public writeups don't even describe |
| CarMarker component — shared vs. driver-app fork | — (governance) | MEDIUM (defect scale) | Untracked capability drift between two copies of the same component; see REC-C-01 |
| AppMap.tsx / RouteLine.tsx / RoutePins.tsx | 4 | GREEN | Genuinely shared, undiverged, single source of truth for both apps (and RoutePins explicitly kept in sync with the web pin spec) |
| usePlacesAutocomplete.ts | 4 | GREEN | Debounce + session-token lifecycle + stale-response discarding is the correct, cost-aware pattern; admin-dashboard's separate copy is a documented platform constraint, not silent drift |

---

## REC-C-01: `CarMarker.tsx` — untracked fork between `shared/` and `driver-app/`

- **As-is decision:** `shared/components/CarMarker.tsx` (938 lines) is the
  canonical car marker, consumed by `rider-app/app/(tabs)/index.tsx`,
  `ride-options.tsx`, `driver-arriving.tsx`, `driver-arrived.tsx`, and
  `ride-in-progress.tsx` (plus 6 rider-app test files). `driver-app/components/CarMarker.tsx`
  (1097 lines) is a separately-maintained copy — not a thin wrapper, a full
  duplicate of the component's source — consumed by
  `driver-app/app/driver/(tabs)/index.tsx` and, via lazy `require()`,
  `driver-app/lib/androidAuto/carSurface.tsx` (the Android Auto head-unit
  surface, base props only: `coordinate`/`heading`). Both copies import the
  same underlying `shared/utils/{vehicleTracking,markerPlayback,gpsSmoothing,fixFeed}.ts`
  and `shared/services/errorReporting.ts` — only the **component** is forked,
  not the math/utility layer. No comment, ACTION_ITEMS entry, or doc anywhere
  states "these two files are intentionally forked and here's why" — the
  divergence is discoverable only by reading both files side by side, which
  is exactly the "silent drift" Dimension 24 warns about.
- **Industry technique:** N/A — this is an internal engineering-hygiene
  finding, not a rider/driver-facing capability gap. No search performed;
  not applicable.
- **Verdict:** MEDIUM (defect scale, per Dimension 24's severity table: "a
  shared UI component forked between two apps with no tracking of the
  divergence"). This can coexist with a GREEN/maturity-4 rating on the
  underlying technique (see the summary table) — a good algorithm in a badly
  governed fork is still a governance defect.
- **Recommendation:** Do not delete or merge either file wholesale (see the
  consumer-impact list above — 5 rider-app screens + 6 test files on one
  side, 1 driver-app screen + 1 Android Auto surface + 1 test file on the
  other; a blind merge risks the rider-app follow-camera behavior documented
  in `onPositionChange`'s own comment, which explicitly depends on this
  component's exact playback timing). Instead: (a) port the specific
  capabilities below that are genuinely portable (REC-C-03, REC-C-04), (b)
  leave the genuinely driver-only capabilities where they are but add one
  header comment to each file cross-referencing the other ("this is a
  tracked fork of X; see docs/audit/ride-experience/module-c-shared.md for
  the capability diff") so the next engineer doesn't have to rediscover this
  audit's own diff from scratch, (c) if headcount allows, the real fix is a
  single component parameterized by an optional `courseUp` capability object
  (see REC-C-02) rather than two files — that is a larger refactor than this
  audit recommends attempting now, given the live-tested risk of touching a
  component five rider-app screens depend on simultaneously.
- **Value to users:** Indirect — a maintained-in-one-place component means a
  future bug fix (like the 5 that have already been ported one-way,
  driver-app → shared, per the file's own comments and ACTION_ITEMS C90)
  lands on both apps at once instead of needing a human to notice and
  re-port it.
- **Value to Spinr:** Reduces the exact failure mode this audit's own §2
  rationale opens with — a fix being found and "closed" multiple times
  independently. `docs/audit/RIDE_EXPERIENCE_INDUSTRY_BENCHMARK_AUDIT_PROMPT.md`
  §2.1 cites the float()-arithmetic bug rediscovered 5 times across B28→B36;
  this fork is the same shape of risk for marker code — three fixes already
  needed a manual one-way port (C90), and this audit itself found two more
  that were never ported (REC-C-03, REC-C-04).
- **Cost:** S for the header cross-reference comments; S–M per individual
  capability port (see below); L (out of scope for this audit's
  recommendation) for a real single-component consolidation. No ongoing
  third-party spend impact — this is pure engineering-time cost.
- **De-dup tag:** `EXTENDS-C90` (C90 already tracks that three ported fixes
  in `shared/CarMarker.tsx` are unverified on a real device; this finding
  extends that by cataloging what has *not yet* been ported at all, which
  C90 does not cover) — also related governance evidence: `C100` (filed
  twice independently on 2026-09-10 by two sessions investigating the same
  `CarMarker.test.tsx` suite) is direct proof this exact file pair already
  causes duplicate audit effort, strengthening the case for the
  cross-reference comment in (b) above.
- **Priority:** P2 (governance/maintainability, not a live defect).

---

## REC-C-02: Course-up camera bearing sync (`onBearingChange` + `mapHeadingRef` + `visualRotationDegrees`)

- **As-is decision:** `driver-app/components/CarMarker.tsx` has two props
  `shared/components/CarMarker.tsx` lacks: `onBearingChange?: (bearing: number) => void`
  (fired every playback tick with the world-space bearing the marker is
  rendering) and `mapHeadingRef?: React.MutableRefObject<number> | null`
  (the live map-camera heading, read inside the ticker so iOS can rotate the
  car PNG by `worldBearing − mapHeading` instead of raw `worldBearing` —
  Apple Maps ignores `Marker.rotation`, so course-up mode needs this offset
  or the icon would double-rotate). `driver-app/app/driver/(tabs)/index.tsx`
  wires `onBearingChange={handleMarkerBearingChange}` (line 1202) into its
  own course-up follow camera (`courseUp` state, line 721) so the camera and
  the icon share one bearing source instead of computing two independently
  — the file's own comment (line 748) explains this was a deliberate fix for
  a prior bug where two independently-computed bearings disagreed at turns.
  The underlying math primitive, `visualRotationDegrees(worldBearing, mapHeading)`,
  already lives in the **shared** `shared/utils/vehicleTracking.ts:323-325` —
  it is simply not imported or wired by `shared/components/CarMarker.tsx`.
  rider-app has no course-up camera today (confirmed: no `courseUp`/`course-up`/
  `onBearingChange` match anywhere in `rider-app/`) — its follow cameras in
  `ride-in-progress.tsx`/`driver-arriving.tsx` are north-up only.
- **Industry technique:** Course-up ("track-up") navigation camera mode —
  where the map rotates to match the vehicle's direction of travel and the
  vehicle icon stays pointed at the top of the screen — is the default/
  standard mode in turn-by-turn navigation products (Google's own Navigation
  SDK camera docs describe "Follow my location" as automatically facing the
  direction of travel; this is the same convention every mainstream
  driving-nav app uses). For a **driver actively navigating**, this is
  expected; for a **rider watching a driver icon on an overview map**,
  north-up is the norm (Uber/Lyft rider apps do not rotate the map to the
  driver's heading — the rider isn't navigating).
- **Verdict:** GREEN / maturity 4 for driver-app (matches category-leading
  driver-side navigation camera behavior). Not applicable / not a gap for
  shared or rider-app — this is legitimately scoped to the driving
  experience, not a missing rider capability.
- **Recommendation:** Driver-app-specific by nature — do **not** port the
  `onBearingChange`/`mapHeadingRef` props or their wiring into
  `shared/CarMarker.tsx` as a default-on behavior, since rider-app has no
  current use for map rotation. However, since the math primitive
  (`visualRotationDegrees`) already lives in shared utils at zero cost, it
  would be low-effort (S) to add the two props to `shared/CarMarker.tsx` as
  **optional, opt-in** (mirroring how `ring` is already optional and
  caller-driven) so that if rider-app ever ships an immersive "watch your
  driver arrive" navigation-style view, the plumbing exists without another
  fork. This is a "leave the door open," not "port now," recommendation —
  there is no current rider-app consumer to justify it today.
- **Value to users:** Driver-side: map orientation matches what every
  driver already expects from Google Maps/Waze, reducing cognitive load
  while driving (a safety-adjacent UX property, not just polish). No rider
  impact either way today.
- **Value to Spinr:** None immediate; the low-cost door-left-open
  recommendation reduces future rework if a rider-side navigation view is
  ever scoped (e.g. as part of the turn-by-turn proposal Module B is
  evaluating, which is driver-facing but could someday extend to a rider
  "track my ride" view).
- **Cost:** S if/when a rider consumer exists; $0 third-party spend (pure
  client-side rendering, no new API calls).
- **De-dup tag:** `NEW` (not previously filed; the seed finding at
  §6.1 of the parent audit prompt already flagged this capability's
  existence generally — "course-up-camera bearing/heading callbacks" — this
  finding is the confirmed, detailed version of that seed with a specific
  recommendation, which the seed explicitly asked each module to produce).
- **Priority:** P4 (no urgency; optional future-proofing only).

---

## REC-C-03: Android rotation is a step function in `shared/`, smoothly interpolated in `driver-app/`

- **As-is decision:** Google Maps' native Android `Marker.rotation` prop is
  not itself animatable via `Animated.timing` (it's a plain native prop, not
  an `Animated.Value` on the platform's own Marker implementation — see both
  files' comments at the Android rotation block). `shared/components/CarMarker.tsx`
  (lines 552-560) sets it directly every tick: `setAndroidRotation(((bearing % 360) + 360) % 360)`
  — a hard step to the new bearing every `TICK_MS` (500ms), with no
  intermediate frames. `driver-app/components/CarMarker.tsx` (lines 442-490)
  instead runs a `requestAnimationFrame` loop (`stepAndroidRotation`/
  `animateAndroidRotationTo`) that interpolates `androidRotation` along the
  shortest arc from wherever the rotation currently is to the newest target,
  over the same `TICK_MS` window position already animates over — the file's
  own comment (lines 428-441) cites a live-testing report ("no smooth
  animation," 2026-09-09) as the reason this was added: a turn's angular
  rate can exceed 30–90° within one 500ms tick, and a step-function rotation
  visibly snaps through the corner even though position stays smooth.
  This is **not** one of the three fixes ACTION_ITEMS C90 already tracks as
  ported-but-unverified (continuity hint, GPS pre-smoothing, ring re-arm) —
  it is a fourth, distinct capability that was never ported at all.
- **Industry technique:** Not itself something published ride-share
  engineering blogs describe at this level of platform-API granularity (this
  is react-native-maps/Android-SDK plumbing, not a product-level technique),
  but the general principle — every rendered frame of a turning vehicle icon
  should show intermediate angles, not jump — is exactly what the
  playback-buffer/interpolation research above (UberCarAnimation tutorials,
  PubNub's smoothing writeup) describes for position, applied here to
  rotation specifically for a platform quirk (Android's `Marker.rotation`
  is not itself an `Animated.Value`, unlike iOS's).
- **Verdict:** RED for `shared/` specifically (a live-testing-confirmed
  visual defect — "no smooth animation" — that has a working fix sitting
  one file away and unported), even though the position-side rotation
  *selection* logic (bearing priority, shortest-arc) is shared and correct.
  driver-app itself is GREEN/maturity 4 here.
- **Recommendation:** Port `driver-app`'s `stepAndroidRotation`/
  `animateAndroidRotationTo` implementation into `shared/components/CarMarker.tsx`,
  replacing the direct `setAndroidRotation(...)` call. This is a rendering-only
  change (no prop/API surface change, no money/state-machine/dispatch
  involvement) confined to the Android rotation path — low blast radius, but
  per CLAUDE.md's pre-merge gates still needs the standard rider-app visual
  smoke-check on a real Android device before merge, since rider-app (unlike
  driver-app, where this exact code has run live since 2026-09-09) has zero
  automated visual-regression tooling and this is exactly the kind of
  "looks right in code, wrong on-device" change ACTION_ITEMS C90 already
  flags as an open risk class for this same file.
- **Value to users:** Riders watching the driver's car icon turn at an
  intersection (5 rider-app screens render this marker) currently see the
  same "snap through the corner" defect driver-app had before its own
  2026-09-09 fix — this closes that gap for the rider-facing surface.
- **Value to Spinr:** Removes a known-and-already-solved visual defect from
  the surface with the least visual QA coverage in the whole stack
  (rider-app has no visual regression tooling at all, per CLAUDE.md) —
  cheap risk reduction since the fix is already written, tested in
  production on the other app, and needs only porting.
- **Cost:** S engineering effort (copy ~50 lines of already-proven logic +
  update the two call sites that currently call `setAndroidRotation`
  directly). $0 third-party spend.
- **De-dup tag:** `NEW` (not covered by C90, which only tracks the three
  fixes already ported; this is a fourth capability C90's own filing did
  not enumerate).
- **Priority:** P2 (real, confirmed-elsewhere visual defect on the
  lowest-QA-coverage surface, but not safety/money/state-machine — doesn't
  warrant P0/P1).

---

## REC-C-04: Route re-anchoring on `routeCoordinates` identity change — missing in `shared/`

- **As-is decision:** `driver-app/components/CarMarker.tsx` (lines 323-357)
  has an effect that re-bases `lastRouteSegmentIndexRef` whenever the
  `routeCoordinates` prop's **identity** changes (i.e., a new array
  reference — which happens every ~20s live-route poll per the seed
  findings): it re-snaps the marker's last rendered position onto the *new*
  polyline with an unrestricted search, so the continuity-hint window
  (`preferredFromIndex` in `snapToRoute`) starts from the right segment on
  the new array instead of carrying forward an index that only made sense
  against the old one. The file's own comment explains the failure mode
  this fixes directly: the in-trip live route is re-anchored at the car's
  current position on every poll, so "index 5 of the old route is index ~0
  of the new one" — without rebasing, the continuity window restricted the
  next search to segments *ahead* of the car, and near a turn the
  first segment inside that window was the post-turn one, so the icon took
  a 90°-off bearing while the car was still on the straight —
  live-tested and named directly: "the car drives sideways" (2026-09-11 test
  ride). **`shared/components/CarMarker.tsx`'s equivalent effect (lines
  290-293) does nothing but store the new reference** —
  `routeRef.current = routeCoordinates;` — with no rebase logic at all. This
  means rider-app, which passes `routeCoordinates` into this same component
  from at least `ride-in-progress.tsx`/`driver-arriving.tsx` per the seed
  findings, is currently exposed to the identical "car drives sideways at a
  turn during a live-route re-poll" defect driver-app already found and
  fixed on 2026-09-11 — one day before this audit's own request date.
- **Industry technique:** Not itself a named public technique (this is a
  narrow, implementation-specific consequence of combining route-snapping
  with a periodically-refreshed live route) — but the general principle
  (map-matching implementations must re-anchor their "last known segment"
  state when the reference path is replaced, not just when the point moves)
  follows directly from how map-matching literature describes maintaining
  continuity across a Viterbi/HMM-style path search (Uber's own map-matching
  uses a Viterbi algorithm over successive road segments per the research
  above) — a fresh path means the previous state index is meaningless until
  re-projected onto it.
- **Verdict:** RED. This is not a maturity/parity question (the underlying
  route-snapping technique is already GREEN/maturity 4, see the summary
  table) — it is an unported, live-testing-confirmed correctness bug present
  in the file every rider-app ride screen currently renders.
- **Recommendation:** Port `driver-app`'s `routeRef` rebase effect (lines
  328-357) into `shared/components/CarMarker.tsx` verbatim (it depends only
  on `snapToRoute` and `prevTargetRef`, both already present in the shared
  file) — this is the single highest-priority finding in this module's
  report because it is a **known, already-fixed-once, currently-unfixed
  rider-facing bug**, exactly the "going in circles" failure mode this
  entire audit was commissioned to surface (parent prompt §2.1). Recommend
  treating this with the same urgency as a newly-filed ACTION_ITEMS entry,
  not just a nice-to-have port.
- **Value to users:** Riders watching the driver's car icon during
  `ride-in-progress`/`driver-arriving` currently can see the same
  car-appears-to-drive-sideways-through-a-turn glitch driver-app's own
  drivers saw and had fixed for them on 2026-09-11 — every live-route re-poll
  near a turn (every ~20s per the seed findings) is a chance to reproduce
  it.
- **Value to Spinr:** Directly closes a rider-facing visual-correctness gap
  with no rider-side visual regression tooling to ever catch it otherwise
  (CLAUDE.md: rider-app has zero automated visual tooling) — this is
  presently invisible to CI and would only surface via a live-testing
  report, the same channel that caught it on driver-app.
- **Cost:** S engineering effort (the fix already exists, verbatim-portable,
  ~30 lines). $0 third-party spend — purely client-side geometry.
- **De-dup tag:** `NEW`. Explicitly **not** covered by C90 (C90's file list
  only names three specific already-ported fixes: `preferredFromIndex`
  continuity hint, GPS pre-smoothing/implausible-jump rejection, and the
  ring-change re-arm effect — this route-rebase fix is a fourth, separate
  driver-app fix from the same 2026-09-11 test-ride session that was never
  ported at all, so it isn't in C90's scope and needs its own tracking).
- **Priority:** P1 — a confirmed, reproducible, rider-facing visual defect
  on a live-tested ride screen with a known fix sitting unported one file
  away; recommend a follow-up PR (with the standard Change Impact Log entry
  per CLAUDE.md, since `ride-in-progress.tsx`/`driver-arriving.tsx` are
  live-tested surfaces) rather than folding it into a larger consolidation
  effort.

---

## REC-C-05: Parked-tick iOS view-transform retarget on map-camera-heading change

- **As-is decision:** Tied to REC-C-02's course-up feature. `driver-app/components/CarMarker.tsx`'s
  playback ticker (lines 676-683), when the marker is parked (`movedM < 0.5`),
  still retargets the iOS rotation transform if the map's camera heading
  changed since the last tick — so toggling between north-up and course-up
  while stationary re-orients the icon immediately instead of waiting for
  the next real GPS movement. `shared/components/CarMarker.tsx`'s equivalent
  parked-tick branch (line 529-531) does nothing (`return; // parked — no
  animation churn, no rotation churn`) — correct for a component with no
  camera-heading concept at all, since there is nothing to retarget against.
- **Industry technique:** N/A — this is a direct corollary of REC-C-02's
  course-up capability, not an independent technique.
- **Verdict:** GREEN / maturity 4 for driver-app (correct handling of an
  edge case its own feature introduces); N/A for shared/rider-app (the
  feature this supports doesn't exist there — see REC-C-02).
- **Recommendation:** No independent action — this should be ported
  together with REC-C-02's props if and when a rider-app course-up consumer
  ever exists, not on its own.
- **Value to users:** Driver-only, already shipped.
- **Value to Spinr:** None independent of REC-C-02.
- **Cost:** Bundled into REC-C-02's estimate; not separately costed.
- **De-dup tag:** `NEW` (minor corollary finding, bundled with REC-C-02).
- **Priority:** P4 (bundled with REC-C-02's own low priority).

---

## REC-C-06: `isOnline` prop — vestigial, rendering-inert compatibility prop

- **As-is decision:** `driver-app/components/CarMarker.tsx` accepts an
  `isOnline?: boolean` prop with a doc comment stating plainly: "Accepted
  for compatibility with the dashboard call site; rendering no longer
  varies by it (historically we avoided re-snapshotting on its changes —
  under RN 0.85 Bridgeless that triggered a native cast crash)." Confirmed:
  `driver-app/app/driver/(tabs)/index.tsx:1196` does pass `isOnline={isOnline}`,
  but the prop is not read anywhere in the render body or effects, and it is
  not compared in `_propsAreEqual`'s memo check either (correctly, since it
  can't affect output). `shared/components/CarMarker.tsx` has no such prop
  at all — not a missing capability, just an artifact of a bug workaround
  driver-app went through that shared never needed to.
- **Industry technique:** N/A — internal API-hygiene note, not a
  user-facing capability.
- **Verdict:** Not ratable on the maturity/gap scale (no capability, no
  rider/driver-visible behavior). Minor governance note: a prop with no
  effect on output is dead API surface that costs a reader time to
  understand is safe to ignore.
- **Recommendation:** No urgency — removing it means touching a live call
  site (`index.tsx:1196`) for zero behavior change, which is exactly the
  kind of "don't touch what isn't broken" the CLAUDE.md surgical-changes
  principle argues against doing opportunistically. Leave as-is; if
  `driver-app/components/CarMarker.tsx` is ever touched for one of the
  other recommendations above, drop the now-fully-explained dead prop and
  its one call site in that same PR rather than as a standalone change.
- **Value to users:** None either way.
- **Value to Spinr:** Marginal readability improvement only, and only
  as a side effect of an already-planned edit.
- **Cost:** Negligible (XS), bundle-only.
- **De-dup tag:** `NEW` (not previously filed; low enough value that it may
  not warrant its own ACTION_ITEMS entry — noted here for completeness per
  Dimension 24's fork-tracking checklist item).
- **Priority:** P4.

---

## REC-C-07 (reverse-direction finding): ExpoImage `cachePolicy="disk"` present in `shared/`, absent in `driver-app/`

- **As-is decision:** This diff runs the opposite direction from the rest
  of this report — found while completing the line-by-line comparison, not
  something the task specifically asked for, but directly relevant to
  Dimension 24's explicit checklist line ("Marker asset caching confirmed —
  ties to Dimension 14's 'car marker images' line"). `shared/components/CarMarker.tsx`'s
  `<ExpoImage>` render (near the end of the file) sets `cachePolicy="disk"`.
  `driver-app/components/CarMarker.tsx`'s equivalent `<ExpoImage>` render has
  no `cachePolicy` prop at all, so it falls back to `expo-image`'s own
  default (`"disk"` for most configurations, but this leaves the choice
  implicit rather than explicit, and an upstream default change would
  silently alter driver-app's caching behavior without shared's).
- **Industry technique:** N/A — this is an implementation-hygiene
  consistency question, not a competitive-parity one; both bundled marker
  assets and admin-uploaded custom marker images (`imageUri`) benefit from
  disk caching regardless of platform, so there's no legitimate reason for
  the two apps to differ here.
- **Verdict:** LOW (defect scale) / not on the maturity scale — a small,
  low-risk inconsistency, not a capability gap.
- **Recommendation:** Add `cachePolicy="disk"` to `driver-app/components/CarMarker.tsx`'s
  `<ExpoImage>` call for consistency and to make the caching behavior
  explicit rather than dependent on an upstream library default.
- **Value to users:** Marginal — likely no observable difference today
  (expo-image's implicit default probably already matches), but removes a
  latent inconsistency that could silently diverge behavior on a future
  `expo-image` upgrade.
- **Value to Spinr:** Small reduction in image-decode/network work on
  driver-app if the implicit default ever differs from `"disk"` in a future
  `expo-image` version; mostly a consistency/explicitness win.
- **Cost:** XS (one-line change). $0 third-party spend.
- **De-dup tag:** `NEW`.
- **Priority:** P4.

---

## REC-C-08: Core marker-tracking technique stack (Kalman filter + playback-delay buffer + Catmull-Rom spline + route-snapping)

- **As-is decision:** `shared/utils/gpsSmoothing.ts` implements a 1-D
  Kalman ("recursive least squares") filter per incoming GPS fix, tuned for
  vehicle speeds (process noise 6 m/s vs. the ~3 m/s pedestrian-tracking
  default the technique commonly uses), applied before the fix enters the
  playback buffer. `shared/utils/markerPlayback.ts` implements a
  PLAYBACK_DELAY_MS=5000 buffer: the marker renders 5 seconds in the past,
  sampling position/bearing from a Catmull-Rom (cubic Hermite) spline
  through 4+ buffered fixes when available, falling back to linear
  interpolation otherwise, with a speed-trend-aware dead-reckoning cap
  (1.5s, shrunk proportionally when the vehicle appears to be decelerating)
  for network gaps. `shared/utils/vehicleTracking.ts:snapToRoute` projects
  each fix onto the nearest route-polyline segment (within 35m) using an
  equirectangular local projection, with a continuity-hint window
  (`preferredFromIndex`) that biases the search toward segments ahead of the
  previous tick's own match to prevent momentary wrong-direction segment
  selection at divided roads/intersections. A physics-based rejection
  (`isImplausibleJump`, gpsSmoothing.ts) drops any fix implying a speed no
  real vehicle could achieve since the last accepted one, before it ever
  reaches the buffer.
- **Industry technique (researched 2026-09-12, WebSearch):** Uber's own
  engineering blog ("Rethinking GPS: Engineering Next-Gen Location at
  Uber," uber.com/blog) confirms Kalman filters are the standard technique
  used to smooth raw GPS into continuous marker motion, with map-matching
  (Viterbi-algorithm road-segment selection) layered on top — Uber's most
  recent evolution has moved to particle filters for phone-side raw
  positioning specifically, which is a different problem (improving the
  *source* location fix itself, often using inertial/WiFi/cell signals) than
  Spinr's problem here (smoothing an already-received lat/lng stream for
  *display*), so this is not a direct apples-to-apples "Spinr is behind"
  comparison. Lyft's own engineering blog independently confirms real-time,
  client-side map-matching work ("A New Real-Time Map-Matching Algorithm at
  Lyft," "Using Client-Side Map Data to Improve Real-Time Positioning,"
  eng.lyft.com) — the same category of technique Spinr's `snapToRoute`
  implements. Google's Roads API and Grab's published map-matching/GPS-
  trajectory research (Grab-Posisi dataset paper) independently confirm
  snap-to-road as the standard technique for noisy consumer GPS in
  ride-hailing/logistics contexts. General playback-buffer + interpolation
  (multiple sources: PubNub's "How to Smooth Your Location Data & Snap to
  Roads," several public UberCarAnimation-style tutorials) confirms
  buffer-then-interpolate is the standard pattern for smooth marker replay
  under real-world network jitter — Spinr's specific choice of Catmull-Rom
  (cubic Hermite) spline interpolation rather than plain linear
  interpolation is *above* what most public tutorial-level implementations
  describe (they typically use linear interpolation between raw points); a
  2018 trajectory-reconstruction paper (arXiv:1803.07184) and the V-Spline
  paper (PMC8125788) confirm Catmull-Rom-family splines are an established,
  computationally-efficient real-time vehicle-trajectory-smoothing technique
  in the wider GIS/telematics literature, not something unusual or
  under-baked for Spinr's scale.
  **Note on the code's own "Lyft technique" attribution** (markerPlayback.ts's
  doc comment): a specific public Lyft engineering post describing a
  "5-second delay buffer" could not be located via WebSearch — Lyft's public
  writing in this space (linked above) covers map-matching, not
  playback-delay buffering specifically. The delay-buffer-plus-interpolation
  *technique itself* is well-documented industry practice (see above); the
  attribution to Lyft by name in particular should be treated as
  unverified/informal team lore rather than a citable public source, and the
  code comment should not be relied on as an external reference if this
  ever needs citing outside the codebase.
- **Verdict:** GREEN / maturity 4, plainly — this is not a bug list, and
  this finding should be read as a genuine strength, not buried under the
  fork/rotation findings above. The layered pipeline (physics-rejection →
  Kalman smoothing → playback buffer → spline interpolation → route-
  snapping with continuity hinting) is conceptually the same multi-stage
  toolkit category-leading ride-share apps use, and the continuity-hint
  refinement in `snapToRoute` specifically addresses a failure mode
  (wrong-direction segment selection near intersections) that most public
  writeups on this topic don't even mention handling. Not scored a 5
  ("differentiated / better than category baseline") because there's no
  evidence of anything here going meaningfully *beyond* what's publicly
  documented as standard practice (e.g. no sensor-fusion/particle-filter
  work, no server-side map-matching against a full road graph) — it is a
  faithful, well-executed implementation of the established pattern, which
  is exactly maturity-4 "at parity."
- **Recommendation:** No change — already at parity. If Spinr later wants a
  maturity-5 differentiator here, the concrete next step the research above
  points to would be moving from client-only route-snapping (already-known
  route polyline) toward genuine live map-matching against the OSRM road
  graph Spinr already self-hosts for `/rides/{id}/live-route` (per the
  parent audit prompt §6.1) — but that is a speculative future enhancement,
  not a gap to close now, and is explicitly out of scope for this
  report-only audit to design.
- **Value to users:** Smooth, physically-plausible vehicle motion on both
  apps' maps — riders/drivers don't consciously notice good tracking, but
  they do notice its absence (the live-testing reports cited throughout
  both CarMarker files — "stays at one place and then jumps," "green
  circle, never a car," "car drives sideways" — are exactly what happens
  when this pipeline regresses).
- **Value to Spinr:** Competitive parity on a highly visible, constantly-
  on-screen UI element during every single ride — this is arguably the
  single most-observed piece of UI in the entire app during an active trip.
- **Cost:** $0 — already built, no ongoing third-party spend (pure
  client-side math, no API calls in this layer).
- **De-dup tag:** `NEW` (this specific GREEN/maturity-4 confirmation,
  freshly researched for this audit, has not been filed before — the parent
  audit prompt's §2.2/§6.1 already asserted this informally as a seed
  observation; this is the confirmed, sourced, dated version Dimension 24
  requires).
- **Priority:** P4 (no action needed — recorded for the scorecard).

---

## REC-C-09: `AppMap.tsx` / `RouteLine.tsx` / `RoutePins.tsx` — genuinely shared, undiverged

- **As-is decision:** `shared/components/AppMap.tsx` is a 21-line
  `MapView` wrapper that only resolves the Android provider; no
  app-specific logic. `shared/components/RouteLine.tsx` and
  `shared/components/RoutePins.tsx` are each imported directly (via the
  `@shared/*` alias or a lazy `require('@shared/components/...')` in the
  Android Auto surface) by both rider-app and driver-app with **no fork** —
  confirmed via repo-wide grep: these two component names appear only under
  `shared/components/`, nowhere else. `RoutePins.tsx`'s own doc comment
  goes further than typical shared-component hygiene: it explicitly notes
  the web surfaces (admin MapLibre maps, the public tracking page) draw the
  *same* marker spec from `routePinSvg()` in `shared/constants/routeMapStyle.ts`,
  with an explicit instruction to "change the spec there, not here, or the
  two drift apart again" — i.e. the drift-prevention discipline this
  audit's CarMarker findings show is missing elsewhere is present and
  actively maintained here.
- **Industry technique:** N/A directly (this finding is about internal
  code-sharing hygiene, not a rider/driver-facing capability) — but the
  underlying rendered result (a continuous gradient route line with
  traveled-portion erasure ahead of the vehicle, `trimTraveled()`) is
  explicitly modeled on "Uber/Lyft's live tracking screens, where the line
  only shows the road ahead" per the code's own comment, consistent with
  what riders observe in category-leading apps.
- **Verdict:** GREEN / maturity 4 — both the sharing discipline and the
  rendered capability (gradient line, traveled-erasure, gap-preserving
  multi-section rendering for completed-ride history) are at parity with
  what riders expect and, on the sharing-discipline axis specifically,
  ahead of this module's own CarMarker finding.
- **Recommendation:** No change — already at parity, and worth holding up
  as the internal model for how CarMarker's own fork (REC-C-01) should
  eventually look.
- **Value to users:** Consistent route-line rendering across every surface
  (rider, driver, Android Auto head unit, and the web-based admin/tracking
  surfaces) that touches a route.
- **Value to Spinr:** Zero duplicate-maintenance risk on this component
  pair, unlike CarMarker.
- **Cost:** $0 — no change recommended.
- **De-dup tag:** `NEW` (a GREEN confirmation, not previously filed).
- **Priority:** P4 (no action).

---

## REC-C-10: `usePlacesAutocomplete.ts` — shared, well-factored, documented platform-constraint divergence

- **As-is decision:** `shared/hooks/usePlacesAutocomplete.ts` centralizes a
  300ms debounce, Google Places session-token lifecycle (session tokens
  reduce Places API billing versus per-keystroke Autocomplete + Details
  calls billed independently), in-flight request sequencing (a
  `requestSeqRef` counter discards stale responses so a slow earlier
  request can't overwrite a newer one), and defers query-shape formatting
  to `@shared/api/places.buildPlacesQuery`. The hook's own doc comment
  states plainly that `admin-dashboard` has "a parallel hook
  (`admin-dashboard/src/hooks/usePlacesAutocomplete.ts`) with the same
  shape — it cannot import this one because `@shared/api/client` is
  React-Native specific." This is a **documented, deliberate** platform
  boundary (React Native's Axios-wrapping client vs. a Next.js API client),
  not silent drift — the two hooks share design and intent but not code,
  for a legitimate cross-platform reason.
- **Industry technique:** N/A directly — this is implementation hygiene,
  not a rider-facing capability question (the rider-facing autocomplete
  experience itself is Module A's scope, per this audit's own module
  boundaries). Session-token-based Places billing is Google's own
  documented cost-reduction mechanism for autocomplete-then-details flows;
  using it here is the correct, expected pattern rather than a novel
  finding.
- **Verdict:** GREEN / maturity 4 — correctly factored, correctly
  documented divergence where a true fork does exist (admin-dashboard),
  and cost-aware (session tokens, debounce, stale-response discarding all
  reduce wasted Places API calls).
- **Recommendation:** No change.
- **Value to users:** Not directly rated here (Module A's scope) — noted
  only for completeness of the shared-library sweep.
- **Value to Spinr:** Confirms the admin-dashboard duplicate is not an
  oversight requiring consolidation — worth Module D/E knowing this when
  building the cost-site inventory, so the admin-dashboard hook isn't
  mistakenly flagged there as unexplained drift.
- **Cost:** $0 — no change recommended.
- **De-dup tag:** `NEW` (GREEN confirmation).
- **Priority:** P4 (no action).

---

## Consumer / blast-radius reference (for any future CarMarker consolidation PR)

Per this module's RULES: neither copy of `CarMarker.tsx` should be deleted
or merged without checking every consumer below first.

**`shared/components/CarMarker.tsx` consumers:**
- `rider-app/app/(tabs)/index.tsx`
- `rider-app/app/ride-options.tsx`
- `rider-app/app/driver-arriving.tsx`
- `rider-app/app/driver-arrived.tsx`
- `rider-app/app/ride-in-progress.tsx`
- Tests: `rider-app/__tests__/{rideOptionsScreen,homeScreen,driverArrivingScreen,driverArrivedScreen,rideInProgressScreen,carMarkerPositionChange}.test.tsx`

**`driver-app/components/CarMarker.tsx` consumers:**
- `driver-app/app/driver/(tabs)/index.tsx` (the "dashboard call site" the
  `isOnline` prop's own doc comment refers to — confirmed at line 1196)
- `driver-app/lib/androidAuto/carSurface.tsx` (Android Auto head-unit
  surface, lazy-`require()`'d after a maps-availability guard; passes only
  `coordinate`/`heading` — none of the course-up/ring/route props)
- Tests: `driver-app/__tests__/components/CarMarker.test.tsx`

No consumer of either copy was found in `admin-dashboard/` — the one grep
hit there (`admin-dashboard/src/app/dashboard/vehicle-types/page.tsx:112`)
is a comment referencing the shared marker *image assets*, not an import of
either component.

---

## What this module did NOT verify

- No real-device or simulator testing was performed — every finding above
  is a static code-level diff and cross-reference against ACTION_ITEMS.md's
  own recorded live-testing reports, not a fresh repro. REC-C-03 and
  REC-C-04 in particular are recommended ports of fixes that were
  *themselves* validated live on driver-app; the port itself would still
  need the same device-level confirmation before being considered done,
  consistent with the open, unverified status of the three related fixes in
  ACTION_ITEMS C90.
- No production telemetry (crash rates, marker-render error rates, actual
  frequency of the "car drives sideways" defect in the wild) was available
  or consulted — Sentry MCP access was not authorized in this session.
- WebSearch results are current as of 2026-09-12 and are general
  engineering-blog/documentation sources, not confirmed against any
  Uber/Lyft/Grab internal implementation detail beyond what each company
  has chosen to publish — treat the technique comparisons as
  well-sourced-but-external, not verified against those companies' actual
  current production code.
- This module did not evaluate `shared/utils/fixFeed.ts` in depth (only
  referenced as an import) since it was not named in this module's SCOPE
  list — flagging in case Module B or E wants it covered elsewhere, since
  both CarMarker copies depend on it for their un-throttled ingest path.

===MODULE-C-COMPLETE===
