import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Stack, useRouter } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { View, ActivityIndicator, StyleSheet, Text, Platform } from 'react-native';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { useFonts, PlusJakartaSans_400Regular, PlusJakartaSans_500Medium, PlusJakartaSans_600SemiBold, PlusJakartaSans_700Bold } from '@expo-google-fonts/plus-jakarta-sans';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import * as SplashScreen from 'expo-splash-screen';

// Keep the native splash up until our React-rendered splash is mounted,
// otherwise the driver home (which contains the SOSButton) momentarily
// flashes through during the boot transition.
SplashScreen.preventAutoHideAsync().catch(() => {});
import Constants, { ExecutionEnvironment } from 'expo-constants';
import { useAuthStore } from '@shared/store/authStore';
import { useLocationStore } from '@shared/store/locationStore';
import { useDriverStore } from '../store/driverStore';
import SpinrConfig from '@shared/config/spinr.config';
import { ErrorBoundary } from '@shared/components/ErrorBoundary';
import { OfflineBanner } from '@shared/components/OfflineBanner';
import { ThemeProvider, useTheme } from '@shared/theme/ThemeContext';
import { PersistQueryClientProvider } from '@tanstack/react-query-persist-client';
import { queryClient, asyncStoragePersister, QUERY_CACHE_BUSTER } from '@shared/api/queryClient';
import { captureMessage, setUser } from '@shared/services/errorReporting';
import {
  initFirebaseServices,
  requestPushPermissionAndGetToken,
  setBackgroundMessageHandler,
  onTokenRefresh,
} from '@shared/services/firebase';

// expo-notifications' push-token APIs were removed from Expo Go in SDK 53,
// and its import throws on web where notifications don't exist. Lazy-require
// so the app still mounts in Expo Go / web.
const isExpoGo = Constants.executionEnvironment === ExecutionEnvironment.StoreClient;
const canUseNotifications = !isExpoGo && Platform.OS !== 'web';
let Notifications: any = null;
if (canUseNotifications) {
  try {
    Notifications = require('expo-notifications');
  } catch (e) {
    console.log('[Push] expo-notifications unavailable:', e);
  }
}

// @logrocket/react-native ships a native module that isn't linked in Expo Go
// — importing it eagerly throws on module-load. Lazy-require so the app
// mounts in Expo Go / web, and no-op the init/identify calls there.
let LogRocket: any = null;
if (!isExpoGo && Platform.OS !== 'web') {
  try {
    LogRocket = require('@logrocket/react-native').default ?? require('@logrocket/react-native');
  } catch (e) {
    console.log('[LogRocket] unavailable:', e);
  }
}

// ── Module-level side effects (must run before React mounts) ──────────

// 1. Foreground-notification presentation. Without this, FCM messages
//    received while the app is in the foreground never render a banner /
//    play a sound, so the driver silently misses ride offers.
if (Notifications) {
  Notifications.setNotificationHandler({
    handleNotification: async () => ({
      shouldShowBanner: true,
      shouldShowList: true,
      shouldPlaySound: true,
      shouldSetBadge: true,
      // Legacy (SDK <53) — harmless on newer SDKs.
      shouldShowAlert: true,
    }),
  });
}

// 2. Background FCM handler. Must be registered at module top level,
//    outside of any React component, so the JS runtime wakes when a
//    message arrives while the app is backgrounded or killed.
//    Persists the full ride-offer payload to AsyncStorage so the driver
//    home screen can hydrate the offer panel instantly on cold start
//    (useDriverDashboard.ts reads PENDING_OFFER_KEY on mount).
const PENDING_OFFER_KEY = 'spinr_pending_ride_offer';
setBackgroundMessageHandler(async (remoteMessage: any) => {
  const data = remoteMessage?.data || {};
  if (data?.type === 'new_ride_assignment' && data?.ride_id) {
    try {
      const AsyncStorage = require('@react-native-async-storage/async-storage').default;
      await AsyncStorage.setItem(
        PENDING_OFFER_KEY,
        JSON.stringify({
          ride_id: data.ride_id,
          pickup_address: data.pickup_address || '',
          dropoff_address: data.dropoff_address || '',
          pickup_lat: parseFloat(data.pickup_lat || '0'),
          pickup_lng: parseFloat(data.pickup_lng || '0'),
          dropoff_lat: parseFloat(data.dropoff_lat || '0'),
          dropoff_lng: parseFloat(data.dropoff_lng || '0'),
          fare: parseFloat(data.fare || '0'),
          distance_km: data.distance_km ? parseFloat(data.distance_km) : undefined,
          duration_minutes: data.duration_minutes ? parseFloat(data.duration_minutes) : undefined,
          rider_name: data.rider_name || undefined,
          rider_rating: data.rider_rating ? parseFloat(data.rider_rating) : undefined,
        }),
      );
    } catch (e) {
      console.warn('[Push] Failed to persist background ride offer:', e);
    }
  }
});

