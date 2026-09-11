import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Animated, Easing, Platform, View } from 'react-native';
import { Image as ExpoImage } from 'expo-image';
import { AnimatedRegion, Marker } from 'react-native-maps';
import {
    coalescePlaybackBearing,
    distanceMeters,
    selectBearing,
    shortestArcRotationTarget,
    snapToRoute,
    visualRotationDegrees,
    type TrackingLatLng,
} from '@shared/utils/vehicleTracking';
import {
    PLAYBACK_DELAY_MS,
    playbackPosition,
    pushFix,
    shouldResetBuffer,
    type PlaybackFix,
} from '@shared/utils/markerPlayback';
import { smoothFix, isImplausibleJump, type SmoothingState } from '@shared/utils/gpsSmoothing';
import type { FixFeed, MarkerFix } from '@shared/utils/fixFeed';
import { captureException } from '@shared/services/errorReporting';

const CAR_IMAGES = {
    standard: require('../assets/images/car_marker.png'),
    xl: require('../assets/images/car_marker_xl.png'),
    premium: require('../assets/images/car_marker_premium.png'),
} as const;

export type CarMarkerVariant = keyof typeof CAR_IMAGES;

/**
 * Map a backend vehicle type name (vehicle_types.name: Economy / Premium /
 * Van / XL) to a marker variant. Unknown or missing names fall back to the
 * standard sedan so the map never breaks on new types.
 */
export function variantForVehicleType(name?: string | null): CarMarkerVariant {
    const n = (name ?? '').toLowerCase();
    if (n.includes('xl') || n.includes('van')) return 'xl';
    if (n.includes('premium') || n.includes('lux')) return 'premium';
    return 'standard';
}

/**
 * Resolve the marker for a driver. Prefers the admin-configured
 * vehicle_types.marker_variant (passed through the API, e.g.
 * /drivers/nearby); falls back to vehicle type name matching for older
 * backends that don't send it. Unknown values fall back too, so a future
 * server-side variant can never crash an old client.
 */
export function resolveMarkerVariant(
    markerVariant?: string | null,
    vehicleTypeName?: string | null,
): CarMarkerVariant {
    if (markerVariant && markerVariant in CAR_IMAGES) {
        return markerVariant as CarMarkerVariant;
    }
    return variantForVehicleType(vehicleTypeName);
}

interface CarMarkerProps {
    coordinate: {
        latitude: number;
        longitude: number;
    };
    heading?: number | null;
    /**
     * Real measurement time of `coordinate` (e.g. expo-location's
     * `loc.timestamp` or the WS fix's server timestamp). Without it the fix is
     * stamped at ARRIVAL, so any upstream batching/throttling distorts the
     * playback velocity — segments look fast then slow. Pass it wherever the
     * source provides one.
     */
    fixTimestampMs?: number | null;
    /**
     * Un-throttled fix stream (see utils/fixFeed). When provided, it is the
     * primary ingest path — the `coordinate` prop then only seeds/anchors —
     * so an upstream render throttle can never starve the playback buffer.
     */
    fixFeed?: FixFeed | null;
    /**
     * Accepted for compatibility with the dashboard call site; rendering no
     * longer varies by it (historically we avoided re-snapshotting on its
     * changes — under RN 0.85 Bridgeless that triggered a native cast crash).
     */
    isOnline?: boolean;
    size?: number;
    zIndex?: number;
    identifier?: string;
    variant?: CarMarkerVariant;
    // Admin-uploaded custom marker (vehicle_types.marker_image_url),
    // prefetched into the native image cache by vehicleTypeStore. Takes
    // precedence over `variant`; on load failure the bundled variant
    // image is rendered instead.
    imageUri?: string | null;
    // Route polyline the vehicle is driving (planned/live route coords).
    // When provided, each GPS fix is snapped onto the nearest route segment
    // (within MAX_ROUTE_SNAP_M) so the car stays on the road, and the
    // segment's direction orients the car — correct even before the first
    // usable GPS heading arrives (a stationary fix reports heading -1, which
    // previously left the car pointing due north across east-west streets).
    routeCoordinates?: readonly TrackingLatLng[] | null;
    /**
     * Optional state-colored presence ring drawn behind the car icon.
     * `color` is caller-resolved (this component has no theme access and no
     * ride-state knowledge by design — pass `colors.success`/`colors.warning`
     * /`colors.primary` etc. from the caller's own `useTheme()`). `pulsing`
     * toggles a looping "radar" pulse (an active, bounded-duration moment —
     * e.g. en route to pickup) vs. a static ring (a steady state — parked
     * online-idle, or a trip already in progress).
     *
     * ONLY pass this on a screen that renders a single CarMarker at a time
     * (a driver's own vehicle, or a rider's one assigned driver). While
     * `pulsing` is true this forces Android's `tracksViewChanges` to stay
     * true for as long as the ring pulses — cheap for exactly one marker,
     * but the same perf trap the mount-bounce animation above was built to
     * avoid if it were ever applied per-marker on a multi-marker screen
     * (e.g. rider-app's nearby-drivers map, `(tabs)/index.tsx`) — never wire
     * this prop there.
     */
    ring?: { color: string; pulsing: boolean } | null;
    /**
     * Fired every playback tick with the position this marker is actually
     * rendering (route-snapped when on-route, TICK_MS-delayed per the
     * playback buffer). A caller that derives a camera position FROM this
     * vehicle's location (e.g. a follow camera that shifts its center ahead
     * of the car) MUST anchor on this position, not a separately-held raw
     * GPS fix — otherwise the camera tracks the live, undelayed position
     * while the icon renders PLAYBACK_DELAY_MS behind it, and any further
     * offset the camera applies (e.g. "pin the car low") compounds in the
     * same direction as that delay instead of being measured from the
     * icon's actual position. At speed, the two can drift far enough apart
     * that the icon renders outside the visible map area — live-testing
     * report 2026-08-31: "icon missing" at 46 km/h in course-up mode. See
     * onBearingChange above for the identical reasoning applied to
     * rotation instead of position.
     */
    onPositionChange?: (coordinate: TrackingLatLng) => void;
    /**
     * Fired every playback tick with the bearing this marker is actually
     * rendering (the same value driving its own icon rotation, TICK_MS-
     * delayed spline/route-aware smoothing included). A caller that also
     * rotates something else in sync with this vehicle (e.g. a course-up
     * map camera) MUST read the bearing from here rather than computing its
     * own independently — two independently-computed bearings inevitably
     * disagree (different lag, thresholds, smoothing), which shows up as
     * the icon not pointing "up" on a course-up map even while the camera
     * is nominally tracking the same vehicle. Not called on every render —
     * only when the ticker actually selects a new bearing (see the
     * selectBearing() call below), same cadence as the icon's own rotation.
     */
    onBearingChange?: (bearing: number) => void;
    /**
     * Live map-camera heading in degrees (0 = north-up). Read from `.current`
     * inside the playback ticker — pass a stable ref, not state, so the
     * parent can update it without re-rendering this marker. Used only on
     * iOS: Apple Maps ignores Marker.rotation, so the car PNG is rotated via
     * a view transform of (worldBearing − mapHeading). Android ignores this
     * (Google Maps applies Marker.rotation in world space itself).
     */
    mapHeadingRef?: React.MutableRefObject<number> | null;
}

