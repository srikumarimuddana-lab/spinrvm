/**
 * Accessibility settings — single toggle to surface the wheelchair-
 * accessible vehicle (WAV) option on the booking screen.
 *
 * The booking screen hides WAV by default so the list stays uncluttered
 * for the 99% of riders who don't need it (mirrors the Uber pattern).
 * Riders who need WAV flip this toggle once; from then on the WAV
 * vehicle option / requires_wav toggle is rendered on every ride-options
 * load. The backend dispatch path (filter_and_rank_drivers, fare
 * estimate, FCM offer payload) is unchanged — only presentational.
 */
import React, { useMemo } from 'react';
import { View, Text, StyleSheet, TouchableOpacity, ScrollView } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';

import CustomToggle from '../components/CustomToggle';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { useRideStore } from '../store/rideStore';

export default function AccessibilityScreen() {
  const router = useRouter();
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);

  const showWavOption = useRideStore((s) => s.showWavOption);
  const setShowWavOption = useRideStore((s) => s.setShowWavOption);

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      <View style={styles.header}>
        <TouchableOpacity style={styles.backBtn} onPress={() => router.back()}>
          <Ionicons name="arrow-back" size={24} color={colors.text} />
        </TouchableOpacity>
        <Text style={styles.headerTitle}>Accessibility</Text>
        <View style={{ width: 44 }} />
      </View>

      <ScrollView contentContainerStyle={styles.content}>
        <Text style={styles.sectionTitle}>VEHICLE PREFERENCES</Text>
        <View style={styles.card}>
          <View style={styles.row}>
            <View style={[styles.iconWrap, { backgroundColor: '#E0F2FE' }]}>
              <Ionicons name="accessibility" size={20} color="#0284C7" />
            </View>
            <View style={{ flex: 1 }}>
              <Text style={styles.rowTitle}>Show wheelchair-accessible options</Text>
              <Text style={styles.rowSubtitle}>
                Adds a WAV toggle to the booking screen. Drivers with WAV-equipped
                vehicles will be matched when you turn the toggle on for a ride.
              </Text>
            </View>
            <CustomToggle value={showWavOption} onValueChange={setShowWavOption} />
          </View>
        </View>

        <Text style={styles.footerText}>
          We hide this option by default to keep the booking screen simple. You can
          turn it on or off any time from this screen.
        </Text>
      </ScrollView>
    </SafeAreaView>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: { flex: 1, backgroundColor: colors.surface },
    header: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      paddingHorizontal: 16,
      paddingVertical: 12,
      borderBottomWidth: 1,
      borderBottomColor: colors.border,
    },
    backBtn: { width: 44, height: 44, justifyContent: 'center', alignItems: 'center' },
    headerTitle: { fontSize: 18, fontWeight: '700', color: colors.text },
    content: { padding: 20, paddingBottom: 40 },
    sectionTitle: {
      fontSize: 13,
      fontWeight: '700',
      color: colors.textDim,
      letterSpacing: 0.5,
      marginBottom: 8,
      marginTop: 16,
    },
    card: { backgroundColor: colors.surfaceLight, borderRadius: 16, paddingHorizontal: 14 },
    row: { flexDirection: 'row', alignItems: 'center', paddingVertical: 14 },
    iconWrap: {
      width: 40,
      height: 40,
      borderRadius: 12,
      justifyContent: 'center',
      alignItems: 'center',
      marginRight: 14,
    },
    rowTitle: { fontSize: 15, fontWeight: '600', color: colors.text },
    rowSubtitle: { fontSize: 12, color: colors.textDim, marginTop: 2, marginRight: 12 },
    footerText: {
      fontSize: 12,
      color: colors.textDim,
      textAlign: 'center',
      marginTop: 24,
      lineHeight: 18,
    },
  });
}
