import React, { useState, useEffect } from 'react';
import { View } from 'react-native';
import { Marker } from 'react-native-maps';
import { VehicleMarker } from './VehicleMarkers';

interface CarMarkerProps {
    coordinate: {
        latitude: number;
        longitude: number;
    };
    heading?: number | null;
    size?: number;
    zIndex?: number;
    identifier?: string;
    /**
     * Vehicle tier the driver is on — backend `icon`
     * (car-compact / car-sport / bus / bus-outline) or the tier name
     * (Economy / Premium / Van / XL). Picks which car silhouette to draw.
     * Defaults to the economy sedan when omitted.
     */
    vehicleType?: string | null;
}

/**
 * Top-down car marker that renders a tier-specific SVG silhouette
 * (see VehicleMarkers.tsx).
 *
 * Renders the car via a child <View> so `size` controls the rendered
 * dimensions and the marker rotates with the driver's heading.
 *
 * Transparent backgrounds are set on every wrapper layer (and on the Marker
 * itself) to kill the default Android callout-style bubble that
 * react-native-maps otherwise draws around custom child views.
 *
 * `tracksViewChanges` starts `true` so the native view catches the SVG
 * after it paints, then flips to `false` to avoid per-frame re-snapshots.
 */
const CarMarkerComponent: React.FC<CarMarkerProps> = ({
    coordinate,
    heading,
    size = 40,
    zIndex = 1,
    identifier,
    vehicleType,
}) => {
    // See driver-app CarMarker for why we keep tracking briefly instead of
    // flipping on Image.onLoad: flipping too fast races the Android
    // Marker snapshot and leaves an invisible marker.
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
                <VehicleMarker type={vehicleType} size={size} />
            </View>
        </Marker>
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
        prev.vehicleType === next.vehicleType
    );
}

export const CarMarker = React.memo(CarMarkerComponent, _propsAreEqual);

export default CarMarker;