// Playback tick: the marker re-targets its position animation this often,
// stepping through the playback buffer (see utils/markerPlayback.ts). Small
// enough for visually continuous motion, large enough that a screen full of
// nearby-driver markers costs negligible JS time.
const TICK_MS = 500;
// Beyond this jump (stale fix after backgrounding, ride handoff) snap instantly —
// gliding across half the city looks worse than a jump.
const SNAP_DISTANCE_M = 500;
// Ignore sub-3m jitter when deriving the fallback bearing from movement.
const MIN_BEARING_MOVE_M = 3;
// A fix farther than this from the route polyline is treated as off-route
// (detour, stale route) and rendered at its raw position instead of lying
// about the car being on the line. Typical urban GPS error is 5–20 m.
const MAX_ROUTE_SNAP_M = 35;
// Cap on the rotation tween so the car visibly turns rather than snapping,
// without lagging a full position-animation behind sharp turns.
const MAX_ROTATE_MS = 600;
// A car-icon Image that fails to decode (transient OOM/codec glitch on a
// low-end device — live-testing report 2026-09-09: "green circle, never a
// car" persisting indefinitely) previously had no way back: onError only
// ever toggled the custom-vs-bundled image choice, which is a no-op when
// there was no custom image to begin with, so a bundled-asset failure left
// hasLoadedImageRef permanently false and the marker froze at the mount
// effect's 5s hard cap showing the ring only, forever. Retrying up to this
// many times (remounting the Image via a bumped key) gives a transient
// failure a chance to self-heal within that same 5s window.
const MAX_IMAGE_RETRIES = 3;

/**
 * Top-down car marker using the transparent PNG from shared/assets.
 *
 * Renders the car image via a child <View><Image/></View> (not the native
 * `image` prop) so `size` controls the rendered dimensions — the native
 * prop renders at the PNG's physical size which is far too large.
 *
 * Transparent backgrounds are set on every wrapper layer (and on the Marker
 * itself) to kill the default Android callout-style bubble that
 * react-native-maps otherwise draws around custom child views.
 *
 * Snapshot lifecycle: `tracksViewChanges` stays true until the car PNG has
 * actually decoded (Image onLoad) plus a short settle delay, then flips false
 * for perf. The previous fixed 800 ms timer raced slow image decodes (cold
 * start, low-end Android, map remounts) and could permanently snapshot an
 * empty view — an invisible car. A hard cap stops per-frame re-snapshots if
 * onLoad never fires; a late onLoad re-arms one final snapshot.
 *
 * Movement: PLAYBACK BUFFER (the Lyft technique). Incoming fixes are queued
 * with their REAL measurement timestamps and the marker renders the car
 * PLAYBACK_DELAY_MS in the past. Each tick animates toward the playback
 * position one tick in the FUTURE with linear easing, so animation velocity
 * equals played-back ground speed and segments join without pulsing.
 * Position/bearing are sampled from a Catmull-Rom spline through the fixes
 * (markerPlayback.ts), so turns are rounded and headings rotate through
 * corners. When the buffer runs dry (network gap) the position dead-reckons
 * along the last segment for a short capped window, then holds honestly.
 *
 * Platform split: Android drives a plain Marker through the NATIVE
 * animateMarkerToCoordinate (UI-thread ValueAnimator — the JS AnimatedRegion
 * path degrades to 5-10 fps on Android, react-native-maps#1765), with the
 * coordinate prop re-synced after each animation window so re-renders can't
 * teleport the marker back to its mount position. iOS keeps Marker.Animated
 * + AnimatedRegion.timing, which is smooth there.
 *
 * Rotation: bearing is selected in JS (route → travel/spline → heading).
 * Android applies it via Marker.rotation (Google Maps). iOS uses Apple Maps,
 * where Marker.rotation is a documented no-op (`@platform iOS: Google Maps
 * only`); the car PNG is rotated with a view transform instead, offset by
 * mapHeadingRef so course-up does not double-rotate a screen-upright
 * annotation. See coalescePlaybackBearing() for why a 500 ms tick's chord
 * can be < 3 m while the car is still moving.
 *
 * Mount animation: a one-shot spring scale+opacity "pop in" plays every time
 * this component mounts — first appearance, and any full remount (e.g. the
 * mapKey remount in (tabs)/index.tsx used to recover a stale marker after
 * offline->online). Deliberately NOT a looping/pulsing animation: a
 * continuous animation would force Android's `tracksViewChanges` to stay
 * true forever, re-snapshotting the marker every frame — the exact perf
 * regression the settle-then-freeze lifecycle above exists to avoid. A
 * short, one-shot spring (native-driven, cheap) avoids that: it finishes
 * within the existing post-image-load settle window, so Android's own
 * JS-driven rotation/position animations are unaffected and the native
 * snapshot still freezes on schedule.
 */
