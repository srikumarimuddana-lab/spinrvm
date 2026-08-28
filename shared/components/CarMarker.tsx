import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Animated, View } from 'react-native';
import { Image as ExpoImage } from 'expo-image';
import { AnimatedRegion, Marker } from 'react-native-maps';
import {
    bearingDegrees,
    distanceMeters,
    shortestArcRotationTarget,
    snapToRoute,
    type TrackingLatLng,
} from '../utils/vehicleTracking';

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

// Position updates arrive every ~3-10 s (WS pings / GPS watch). Glide the car
// between fixes over the observed gap so it moves like Uber/Lyft instead of
// teleporting, but clamp so a delayed ping doesn't produce a minutes-long crawl.
const MIN_ANIM_MS = 300;
const MAX_ANIM_MS = 3000;
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
 * Movement: position changes animate via AnimatedRegion.timing on BOTH
 * platforms. The previous split (AnimatedRegion on iOS,
 * animateMarkerToCoordinate on Android) left the Android AnimatedRegion
 * frozen at the mount coordinate — any re-render (heading change, parent
 * update) re-applied that stale coordinate to the native marker, snapping
 * the car back to where it first appeared before the next animation pulled
 * it forward again ("teleporting"). Driving the region itself keeps the
 * coordinate prop truthful on every render.
 *
 * Rotation: animated through an Animated.Value along the shortest arc, so
 * the car turns smoothly instead of snapping its angle. Bearing source
 * priority: route-segment direction (when `routeCoordinates` is provided and
 * the fix snaps onto the route) → GPS heading (when valid; expo-location
 * reports -1 standing still) → direction of travel between fixes.
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
    const lastFixTsRef = useRef(Date.now());

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
        if (bearing != null) animateRotationTo(bearing, duration);

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
        animateRotationTo(heading as number, MAX_ROTATE_MS);
        // animateRotationTo and the route ref are stable.
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
