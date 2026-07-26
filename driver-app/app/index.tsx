import React, { useCallback, useEffect, useRef } from 'react';
import { AppState, AppStateStatus, View, Text, StyleSheet } from 'react-native';
import { useRouter, useNavigationContainerRef } from 'expo-router';
import { useAuthStore } from '@shared/store/authStore';
import { createLogger } from '@shared/utils/logger';

const log = createLogger('Index');

export default function Index() {
  const router = useRouter();
  const navigationRef = useNavigationContainerRef();
  const { isInitialized, token, user, logout, sessionRecoverable, initialize } = useAuthStore();
  const hasNavigated = useRef(false);

  // Retry auth on AppState resume when session is recoverable (transient
  // refresh failure with a valid refresh token still in SecureStore).
  const handleAppStateChange = useCallback((state: AppStateStatus) => {
    if (state === 'active' && useAuthStore.getState().sessionRecoverable) {
      log.info('App resumed with recoverable session — retrying auth');
      hasNavigated.current = false;
      initialize();
    }
  }, [initialize]);

  useEffect(() => {
    const sub = AppState.addEventListener('change', handleAppStateChange);
    return () => sub.remove();
  }, [handleAppStateChange]);

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
        <Text style={styles.reconnectingText}>Reconnecting…</Text>
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
  },
  reconnectingText: {
    fontSize: 16,
    color: '#666',
  },
});
