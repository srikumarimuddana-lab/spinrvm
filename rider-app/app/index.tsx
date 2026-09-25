import React, { useCallback, useEffect, useRef } from 'react';
import { ActivityIndicator, AppState, AppStateStatus, View } from 'react-native';
import { useRouter } from 'expo-router';
import { useAuthStore } from '@shared/store/authStore';
import { useRideStore } from '../store/rideStore';
import api from '@shared/api/client';
import { useTheme } from '@shared/theme/ThemeContext';

const RETRY_INTERVAL_MS = 5000;

export default function Index() {
  const router = useRouter();
  const { isInitialized, token, user, sessionRecoverable, initialize } = useAuthStore();
  const { colors } = useTheme();
  const retryingRef = useRef(false);

  // App Check (and other transient refresh failures) leave the 30-day login
  // in secure storage and set sessionRecoverable. Sending the rider to
  // /login here is the logout they see after the app has been in the background.
  const retryAuth = useCallback(async () => {
    if (retryingRef.current) return;
    if (!useAuthStore.getState().sessionRecoverable) return;
    retryingRef.current = true;
    try {
      await initialize();
    } finally {
      retryingRef.current = false;
    }
  }, [initialize]);

  useEffect(() => {
    if (!sessionRecoverable) return;
    void retryAuth();
    const id = setInterval(retryAuth, RETRY_INTERVAL_MS);
    const onAppState = (state: AppStateStatus) => {
      if (state === 'active') void retryAuth();
    };
    const sub = AppState.addEventListener('change', onAppState);
    return () => {
      clearInterval(id);
      sub.remove();
    };
  }, [sessionRecoverable, retryAuth]);

  useEffect(() => {
    if (!isInitialized) return;
    if (sessionRecoverable) return;

    (async () => {
      const hasProfileData = !!(user?.first_name && user?.last_name && user?.email);
      const profileComplete = !!user?.profile_complete || hasProfileData;

      if (!token) {
        router.replace('/login');
        return;
      }
      if (user && !profileComplete) {
        router.replace('/profile-setup');
        return;
      }

      try {
        await useRideStore.getState().hydrateActiveRide();
        const result = await useRideStore.getState().fetchActiveRide();
        if (result?.active && result.ride) {
          const { status, id: rideId } = result.ride;
          if (status === 'completed') {
            const ps = result.ride.payment_status;
            if (ps !== 'paid' && ps !== 'waived_admin') {
              router.replace({ pathname: '/ride-completed', params: { rideId } } as any);
              return;
            }
          } else if (status === 'in_progress') {
            router.replace({ pathname: '/ride-in-progress', params: { rideId } } as any);
            return;
          } else if (status === 'driver_arrived') {
            router.replace({ pathname: '/driver-arrived', params: { rideId } } as any);
            return;
          } else if (status === 'driver_assigned' || status === 'driver_accepted' || status === 'searching') {
            router.replace({ pathname: '/driver-arriving', params: { rideId } } as any);
            return;
          }
        }
      } catch {
        // fall through to home on error
      }
      // Legacy/re-consent notice: cold-start landing on home for an already-
      // authenticated, profile-complete rider with no active-ride redirect
      // above (so this never interrupts a rider mid-ride). Mirrors otp.tsx's
      // fresh-login check for a pre-existing account whose consent isn't
      // recorded — fail-open so a network hiccup never blocks entry to the
      // app. Dark-shipped: while app_settings.legacy_consent_notice_enabled
      // is off, /consent/status always reports needs_notice: false.
      api.get('/consent/status').then(
        (res) => {
          if ((res.data as any)?.needs_notice) {
            router.replace('/legacy-consent-notice' as any);
          } else {
            router.replace('/(tabs)');
          }
        },
        () => router.replace('/(tabs)'), // fail open — never block cold start on this check
      );
    })();
    // router is expo-router's stable singleton.
  }, [isInitialized, token, user, router, sessionRecoverable]);

  // Transparent pass-through — BrandSplash (in _layout.tsx) is the only
  // branded loading screen. This screen just routes; it has no visual chrome.
  return (
    <View style={{ flex: 1, backgroundColor: colors.background, alignItems: 'center', justifyContent: 'center' }}>
      {sessionRecoverable ? <ActivityIndicator color={colors.primary} accessibilityLabel="Reconnecting" /> : null}
    </View>
  );
}
