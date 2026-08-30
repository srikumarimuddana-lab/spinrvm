import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Animated, View } from 'react-native';
import { Image as ExpoImage } from 'expo-image';
import { AnimatedRegion, Marker } from 'react-native-maps';
import {
    distanceMeters,
    selectBearing,
    shortestArcRotationTarget,
    snapToRoute,
    type TrackingLatLng,
} from '../utils/vehicleTracking';
import {
    PLAYBACK_DELAY_MS,
    playbackPosition,
    pushFix,
    shouldResetBuffer,
    type PlaybackFix,
} from '../utils/markerPlayback';

const CAR_IMAGES = {
    standard: require('../assets/car_marker.png'),
    xl: require('../assets/car_marker_xl.png'),
    premium: require('../assets/car_marker_premium.png'),
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
 * with timestamps and the marker renders the car PLAYBACK_DELAY_MS in the
 * past, stepping through the queue every TICK_MS via AnimatedRegion.timing —
 * so motion is continuous instead of "arrive, park, leap" between fixes.
 * When the buffer runs dry (network gap) the position dead-reckons along the
 * last segment for a short capped window, then holds honestly. See
 * utils/markerPlayback.ts for the engine and the reasoning. AnimatedRegion
 * is driven on BOTH platforms (an earlier Android-only
 * animateMarkerToCoordinate path left the region stale and re-renders
 * snapped the car back to its mount position — "teleporting").
 *
 * Rotation: animated through an Animated.Value along the shortest arc, so
 * the car turns smoothly instead of snapping its angle. Bearing source
 * priority: route-segment direction (when `routeCoordinates` is provided and
 * the played-back position snaps onto the route) → direction of travel →
 * the reported GPS heading, used only until movement has established a
 * bearing. Movement deliberately outranks the reported heading; see
 * selectBearing() for why a reported heading of 0 cannot be trusted on
 * Android.
 */
const CarMarkerComponent: React.FC<CarMarkerProps> = ({
    coordinate,
    heading,
    size = 40,
    zIndex = 1,
    identifier,
    variant = 'standard',
    imageUri,
    routeCoordinates,
}) => {
    const markerRef = useRef<any>(null);
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
        if (shouldResetBuffer(bufferRef.current, coordinate, SNAP_DISTANCE_M)) {
            bufferRef.current.length = 0;
            hasMovementBearingRef.current = false;
        }
        pushFix(bufferRef.current, { ...coordinate, timestampMs: now }, now);
    }, [coordinate.latitude, coordinate.longitude, coordinate]);

    // ── Playback ticker: every TICK_MS, place the marker where the buffer
    // says the car was PLAYBACK_DELAY_MS ago and glide there over one tick.
    // Cheap when idle: a held position that hasn't moved re-targets nothing.
    useEffect(() => {
        const id = setInterval(() => {
            const p = playbackPosition(bufferRef.current, Date.now() - PLAYBACK_DELAY_MS);
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

            // Bearing priority: route segment → direction of travel → reported
            // GPS heading (cold start only). See selectBearing() for why
            // movement outranks the reported heading (Android's placeholder 0).
            const { bearing, source } = selectBearing({
                snap,
                movedMeters: movedM,
                from,
                to: target,
                heading: headingRef.current,
                hasMovementBearing: hasMovementBearingRef.current,
                minMoveMeters: MIN_BEARING_MOVE_M,
            });
            prevTargetRef.current = target;
            if (bearing != null) {
                if (source === 'route' || source === 'travel') hasMovementBearingRef.current = true;
                animateRotationTo(bearing, TICK_MS);
            }

            animatedRegion
                .timing({
                    latitude: target.latitude,
                    longitude: target.longitude,
                    duration: TICK_MS,
                    useNativeDriver: false,
                } as any)
                .start();
        }, TICK_MS);
        return () => clearInterval(id);
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

    // Custom marker failed to load (offline + cold cache, dead URL) — fall
    // back to the bundled variant. Reset when the URL changes so a fixed
    // upload is retried.
    const [imageFailed, setImageFailed] = useState(false);
    useEffect(() => setImageFailed(false), [imageUri]);
    const useCustomImage = !!imageUri && !imageFailed;

    return (
        <Marker.Animated
            ref={markerRef}
            coordinate={animatedRegion as any}
            anchor={{ x: 0.5, y: 0.5 }}
            flat
            rotation={rotationAnim as any}
            tracksViewChanges={tracksViewChanges}
            zIndex={zIndex}
            identifier={identifier}
            style={{ backgroundColor: 'transparent' }}
        >
            <View
                style={{
                    width: size,
                    height: size,
                    backgroundColor: 'transparent',
                    alignItems: 'center',
                    justifyContent: 'center',
                }}
            >
                <ExpoImage
                    source={useCustomImage ? { uri: imageUri as string } : CAR_IMAGES[variant]}
                    onError={() => { setImageFailed(true); setTracksViewChanges(true); }}
                    onLoad={handleImageLoaded}
                    contentFit="contain"
                    cachePolicy="disk"
                    style={{
                        width: size,
                        height: size,
                        backgroundColor: 'transparent',
                    }}
                />
            </View>
        </Marker.Animated>
    );
};

function _propsAreEqual(prev: CarMarkerProps, next: CarMarkerProps): boolean {
    return (
        prev.coordinate.latitude === next.coordinate.latitude &&
        prev.coordinate.longitude === next.coordinate.longitude &&
        prev.heading === next.heading &&
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
