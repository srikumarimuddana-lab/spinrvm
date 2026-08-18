import React, { useMemo, useState } from 'react';
import { View, Text, StyleSheet, TouchableOpacity, ActivityIndicator } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { useRouter, useLocalSearchParams } from 'expo-router';
import api, { getApiErrorMessage } from '@shared/api/client';
import { useAuthStore, type User } from '@shared/store/authStore';
import { showToast } from '../hooks/useToast';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

/**
 * Self-serve reactivation any time before the 7-year deletion retention
 * ceiling (Saskatchewan Transportation Act / PIPEDA lawful-retention carve-out).
 * Reached from the OTP screen when /auth/verify-otp returns requires_reactivation
 * for a pending_deletion account. Confirming calls /auth/reactivate, which clears
 * the pending deletion and logs the driver back in.
 */
export default function ReactivateAccountScreen() {
  const router = useRouter();
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);
  const { reactivationToken, deletionScheduledAt } = useLocalSearchParams<{
    reactivationToken: string;
    deletionScheduledAt: string;
  }>();
  const [busy, setBusy] = useState(false);

  const scheduledLabel = useMemo(() => {
    if (!deletionScheduledAt) return null;
    const d = new Date(deletionScheduledAt);
    return isNaN(d.getTime())
      ? null
      : d.toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' });
  }, [deletionScheduledAt]);

  const goToApp = (userData?: User) => {
    const hasProfileData = !!(userData?.first_name && userData?.last_name && userData?.email);
    if (userData?.profile_complete || hasProfileData) {
      router.replace('/driver' as any);
    } else {
      router.replace('/profile-setup' as any);
    }
  };

  const handleReactivate = async () => {
    if (!reactivationToken) {
      showToast('error', 'Reactivation Failed', 'Missing reactivation token. Please sign in again.');
      router.replace('/login' as any);
      return;
    }
    setBusy(true);
    try {
      const res = await api.post('/auth/reactivate', { reactivation_token: reactivationToken });
      const data = res.data as any;
      const { token, refresh_token, expires_in, user: userData } = data;
      if (token) {
        await useAuthStore.getState().setTokens(token, refresh_token ?? '', expires_in ?? 900);
      }
      if (userData) {
        useAuthStore.setState({ user: userData, isInitialized: true, isLoading: false });
      } else {
        await useAuthStore.getState().initialize();
      }
      showToast('success', 'Welcome back', 'Your account has been reactivated.');
      goToApp(userData);
    } catch (err: any) {
      const status = err?.response?.status;
      if (status === 410) {
        showToast('error', 'Account Deleted', 'This account has been permanently deleted. Please create a new one.');
        router.replace('/login' as any);
      } else {
        showToast('error', 'Reactivation Failed', getApiErrorMessage(err, 'Could not reactivate. Please try again.'));
      }
    } finally {
      setBusy(false);
    }
  };

  const handleKeepDeleted = () => {
    router.replace('/login' as any);
  };

  return (
    <SafeAreaView style={styles.container}>
      <View style={styles.content}>
        <View style={styles.iconCircle}>
          <Ionicons name="refresh-circle-outline" size={48} color={colors.primary} />
        </View>

        <Text style={styles.title}>Reactivate your account?</Text>
        <Text style={styles.body}>
          This account is scheduled for deletion
          {scheduledLabel ? ` on ${scheduledLabel}` : ' soon'}. You can reactivate it now to keep driving with Spinr.
        </Text>
        <Text style={styles.note}>
          Note: ride and earnings history removed when you requested deletion cannot be restored.
        </Text>

        <TouchableOpacity
          style={[styles.primaryBtn, busy && styles.btnDisabled]}
          onPress={handleReactivate}
          disabled={busy}
          activeOpacity={0.85}
          accessibilityLabel="Reactivate my account"
        >
          {busy ? (
            <ActivityIndicator color="#fff" size="small" />
          ) : (
            <Text style={styles.primaryBtnText}>Reactivate my account</Text>
          )}
        </TouchableOpacity>

        <TouchableOpacity
          style={styles.secondaryBtn}
          onPress={handleKeepDeleted}
          disabled={busy}
          activeOpacity={0.7}
          accessibilityLabel="Keep my account scheduled for deletion"
        >
          <Text style={styles.secondaryBtnText}>Keep it deleted</Text>
        </TouchableOpacity>
      </View>
    </SafeAreaView>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: { flex: 1, backgroundColor: colors.surface },
    content: { flex: 1, paddingHorizontal: 28, justifyContent: 'center', alignItems: 'center' },
    iconCircle: {
      width: 96,
      height: 96,
      borderRadius: 48,
      backgroundColor: `${colors.primary}14`,
      justifyContent: 'center',
      alignItems: 'center',
      marginBottom: 28,
    },
    title: { fontSize: 24, fontWeight: '800', color: colors.text, textAlign: 'center', marginBottom: 12 },
    body: { fontSize: 15, color: colors.textDim, textAlign: 'center', lineHeight: 22, marginBottom: 12 },
    note: { fontSize: 12, color: colors.textDim, textAlign: 'center', lineHeight: 18, marginBottom: 32 },
    primaryBtn: {
      width: '100%',
      backgroundColor: colors.primary,
      borderRadius: 16,
      height: 56,
      justifyContent: 'center',
      alignItems: 'center',
      marginBottom: 14,
    },
    btnDisabled: { opacity: 0.6 },
    primaryBtnText: { color: '#fff', fontSize: 16, fontWeight: '700' },
    secondaryBtn: { height: 48, justifyContent: 'center', alignItems: 'center' },
    secondaryBtnText: { color: colors.textDim, fontSize: 15, fontWeight: '600' },
  });
}
