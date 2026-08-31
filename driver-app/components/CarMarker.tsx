import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Animated, Easing, Platform, Image } from 'react-native';
import { AnimatedRegion, Marker } from 'react-native-maps';
import {
    distanceMeters,
    selectBearing,
    shortestArcRotationTarget,
    snapToRoute,
    type TrackingLatLng,
} from '@shared/utils/vehicleTracking';
import {
    PLAYBACK_DELAY_MS,
    playbackPosition,
    pushFix,
    shouldResetBuffer,
    type PlaybackFix,
} from '@shared/utils/markerPlayback';
import type { FixFeed, MarkerFix } from '@shared/utils/fixFeed';

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
 * Rotation: animated through an Animated.Value along the shortest arc, so
 * the car turns smoothly instead of snapping its angle. Bearing source
 * priority: route-segment direction (when `routeCoordinates` is provided and
 * the played-back position snaps onto the route) → direction of travel →
 * the reported GPS heading, used only until movement has established a
 * bearing. Movement deliberately outranks the reported heading; see
 * selectBearing() for why a reported heading of 0 cannot be trusted on
 * Android.
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
    // Timestamped fix queue the playback ticker consumes. Seeded lazily on
    // first ingest so the initializer stays pure.
    const bufferRef = useRef<PlaybackFix[]>([]);
    // Latest heading prop, read by the ticker (which must not re-fire per
    // heading change). Synced in an effect, never during render.
    const headingRef = useRef<number | null | undefined>(heading);
    useEffect(() => {
        headingRef.current = heading;
    }, [heading]);

    // Route lookup happens inside the position effect via a ref so a route
    // refresh (live-route poll returns a new array every ~20 s) re-snaps the
    // NEXT fix without re-firing the effect mid-glide. Synced in its own
    // effect, declared BEFORE the consumers so it runs first each commit.
    const routeRef = useRef(routeCoordinates);
    useEffect(() => {
        routeRef.current = routeCoordinates;
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
    // Android rotation steps per tick (the rotation prop is not animatable on
    // a plain Marker). The spline bearing is C¹-continuous, so consecutive
    // steps are a few degrees — visually smooth at 2 steps/second.
    const [androidRotation, setAndroidRotation] = useState(
        heading != null && Number.isFinite(heading) && heading >= 0 ? heading : 0,
    );
    const resyncTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);


    // Single ingest path used by both the feed subscription and the prop
    // effect. Reads only refs — stable by construction.
    const ingestFix = useCallback((fix: MarkerFix) => {
        const now = Date.now();
        const coord = { latitude: fix.latitude, longitude: fix.longitude };
        if (shouldResetBuffer(bufferRef.current, coord, SNAP_DISTANCE_M)) {
            bufferRef.current.length = 0;
            hasMovementBearingRef.current = false;
            if (Platform.OS === 'android') {
                setAndroidCoord(coord);
                prevTargetRef.current = coord;
            }
        }
        const ts =
            Number.isFinite(fix.timestampMs) && Math.abs(now - fix.timestampMs) < 60_000
                ? fix.timestampMs
                : now;
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
            // otherwise render it raw (off-route/detour honesty).
            const snap = snapToRoute(p.coordinate, routeRef.current, MAX_ROUTE_SNAP_M);
            const target = snap?.coordinate ?? p.coordinate;

            const from = prevTargetRef.current;
            const movedM = distanceMeters(
                from.latitude, from.longitude, target.latitude, target.longitude,
            );
            if (movedM < 0.5 && p.mode !== 'interpolating' && p.mode !== 'extrapolating') {
                return; // parked — no animation churn, no rotation churn
            }

            // Bearing priority: route segment → spline tangent / direction of
            // travel → reported GPS heading (cold start only). See
            // selectBearing() for why movement outranks the reported heading
            // (Android's placeholder 0). The spline tangent (p.bearing) is
            // preferred over the chord between tick targets when available —
            // it rotates through turns instead of kinking at fixes.
            const selected = selectBearing({
                snap,
                movedMeters: movedM,
                from,
                to: target,
                heading: headingRef.current,
                hasMovementBearing: hasMovementBearingRef.current,
                minMoveMeters: MIN_BEARING_MOVE_M,
            });
            const bearing =
                selected.source === 'travel' && p.bearing != null ? p.bearing : selected.bearing;
            prevTargetRef.current = target;
            if (bearing != null) {
                if (selected.source === 'route' || selected.source === 'travel') {
                    hasMovementBearingRef.current = true;
                }
                if (isAndroid) {
                    setAndroidRotation(((bearing % 360) + 360) % 360);
                } else {
                    animateRotationTo(bearing, TICK_MS);
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
    useEffect(() => {
        // Hard cap: never re-snapshot indefinitely even if onLoad is lost.
        const cap = setTimeout(() => setTracksViewChanges(false), 5000);
        return () => {
            clearTimeout(cap);
            if (settleTimerRef.current) clearTimeout(settleTimerRef.current);
        };
    }, []);
    const handleImageLoaded = () => {
        // Image bitmap is decoded — keep tracking through one more frame so the
        // native Marker snapshot contains the car, then stop for perf.
        setTracksViewChanges(true);
        if (settleTimerRef.current) clearTimeout(settleTimerRef.current);
        settleTimerRef.current = setTimeout(() => setTracksViewChanges(false), 350);
    };

    // One-shot "pop in" on mount — see the class doc comment above for why
    // this is a single spring rather than a loop. Native-driven: opacity and
    // transform:scale both support the native driver, so this costs nothing
    // on the JS thread and doesn't compete with the (JS-driven, non-style)
    // rotation animation above.
    // eslint-disable-next-line react-hooks/refs
    const mountAnim = useRef(new Animated.Value(0)).current;
    useEffect(() => {
        Animated.spring(mountAnim, {
            toValue: 1,
            friction: 6,
            tension: 80,
            useNativeDriver: true,
        }).start();
        // mountAnim is a stable ref value; this must run once on mount only.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    // Custom marker failed to load (offline + cold cache, dead URL) — fall
    // back to the bundled variant. Reset when the URL changes so a fixed
    // upload is retried.
    const [imageFailed, setImageFailed] = useState(false);
    // eslint-disable-next-line react-hooks/set-state-in-effect
    useEffect(() => setImageFailed(false), [imageUri]);
    const useCustomImage = !!imageUri && !imageFailed;

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

    return (
        <MarkerComponent
            ref={markerRef}
            coordinate={isAndroid ? androidCoord : (animatedRegion as any)}
            anchor={{ x: 0.5, y: 0.5 }}
            flat
            rotation={isAndroid ? androidRotation : (rotationAnim as any)}
            tracksViewChanges={tracksViewChanges}
            zIndex={zIndex}
            identifier={identifier}
            style={{ backgroundColor: 'transparent' }}
        >
            {/* eslint-disable-next-line react-hooks/refs -- mountAnimatedStyle is a plain object computed above from the stable mountAnim ref value, not a fresh ref read */}
            <Animated.View style={mountAnimatedStyle}>
                <Image
                    source={useCustomImage ? { uri: imageUri as string } : CAR_IMAGES[variant]}
                    onError={() => { setImageFailed(true); setTracksViewChanges(true); }}
                    onLoad={handleImageLoaded}
                    resizeMode="contain"
                    style={{
                        width: size,
                        height: size,
                        backgroundColor: 'transparent',
                    }}
                />
            </Animated.View>
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
        prev.routeCoordinates === next.routeCoordinates
    );
}

export const CarMarker = React.memo(CarMarkerComponent, _propsAreEqual);

export default CarMarker;
