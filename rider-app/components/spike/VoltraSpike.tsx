/**
 * SPIKE — Phase 0 gate for the live-ride-activity plan (docs/live-ride-activity-plan.md).
 *
 * Throwaway iOS smoke test: proves Voltra (@use-voltra/ios-client) builds and
 * runs a trivial Live Activity on Expo 55 / RN 0.85 (New Architecture). NOT
 * production code — delete with the spike branch once the gate is decided.
 *
 * iOS-only: this module top-level-imports the iOS-only native client, so it must
 * only be `require`d on iOS (see app/spike-live-activity.tsx). Live Activities
 * need iOS 16.4+ on a physical device (simulator can render the Lock Screen
 * activity but not push). No backend push here — local start/update/end only.
 */
import React, { useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
// eslint-disable-next-line import/no-unresolved -- spike dep, present only on the Voltra spike branch
import { useLiveActivity, Voltra } from '@use-voltra/ios-client';

export default function VoltraSpike() {
    const [etaMin, setEtaMin] = useState(5);
    const [log, setLog] = useState<string>('idle');

    // Live Activity UI as React components — no Swift, no widget target.
    const lockScreen = (
        <Voltra.VStack style={{ padding: 16, borderRadius: 14, backgroundColor: '#111827' }}>
            <Voltra.Text style={{ color: '#FFFFFF', fontSize: 18, fontWeight: '700' }}>
                Driver on the way
            </Voltra.Text>
            <Voltra.Text style={{ color: '#9CA3AF', fontSize: 14 }}>
                ETA {etaMin} min · Spinr spike
            </Voltra.Text>
        </Voltra.VStack>
    );

    const { start, update, end } = useLiveActivity(
        { lockScreen },
        { activityName: 'spinr-spike', autoStart: false },
    );

    const run = (label: string, fn: () => unknown) => {
        try {
            fn();
            setLog(`${label}: ok`);
        } catch (e) {
            setLog(`${label}: ERROR ${String(e)}`);
        }
    };

    return (
        <View style={styles.root}>
            <Text style={styles.title}>Voltra Live Activity — spike</Text>
            <Text style={styles.sub}>ETA {etaMin} min</Text>

            <Pressable style={styles.btn} onPress={() => run('start', () => start())}>
                <Text style={styles.btnText}>Start activity</Text>
            </Pressable>
            <Pressable
                style={styles.btn}
                onPress={() => run('update', () => {
                    setEtaMin((m) => Math.max(0, m - 1));
                    update();
                })}
            >
                <Text style={styles.btnText}>Update (ETA −1)</Text>
            </Pressable>
            <Pressable style={[styles.btn, styles.end]} onPress={() => run('end', () => end())}>
                <Text style={styles.btnText}>End activity</Text>
            </Pressable>

            <Text style={styles.log}>last: {log}</Text>
        </View>
    );
}

const styles = StyleSheet.create({
    root: { flex: 1, padding: 24, gap: 12, justifyContent: 'center' },
    title: { fontSize: 20, fontWeight: '700', marginBottom: 4 },
    sub: { fontSize: 15, color: '#555', marginBottom: 12 },
    btn: { backgroundColor: '#6D28D9', padding: 14, borderRadius: 10, alignItems: 'center' },
    end: { backgroundColor: '#B91C1C' },
    btnText: { color: '#FFFFFF', fontSize: 16, fontWeight: '600' },
    log: { marginTop: 16, fontSize: 13, color: '#444' },
});