// DV-9: Route push-notification taps to the correct in-app screen.
// new_ride_assignment → driver home (offer panel hydrates from AsyncStorage);
// everything else → notifications inbox as the safe fallback so the driver
// can read the notification content rather than landing on a random screen.
function usePushNotificationRouter() {
  const router = useRouter();
  useEffect(() => {
    if (!Notifications) return;
    const sub = Notifications.addNotificationResponseReceivedListener(
      (response: any) => {
        const data = response?.notification?.request?.content?.data ?? {};
        if (data?.type === 'new_ride_assignment') {
          router.push('/driver/');
        } else {
          router.push('/driver/notifications');
        }
      },
    );
    return () => sub?.remove?.();
  }, [router]);
}

export default function RootLayout() {
  const [fontsLoaded, fontError] = useFonts({
    PlusJakartaSans_400Regular,
    PlusJakartaSans_500Medium,
    PlusJakartaSans_600SemiBold,
    PlusJakartaSans_700Bold,
  });

  const { initialize: initializeAuth, isInitialized: isAuthInitialized, token: authToken } = useAuthStore();
  const { initialize: initializeLocation, isInitialized: isLocationInitialized } = useLocationStore();
  const [isOffline, setIsOffline] = useState(false);
  // Guard so we only register the FCM token once per auth session.
  const fcmRegisteredRef = useRef(false);
  const prevAuthTokenRef = useRef(authToken);

  // Clear driver ride state on logout so a re-login doesn't see the
  // previous session's in-progress offer / active ride.
  useEffect(() => {
    if (prevAuthTokenRef.current && !authToken) {
      useDriverStore.getState().resetRideState();
    }
    prevAuthTokenRef.current = authToken;
  }, [authToken]);

  // ── LogRocket session recording ──
  // Guard against Expo Go — @logrocket/react-native ships a native module
  // that isn't linked there, so calling init would throw. Same gating
  // pattern as @shared/services/firebase.
  useEffect(() => {
    if (!LogRocket) return;
    try {
      LogRocket.init('gfuign/spinr');
    } catch (e) {
      console.log('[LogRocket] init failed:', e);
    }
  }, []);

  // ── Cold-start init: auth, location, Firebase native modules, Android channel ──
  useEffect(() => {
    const init = async () => {
      try {
        // Session flow (driver app):
        //   - On cold start, call initializeAuth() — it reads the stored
        //     JWT from SecureStore and calls /auth/me to hydrate the user.
        //   - If there's a valid session AND the user row has a profile,
        //     index.tsx routes straight to /driver (home).
        //   - If there's no session, index.tsx routes to /login (phone).
        //   - After OTP, otp.tsx routes to /driver or /profile-setup based
        //     on whether the backend returned a profile-complete user.
        //
        // The routing decision itself lives in driver-app/app/index.tsx and
        // uses BOTH `user.profile_complete` AND a fallback check on
        // first_name/last_name/email, so a stale `profile_complete=false`
        // flag can't push a user with existing profile data back into
        // onboarding.
        await Promise.all([initializeAuth(), initializeLocation()]);

        // Firebase native modules: Crashlytics + App Check. FCM token
        // registration is deferred to a separate effect that waits for
        // an authenticated session (below).
        await initFirebaseServices();

        captureMessage('driver-app cold start', 'log');

        // Android notification channels. Android 8+ REQUIRES a channel
        // or FCM messages are silently dropped. `ride-offers` is MAX
        // importance so the device wakes and rings for new ride offers;
        // `default` is used for everything else.
        if (Notifications && Platform.OS === 'android') {
          try {
            await Notifications.setNotificationChannelAsync('ride-offers', {
              name: 'Ride Offers',
              description: 'Incoming ride requests — must wake the device.',
              importance: Notifications.AndroidImportance.MAX,
              sound: 'default',
              vibrationPattern: [0, 500, 200, 500],
              lightColor: '#FF3B30',
              lockscreenVisibility: Notifications.AndroidNotificationVisibility.PUBLIC,
              bypassDnd: true,
              enableVibrate: true,
            });
            await Notifications.setNotificationChannelAsync('default', {
              name: 'Default',
              importance: Notifications.AndroidImportance.HIGH,
              sound: 'default',
            });
          } catch (e) {
            console.log('[Push] Android channel setup failed:', e);
          }
        }
      } catch (err: any) {
        console.error('Initialization error:', err);
      }
    };
    init();

    // Load Google Maps script for Web
    if (Platform.OS === 'web') {
      const script = document.createElement('script');
      script.src = `https://maps.googleapis.com/maps/api/js?key=${process.env.EXPO_PUBLIC_GOOGLE_MAPS_API_KEY}&libraries=places`;
      script.async = true;
      document.body.appendChild(script);
    }
  }, []);

  // ── FCM token registration, gated on auth ──
  // `POST /notifications/register-token` requires an authenticated user,
  // so we must wait until the auth store has a JWT. Previously this call
  // ran on cold-start unconditionally and silently 401'd for any user
  // who wasn't already logged in — meaning drivers who signed in for
  // the first time never got a server-side token registered.
  useEffect(() => {
    if (!isAuthInitialized || !authToken || fcmRegisteredRef.current) return;

    (async () => {
      try {
        const fcmToken = await requestPushPermissionAndGetToken();
        if (!fcmToken) return;
        const api = (await import('@shared/api/client')).default;
        await api.post('/notifications/register-token', {
          token: fcmToken,
          platform: Platform.OS,
        });
        fcmRegisteredRef.current = true;
        const uid = useAuthStore.getState().user?.id;
        if (uid) {
          setUser(uid, { role: 'driver' });
          if (LogRocket) {
            try { LogRocket.identify(uid, { role: 'driver' }); } catch (e) { console.log('[LogRocket] identify failed:', e); }
          }
        }
        console.log('[Push] FCM token registered with backend');
      } catch (e) {
        console.log('[Push] FCM token registration failed:', e);
      }
    })();

    // Subscribe to FCM token rotations. Firebase occasionally rotates
    // the device token; without re-registering, push delivery silently
    // fails until the next cold start. onTokenRefresh fires with the
    // new token string and we POST it the same way as the initial
    // registration above.
    const unsubTokenRefresh = onTokenRefresh(async (newToken: string) => {
      try {
        const api = (await import('@shared/api/client')).default;
        await api.post('/notifications/register-token', {
          token: newToken,
          platform: Platform.OS,
        });
        console.log('[Push] Refreshed FCM token registered with backend');
      } catch (e) {
        console.log('[Push] Refreshed FCM token registration failed:', e);
      }
    });

    return () => {
      if (typeof unsubTokenRefresh === 'function') unsubTokenRefresh();
    };
  }, [isAuthInitialized, authToken]);

  const onLoadingLayout = useCallback(() => {
    SplashScreen.hideAsync().catch(() => {});
  }, []);

  if (!fontsLoaded || fontError || !isAuthInitialized || !isLocationInitialized) {
    return (
      <ErrorBoundary>
        <View style={styles.loadingContainer} onLayout={onLoadingLayout}>
          <Text style={styles.logoText}>Spinr</Text>
          <ActivityIndicator size="large" color="#FFFFFF" style={{ marginTop: 20 }} />
        </View>
      </ErrorBoundary>
    );
  }

  return (
    <PersistQueryClientProvider
      client={queryClient}
      persistOptions={{
        persister: asyncStoragePersister,
        // 24h max age — anything older is dropped on rehydrate, so the
        // app can't boot with a week-old earnings number on screen.
        maxAge: 24 * 60 * 60 * 1000,
        buster: QUERY_CACHE_BUSTER,
      }}
    >
      <ThemeProvider>
        <DriverRootLayoutInner isOffline={isOffline} setIsOffline={setIsOffline} />
      </ThemeProvider>
    </PersistQueryClientProvider>
  );
}

