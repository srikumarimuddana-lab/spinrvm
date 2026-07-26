import React, { useCallback, useEffect, useRef, useState } from 'react';
import { AppState, AppStateStatus, View, Text, Pressable, ActivityIndicator, StyleSheet } from 'react-native';
import { useRouter, useNavigationContainerRef } from 'expo-router';
import { useAuthStore } from '@shared/store/authStore';
import { createLogger } from '@shared/utils/logger';

const log = createLogger('Index');

// Retry cadence while the session is recoverable. initialize() has no
// re-entry guard of its own, so retries are serialised through retryingRef —
// overlapping refreshes would re-open the rotation race that the
// single-refresh-owner fix closed.
const RETRY_INTERVAL_MS = 5000;
// Attempts before offering a manual way out. 3 × 5s ≈ 15s of silent retrying
// before the driver gets a button instead of a dead screen.
const ATTEMPTS_BEFORE_ESCAPE = 3;

export default function Index() {
  const router = useRouter();
  const navigationRef = useNavigationContainerRef();
  const { isInitialized, token, user, logout, sessionRecoverable, initialize } = useAuthStore();
  const hasNavigated = useRef(false);
  const retryingRef = useRef(false);
  const [attempts, setAttempts] = useState(0);

  // Single serialised retry. Safe to call from both the interval and the
  // AppState listener without stacking concurrent /auth/refresh calls.
  const retryAuth = useCallback(async () => {
    if (retryingRef.current) return;
    if (!useAuthStore.getState().sessionRecoverable) return;
    retryingRef.current = true;
    setAttempts((n) => n + 1);
    try {
      hasNavigated.current = false;
      await initialize();
    } catch (e) {
      log.warn('Recovery attempt failed', e);
    } finally {
      retryingRef.current = false;
    }
  }, [initialize]);

  // Timer-driven retry. The AppState listener alone was not enough: when the
  // transient failure happens while the app is already foregrounded (cold
  // launch on a weak connection) no AppState transition ever fires, which left
  // the driver stuck on this screen with no way forward.
  useEffect(() => {
    if (!sessionRecoverable) {
      setAttempts(0);
      return;
    }
    const id = setInterval(retryAuth, RETRY_INTERVAL_MS);
    return () => clearInterval(id);
  }, [sessionRecoverable, retryAuth]);

  // Resuming from background is a strong signal that connectivity changed —
  // retry immediately rather than waiting out the current interval tick.
  const handleAppStateChange = useCallback((state: AppStateStatus) => {
    if (state === 'active' && useAuthStore.getState().sessionRecoverable) {
      log.info('App resumed with recoverable session — retrying auth');
      retryAuth();
    }
  }, [retryAuth]);

  useEffect(() => {
    const sub = AppState.addEventListener('change', handleAppStateChange);
    return () => sub.remove();
  }, [handleAppStateChange]);

  const handleSignInInstead = useCallback(async () => {
    await logout();
    router.replace('/login' as any);
  }, [logout, router]);

  useEffect(() => {
    if (!isInitialized || hasNavigated.current) return;
    if (!navigationRef.isReady()) return;

    // Session is recoverable (transient failure, refresh token intact) —
    // show a reconnecting indicator instead of bouncing to /login.
    if (sessionRecoverable) return;

    hasNavigated.current = true;

    if (!token || !user) {
      router.replace('/login' as any);
      return;
    }

    const hasProfileData = !!(user.first_name && user.last_name && user.email);
    const profileComplete = !!user.profile_complete || hasProfileData;

    if (!profileComplete) {
      log.info('Profile incomplete, clearing session → /login');
      logout().then(() => {
        router.replace('/login' as any);
      });
    } else {
      router.replace('/driver/' as any);
    }
  }, [isInitialized, token, user, navigationRef.isReady(), sessionRecoverable]);

  if (sessionRecoverable) {
    return (
      <View style={styles.reconnecting}>
        <ActivityIndicator color="#6C63FF" />
        <Text style={styles.reconnectingText}>Reconnecting…</Text>
        {attempts >= ATTEMPTS_BEFORE_ESCAPE && (
          <>
            <Text style={styles.hintText}>Still having trouble connecting.</Text>
            <Pressable
              onPress={handleSignInInstead}
              accessibilityRole="button"
              accessibilityLabel="Sign in instead"
              style={styles.escapeButton}
            >
              <Text style={styles.escapeButtonText}>Sign in instead</Text>
            </Pressable>
          </>
        )}
      </View>
    );
  }

  // Transparent pass-through — BrandSplash (in _layout.tsx) is the only
  // branded loading screen. This screen just routes; it has no visual chrome.
  return <View style={{ flex: 1, backgroundColor: '#FFFFFF' }} />;
}

const styles = StyleSheet.create({
  reconnecting: {
    flex: 1,
    backgroundColor: '#FFFFFF',
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 32,
  },
  reconnectingText: {
    marginTop: 12,
    fontSize: 16,
    color: '#666',
  },
  hintText: {
    marginTop: 24,
    fontSize: 14,
    color: '#888',
    textAlign: 'center',
  },
  escapeButton: {
    marginTop: 12,
    paddingVertical: 10,
    paddingHorizontal: 20,
  },
  escapeButtonText: {
    fontSize: 16,
    fontWeight: '600',
    color: '#6C63FF',
  },
});
