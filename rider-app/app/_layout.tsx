import React, { useCallback, useEffect, useRef, useState } from 'react';

// Exported so payment screens can guard CardField rendering behind a valid key.
export const StripeKeyContext = React.createContext<string | null>(null);
import { Stack, router } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { Alert, View, ActivityIndicator, StyleSheet, Text, Platform } from 'react-native';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { StripeProvider } from '@stripe/stripe-react-native';
import { useFonts, PlusJakartaSans_400Regular, PlusJakartaSans_500Medium, PlusJakartaSans_600SemiBold, PlusJakartaSans_700Bold } from '@expo-google-fonts/plus-jakarta-sans';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import * as SplashScreen from 'expo-splash-screen';

// Keep the native splash up until our React-rendered splash is mounted,
// otherwise the destination tab (which contains the SOSButton) momentarily
// flashes through during the boot transition.
SplashScreen.preventAutoHideAsync().catch(() => {});
import Constants, { ExecutionEnvironment } from 'expo-constants';
import NetInfo from '@react-native-community/netinfo';
import api from '@shared/api/client';
import { useAuthStore } from '@shared/store/authStore';
import { useLocationStore } from '@shared/store/locationStore';
import { useRideStore } from '../store/rideStore';
import { useWorkProfileStore } from '../store/workProfileStore';
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
import { handleScheduledRideReminderFCM } from '../hooks/useScheduledRideReminder';

