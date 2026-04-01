import React from 'react';
import { View, StyleSheet } from 'react-native';
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
 * Top-down white car marker — clean, minimal, works inside map Markers.
 * Pure View-based (no images/SVGs) for reliable rendering.
 */
export const CarMarker: React.FC<CarMarkerProps> = ({
    coordinate,
    heading,
    isOnline = true,
    size = 6,
}) => {
    const w = size * 2;
    const h = size * 3.2;
    const color = isOnline ? '#FFFFFF' : '#D1D5DB';

    return (
        <Marker
            coordinate={coordinate}
            anchor={{ x: 0.5, y: 0.5 }}
            flat
            rotation={heading ?? 0}
            tracksViewChanges={false}
            zIndex={100}
        >
            <View style={{ width: w + 12, height: h + 12, alignItems: 'center', justifyContent: 'center' }}>
                {/* Shadow */}
                <View style={[styles.shadow, { width: w - 2, height: h - 2, borderRadius: w * 0.38 }]} />
                {/* Body */}
                <View style={[styles.body, { width: w, height: h, borderRadius: w * 0.4, backgroundColor: color }]}>
                    {/* Headlights */}
                    <View style={[styles.hl, { top: 3, left: 3 }]} />
                    <View style={[styles.hl, { top: 3, right: 3 }]} />
                    {/* Windshield */}
                    <View style={[styles.glass, { top: h * 0.16, width: w * 0.65, height: h * 0.22, borderRadius: w * 0.15 }]} />
                    {/* Roof */}
                    <View style={[styles.roof, { top: h * 0.42, width: w * 0.72, height: h * 0.22, borderRadius: w * 0.1 }]} />
                    {/* Rear window */}
                    <View style={[styles.rearGlass, { bottom: h * 0.14, width: w * 0.58, height: h * 0.15, borderRadius: w * 0.12 }]} />
                    {/* Taillights */}
                    <View style={[styles.tl, { bottom: 3, left: 4 }]} />
                    <View style={[styles.tl, { bottom: 3, right: 4 }]} />
                    {/* Mirrors */}
                    <View style={[styles.mirror, { top: h * 0.32, left: -3, backgroundColor: color }]} />
                    <View style={[styles.mirror, { top: h * 0.32, right: -3, backgroundColor: color }]} />
                </View>
            </View>
        </Marker>
    );
};

const styles = StyleSheet.create({
    shadow: {
        position: 'absolute',
        backgroundColor: 'rgba(0,0,0,0.25)',
        transform: [{ translateY: 2 }],
    },
    body: {
        alignItems: 'center',
        elevation: 6,
        shadowColor: '#000',
        shadowOffset: { width: 0, height: 3 },
        shadowOpacity: 0.3,
        shadowRadius: 4,
        borderWidth: 0.5,
        borderColor: 'rgba(0,0,0,0.1)',
    },
    glass: { position: 'absolute', backgroundColor: '#5BADE6', alignSelf: 'center' },
    rearGlass: { position: 'absolute', backgroundColor: '#5BADE6', opacity: 0.8, alignSelf: 'center' },
    roof: { position: 'absolute', backgroundColor: 'rgba(0,0,0,0.04)', alignSelf: 'center' },
    hl: { position: 'absolute', width: 5, height: 3, borderRadius: 1.5, backgroundColor: '#F5D560' },
    tl: { position: 'absolute', width: 5, height: 3, borderRadius: 1.5, backgroundColor: '#E05050' },
    mirror: { position: 'absolute', width: 4, height: 7, borderRadius: 2, elevation: 2, borderWidth: 0.3, borderColor: 'rgba(0,0,0,0.1)' },
});

export default CarMarker;
