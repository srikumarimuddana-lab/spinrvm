import React, { useEffect, useMemo, useState } from 'react';
import { View, Text, StyleSheet, TouchableOpacity, ActivityIndicator, ScrollView } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { useRouter } from 'expo-router';
import api, { getApiErrorMessage } from '@shared/api/client';
import { showToast } from '../store/toastStore';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

/**
 * One-time notice for a rider whose account has no recorded consent to
 * Spinr's current data practices — either because it was created from the
 * previous app's records (legacy import) or because it predates Spinr's
 * consent-version tracking. Reached from otp.tsx's post-login redirect when
 * GET /consent/status reports needs_notice: true.
 *
 * Dark-shipped alongside its own flag (backend: app_settings.
 * legacy_consent_notice_enabled) — re-checks status on mount so this screen
 * self-heals if reached any other way while the flag is off or the user's
 * consent is already current: it redirects straight to the app instead of
 * showing a notice that isn't actually needed.
 *
 * See docs/change-log/2026-08-19-legacy-consent-notice.md for the full
 * design rationale (why this is generic re-consent, not import-only).
 */
export default function LegacyConsentNoticeScreen() {
  const router = useRouter();
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);
  const [checking, setChecking] = useState(true);
  const [accepting, setAccepting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await api.get('/consent/status');
        const needsNotice = !!(res.data as any)?.needs_notice;
        if (!cancelled && !needsNotice) {
          router.replace('/(tabs)' as any);
          return;
        }
      } catch {
        // Can't confirm status — fail open to the app rather than trap a
        // rider on a screen that failed to load, same posture as any other
        // best-effort check in this codebase (e.g. driverStore's late-tip
        // absorption metric: a monitoring/gating check must never block the
        // primary flow it's attached to).
        if (!cancelled) {
          router.replace('/(tabs)' as any);
          return;
        }
      }
      if (!cancelled) setChecking(false);
    })();
    return () => {
      cancelled = true;
    };
    // router is expo-router's stable singleton.
  }, [router]);

  const handleAccept = async () => {
    setAccepting(true);
    try {
      await api.post('/consent/accept');
      router.replace('/(tabs)' as any);
    } catch (err: any) {
      showToast('Something went wrong', getApiErrorMessage(err, 'Please try again.'), 'danger');
    } finally {
      setAccepting(false);
    }
  };

  const handleViewPolicy = () => {
    router.push('/legal' as any);
  };

  if (checking) {
    return (
      <SafeAreaView style={styles.container}>
        <View style={styles.loadingContainer}>
          <ActivityIndicator color={colors.primary} size="large" />
        </View>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.container}>
      <ScrollView
        contentContainerStyle={styles.content}
        keyboardShouldPersistTaps="handled"
        showsVerticalScrollIndicator={false}
        bounces={false}
      >
        <View style={styles.iconCircle}>
          <Ionicons name="document-text-outline" size={48} color={colors.primary} />
        </View>

        <Text style={styles.title}>An update on how we handle your information</Text>
        <Text style={styles.body}>
          Spinr Mobility Inc. is committed to protecting your privacy under Canada&apos;s federal
          privacy law (PIPEDA). Your account carries no record of having reviewed our current
          Privacy Policy and Terms of Service — this can happen if your account was created before
          we started tracking consent, or if it was carried over from an earlier version of Spinr.
        </Text>
        <Text style={styles.note}>
          Nothing about your account or how you use Spinr changes today. We just need your
          acknowledgment on file.
        </Text>

        <TouchableOpacity
          style={[styles.primaryBtn, accepting && styles.btnDisabled]}
          onPress={handleAccept}
          disabled={accepting}
          activeOpacity={0.85}
          accessibilityLabel="I understand, continue to Spinr"
        >
          {accepting ? (
            <ActivityIndicator color="#fff" size="small" />
          ) : (
            <Text style={styles.primaryBtnText}>I understand, continue</Text>
          )}
        </TouchableOpacity>

        <TouchableOpacity
          style={styles.secondaryBtn}
          onPress={handleViewPolicy}
          disabled={accepting}
          activeOpacity={0.7}
          accessibilityLabel="Read the full Privacy Policy and Terms of Service"
        >
          <Text style={styles.secondaryBtnText}>Read the full policy</Text>
        </TouchableOpacity>
      </ScrollView>
    </SafeAreaView>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: { flex: 1, backgroundColor: colors.surface },
    loadingContainer: { flex: 1, justifyContent: 'center', alignItems: 'center' },
    content: { flexGrow: 1, paddingHorizontal: 28, paddingVertical: 24, justifyContent: 'center', alignItems: 'center' },
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
