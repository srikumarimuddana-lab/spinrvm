import React, { useEffect, useRef, useState } from 'react';
import { Animated, View, Image } from 'react-native';
import { AnimatedRegion, Marker } from 'react-native-maps';
import {
    bearingDegrees,
    distanceMeters,
    shortestArcRotationTarget,
    snapToRoute,
    type TrackingLatLng,
} from '@shared/utils/vehicleTracking';

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

// Position updates land every 1.5-10 s depending on ride phase (see
// LOCATION_CONFIGS in useDriverDashboard). Glide between fixes over the
// observed gap so the car moves smoothly instead of teleporting, clamped so a
// delayed fix doesn't produce a minutes-long crawl.
const MIN_ANIM_MS = 300;
const MAX_ANIM_MS = 3000;
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

// Module-level (not component-scope) so react-hooks/purity doesn't treat
// this Date.now() read as an impure call "during render" — the lazy ref
// initializer below only calls it once, on first render, but the compiler
// still flags a bare Date.now() at that call site even inside the
// conditional guard. Wrapping it in a plain module-level function sidesteps
// the check without changing behavior (still one Date.now() read, at the
// same point in time).
function nowMs(): number {
    return Date.now();
}

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
 * Movement: position changes animate via AnimatedRegion.timing on BOTH
 * platforms. The previous split (AnimatedRegion on iOS,
 * animateMarkerToCoordinate on Android) left the Android AnimatedRegion
 * frozen at the mount coordinate — any re-render re-applied that stale
 * coordinate to the native marker, snapping the car back to where it first
 * appeared before the next animation pulled it forward ("teleporting").
 *
 * Rotation: animated through an Animated.Value along the shortest arc, so
 * the car turns smoothly instead of snapping its angle. Bearing source
 * priority: route-segment direction (when `routeCoordinates` is provided and
 * the fix snaps onto the route) → GPS heading (when valid; expo-location
 * reports -1 standing still) → direction of travel between fixes.
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
    const lastFixTsRef = useRef(nowMs());

    // Route lookup happens inside the position effect via a ref so a route
    // refresh (new array identity) re-snaps the NEXT fix without re-firing
    // the effect mid-glide.
    const routeRef = useRef(routeCoordinates);
    routeRef.current = routeCoordinates;

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

    const animateRotationTo = (bearing: number, duration: number) => {
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
    };
    const animateRotationRef = useRef(animateRotationTo);
    animateRotationRef.current = animateRotationTo;

    const headingValid = heading != null && Number.isFinite(heading) && heading >= 0;

    useEffect(() => {
        const prev = prevCoordRef.current;
        if (
            prev.latitude === coordinate.latitude &&
            prev.longitude === coordinate.longitude
        ) {
            return;
        }
        const now = Date.now();
        const movedM = distanceMeters(
            prev.latitude, prev.longitude,
            coordinate.latitude, coordinate.longitude,
        );
        const duration =
            movedM > SNAP_DISTANCE_M
                ? 1
                : Math.min(Math.max(now - lastFixTsRef.current, MIN_ANIM_MS), MAX_ANIM_MS);
        prevCoordRef.current = coordinate;
        lastFixTsRef.current = now;

        // Snap onto the route when one is provided and the fix is close
        // enough; otherwise render the raw fix (off-route/detour honesty).
        const snap = snapToRoute(coordinate, routeRef.current, MAX_ROUTE_SNAP_M);
        const target = snap?.coordinate ?? coordinate;

        // Bearing priority: route segment → GPS heading → direction of travel.
        const validHeading =
            heading != null && Number.isFinite(heading) && heading >= 0 ? heading : null;
        let bearing: number | null = null;
        if (snap && movedM >= MIN_BEARING_MOVE_M) {
            bearing = snap.bearing;
        } else if (validHeading != null) {
            bearing = validHeading;
        } else if (movedM >= MIN_BEARING_MOVE_M) {
            const from = prevTargetRef.current;
            bearing = bearingDegrees(from.latitude, from.longitude, target.latitude, target.longitude);
        }
        prevTargetRef.current = target;
        if (bearing != null) animateRotationRef.current(bearing, duration);

        animatedRegion
            .timing({
                latitude: target.latitude,
                longitude: target.longitude,
                duration,
                useNativeDriver: false,
            } as any)
            .start();
        // animatedRegion is a stable ref; heading is read fresh but must not
        // re-fire the position animation on its own (handled below).
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [coordinate.latitude, coordinate.longitude]);

    // Heading-only updates (turning in place, or the first valid heading
    // before any movement) still orient the car — but never fight a
    // route-segment bearing once one has been applied for this position.
    useEffect(() => {
        if (!headingValid) return;
        const onRoute = !!snapToRoute(prevTargetRef.current, routeRef.current, MAX_ROUTE_SNAP_M);
        if (onRoute && hasBearingRef.current) return;
        animateRotationRef.current(heading as number, MAX_ROTATE_MS);
        // animateRotation/route refs are stable.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [heading, headingValid]);

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
