import React, { useState, useEffect } from 'react';
import { View, Image } from 'react-native';
import { Marker } from 'react-native-maps';

interface CarMarkerProps {
    coordinate: {
        latitude: number;
        longitude: number;
    };
    heading?: number | null;
    size?: number;
    zIndex?: number;
    identifier?: string;
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
 * `tracksViewChanges` starts `true` so the native view catches the image
 * after it loads, then flips to `false` to avoid per-frame re-snapshots.
 */
const CarMarkerComponent: React.FC<CarMarkerProps> = ({
    coordinate,
    heading,
    size = 40,
    zIndex = 1,
    identifier,
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
                <Image
                    source={require('../assets/car_marker.png')}
                    style={{
                        width: size,
                        height: size,
                        resizeMode: 'contain',
                        backgroundColor: 'transparent',
                    }}
                />
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
        prev.identifier === next.identifier
    );
}

export const CarMarker = React.memo(CarMarkerComponent, _propsAreEqual);

export default CarMarker;
