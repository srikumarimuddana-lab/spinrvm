import React, { useEffect, useRef, useState } from 'react';
import { Stack } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { View, ActivityIndicator, StyleSheet, Text, Platform } from 'react-native';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { StripeProvider } from '@stripe/stripe-react-native';
import { useFonts, PlusJakartaSans_400Regular, PlusJakartaSans_500Medium, PlusJakartaSans_600SemiBold, PlusJakartaSans_700Bold } from '@expo-google-fonts/plus-jakarta-sans';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import Constants, { ExecutionEnvironment } from 'expo-constants';
import NetInfo from '@react-native-community/netinfo';
import { useAuthStore } from '@shared/store/authStore';
import { useLocationStore } from '@shared/store/locationStore';
import { useRideStore } from '../store/rideStore';
import { useRiderSocket } from '../hooks/useRiderSocket';
import SpinrConfig from '@shared/config/spinr.config';
import { ErrorBoundary } from '@shared/components/ErrorBoundary';
import { OfflineBanner } from '@shared/components/OfflineBanner';
import { ThemeProvider, useTheme } from '@shared/theme/ThemeContext';
import { captureMessage, setUser } from '@shared/services/errorReporting';
import Analytics from '@shared/analytics';
import {
  initFirebaseServices,
  requestPushPermissionAndGetToken,
  onForegroundMessage,
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

// ── Module-level side effects (must run before React mounts) ──────────
// These are identical in structure to driver-app/app/_layout.tsx — the
// rider app had the same set of notification-plumbing bugs (silent
// foreground messages, Android channel-less drops, background handler
// missing, unconditional FCM token registration) and the fix pattern
// is the same. Only the channel name + the store hook-in differ.

// 1. Foreground-notification presentation. Without this, FCM messages
//    received while the rider app is in the foreground never render a
//    banner or play a sound — the rider would silently miss ride-status
//    updates ("driver accepted", "driver arrived", "ride started") if
//    they happened to have the app open.
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
//    message arrives while the app is backgrounded or killed. The OS
//    notification itself is rendered by the Android channel
//    configured below (or APNs on iOS).
setBackgroundMessageHandler(async (remoteMessage: any) => {
  console.log('[Push] Rider background FCM:', remoteMessage?.data?.type || remoteMessage?.notification?.title);
});

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
  // Stripe publishable key is fetched from the backend at boot so operators
  // can rotate it without an app release. Until it loads, we render children
  // without StripeProvider — payment screens that call useStripe() will
  // early-return a friendly "Payments unavailable" state in that window.
  const [stripePublishableKey, setStripePublishableKey] = useState<string | null>(null);
  // Guard so we only register the FCM token once per auth session.
  const fcmRegisteredRef = useRef(false);

  // ── Real-time WebSocket for ride-state + driver-location updates ─
  // Connects when the rider has an active ride (currentRide is set in
  // the store by ride-options.tsx after createRide). Disconnects
  // automatically when the ride finishes or is cancelled. Screens that
  // previously polled every 3s now poll every 15s as a fallback — the
  // WebSocket delivers the same updates in <100ms.
  const { connectionState: wsState } = useRiderSocket();

  // ── Fetch Stripe publishable key from backend /settings ──
  // Public endpoint — no auth required. Key comes from the admin
  // settings row so ops can rotate without a new app build. Tokenization
  // (manage-cards.tsx, payment-confirm.tsx) depends on this being set.
  useEffect(() => {
    (async () => {
      try {
        const api = (await import('@shared/api/client')).default;
        const res = await api.get<{ stripe_publishable_key?: string }>('/settings');
        const key = res.data?.stripe_publishable_key;
        if (key) setStripePublishableKey(key);
      } catch (e) {
        console.log('[Stripe] Failed to fetch publishable key:', e);
      }
    })();
  }, []);

  // ── Cold-start init: auth, location, Firebase, Android channel ──
  useEffect(() => {
    const init = async () => {
      try {
        await Promise.all([initializeAuth(), initializeLocation()]);

        // Firebase native modules: Crashlytics + App Check. FCM token
        // registration is deferred to a separate effect that waits for
        // an authenticated session (below).
        await initFirebaseServices();

        captureMessage('rider-app cold start', 'log');

        // Android notification channels. Android 8+ REQUIRES a channel
        // or FCM messages are silently dropped. `ride-updates` is
        // HIGH importance (not MAX like the driver app's `ride-offers`
        // channel — riders don't need the device to wake for every
        // ride-state ping; a normal heads-up notification is enough).
        if (Notifications && Platform.OS === 'android') {
          try {
            await Notifications.setNotificationChannelAsync('ride-updates', {
              name: 'Ride Updates',
              description: 'Status updates for your current ride (driver accepted, arrived, trip started, etc.).',
              importance: Notifications.AndroidImportance.HIGH,
              sound: 'default',
              vibrationPattern: [0, 300, 150, 300],
              lockscreenVisibility: Notifications.AndroidNotificationVisibility.PUBLIC,
              enableVibrate: true,
            });
            await Notifications.setNotificationChannelAsync('default', {
              name: 'Default',
              importance: Notifications.AndroidImportance.DEFAULT,
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
      const apiKey = Constants.expoConfig?.extra?.EXPO_PUBLIC_GOOGLE_MAPS_API_KEY;

      if (!apiKey) {
        console.error('Google Maps API key is missing. Please check your app.config.js');
      } else {
        script.src = `https://maps.googleapis.com/maps/api/js?key=${apiKey}&libraries=places`;
        script.async = true;
        script.onerror = () => {
          console.error('Failed to load Google Maps script');
        };
        document.body.appendChild(script);
      }
    }
  }, []);

  // ── FCM token registration, gated on auth ──
  // `POST /notifications/register-token` requires an authenticated user,
  // so we must wait until the auth store has a JWT. Previously this call
  // ran on cold-start unconditionally and silently 401'd for any user
  // who wasn't already logged in — meaning first-time riders never got
  // a server-side token registered and never received push notifications.
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
        // Tag error reports with user identity from this point on.
        const uid = useAuthStore.getState().user?.id;
        if (uid) setUser(uid);
        Analytics.login();
        console.log('[Push] Rider FCM token registered with backend');
      } catch (e) {
        console.log('[Push] Rider FCM token registration failed:', e);
      }
    })();

    // Subscribe to FCM token rotations so push delivery doesn't
    // silently fail when Firebase rotates the device token.
    const unsubTokenRefresh = onTokenRefresh(async (newToken: string) => {
      try {
        const api = (await import('@shared/api/client')).default;
        await api.post('/notifications/register-token', {
          token: newToken,
          platform: Platform.OS,
        });
        console.log('[Push] Rider refreshed FCM token registered with backend');
      } catch (e) {
        console.log('[Push] Rider refreshed FCM token registration failed:', e);
      }
    });

    return () => {
      if (typeof unsubTokenRefresh === 'function') unsubTokenRefresh();
    };
  }, [isAuthInitialized, authToken]);

  // ── Foreground FCM message handler ──
  // When the backend pushes a ride-state update (driver accepted,
  // arrived, ride started, ride cancelled), refresh the currentRide
  // from the API so the UI snaps to the new state immediately instead
  // of waiting up to 5 seconds for the next polling cycle in
  // ride-status.tsx / driver-arriving.tsx / ride-in-progress.tsx.
  //
  // Backend notifications currently carry only title/body (no `data`
  // field), so we can't route by event type — we refetch whatever ride
  // the store considers active. If there's no currentRide we no-op.
  // Gated on `isAuthInitialized` so the subscription doesn't race the
  // rest of cold-start.
  useEffect(() => {
    if (!isAuthInitialized) return;

    const unsubscribe = onForegroundMessage((remoteMessage: any) => {
      console.log('[Push] Rider foreground FCM:', remoteMessage?.notification?.title);
      const currentRide = useRideStore.getState().currentRide;
      if (currentRide?.id) {
        useRideStore.getState().fetchRide(currentRide.id).catch(() => {
          // Polling will cover the next attempt; don't surface.
        });
      }
    });

    return () => {
      if (typeof unsubscribe === 'function') unsubscribe();
    };
  }, [isAuthInitialized]);

  // ── Network connectivity monitoring for offline sync ──
  useEffect(() => {
    const unsubscribe = NetInfo.addEventListener(state => {
      const wasOffline = isOffline;
      const isNowOffline = !state.isConnected || !state.isInternetReachable;

      setIsOffline(isNowOffline);

      // When coming back online, sync any queued offline requests
      if (wasOffline && !isNowOffline && isAuthInitialized) {
        useRideStore.getState().syncOfflineRequests().catch(error => {
          console.error('Failed to sync offline requests:', error);
        });
      }
    });

    return unsubscribe;
  }, [isAuthInitialized, isOffline]);

  if (!fontsLoaded || fontError || !isAuthInitialized || !isLocationInitialized) {
    return (
      <ErrorBoundary>
        <View style={styles.loadingContainer}>
          <Text style={styles.logoText}>Spinr</Text>
          <ActivityIndicator size="large" color="#FFFFFF" style={{ marginTop: 20 }} />
        </View>
      </ErrorBoundary>
    );
  }

  return (
    <ThemeProvider>
      <RootLayoutInner isOffline={isOffline} setIsOffline={setIsOffline} stripePublishableKey={stripePublishableKey} />
    </ThemeProvider>
  );
}

