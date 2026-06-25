import React, { useEffect, useRef } from 'react';
import { View, Text, StyleSheet, ActivityIndicator } from 'react-native';
import { useRouter } from 'expo-router';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

/**
 * Stripe Checkout cancel landing screen.
 *
 * The checkout's cancel_url resolves to spinr-driver://subscription/cancel,
 * which Expo Router maps to `/subscription/cancel`. Without this file the
 * router would show its "Unmatched Route" 404 when a driver backs out of
 * checkout. No payment happened, so just route back to the Spinr Pass screen.
 */
export default function SubscriptionCancelScreen() {
  const router = useRouter();
  const { colors } = useTheme();
  const styles = createStyles(colors);
  const handledRef = useRef(false);

  useEffect(() => {
    if (handledRef.current) return;
    handledRef.current = true;
    const t = setTimeout(() => router.replace('/driver/subscription'), 600);
    return () => clearTimeout(t);
  }, [router]);

  return (
    <View style={styles.container}>
      <ActivityIndicator size="large" color={colors.primary} />
      <Text style={styles.subtitle}>Taking you back…</Text>
    </View>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: {
      flex: 1,
      backgroundColor: colors.surface,
      justifyContent: 'center',
      alignItems: 'center',
      padding: 32,
    },
    subtitle: { fontSize: 14, color: colors.textDim, marginTop: 16, textAlign: 'center' },
  });
}
