import React, { useEffect, useState } from 'react';
import { View, StyleSheet, TouchableOpacity, ActivityIndicator } from 'react-native';
import { Text } from '@shared/components/Text';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { useTheme } from '@shared/theme/ThemeContext';
import api from '@shared/api/client';

/**
 * Shown after OTP when the driver row is banned or suspended.
 * Go-online already rejects these accounts; this screen keeps them off the
 * live dashboard. Appeal status comes from the existing appeals list.
 */
export default function AccountDeactivatedScreen() {
  const router = useRouter();
  const { colors } = useTheme();
  const params = useLocalSearchParams<{ status?: string }>();
  const [pendingAppeal, setPendingAppeal] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.get('/drivers/appeals').then(
      (res) => {
        if (cancelled) return;
        const rows = Array.isArray(res.data) ? res.data : (res.data as { appeals?: { status?: string }[] })?.appeals ?? [];
        setPendingAppeal(rows.some((row) => row?.status === 'pending'));
      },
      () => {
        if (!cancelled) setPendingAppeal(false);
      },
    );
    return () => {
      cancelled = true;
    };
  }, []);

  const status = params.status === 'banned' ? 'banned' : params.status === 'unknown' ? 'unknown' : 'suspended';
  const title = status === 'banned' ? 'Account banned' : status === 'unknown' ? 'Could not confirm your account' : 'Account suspended';

  return (
    <View style={[styles.wrap, { backgroundColor: colors.background }]}>
      <Text style={[styles.title, { color: colors.text }]}>{title}</Text>
      <Text style={[styles.body, { color: colors.textSecondary }]}>
        {status === 'unknown'
          ? 'We could not confirm whether this account can go online. Retry before opening the dashboard.'
          : pendingAppeal
          ? 'Your appeal is under review. You can keep using this screen to check the result.'
          : 'You cannot go online. You can submit an appeal for review.'}
      </Text>
      {pendingAppeal === null ? <ActivityIndicator color={colors.primary} /> : null}
      <TouchableOpacity
        accessibilityRole="button"
        accessibilityLabel="Open appeal"
        style={[styles.button, { backgroundColor: colors.primary }]}
        onPress={() => {
          if (status === 'unknown') {
            router.replace('/driver' as never);
            return;
          }
          router.push('/appeal' as never);
        }}
      >
        <Text style={styles.buttonText}>
          {status === 'unknown' ? 'Continue' : pendingAppeal ? 'View appeal' : 'Submit an appeal'}
        </Text>
      </TouchableOpacity>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { flex: 1, padding: 24, justifyContent: 'center' },
  title: { fontSize: 24, fontWeight: '700', marginBottom: 12 },
  body: { fontSize: 16, lineHeight: 22, marginBottom: 24 },
  button: { borderRadius: 12, paddingVertical: 14, alignItems: 'center' },
  buttonText: { color: '#fff', fontSize: 16, fontWeight: '600' },
});