const CarMarkerComponent: React.FC<CarMarkerProps> = ({
    coordinate,
    heading,
    fixTimestampMs,
    fixFeed,
    size = 40,
    zIndex = 1,
    identifier,
    variant = 'standard',
    imageUri,
    routeCoordinates,
    ring,
    onPositionChange,
    onBearingChange,
    mapHeadingRef,
}) => {
    const markerRef = useRef<any>(null);
    // Stable Animated holders created once; reading .current at init is safe.
    // eslint-disable-next-line react-hooks/refs
    const animatedRegion = useRef(
        new AnimatedRegion({
            latitude: coordinate.latitude,
            longitude: coordinate.longitude,
            latitudeDelta: 0,
            longitudeDelta: 0,
        }),
    ).current;
    const prevCoordRef = useRef(coordinate);
    // Where the marker was last animated TO (snapped when on-route) — the
    // travel-bearing fallback measures from here, not the raw prev fix.
    const prevTargetRef = useRef<TrackingLatLng>(coordinate);
    // The last route segment the marker snapped to — passed back into
    // snapToRoute as its continuity hint (see vehicleTracking.ts) so a
    // momentary nearby-but-wrong-direction segment (a divided road, an
    // out-and-back street, a crossing street at an intersection) can't win
    // the nearest-distance search and flip the bearing 90–180° for one tick.
    // Re-based (not cleared) whenever routeCoordinates changes identity — see
    // the routeRef effect below. snapToRoute's unrestricted fallback only
    // kicks in when the continuity window finds NOTHING within maxSnapMeters,
    // which is not the case near a turn on a re-anchored live route.
    const lastRouteSegmentIndexRef = useRef<number | null>(null);
    // Timestamped fix queue the playback ticker consumes. Seeded lazily on
    // first ingest so the initializer stays pure.
    const bufferRef = useRef<PlaybackFix[]>([]);
    // Running Kalman-style smoothing estimate (see gpsSmoothing.ts), applied
    // to every raw fix BEFORE it enters bufferRef — damps single-fix GPS
    // jitter that the playback buffer's spline only smooths BETWEEN fixes,
    // not within one. Re-seeded (set to null) whenever the buffer itself
    // resets, so a real teleport isn't dragged back toward the old estimate.
    const smoothingStateRef = useRef<SmoothingState | null>(null);
    // Last RAW (pre-smoothing) fix that passed isImplausibleJump — the
    // physics baseline the next fix is checked against. Deliberately not
    // smoothingStateRef (a damped estimate) or bufferRef's tail (already
    // fed into the playback buffer): this must be the last fix actually
    // accepted as real, so a run of rejected glitches can never compound
    // into a baseline that itself drifted away from the truth.
    const lastAcceptedRawFixRef = useRef<{ latitude: number; longitude: number; timestampMs: number } | null>(null);
    // Latest heading prop, read by the ticker (which must not re-fire per
    // heading change). Synced in an effect, never during render.
    const headingRef = useRef<number | null | undefined>(heading);
    useEffect(() => {
        headingRef.current = heading;
    }, [heading]);
    // Latest onBearingChange/onPositionChange, read by the ticker (which
    // must not re-fire per callback-identity change — same reasoning as
    // headingRef above).
    const onBearingChangeRef = useRef(onBearingChange);
    useEffect(() => {
        onBearingChangeRef.current = onBearingChange;
    }, [onBearingChange]);
    const onPositionChangeRef = useRef(onPositionChange);
    useEffect(() => {
        onPositionChangeRef.current = onPositionChange;
    }, [onPositionChange]);

    // Route lookup happens inside the position effect via a ref so a route
    // refresh (live-route poll returns a new array every ~20 s) re-snaps the
    // NEXT fix without re-firing the effect mid-glide. Synced in its own
    // effect, declared BEFORE the consumers so it runs first each commit.
    const routeRef = useRef(routeCoordinates);
    useEffect(() => {
        const prev = routeRef.current;
        routeRef.current = routeCoordinates;
        if (prev === routeCoordinates) return;
        // Re-base the continuity hint on the NEW polyline. Segment indices are
        // only meaningful against the array they came from, and the in-trip
        // live route is re-anchored at the car's current position on every
        // 6 s poll — so index 5 of the old route is index ~0 of the new one.
        // Carrying the old index forward as `preferredFromIndex` restricted
        // the next search to segments AHEAD of the car; approaching a turn,
        // the first segment inside that window was the post-turn one, and
        // the icon took its bearing (90° off) while the car was still on the
        // straight — the "car drives sideways" seen on the 2026-09-11 test
        // ride. Snapping the marker's current position onto the new route
        // with an unrestricted search gives the right starting segment
        // without losing continuity across a same-path re-poll. Not clearing
        // to null, deliberately: that would let a nearby wrong-direction
        // segment win the very next tick (the case the hint exists for).
        if (!routeCoordinates || routeCoordinates.length < 2) {
            lastRouteSegmentIndexRef.current = null;
            return;
        }
        const rebased = snapToRoute(
            prevTargetRef.current,
            routeCoordinates,
            MAX_ROUTE_SNAP_M,
            null,
        );
        lastRouteSegmentIndexRef.current = rebased?.segmentIndex ?? null;
    }, [routeCoordinates]);

    // Continuously-accumulated rotation (can exceed 0–360 so shortest-arc
    // tweens never spin the long way round). Driven imperatively — no
    // per-fix re-render just to turn the car.
    // eslint-disable-next-line react-hooks/refs
    const rotationAnim = useRef(
        new Animated.Value(
            heading != null && Number.isFinite(heading) && heading >= 0 ? heading : 0,
        ),
    ).current;
    const rotationValueRef = useRef(
        heading != null && Number.isFinite(heading) && heading >= 0 ? heading : 0,
    );
    // Whether any real bearing has been applied yet — before that, a raw
    // heading-only update may still orient the car.
    const hasBearingRef = useRef(
        heading != null && Number.isFinite(heading) && heading >= 0,
    );
    // Whether a bearing derived from real movement (route segment or travel
    // direction) has been applied yet. Once one has, a reported GPS heading
    // can no longer override it — see the priority note in the position
    // effect below.
    const hasMovementBearingRef = useRef(false);
    // Last world-space course applied (not the iOS screen-space visual).
    // Re-read on parked ticks so a course-up toggle still retargets the
    // Apple Maps view transform without waiting for the next GPS move.
    const lastWorldBearingRef = useRef<number | null>(
        heading != null && Number.isFinite(heading) && heading >= 0 ? heading : null,
    );
    // Parent's camera-heading ref: captured by identity on mount (stable
    // useRef from the dashboard). Synced so a late-bound prop still works.
    const mapHeadingRefInternal = useRef(mapHeadingRef);
    useEffect(() => {
        mapHeadingRefInternal.current = mapHeadingRef;
    }, [mapHeadingRef]);

    // Stable by construction — reads only refs and the stable Animated value.
    const animateRotationTo = useCallback(
        (bearing: number, duration: number) => {
            const target = shortestArcRotationTarget(rotationValueRef.current, bearing);
            if (target === rotationValueRef.current) return;
            rotationValueRef.current = target;
            hasBearingRef.current = true;
            Animated.timing(rotationAnim, {
                toValue: target,
                duration: Math.min(duration, MAX_ROTATE_MS),
                // Marker rotation is a non-style native prop — JS driver only.
                useNativeDriver: false,
            }).start();
        },
        // rotationAnim is a stable ref value.
        // eslint-disable-next-line react-hooks/exhaustive-deps
        [],
    );

    // Android renders a PLAIN Marker driven by the native
    // animateMarkerToCoordinate (a UI-thread ValueAnimator): the
    // AnimatedRegion path marches every animation frame over the JS bridge
    // and is documented to degrade to 5–10 fps under load
    // (react-native-maps#1765); the native call is the mechanism the
    // Uber-style Android implementations use. Teleport guard: the coordinate
    // prop is kept in a state that re-syncs to each tick's target only AFTER
    // the animation window, so a re-render's prop-diff sets the native
    // coordinate to where the marker already is (visual no-op) instead of
    // yanking it back to its mount position — the regression that motivated
    // the earlier all-JS approach. animateMarkerToCoordinate must not be
    // called on Marker.Animated (react-native-maps#3913), hence the plain
    // Marker here.
    const isAndroid = Platform.OS === 'android';
    const [androidCoord, setAndroidCoord] = useState(coordinate);
    // Android's rotation is a plain native prop (not an Animated.Value — see
    // the Marker.Animated/animateMarkerToCoordinate conflict noted above), so
    // it can't be tweened via Animated.timing the way iOS's rotationAnim is.
    // On a straight road that's fine (the spline bearing is C¹-continuous, so
    // consecutive TARGETS a tick apart are close), but a turn's angular RATE
    // can still exceed 30–90° within one 500ms tick — a rotation prop that
    // jumps straight to that step, with nothing tweening the frames in
    // between, reads as a visible snap through the corner even though
    // position (animated natively via animateMarkerToCoordinate) stays
    // smooth: live-testing report 2026-09-09, "no smooth animation." This
    // requestAnimationFrame loop below interpolates androidRotation along the
    // shortest arc from wherever it currently is toward the newest target,
    // over the same duration position animates over, so rotation keeps pace
    // with position instead of jumping ahead of it.
    const [androidRotation, setAndroidRotation] = useState(
        heading != null && Number.isFinite(heading) && heading >= 0 ? heading : 0,
    );
    const androidRotationCurrentRef = useRef(rotationValueRef.current);
    const androidRotationFromRef = useRef(rotationValueRef.current);
    const androidRotationTargetRef = useRef(rotationValueRef.current);
    const androidRotationStartRef = useRef(0);
    const androidRotationDurationRef = useRef(TICK_MS);
    const androidRotationRafRef = useRef<number | null>(null);
    const stepAndroidRotation = useCallback(() => {
        const elapsed = Date.now() - androidRotationStartRef.current;
        const duration = androidRotationDurationRef.current;
        const t = duration > 0 ? Math.min(1, elapsed / duration) : 1;
        const from = androidRotationFromRef.current;
        const to = androidRotationTargetRef.current;
        const value = from + (to - from) * t;
        androidRotationCurrentRef.current = value;
        setAndroidRotation(((value % 360) + 360) % 360);
        if (t < 1) {
            androidRotationRafRef.current = requestAnimationFrame(stepAndroidRotation);
        } else {
            androidRotationRafRef.current = null;
        }
    }, []);
    // Stable by construction — reads/writes only refs and the stable
    // stepAndroidRotation callback.
    const animateAndroidRotationTo = useCallback(
        (bearing: number, duration: number) => {
            const target = shortestArcRotationTarget(rotationValueRef.current, bearing);
            if (target === rotationValueRef.current) return;
            rotationValueRef.current = target;
            hasBearingRef.current = true;
            // Start the new tween from wherever the current tween actually
            // is right now (not its old target) — else an in-flight tween
            // would visibly jump to its previous target before starting the
            // next leg.
            androidRotationFromRef.current = androidRotationCurrentRef.current;
            androidRotationTargetRef.current = target;
            androidRotationStartRef.current = Date.now();
            androidRotationDurationRef.current = Math.min(duration, MAX_ROTATE_MS);
            if (androidRotationRafRef.current == null) {
                androidRotationRafRef.current = requestAnimationFrame(stepAndroidRotation);
            }
        },
        [stepAndroidRotation],
    );
    useEffect(() => () => {
        if (androidRotationRafRef.current != null) cancelAnimationFrame(androidRotationRafRef.current);
    }, []);
    const resyncTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);


    // Single ingest path used by both the feed subscription and the prop
    // effect. Reads only refs — stable by construction.
    const ingestFix = useCallback((fix: MarkerFix) => {
        const now = Date.now();
        const rawCoord = { latitude: fix.latitude, longitude: fix.longitude };
        const ts =
            Number.isFinite(fix.timestampMs) && Math.abs(now - fix.timestampMs) < 60_000
                ? fix.timestampMs
                : now;
        // Physics-based rejection (Uber Beacon-style, without full sensor
        // fusion): a fix implying an impossible speed since the last
        // accepted one is GPS noise/multipath, not a real position — drop it
        // outright rather than accepting it or letting SNAP_DISTANCE_M below
        // treat it as a legitimate teleport. Elapsed time is what tells a
        // real gap (backgrounding, tunnel) apart from a glitch, not distance
        // alone — see isImplausibleJump's own doc.
        if (isImplausibleJump(lastAcceptedRawFixRef.current, { ...rawCoord, timestampMs: ts })) {
            return;
        }
        lastAcceptedRawFixRef.current = { ...rawCoord, timestampMs: ts };
        // shouldResetBuffer only ever compares a new fix against an EXISTING
        // last fix (it bails out with `if (!last) return false`), so the
        // very first fix into a freshly-mounted/empty buffer can never
        // trigger it — no matter how far the real position has moved since
        // this component last rendered. That gap bites index.tsx's own
        // mapKey remount-on-going-online (added to fix the marker never
        // reappearing after offline→online, see that file's comment): the
        // remount reseeds this component with a STALE mount coordinate and
        // an empty buffer, so the first real GPS fix that then arrives —
        // which can be a real few-hundred-metre jump if the driver moved
        // while offline — gets treated as an ordinary ~500 ms tween target
        // instead of a reset anchor, animating one fast straight-line glide
        // across whatever lies between (buildings included) — live-testing
        // report 2026-09-11: "icon moved through the building using
        // shortest path" right after coming back online, following the
        // separately-reported delay waiting for that first fix (GPS
        // time-to-first-fix after being paused). Treating an empty buffer
        // the same as a distance-triggered reset closes this: the first fix
        // after any remount always snaps instead of gliding.
        const isFirstFix = bufferRef.current.length === 0;
        if (isFirstFix || shouldResetBuffer(bufferRef.current, rawCoord, SNAP_DISTANCE_M)) {
            bufferRef.current.length = 0;
            // Stale estimate would otherwise drag the newly-reset position
            // back toward wherever the car used to be — re-seed at the raw
            // (unsmoothed) fix instead.
            smoothingStateRef.current = null;
            hasMovementBearingRef.current = false;
            // Reset on BOTH platforms — the ticker's next tick measures
            // "moved" distance from this, so leaving it stale (as before,
            // iOS-only) would still glide iOS from wherever it last was.
            prevTargetRef.current = rawCoord;
            if (Platform.OS === 'android') {
                setAndroidCoord(rawCoord);
            } else {
                // iOS had no equivalent instant-seed here at all before this
                // fix — animatedRegion.setValue() sets the underlying
                // Animated.Values directly with no animation, so the very
                // next tick's `.timing()` call starts FROM this raw fix
                // instead of gliding in from wherever the marker's stale
                // mount position was. Guarded the same way the Android
                // animateMarkerToCoordinate call below falls back for a
                // missing native method — if a future/older react-native-
                // maps build ever lacks setValue, skip the seed rather than
                // throw; the ticker still renders correctly, just glides.
                if (typeof (animatedRegion as any).setValue === 'function') {
                    animatedRegion.setValue({
                        latitude: rawCoord.latitude,
                        longitude: rawCoord.longitude,
                        latitudeDelta: 0,
                        longitudeDelta: 0,
                    });
                }
            }
        }
        smoothingStateRef.current = smoothFix(smoothingStateRef.current, { ...rawCoord, timestampMs: ts });
        const coord = { latitude: smoothingStateRef.current.latitude, longitude: smoothingStateRef.current.longitude };
        pushFix(bufferRef.current, { ...coord, timestampMs: ts }, now);
    }, []);

    // Un-throttled feed subscription — the primary ingest when provided.
    const hasFeedRef = useRef(!!fixFeed);
    useEffect(() => {
        hasFeedRef.current = !!fixFeed;
        if (!fixFeed) return;
        return fixFeed.subscribe((fix) => {
            if (fix.heading != null) headingRef.current = fix.heading;
            ingestFix(fix);
        });
    }, [fixFeed, ingestFix]);

    // ── Fix ingest: every coordinate prop change lands in the playback
    // buffer. A jump past SNAP_DISTANCE_M (stale fix after backgrounding,
    // ride handoff) resets the buffer so the marker snaps once instead of
    // gliding across the city; it also clears the movement-bearing latch,
    // since the old direction says nothing about the new location.
    useEffect(() => {
        const prev = prevCoordRef.current;
        const now = Date.now();
        if (
            bufferRef.current.length > 0 &&
            prev.latitude === coordinate.latitude &&
            prev.longitude === coordinate.longitude
        ) {
            return;
        }
        prevCoordRef.current = coordinate;
        // With a live feed the prop stream is the throttled/stale echo of the
        // same fixes — ingesting both would inject arrival-time duplicates.
        // The prop then only seeds an empty buffer (mount, post-reset).
        if (hasFeedRef.current && bufferRef.current.length > 0) return;
        // Stamp with the real measurement time when the source provides one —
        // arrival time is distorted by upstream batching/throttling (a fix
        // held 3 s by a render throttle would otherwise read as 3 s of extra
        // travel time, halving the played-back speed). ingestFix guards a
        // nonsense device clock by falling back to arrival time.
        ingestFix({ ...coordinate, timestampMs: fixTimestampMs ?? now });
        // fixTimestampMs intentionally not a dep: it describes this coordinate.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [coordinate.latitude, coordinate.longitude, coordinate]);

    // ── Playback ticker. Every TICK_MS the marker animates toward the
    // playback position ONE TICK IN THE FUTURE with LINEAR easing, so the
    // animation's velocity equals the played-back ground speed by
    // construction and consecutive segments join without the
    // accelerate-brake pulse the default Easing.inOut produced twice a
    // second (Gojek's "duration is the difference between smooth and
    // choppy"). Cheap when idle: a held position re-targets nothing.
    useEffect(() => {
        const id = setInterval(() => {
            // Lookahead target: where playback will be when this tick's
            // animation completes.
            const p = playbackPosition(
                bufferRef.current,
                Date.now() - PLAYBACK_DELAY_MS + TICK_MS,
            );
            if (!p) return;

            // Snap the played-back position onto the route when close enough;
            // otherwise render it raw (off-route/detour honesty). Passes the
            // last snapped segment as a continuity hint (see
            // lastRouteSegmentIndexRef's own doc comment above).
            const snap = snapToRoute(
                p.coordinate,
                routeRef.current,
                MAX_ROUTE_SNAP_M,
                lastRouteSegmentIndexRef.current,
            );
            lastRouteSegmentIndexRef.current = snap?.segmentIndex ?? null;
            const target = snap?.coordinate ?? p.coordinate;
            onPositionChangeRef.current?.(target);

            const from = prevTargetRef.current;
            const movedM = distanceMeters(
                from.latitude, from.longitude, target.latitude, target.longitude,
            );
            if (movedM < 0.5 && p.mode !== 'interpolating' && p.mode !== 'extrapolating') {
                // Parked — skip position churn, but still retarget the iOS
                // view transform if the map camera heading changed (compass
                // toggle) so north-up ↔ course-up does not leave the hood
                // pointing at the last screen-space angle.
                if (!isAndroid && lastWorldBearingRef.current != null) {
                    const parkedVisual = visualRotationDegrees(
                        lastWorldBearingRef.current,
                        mapHeadingRefInternal.current?.current ?? 0,
                    );
                    animateRotationTo(parkedVisual, 400);
                }
                return;
            }

            // Bearing priority: route segment → spline tangent / direction of
            // travel → reported GPS heading (cold start only). See
            // selectBearing() / coalescePlaybackBearing() — per-tick chords
            // are often < 3 m even while driving.
            const selected = coalescePlaybackBearing(
                selectBearing({
                    snap,
                    movedMeters: movedM,
                    from,
                    to: target,
                    heading: headingRef.current,
                    hasMovementBearing: hasMovementBearingRef.current,
                    minMoveMeters: MIN_BEARING_MOVE_M,
                }),
                { bearing: p.bearing, mode: p.mode },
            );
            const bearing = selected.bearing;
            prevTargetRef.current = target;
            if (bearing != null) {
                if (selected.source === 'route' || selected.source === 'travel') {
                    hasMovementBearingRef.current = true;
                }
                lastWorldBearingRef.current = bearing;
                // World-space course — the follow camera must get this, not
                // the iOS screen-space visual, or course-up would lock at 0.
                onBearingChangeRef.current?.(bearing);
                if (isAndroid) {
                    animateAndroidRotationTo(bearing, TICK_MS);
                } else {
                    animateRotationTo(
                        visualRotationDegrees(
                            bearing,
                            mapHeadingRefInternal.current?.current ?? 0,
                        ),
                        TICK_MS,
                    );
                }
            }

            if (isAndroid) {
                const node = markerRef.current;
                if (node?.animateMarkerToCoordinate) {
                    node.animateMarkerToCoordinate(target, TICK_MS);
                    if (resyncTimerRef.current) clearTimeout(resyncTimerRef.current);
                    resyncTimerRef.current = setTimeout(() => setAndroidCoord(target), TICK_MS);
                } else {
                    // New-arch/interop builds where the native method is
                    // missing: fall back to a direct prop set (steps at
                    // 2 fps, but never a frozen marker).
                    setAndroidCoord(target);
                }
                return;
            }
            animatedRegion
                .timing({
                    latitude: target.latitude,
                    longitude: target.longitude,
                    duration: TICK_MS,
                    easing: Easing.linear,
                    useNativeDriver: false,
                } as any)
                .start();
        }, TICK_MS);
        return () => {
            clearInterval(id);
            if (resyncTimerRef.current) clearTimeout(resyncTimerRef.current);
        };
        // All inputs are stable refs; the ticker itself must never restart.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const [tracksViewChanges, setTracksViewChanges] = useState(true);
    const settleTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    // Whether the car Image has ever actually decoded — read by the ring
    // re-arm effect below to decide whether a fresh snapshot can safely
    // re-freeze quickly or must wait for the image itself.
    const hasLoadedImageRef = useRef(false);
    useEffect(() => {
        // Hard cap: never re-snapshot indefinitely even if onLoad is lost.
        const cap = setTimeout(() => setTracksViewChanges(false), 5000);
        return () => {
            clearTimeout(cap);
            if (settleTimerRef.current) clearTimeout(settleTimerRef.current);
        };
    }, []);
    const handleImageLoaded = () => {
        // Image bitmap is decoded — force a tracksViewChanges false→true
        // transition so Android Google Maps re-snapshots the marker with the
        // car visible. setTracksViewChanges(true) when already true is a
        // React no-op (no re-render, no bitmap re-capture). The brief false
        // is invisible — the next-frame true commits a fresh snapshot.
        hasLoadedImageRef.current = true;
        setTracksViewChanges(false);
        requestAnimationFrame(() => {
            setTracksViewChanges(true);
            if (settleTimerRef.current) clearTimeout(settleTimerRef.current);
            settleTimerRef.current = setTimeout(() => setTracksViewChanges(false), 350);
        });
    };

    // Re-arm the snapshot on ANY ring identity change (color or presence),
    // not just a transition into pulsing. Root cause (live-testing reports:
    // "only a green circle, no car icon" — persisting even after going back
    // offline): index.tsx forces a full MapView remount on offline→online
    // (see its mapKey comment — a fix for a DIFFERENT bug, the car icon
    // never reappearing after that same transition). That remount restarts
    // this component fresh, with the online-idle ring present from frame
    // one — a race between the car Image's decode and whatever moment the
    // native renderer happens to snapshot. If the ring (a plain colored
    // View, paints instantly) wins that race, the snapshot freezes with the
    // ring but no car — and because a frozen Android marker snapshot
    // ignores every later prop change, that broken bitmap then persists
    // through subsequent transitions too, including going offline again
    // (ring prop back to null), since nothing re-arms tracksViewChanges on
    // that change either. Keying an effect on the ring's own identity closes
    // both gaps: any appearance, color change, or disappearance of the ring
    // now gets at least one fresh snapshot attempt.
    const ringChangeKey = ring ? `${ring.color}:${ring.pulsing}` : null;
    const prevRingChangeKeyRef = useRef<string | null>(null);
    useEffect(() => {
        const changed = prevRingChangeKeyRef.current !== ringChangeKey;
        prevRingChangeKeyRef.current = ringChangeKey;
        if (!changed) return;
        setTracksViewChanges(true);
        if (settleTimerRef.current) clearTimeout(settleTimerRef.current);
        if (hasLoadedImageRef.current) {
            // Image is already decoded — safe to re-freeze on the same
            // schedule handleImageLoaded uses.
            settleTimerRef.current = setTimeout(() => setTracksViewChanges(false), 350);
        }
        // Else: leave tracksViewChanges true. The image hasn't loaded yet on
        // this mount, so freezing now would just reproduce the bug this
        // effect exists to fix — handleImageLoaded (once the image actually
        // decodes) or the mount effect's 5s hard cap above will freeze it
        // instead.
    }, [ringChangeKey]);

    // One-shot "pop in" on mount (iOS only). On Android, Google Maps renders
    // custom markers as a bitmap snapshot of the React view. Starting at
    // opacity 0 / scale 0 means the first snapshot captures the ring (outside
    // this wrapper) but not the car (inside it, invisible). Native-driver
    // animations don't reliably trigger the re-snapshot mechanism, so the
    // bitmap can freeze ring-only — the "green circle, no car" bug. Starting
    // at 1 on Android ensures the car is visible from the very first frame.
    // eslint-disable-next-line react-hooks/refs
    const mountAnim = useRef(new Animated.Value(isAndroid ? 1 : 0)).current;
    useEffect(() => {
        if (isAndroid) return;
        Animated.spring(mountAnim, {
            toValue: 1,
            friction: 6,
            tension: 80,
            useNativeDriver: true,
        }).start();
        // mountAnim is a stable ref value; this must run once on mount only.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    // Optional state-colored presence ring — see the `ring` prop doc comment
    // above for the perf constraint (single-marker screens only). Two
    // layers: a static low-opacity circle always shown while `ring` is set,
    // plus (only while `ring.pulsing`) a second circle that scales up and
    // fades out in a loop. Animated.loop's default `resetBeforeIteration`
    // snaps the value back to 0 each cycle, so this is one Animated.timing,
    // not a hand-rolled sequence.
    // eslint-disable-next-line react-hooks/refs
    const ringPulseAnim = useRef(new Animated.Value(0)).current;
    const ringLoopRef = useRef<Animated.CompositeAnimation | null>(null);
    useEffect(() => {
        if (ringLoopRef.current) {
            ringLoopRef.current.stop();
            ringLoopRef.current = null;
        }
        if (!ring?.pulsing) {
            ringPulseAnim.setValue(0);
            return;
        }
        ringPulseAnim.setValue(0);
        const loop = Animated.loop(
            Animated.timing(ringPulseAnim, {
                toValue: 1,
                duration: 1400,
                easing: Easing.out(Easing.ease),
                useNativeDriver: true,
            }),
        );
        ringLoopRef.current = loop;
        loop.start();
        return () => {
            loop.stop();
            ringLoopRef.current = null;
        };
    }, [ring?.pulsing, ringPulseAnim]);

    // Custom marker failed to load (offline + cold cache, dead URL) — fall
    // back to the bundled variant. Reset when the URL changes so a fixed
    // upload is retried.
    const [imageFailed, setImageFailed] = useState(false);
    // Bumped on every onError, up to MAX_IMAGE_RETRIES — included in the
    // <Image>'s key below so a failed decode gets a fresh native Image
    // instance to retry with, instead of the bundled fallback (which has
    // nowhere further to fall back to) simply staying broken. See
    // MAX_IMAGE_RETRIES' own doc comment for why this exists.
    const [imageAttempt, setImageAttempt] = useState(0);
    const imageRetryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    // Reported to error tracking at most once per mount — a flapping image
    // must not spam Sentry every retry cycle.
    const imageErrorReportedRef = useRef(false);
    // eslint-disable-next-line react-hooks/set-state-in-effect
    useEffect(() => {
        setImageFailed(false);
        setImageAttempt(0);
        imageErrorReportedRef.current = false;
    }, [imageUri]);
    useEffect(() => () => {
        if (imageRetryTimerRef.current) clearTimeout(imageRetryTimerRef.current);
    }, []);
    const useCustomImage = !!imageUri && !imageFailed;

    // Do not silently swallow a decode failure (CLAUDE.md: DB/auth/payment
    // errors must surface loudly — the same applies here, since a silently
    // broken car icon is a live-testing-confirmed regression with no other
    // signal). Retries with backoff first (transient OOM/codec glitches on
    // low-end devices self-heal); once retries are exhausted, report once so
    // this is visible in production monitoring instead of a driver silently
    // shipping with no vehicle icon for the rest of their session.
    const handleImageError = useCallback(() => {
        setImageFailed(true);
        setTracksViewChanges(true);
        setImageAttempt((attempt) => {
            if (attempt >= MAX_IMAGE_RETRIES) {
                if (!imageErrorReportedRef.current) {
                    imageErrorReportedRef.current = true;
                    captureException(
                        new Error('CarMarker: car icon image failed to decode after retries'),
                        { domain: 'drivers', surface: 'driver-app' },
                    );
                }
                return attempt;
            }
            if (imageRetryTimerRef.current) clearTimeout(imageRetryTimerRef.current);
            imageRetryTimerRef.current = setTimeout(() => {
                setImageAttempt((n) => n + 1);
            }, 300 * (attempt + 1));
            return attempt;
        });
    }, []);

    // Android: plain Marker + native animator (see the teleport-guard note
    // above). iOS: Marker.Animated + AnimatedRegion, which is smooth there.
    const MarkerComponent: any = isAndroid ? Marker : Marker.Animated;

    // Precomputed so the JSX below reads mountAnim only through a plain
    // variable, not a fresh ref access at render/style-object time.
    const mountAnimatedStyle = {
        width: size,
        height: size,
        backgroundColor: 'transparent' as const,
        alignItems: 'center' as const,
        justifyContent: 'center' as const,
        opacity: mountAnim,
        transform: [{ scale: mountAnim }],
    };
    // Separate view from mountAnimatedStyle: opacity/scale use the native
    // driver; Marker.rotation's Animated.Value is JS-driven. Combining both
    // on one view throws. iOS only — Android rotates via Marker.rotation.
    /* eslint-disable react-hooks/refs -- rotationAnim is the stable Animated.Value from useRef(...).current */
    const iosRotateStyle = isAndroid
        ? null
        : {
              width: size,
              height: size,
              alignItems: 'center' as const,
              justifyContent: 'center' as const,
              transform: [
                  {
                      rotate: rotationAnim.interpolate({
                          inputRange: [-360000, 360000],
                          outputRange: ['-360000deg', '360000deg'],
                      }),
                  },
              ],
          };
    /* eslint-enable react-hooks/refs */

    // Ring geometry: the static ring sits at ~1.35x the car icon; the pulse
    // (when present) scales up to ~1.7x THAT, so the outer wrapper needs
    // ~2.3x the icon size to avoid clipping the pulse at its largest frame.
    // Precomputed (not computed inline in JSX) for the same react-hooks/refs
    // reason as mountAnimatedStyle above.
    const ringDiameter = size * 1.35;
    const ringMaxDiameter = size * 2.3;
    const staticRingStyle = ring
        ? {
              position: 'absolute' as const,
              width: ringDiameter,
              height: ringDiameter,
              borderRadius: ringDiameter / 2,
              backgroundColor: ring.color,
              opacity: 0.28,
          }
        : null;
    /* eslint-disable react-hooks/refs -- ringPulseAnim is the stable Animated.Value from useRef(...).current above, not a fresh ref read */
    const pulseRingAnimatedStyle = ring
        ? {
              position: 'absolute' as const,
              width: ringDiameter,
              height: ringDiameter,
              borderRadius: ringDiameter / 2,
              backgroundColor: ring.color,
              opacity: ringPulseAnim.interpolate({ inputRange: [0, 1], outputRange: [0.4, 0] }),
              transform: [{ scale: ringPulseAnim.interpolate({ inputRange: [0, 1], outputRange: [1, 1.7] }) }],
          }
        : null;
    /* eslint-enable react-hooks/refs */
    const outerSize = ring ? ringMaxDiameter : size;
    // Android: freeze the custom-view snapshot after the image loads (rotation
    // is a native GMSMarker prop, independent of the bitmap). iOS Apple Maps
    // ignores Marker.rotation, so heading is a view transform — freezing the
    // snapshot would pin the PNG north forever. Single marker on this screen.
    const effectiveTracksViewChanges = isAndroid
        ? (ring?.pulsing ? true : tracksViewChanges)
        : true;

    return (
        <MarkerComponent
            ref={markerRef}
            coordinate={isAndroid ? androidCoord : (animatedRegion as any)}
            anchor={{ x: 0.5, y: 0.5 }}
            flat
            rotation={isAndroid ? androidRotation : (rotationAnim as any)}
            tracksViewChanges={effectiveTracksViewChanges}
            zIndex={zIndex}
            identifier={identifier}
            style={{ backgroundColor: 'transparent' }}
        >
            <View
                style={{
                    width: outerSize,
                    height: outerSize,
                    alignItems: 'center',
                    justifyContent: 'center',
                    backgroundColor: 'transparent',
                }}
            >
                {ring && (
                    <View
                        pointerEvents="none"
                        style={{
                            position: 'absolute',
                            width: ringMaxDiameter,
                            height: ringMaxDiameter,
                            alignItems: 'center',
                            justifyContent: 'center',
                        }}
                    >
                        <View style={staticRingStyle as any} />
                        {ring.pulsing && <Animated.View style={pulseRingAnimatedStyle as any} />}
                    </View>
                )}
                {/* eslint-disable-next-line react-hooks/refs -- mountAnimatedStyle is a plain object computed above from the stable mountAnim ref value, not a fresh ref read */}
                <Animated.View style={mountAnimatedStyle}>
                    <Animated.View
                        testID={isAndroid ? undefined : 'car-marker-ios-rotate'}
                        pointerEvents="none"
                        style={iosRotateStyle ?? { width: size, height: size }}
                    >
                        <ExpoImage
                            key={imageAttempt}
                            source={useCustomImage ? { uri: imageUri as string } : CAR_IMAGES[variant]}
                            onError={handleImageError}
                            onLoad={handleImageLoaded}
                            contentFit="contain"
                            style={{
                                width: size,
                                height: size,
                                backgroundColor: 'transparent',
                            }}
                        />
                    </Animated.View>
                </Animated.View>
            </View>
        </MarkerComponent>
    );
};

function _propsAreEqual(prev: CarMarkerProps, next: CarMarkerProps): boolean {
    return (
        prev.coordinate.latitude === next.coordinate.latitude &&
        prev.coordinate.longitude === next.coordinate.longitude &&
        prev.heading === next.heading &&
        prev.fixTimestampMs === next.fixTimestampMs &&
        prev.fixFeed === next.fixFeed &&
        prev.size === next.size &&
        prev.zIndex === next.zIndex &&
        prev.identifier === next.identifier &&
        prev.variant === next.variant &&
        prev.imageUri === next.imageUri &&
        prev.routeCoordinates === next.routeCoordinates &&
        prev.mapHeadingRef === next.mapHeadingRef &&
        // Compared by value, not reference: callers commonly pass a fresh
        // `{ color, pulsing }` object literal each render, which would
        // otherwise defeat memoization for every ring-using call site.
        prev.ring?.color === next.ring?.color &&
        prev.ring?.pulsing === next.ring?.pulsing
    );
}

export const CarMarker = React.memo(CarMarkerComponent, _propsAreEqual);

export default CarMarker;
