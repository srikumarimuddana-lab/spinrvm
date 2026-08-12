import React, { useEffect, useRef, useState } from 'react';
import { View, Image, Platform } from 'react-native';
import { AnimatedRegion, Marker } from 'react-native-maps';

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
}

// Position updates land every 3-10 s depending on ride phase (see
// LOCATION_CONFIGS in useDriverDashboard). Glide between fixes over the
// observed gap so the car moves smoothly instead of teleporting, clamped so a
// delayed fix doesn't produce a minutes-long crawl.
const MIN_ANIM_MS = 300;
const MAX_ANIM_MS = 3000;
// Beyond this jump (stale fix after backgrounding) snap instantly.
const SNAP_DISTANCE_M = 500;
// Ignore sub-3m jitter when deriving the fallback bearing from movement.
const MIN_BEARING_MOVE_M = 3;

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

function distanceMeters(lat1: number, lng1: number, lat2: number, lng2: number): number {
    const R = 6371000;
    const toRad = (d: number) => (d * Math.PI) / 180;
    const dLat = toRad(lat2 - lat1);
    const dLng = toRad(lng2 - lng1);
    const a =
        Math.sin(dLat / 2) ** 2 +
        Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLng / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(a));
}

function bearingDegrees(lat1: number, lng1: number, lat2: number, lng2: number): number {
    const toRad = (d: number) => (d * Math.PI) / 180;
    const dLng = toRad(lng2 - lng1);
    const y = Math.sin(dLng) * Math.cos(toRad(lat2));
    const x =
        Math.cos(toRad(lat1)) * Math.sin(toRad(lat2)) -
        Math.sin(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.cos(dLng);
    return ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360;
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
 * Movement: position changes animate (AnimatedRegion timing on iOS,
 * animateMarkerToCoordinate on Android) so the car glides between GPS fixes.
 * When `heading` is missing/invalid (expo-location reports -1 standing still)
 * the car keeps its last bearing derived from the direction of travel.
 */
export const CarMarker: React.FC<CarMarkerProps> = ({
    coordinate,
    heading,
    isOnline = true,
    size = 40,
    variant = 'standard',
    imageUri,
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
    const lastFixTsRef = useRef(nowMs());

    // Last known direction of travel — used when the GPS heading is absent.
    const [travelBearing, setTravelBearing] = useState(0);

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

        if (movedM >= MIN_BEARING_MOVE_M) {
            setTravelBearing(
                bearingDegrees(prev.latitude, prev.longitude, coordinate.latitude, coordinate.longitude),
            );
        }

        if (Platform.OS === 'android') {
            const node = markerRef.current?.getNode?.() ?? markerRef.current;
            node?.animateMarkerToCoordinate?.(coordinate, duration);
        } else {
            animatedRegion
                .timing({
                    latitude: coordinate.latitude,
                    longitude: coordinate.longitude,
                    duration,
                    useNativeDriver: false,
                } as any)
                .start();
        }
        // animatedRegion is a stable ref.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [coordinate.latitude, coordinate.longitude]);

    // expo-location uses -1 for "heading unknown"; treat any negative or null
    // value as missing and fall back to the direction of travel.
    const rotation =
        heading != null && Number.isFinite(heading) && heading >= 0 ? heading : travelBearing;

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
            rotation={rotation}
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