function RootLayoutInner({
  isOffline,
  setIsOffline,
  stripePublishableKey,
}: {
  isOffline: boolean;
  setIsOffline: (v: boolean) => void;
  stripePublishableKey: string | null;
}) {
  const { isDark } = useTheme();
  return (
    <ErrorBoundary>
      <OfflineBanner visible={isOffline} onVisibilityChange={setIsOffline} />
      <GestureHandlerRootView>
        <View style={{ flex: 1 }}>
          <SafeAreaProvider>
            <StatusBar style={isOffline ? "light" : isDark ? "light" : "dark"} />
            {/* StripeProvider always wraps the Stack so useStripe() /
                <CardField> work on any screen. When the publishable key
                isn't loaded yet (or the fetch failed) we pass an empty
                string; createPaymentMethod will reject with a clear
                error and the manage-cards screen surfaces it as
                "Payments unavailable — try again shortly". */}
            <StripeProvider
              publishableKey={stripePublishableKey || ''}
              merchantIdentifier="merchant.com.spinr.user"
            >
            <Stack
              screenOptions={{
                headerShown: false,
                animation: 'slide_from_right',
              }}
            >
              {/* Auth */}
              <Stack.Screen name="index" />
              <Stack.Screen name="login" />
              <Stack.Screen name="otp" />
              <Stack.Screen name="profile-setup" options={{ headerShown: false }} />

              {/* Main */}
              <Stack.Screen name="(tabs)" options={{ animation: 'fade' }} />

              {/* Ride flow */}
              <Stack.Screen name="search-destination" options={{ animation: 'slide_from_bottom' }} />
              <Stack.Screen name="pick-on-map" options={{ animation: 'slide_from_bottom', headerShown: false }} />
              <Stack.Screen name="ride-options" />
              <Stack.Screen name="payment-confirm" />
              <Stack.Screen name="ride-status" options={{ gestureEnabled: false }} />
              <Stack.Screen name="driver-arriving" options={{ gestureEnabled: false }} />
              <Stack.Screen name="driver-arrived" options={{ gestureEnabled: false }} />
              <Stack.Screen name="ride-in-progress" options={{ gestureEnabled: false }} />
              <Stack.Screen name="ride-completed" options={{ gestureEnabled: false }} />
              <Stack.Screen name="rate-ride" />
              <Stack.Screen name="chat-driver" />

              {/* Account */}
              <Stack.Screen name="manage-cards" />
              <Stack.Screen name="saved-places" />
              <Stack.Screen name="promotions" />
              <Stack.Screen name="privacy-settings" />
              <Stack.Screen name="emergency-contacts" />
              <Stack.Screen name="report-safety" />
              <Stack.Screen name="support" />
              <Stack.Screen name="legal" />
              <Stack.Screen name="become-driver" />
              <Stack.Screen name="ride-details" />
              <Stack.Screen name="settings" />
            </Stack>
            </StripeProvider>
          </SafeAreaProvider>
        </View>
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
