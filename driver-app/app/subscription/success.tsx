import React, { useEffect, useRef, useState } from 'react';
import { View, Text, StyleSheet, ActivityIndicator } from 'react-native';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import api from '@shared/api/client';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { showToast } from '../../hooks/useToast';

/**
 * Stripe Checkout return landing screen.
 *
 * The subscription checkout's success_url resolves (via the backend bounce at
 * /drivers/subscription/checkout-return) to the app's custom scheme:
 *   spinr-driver://subscription/success?session_id=cs_xxx
 * Expo Router maps that deep link to the path `/subscription/success`, so this
 * file MUST exist — otherwise the router shows its built-in "Unmatched Route"
 * 404 (the bug this screen fixes).
 *
 * In the happy path WebBrowser.openAuthSessionAsync intercepts the redirect and
 * the subscription screen verifies inline, so this screen is never seen. It is a
 * defense-in-depth net for the cases where the OS hands the deep link to the
 * router instead (cold start, the in-app browser tab being dismissed, Android
 * Custom Tab quirks): verify the session, tell the driver where they stand, and
 * route them back into the Spinr Pass screen with fresh data.
 */
export default function SubscriptionSuccessScreen() {
  const router = useRouter();
  const { session_id } = useLocalSearchParams<{ session_id?: string }>();
  const { colors } = useTheme();
  const styles = createStyles(colors);
  const [verifying, setVerifying] = useState(true);
  // StrictMode / re-mount guard so we don't double-verify or double-redirect.
  const handledRef = useRef(false);

  useEffect(() => {
    if (handledRef.current) return;
    handledRef.current = true;

    let redirectTimer: ReturnType<typeof setTimeout>;

    const finish = () => {
      // Replace (not push) so the back gesture can't return to this transient
      // landing screen. /driver/subscription re-fetches plans + current pass.
      redirectTimer = setTimeout(() => router.replace('/driver/subscription'), 1200);
    };

    (async () => {
      const sessionId = Array.isArray(session_id) ? session_id[0] : session_id;
      if (!sessionId) {
        // No session id to verify (e.g. a stray open) — just bounce back.
        setVerifying(false);
        finish();
        return;
      }
      try {
        const res = await api.get<{ status?: string }>(
          `/drivers/subscription/verify-session?session_id=${encodeURIComponent(sessionId)}`,
        );
        if (res.data?.status === 'active') {
          showToast('success', 'Payment Successful!', 'Your Spinr Pass is now active. Go online and start earning!');
        } else {
          showToast('info', 'Processing...', 'Your payment is being confirmed. This may take a moment.');
        }
      } catch (e) {
        // Don't strand the driver on this screen if verify fails — the webhook
        // still activates the pass server-side; the subscription screen will
        // reflect the real state on its own reload.
        console.log('[SubscriptionSuccess] verify-session error:', e);
        showToast('info', 'Processing...', 'Your payment is being confirmed. This may take a moment.');
      } finally {
        setVerifying(false);
        finish();
      }
    })();

    return () => clearTimeout(redirectTimer);
  }, [session_id, router]);

  return (
    <View style={styles.container}>
      {verifying ? (
        <>
          <ActivityIndicator size="large" color={colors.primary} />
          <Text style={styles.title}>Confirming your payment…</Text>
          <Text style={styles.subtitle}>Just a moment.</Text>
        </>
      ) : (
        <>
          <Ionicons name="checkmark-circle" size={64} color="#10B981" />
          <Text style={styles.title}>All set</Text>
          <Text style={styles.subtitle}>Taking you back to your Spinr Pass…</Text>
        </>
      )}
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
    title: { fontSize: 20, fontWeight: '800', color: colors.text, marginTop: 20, textAlign: 'center' },
    subtitle: { fontSize: 14, color: colors.textDim, marginTop: 8, textAlign: 'center' },
  });
}
