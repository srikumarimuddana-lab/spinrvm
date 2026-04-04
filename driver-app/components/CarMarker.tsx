import React, { useState, useEffect } from 'react';
import { View, Image } from 'react-native';
import { Marker } from 'react-native-maps';

interface CarMarkerProps {
    coordinate: {
        latitude: number;
        longitude: number;
    };
    heading?: number | null;
    isOnline?: boolean;
    size?: number;
}

/**
 * Top-down car marker with configurable size.
 *
 * We use a child <View><Image/></View> (not the native `image` prop) so we
 * can control the rendered size via the `size` prop — the native prop
 * renders at the PNG's physical dimensions which is way too large.
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
 *    `tracksViewChanges` starts as `true` so the native view catches the
 *    image once it loads, then flips to `false` so we don't re-snapshot
 *    on every frame (perf).
 */
export const CarMarker: React.FC<CarMarkerProps> = ({
    coordinate,
    heading,
    isOnline = true,
    size = 40,
}) => {
    // Keep tracking view changes briefly so Android's Marker has time to
    // snapshot the child View once the image paints. Flipping this too
    // early (e.g. the instant onLoad fires) can race the snapshot and
    // produce an invisible marker — especially with small/cached images.
    const [tracksViewChanges, setTracksViewChanges] = useState(true);
    useEffect(() => {
        const t = setTimeout(() => setTracksViewChanges(false), 800);
        return () => clearTimeout(t);
    }, []);
    const opacity = isOnline ? 1 : 0.6;

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
                    opacity,
                }}
            >
                <Image
                    source={require('../assets/images/car_marker.png')}
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

export default CarMarker;
