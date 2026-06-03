import React, { useState, useEffect } from 'react';
import { View } from 'react-native';
import { Marker } from 'react-native-maps';
import { VehicleMarker } from '@shared/components/VehicleMarkers';

interface CarMarkerProps {
    coordinate: {
        latitude: number;
        longitude: number;
    };
    heading?: number | null;
    isOnline?: boolean;
    size?: number;
    /**
     * The driver's own vehicle tier — backend `icon`
     * (car-compact / car-sport / bus / bus-outline) or tier name.
     * Defaults to the economy sedan when omitted.
     */
    vehicleType?: string | null;
}

/**
 * Top-down car marker that renders a tier-specific SVG silhouette
 * (see @shared/components/VehicleMarkers). The `size` prop controls the
 * rendered dimensions; the car greys out while the driver is offline.
 *
 * Two gotchas this component solves:
 *
 * 1. Square/white bubble around the car on Android.
 *    react-native-maps wraps custom child views in a default callout-style
 *    container unless the child has an explicit transparent background.
 *    We set backgroundColor: 'transparent' on EVERY wrapper layer to kill
 *    it, and pass `style={{ backgroundColor: 'transparent' }}` to the
 *    Marker itself to suppress the native bubble.
 *
 * 2. Marker doesn't render until a location update arrives.
 *    `tracksViewChanges` starts as `true` so the native view snapshots the
 *    SVG once it paints, then flips to `false` so we don't re-snapshot
 *    on every frame (perf).
 */
export const CarMarker: React.FC<CarMarkerProps> = ({
    coordinate,
    heading,
    isOnline = true,
    size = 40,
    vehicleType,
}) => {
    // One-shot: keep tracksViewChanges true briefly on mount so Android's
    // Marker snapshots the child View once the image paints. We do NOT
    // re-enable on isOnline change because under RN 0.85 Bridgeless mode
    // the re-snapshot triggers a native cast crash (ReactViewGroup cannot
    // be cast to com.rnmaps.maps.i onLayoutChange).
    const [tracksViewChanges, setTracksViewChanges] = useState(true);
    useEffect(() => {
        const t = setTimeout(() => setTracksViewChanges(false), 800);
        return () => clearTimeout(t);
    }, []);

    return (
        <Marker
            coordinate={coordinate}
            anchor={{ x: 0.5, y: 0.5 }}
            flat
            rotation={heading ?? 0}
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
                <VehicleMarker
                    type={vehicleType}
                    size={size}
                    // Grey out the car while the driver is offline so the
                    // map reads "not dispatchable" at a glance.
                    color={isOnline ? undefined : '#9AA0A6'}
                />
            </View>
        </Marker>
    );
};

export default CarMarker;