function DriverRootLayoutInner({
  isOffline,
  setIsOffline,
}: {
  isOffline: boolean;
  setIsOffline: (v: boolean) => void;
}) {
  const { isDark } = useTheme();
  usePushNotificationRouter();
  return (
    <ErrorBoundary>
      <OfflineBanner visible={isOffline} onVisibilityChange={setIsOffline} />
      <GestureHandlerRootView style={{ flex: 1 }}>
        <SafeAreaProvider>
          <StatusBar style={isOffline ? "light" : isDark ? "light" : "dark"} />
          <Stack
            screenOptions={{
              headerShown: false,
              animation: 'slide_from_right',
            }}
          >
            <Stack.Screen name="index" options={{ animation: 'none' }} />
            <Stack.Screen name="login" />
            <Stack.Screen name="otp" />
            <Stack.Screen name="profile-setup" options={{ gestureEnabled: false }} />
            <Stack.Screen name="become-driver" options={{ gestureEnabled: false }} />
            {/* Instant cut so the driver home's SOSButton doesn't flash
                through a cross-fade out of the splash. */}
            <Stack.Screen name="driver" options={{ animation: "none", gestureEnabled: false }} />
          </Stack>
        </SafeAreaProvider>
      </GestureHandlerRootView>
    </ErrorBoundary>
  );
}

const styles = StyleSheet.create({
  loadingContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: SpinrConfig.theme.colors.primary,
  },
  logoText: {
    fontSize: 56,
    fontWeight: '700',
    color: '#FFFFFF',
  },
});
