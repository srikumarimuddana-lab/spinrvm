/**
 * SPIKE route — Phase 0 gate, live-ride-activity plan. Navigate to
 * `/spike-live-activity` in the Dev Client to smoke-test Voltra on iOS.
 * Throwaway: delete with the spike branch. NOT linked from any production nav.
 *
 * The Voltra client is iOS-only, so we `require` the spike component only on iOS
 * (a top-level import would pull an iOS-only TurboModule into the Android bundle
 * and crash at eval). On Android this screen just explains it's iOS-only.
 */
import React from 'react';
import { Platform, StyleSheet, Text, View } from 'react-native';

let VoltraSpike: React.ComponentType | null = null;
if (Platform.OS === 'ios') {
    // eslint-disable-next-line @typescript-eslint/no-require-imports -- iOS-only lazy load
    VoltraSpike = require('../components/spike/VoltraSpike').default;
}

export default function SpikeLiveActivityScreen() {
    if (VoltraSpike) return <VoltraSpike />;
    return (
        <View style={styles.root}>
            <Text style={styles.text}>
                Voltra Live Activity spike is iOS-only. Open this route on an iOS 16.4+ device.
            </Text>
        </View>
    );
}

const styles = StyleSheet.create({
    root: { flex: 1, padding: 24, justifyContent: 'center', alignItems: 'center' },
    text: { fontSize: 15, color: '#444', textAlign: 'center' },
});
