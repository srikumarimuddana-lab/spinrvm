import React, { useEffect, useRef, useState } from 'react';
import { View, Platform } from 'react-native';
import { Image as ExpoImage } from 'expo-image';
import { AnimatedRegion, Marker } from 'react-native-maps';

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
 * Movement: position changes animate (AnimatedRegion timing on iOS,
 * animateMarkerToCoordinate on Android) so the car glides between GPS fixes.
 * When `heading` is missing/invalid (expo-location reports -1 standing still)
 * the car keeps its last bearing derived from the direction of travel.
 */
const CarMarkerComponent: React.FC<CarMarkerProps> = ({
    coordinate,
    heading,
    size = 40,
    zIndex = 1,
    identifier,
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
    const lastFixTsRef = useRef(Date.now());

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
        prev.imageUri === next.imageUri
    );
}

export const CarMarker = React.memo(CarMarkerComponent, _propsAreEqual);

export default CarMarker;