// R-P1-29: Route to the correct screen based on FCM notification data.
// Called both for killed-state (getInitialNotification) and tapped-while-backgrounded notifications.
function routeFromNotificationData(data: Record<string, string> | undefined) {
  if (!data?.type || !data?.ride_id) return;
  const { type, ride_id } = data;
  switch (type) {
    case 'driver_accepted':
    case 'driver_arrived':
      router.push({ pathname: '/driver-arriving', params: { rideId: ride_id } } as any);
      break;
    case 'ride_started':
      router.push({ pathname: '/ride-in-progress', params: { rideId: ride_id } } as any);
      break;
    case 'ride_completed':
      router.push({ pathname: '/ride-completed', params: { rideId: ride_id } } as any);
      break;
    case 'ride_cancelled':
      router.replace('/(tabs)' as any);
      break;
    default:
      break;
  }
}

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
  const hydrateWorkProfile = useWorkProfileStore(s => s.hydrate);
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

  // ── Fetch Stripe publishable key from backend /settings ──
  // Public endpoint — no auth required. Key comes from the admin
  // settings row so ops can rotate without a new app build. Tokenization
  // (manage-cards.tsx, payment-confirm.tsx) depends on this being set.
  useEffect(() => {
    (async () => {
      try {
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
        await Promise.all([initializeAuth(), initializeLocation(), hydrateWorkProfile()]);

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
            // Channel for scheduled ride reminders (T-15 min alert)
            await Notifications.setNotificationChannelAsync('scheduled-reminders', {
              name: 'Scheduled Ride Reminders',
              description: 'Reminder 15 minutes before your scheduled ride departs.',
              importance: Notifications.AndroidImportance.HIGH,
              sound: 'default',
              vibrationPattern: [0, 250, 150, 250],
              lockscreenVisibility: Notifications.AndroidNotificationVisibility.PUBLIC,
              enableVibrate: true,
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
        await api.post('/notifications/register-token', {
          token: fcmToken,
          platform: Platform.OS,
        });
        fcmRegisteredRef.current = true;
        // Tag error reports with user identity from this point on.
        const uid = useAuthStore.getState().user?.id;
        if (uid) {
          setUser(uid);
          if (LogRocket) {
            try { LogRocket.identify(uid); } catch (e) { console.log('[LogRocket] identify failed:', e); }
          }
        }
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
  useEffect(() => {
    if (!isAuthInitialized) return;

    const unsubscribe = onForegroundMessage((remoteMessage: any) => {
      console.log('[Push] Rider foreground FCM:', remoteMessage?.notification?.title);

      // Scheduled ride reminder — show a local notification so the alert
      // appears even when the app is foregrounded (FCM suppresses the
      // OS banner for foreground messages).
      const reminderRideId = handleScheduledRideReminderFCM(remoteMessage);
      if (reminderRideId) {
        if (Notifications) {
          const scheduledTime = remoteMessage?.data?.scheduled_time;
          const timeLabel = scheduledTime
            ? new Date(scheduledTime).toLocaleTimeString('en-CA', { hour: '2-digit', minute: '2-digit' })
            : 'soon';
          Notifications.scheduleNotificationAsync({
            content: {
              title: '🚗 Ride in 15 minutes',
              body: `Your scheduled Spinr ride departs at ${timeLabel}. Get ready!`,
              data: { type: 'scheduled_ride_reminder', rideId: reminderRideId },
              sound: 'default',
              ...(Platform.OS === 'android' ? { channelId: 'scheduled-reminders' } : {}),
            },
            trigger: null, // show immediately
          }).catch(() => {});
        }
        return; // don't also refetch ride for reminder events
      }

      // Safety check-in — ask the rider if they're okay; POST confirmation on tap.
      if (remoteMessage?.data?.type === 'safety_checkin') {
        const checkinRideId = remoteMessage?.data?.ride_id as string | undefined;
        if (checkinRideId) {
          Alert.alert(
            'Safety check-in',
            "Just checking in — are you okay?\n\nIf you don't respond, we'll follow up with you shortly.",
            [
              {
                text: "I'm okay",
                onPress: () => {
                  api.post(`/rides/${checkinRideId}/safety-checkin`).catch(() => {});
                },
              },
            ],
            { cancelable: false },
          );
        }
        return;
      }

      // All other FCM types — refresh the active ride state
      const currentRide = useRideStore.getState().currentRide;
      if (currentRide?.id) {
        useRideStore.getState().fetchRide(currentRide.id).catch(() => {});
      }
    });

    return () => {
      if (typeof unsubscribe === 'function') unsubscribe();
    };
  }, [isAuthInitialized]);

  // R-P1-29: Killed-state deep linking — check if the app was opened by tapping
  // a notification while it was fully killed. expo-notifications.getInitialNotificationResponseAsync()
  // returns the tapped notification response in that case.
  // The routing call is deferred 100ms to ensure the Expo Router Stack is fully
  // mounted before navigation is attempted — calls fired before hydration are
  // silently dropped.
  useEffect(() => {
    // Only route from notification AFTER auth is initialized and app is ready
    if (!isAuthInitialized || !canUseNotifications || !Notifications) return;
    let timer: ReturnType<typeof setTimeout>;
    (async () => {
      try {
        const response = await Notifications.getInitialNotificationResponseAsync?.();
        if (response?.notification?.request?.content?.data) {
          const data = response.notification.request.content.data as Record<string, string>;
          console.log('[Push] Killed-state notification tap — routing from data:', data);
          // Add a small defer to ensure Stack is mounted
          timer = setTimeout(() => {
            routeFromNotificationData(data);
          }, 100);
        }
      } catch (e) {
        console.log('[Push] getInitialNotification failed:', e);
      }
    })();
    return () => clearTimeout(timer);
  }, [isAuthInitialized]);

  // Clear ride session data when the user logs out so a subsequent login
  // doesn't show the previous session's ride, driver, or chat state.
  const prevAuthTokenRef = useRef(authToken);
  useEffect(() => {
    if (prevAuthTokenRef.current && !authToken) {
      useRideStore.getState().clearRide();
    }
    prevAuthTokenRef.current = authToken;
  }, [authToken]);

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

  const onLoadingLayout = useCallback(() => {
    // React splash is on screen — safe to drop the native splash.
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
    <ThemeProvider>
      <RootLayoutInner isOffline={isOffline} setIsOffline={setIsOffline} stripePublishableKey={stripePublishableKey} wsState={wsState} />
    </ThemeProvider>
  );
}

function MaybeStripeProvider({
  publishableKey,
  children,
}: {
  publishableKey: string | null;
  children: React.ReactNode;
}) {
  if (!publishableKey) return <>{children}</>;
  return (
    <StripeProvider publishableKey={publishableKey} merchantIdentifier="merchant.com.spinr.user">
      {children as React.ReactElement}
    </StripeProvider>
  );
}

function RootLayoutInner({
  isOffline,
  setIsOffline,
  stripePublishableKey,
  wsState,
}: {
  isOffline: boolean;
  setIsOffline: (v: boolean) => void;
  stripePublishableKey: string | null;
  wsState: import('../hooks/useRiderSocket').RiderSocketState;
}) {
  const { isDark } = useTheme();
  return (
    <ErrorBoundary>
      <OfflineBanner visible={isOffline} onVisibilityChange={setIsOffline} />
      {(wsState === 'reconnecting' || wsState === 'connecting') && (
        <View style={{ backgroundColor: '#F59E0B', paddingVertical: 4, alignItems: 'center' }}>
          <Text style={{ color: '#fff', fontSize: 12, fontWeight: '600' }}>
            Reconnecting to ride updates…
          </Text>
        </View>
      )}
      <GestureHandlerRootView>
        <View style={{ flex: 1 }}>
          <SafeAreaProvider>
            <StatusBar style={isOffline ? "light" : isDark ? "light" : "dark"} />
            {/* MaybeStripeProvider defers mounting the native Stripe SDK until
                the real publishable key is fetched from the backend. Passing
                an empty string (or a malformed key) to StripeProvider's native
                module throws IllegalArgumentException on Android and crashes
                the app. StripeKeyContext exposes the key to child screens so
                they can gate CardField rendering independently. */}
            <StripeKeyContext.Provider value={stripePublishableKey}>
            <MaybeStripeProvider publishableKey={stripePublishableKey}>
            <Stack
              screenOptions={{
                headerShown: false,
                animation: 'slide_from_right',
              }}
            >
              {/* Auth */}
              <Stack.Screen name="index" options={{ animation: 'none' }} />
              <Stack.Screen name="login" />
              <Stack.Screen name="otp" />
              <Stack.Screen name="profile-setup" options={{ headerShown: false }} />

              {/* Main — instant cut from splash so the home tab's SOSButton
                  doesn't flash through a cross-fade transition. */}
              <Stack.Screen name="(tabs)" options={{ animation: 'none' }} />

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
            </MaybeStripeProvider>
            </StripeKeyContext.Provider>
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
