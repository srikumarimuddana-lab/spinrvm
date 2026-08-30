import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Animated, View, Image } from 'react-native';
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

const CAR_IMAGES = {
    standard: require('../assets/images/car_marker.png'),
    xl: require('../assets/images/car_marker_xl.png'),
    premium: require('../assets/images/car_marker_premium.png'),
} as const;

export type CarMarkerVariant = keyof typeof CAR_IMAGES;

/**
 * Resolve the marker for a vehicle type. Prefers the admin-configured
 * vehicle_types.marker_variant (loaded from GET /vehicle-types); falls back
 * to name matching for older backends, then to the standard sedan.
 */
export function resolveMarkerVariant(
    markerVariant?: string | null,
    vehicleTypeName?: string | null,
): CarMarkerVariant {
    if (markerVariant && markerVariant in CAR_IMAGES) {
        return markerVariant as CarMarkerVariant;
    }
    const n = (vehicleTypeName ?? '').toLowerCase();
    if (n.includes('xl') || n.includes('van')) return 'xl';
    if (n.includes('premium') || n.includes('lux')) return 'premium';
    return 'standard';
}

interface CarMarkerProps {
    coordinate: {
        latitude: number;
        longitude: number;
    };
    heading?: number | null;
    isOnline?: boolean;
    size?: number;
    variant?: CarMarkerVariant;
    // Admin-uploaded custom marker (vehicle_types.marker_image_url),
    // prefetched by vehicleTypeStore. Takes precedence over `variant`;
    // falls back to the bundled variant image on load failure.
    imageUri?: string | null;
    // Route polyline the driver is navigating (driver→pickup or
    // pickup→dropoff). When provided, GPS fixes within MAX_ROUTE_SNAP_M snap
    // onto the route and the car orients along the road segment — correct
    // even while stationary, when expo-location reports heading -1.
    routeCoordinates?: readonly TrackingLatLng[] | null;
}

// Playback tick: the marker re-targets its position animation this often,
// stepping through the playback buffer (see @shared/utils/markerPlayback).
const TICK_MS = 500;
// Beyond this jump (stale fix after backgrounding) snap instantly.
const SNAP_DISTANCE_M = 500;
// Ignore sub-3m jitter when deriving the fallback bearing from movement.
const MIN_BEARING_MOVE_M = 3;
// Fix farther than this from the route polyline = off-route (detour, stale
// route); render the raw position instead of pretending the car is on-line.
const MAX_ROUTE_SNAP_M = 35;
// Cap on the rotation tween so the car visibly turns rather than snapping,
// without lagging a full position animation behind sharp turns.
const MAX_ROTATE_MS = 600;

/**
 * Top-down car marker with configurable size.
 *
 * We use a child <View><Image/></View> (not the native `image` prop) so we
 * can control the rendered size via the `size` prop — the native prop
 * renders at the PNG's physical dimensions which is way too large.
 *
 * Square/white bubble on Android: react-native-maps wraps custom child views
 * in a default callout-style container unless every wrapper layer (and the
 * Marker itself) has an explicit transparent background.
 *
 * Snapshot lifecycle: `tracksViewChanges` stays true until the car PNG has
 * actually decoded (Image onLoad) plus a short settle delay, then flips false
 * for perf. The previous fixed 800 ms timer raced slow image decodes (cold
 * start, low-end Android, the mapKey remount after each ride) and could
 * permanently snapshot an empty view — an invisible car. A hard cap stops
 * per-frame re-snapshots if onLoad never fires; a late onLoad re-arms one
 * final snapshot. We still never re-snapshot on isOnline changes — under
 * RN 0.85 Bridgeless that triggered a native cast crash (ReactViewGroup
 * cannot be cast to com.rnmaps.maps.i onLayoutChange).
 *
 * Movement: PLAYBACK BUFFER (the Lyft technique). Incoming fixes are queued
 * with timestamps and the marker renders the car PLAYBACK_DELAY_MS in the
 * past, stepping through the queue every TICK_MS via AnimatedRegion.timing —
 * continuous motion instead of "arrive, park, leap" between fixes; capped
 * dead reckoning covers short network gaps. Engine + reasoning:
 * @shared/utils/markerPlayback. AnimatedRegion is driven on BOTH platforms
 * (an earlier Android-only animateMarkerToCoordinate path left the region
 * stale and re-renders snapped the car back to its mount position).
 *
 * Rotation: animated through an Animated.Value along the shortest arc.
 * Bearing priority: route segment → direction of travel → reported GPS
 * heading (cold start only) — see selectBearing() for why movement outranks
 * the reported heading (Android's placeholder 0).
 */
export const CarMarker: React.FC<CarMarkerProps> = ({
    coordinate,
    heading,
    isOnline = true,
    size = 40,
    variant = 'standard',
    imageUri,
    routeCoordinates,
}) => {
    const markerRef = useRef<any>(null);
    // react-hooks/refs ("Cannot access refs during render"): AnimatedRegion
    // is the same kind of stable animation-driver object as Animated.Value
    // elsewhere in this app — mutated only via .timing() inside the
    // position-update effect below, read only in the JSX `coordinate` prop.
    // Verified safe — grepped this file for `.current =`: animatedRegion is
    // never reassigned after creation (the existing "animatedRegion is a
    // stable ref" comment a few lines down already documents this for the
    // exhaustive-deps suppression on the same value).
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
    // refresh (new array identity) re-snaps the NEXT fix without re-firing
    // the effect mid-glide.
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

    // ── Fix ingest: every coordinate prop change lands in the playback
    // buffer. A jump past SNAP_DISTANCE_M (stale fix after backgrounding)
    // resets the buffer so the marker snaps once instead of gliding across
    // the city; it also clears the movement-bearing latch, since the old
    // direction says nothing about the new location.
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
    // upload is retried. Adjusted directly during render (React's documented
    // "adjusting state when a prop changes" pattern) rather than via a
    // useEffect — same reset semantics, but avoids the one-render flash of
    // the stale imageFailed value that the effect version would show before
    // its post-commit cleanup pass corrected it.
    const [imageFailed, setImageFailed] = useState(false);
    const [prevImageUri, setPrevImageUri] = useState(imageUri);
    if (imageUri !== prevImageUri) {
        setPrevImageUri(imageUri);
        setImageFailed(false);
    }
    const useCustomImage = !!imageUri && !imageFailed;

    return (
        <Marker.Animated
            ref={markerRef}
            coordinate={animatedRegion as any}
            anchor={{ x: 0.5, y: 0.5 }}
            flat
            rotation={rotationAnim as any}
            tracksViewChanges={tracksViewChanges}
            zIndex={100}
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
                <Image
                    source={useCustomImage ? { uri: imageUri as string } : CAR_IMAGES[variant]}
                    onError={() => { setImageFailed(true); setTracksViewChanges(true); }}
                    onLoad={handleImageLoaded}
                    style={{
                        width: size,
                        height: size,
                        resizeMode: 'contain',
                        backgroundColor: 'transparent',
                    }}
                />
            </View>
        </Marker.Animated>
    );
};

export default CarMarker;
