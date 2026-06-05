import '../utils/backgroundLocation';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Stack, useRouter } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { View, Text, Platform, LogBox, AppState } from 'react-native';

// LogBox's notification container uses a codegen native component that's
// broken under Bridgeless mode (RN 0.85.2). Disable it in dev to prevent
// the "_LogBoxNotificationContainer" crash. Errors still appear in Metro.
if (__DEV__) {
  LogBox.ignoreAllLogs(true);
}
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { useFonts, PlusJakartaSans_400Regular, PlusJakartaSans_500Medium, PlusJakartaSans_600SemiBold, PlusJakartaSans_700Bold } from '@expo-google-fonts/plus-jakarta-sans';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import Toast from 'react-native-toast-message';
import { AlertDialog } from '../components/AlertDialog';
import Constants, { ExecutionEnvironment } from 'expo-constants';
import { useAuthStore } from '@shared/store/authStore';
import { useLocationStore } from '@shared/store/locationStore';
import { useDriverStore } from '../store/driverStore';
import BrandSplash from '../components/BrandSplash';
import { ErrorBoundary } from '@shared/components/ErrorBoundary';
import { OfflineBanner } from '@shared/components/OfflineBanner';
import { ThemeProvider, useTheme } from '@shared/theme/ThemeContext';
import { QueryClientProvider } from '@tanstack/react-query';
import { PersistQueryClientProvider } from '@tanstack/react-query-persist-client';
import { queryClient, asyncStoragePersister, QUERY_CACHE_BUSTER } from '@shared/api/queryClient';
import { captureMessage, setUser } from '@shared/services/errorReporting';
import {
  initFirebaseServices,
  requestNotificationPermission,
  requestPushPermissionAndGetToken,
  setBackgroundMessageHandler,
  onTokenRefresh,
  getAppCheckToken,
} from '@shared/services/firebase';
import { setAppCheckTokenProvider } from '@shared/api/client';
// Notifee — rich notifications (heads-up + full-screen intent + Accept/Decline
// action buttons). Lazy-required because the native module isn't linked in
// Expo Go / web; we treat the import failure the same as Expo Go: no-op.
let notifee: any = null;
let parseRideOfferEvent: any = null;
let displayRideOfferNotification: any = null;
let dismissRideOfferNotification: any = null;
let ensureNotifeeReady: any = null;
if (Platform.OS === 'android' || Platform.OS === 'ios') {
  try {
    notifee = require('@notifee/react-native').default;
    const svc = require('../services/notifeeService');
    parseRideOfferEvent = svc.parseRideOfferEvent;
    displayRideOfferNotification = svc.displayRideOfferNotification;
    dismissRideOfferNotification = svc.dismissRideOfferNotification;
    ensureNotifeeReady = svc.ensureNotifeeReady;
  } catch (e) {
    console.log('[Notifee] native module not available — falling back to expo-notifications only');
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

// 1. Foreground-notification presentation. Without this, FCM messages
//    received while the app is in the foreground never render a banner /
//    play a sound, so the driver silently misses ride offers.
if (Notifications) {
  Notifications.setNotificationHandler({
    handleNotification: async (notification: any) => {
      const data = notification?.request?.content?.data ?? {};
      // Ride offers are surfaced via Notifee full-screen intent — suppress
      // the plain native banner so the driver doesn't see both.
      // Ride status changes (completed, cancelled, etc.) are handled in-app
      // via WS + the dashboard panels — no native notification needed.
      const suppressTypes = ['new_ride_assignment', 'ride_completed', 'ride_cancelled', 'ride_status_changed'];
      if (suppressTypes.includes(data?.type)) {
        return { shouldShowBanner: false, shouldShowList: false, shouldPlaySound: false, shouldSetBadge: false, shouldShowAlert: false };
      }
      return {
        shouldShowBanner: true,
        shouldShowList: true,
        shouldPlaySound: true,
        shouldSetBadge: true,
        shouldShowAlert: true,
      };
    },
  });
}

// 2. Background FCM handler. Must be registered at module top level,
//    outside of any React component, so the JS runtime wakes when a
//    message arrives while the app is backgrounded or killed.
//    Persists the full ride-offer payload to AsyncStorage so the driver
//    home screen can hydrate the offer panel instantly on cold start
//    (useDriverDashboard.ts reads PENDING_OFFER_KEY on mount).
const PENDING_OFFER_KEY = 'spinr_pending_ride_offer';
const PENDING_ACTION_KEY = 'spinr_pending_notifee_action';
setBackgroundMessageHandler(async (remoteMessage: any) => {
  const data = remoteMessage?.data || {};
  if (data?.type === 'new_ride_assignment' && data?.ride_id) {
    // FCM data uses same keys as WS dispatch_payload, all stringified.
    const safeParse = <T,>(s: any): T | undefined => {
      if (!s || s === 'null' || s === 'None') return undefined;
      if (typeof s !== 'string') return s as T;
      try { return JSON.parse(s) as T; } catch { return undefined; }
    };
    const toNum = (v: any): number | undefined => {
      if (!v || v === '' || v === 'None') return undefined;
      const n = parseFloat(v);
      return Number.isFinite(n) ? n : undefined;
    };
    const fare = toNum(data.fare) ?? 0;
    const totalBonus = toNum(data.total_bonus) ?? 0;
    const surgeMultiplier = toNum(data.surge_multiplier);
    const incentives = safeParse<any[]>(data.incentives);
    const questHint = safeParse<any>(data.quest_hint);

    // 1. Persist the full offer payload so the in-app panel can hydrate
    //    instantly on cold start (driver dashboard reads on mount).
    try {
      const AsyncStorage = require('@react-native-async-storage/async-storage').default;
      await AsyncStorage.setItem(
        PENDING_OFFER_KEY,
        JSON.stringify({
          ride_id: data.ride_id,
          booking_id: data.booking_id || data.ride_id,
          pickup_address: data.pickup_address || '',
          dropoff_address: data.dropoff_address || '',
          pickup_lat: toNum(data.pickup_lat) ?? 0,
          pickup_lng: toNum(data.pickup_lng) ?? 0,
          dropoff_lat: toNum(data.dropoff_lat) ?? 0,
          dropoff_lng: toNum(data.dropoff_lng) ?? 0,
          fare,
          distance_km: toNum(data.distance_km),
          duration_minutes: toNum(data.duration_minutes),
          rider_name: data.rider_name || undefined,
          rider_rating: toNum(data.rider_rating),
          requires_wav: data.requires_wav === 'true' || data.requires_wav === 'True',
          countdown_seconds: toNum(data.countdown_seconds),
          offer_expires_at: data.offer_expires_at || undefined,
          surge_multiplier: surgeMultiplier,
          incentives,
          total_bonus: totalBonus || undefined,
          quest_hint: questHint,
          payment_method: data.payment_method || undefined,
        }),
      );
    } catch (e) {
      console.warn('[Push] Failed to persist background ride offer:', e);
    }

    // 2. Surface the Uber-style heads-up + full-screen-intent notification
    //    via Notifee. This is what the driver actually sees on the lock
    //    screen, with Accept/Decline buttons.
    if (displayRideOfferNotification) {
      try {
        await displayRideOfferNotification({
          ride_id: data.ride_id,
          booking_id: data.booking_id || data.ride_id,
          pickup_address: data.pickup_address,
          dropoff_address: data.dropoff_address,
          fare,
          total_bonus: totalBonus,
          distance_km: toNum(data.distance_km),
          duration_minutes: toNum(data.duration_minutes),
          surge_multiplier: surgeMultiplier,
          rider_name: data.rider_name,
          incentives_count: Array.isArray(incentives) ? incentives.length : 0,
        });
      } catch (e) {
        console.warn('[Notifee] displayRideOfferNotification failed:', e);
      }
    }
  }
});

// Notifee background event listener — fires when the user taps Accept/Decline
// from the lock screen or notification shade while the app is killed. Must be
// registered at module top level (mirrors the FCM background handler). We
// stash the action in AsyncStorage so useDriverDashboard.ts can consume it on
// mount and call accept/decline against the backend.
if (notifee && parseRideOfferEvent) {
  notifee.onBackgroundEvent(async (event: any) => {
    const parsed = parseRideOfferEvent(event);
    if (!parsed || !parsed.ride_id) return;
    try {
      const AsyncStorage = require('@react-native-async-storage/async-storage').default;
      await AsyncStorage.setItem(
        PENDING_ACTION_KEY,
        JSON.stringify({ action: parsed.action, ride_id: parsed.ride_id, ts: Date.now() }),
      );
      // Dismiss the notification so the driver doesn't see stale "Accept"
      // buttons while we open the app.
      if (dismissRideOfferNotification) await dismissRideOfferNotification();
    } catch (e) {
      console.warn('[Notifee] background action persist failed:', e);
    }
  });
}

// DV-9: Route push-notification taps to the correct in-app screen.
// new_ride_assignment → driver home (offer panel hydrates from AsyncStorage);
// everything else → notifications inbox as the safe fallback so the driver
// can read the notification content rather than landing on a random screen.
function usePushNotificationRouter() {
  const router = useRouter();

  // Foreground / backgrounded-tap listener (fires when app is already running).
  useEffect(() => {
    if (!Notifications) return;
    const sub = Notifications.addNotificationResponseReceivedListener(
      (response: any) => {
        const data = response?.notification?.request?.content?.data ?? {};
        if (data?.type === 'new_ride_assignment') {
          router.push('/driver/' as any);
        } else if (data?.type === 'chat_message' && data?.ride_id) {
          router.push(`/driver/chat?rideId=${data.ride_id}` as any);
        } else if ((data?.type === 'lost_and_found' || data?.type === 'lost_and_found_message') && data?.case_id) {
          router.push({ pathname: '/driver/lost-and-found-chat', params: { caseId: data.case_id } } as any);
        } else {
          router.push('/driver/notifications');
        }
      },
    );
    return () => sub?.remove?.();
  }, [router]);

  // Notifee foreground event listener — fires when the user taps the
  // Accept/Decline action buttons or the notification body while the app
  // is in the foreground or backgrounded (but not killed; killed-state
  // events come through the module-level notifee.onBackgroundEvent above).
  useEffect(() => {
    if (!notifee || !parseRideOfferEvent) return;
    const unsub = notifee.onForegroundEvent(async (event: any) => {
      const parsed = parseRideOfferEvent(event);
      if (!parsed || !parsed.ride_id) return;
      // Always route to the driver home so the offer panel is on screen;
      // accept/decline execution happens inside the dashboard which polls
      // the pending-action AsyncStorage key on mount + on focus.
      try {
        const AsyncStorage = require('@react-native-async-storage/async-storage').default;
        await AsyncStorage.setItem(
          PENDING_ACTION_KEY,
          JSON.stringify({ action: parsed.action, ride_id: parsed.ride_id, ts: Date.now() }),
        );
        if (dismissRideOfferNotification) await dismissRideOfferNotification();
      } catch (e) {
        console.warn('[Notifee] foreground action persist failed:', e);
      }
      router.push('/driver/' as any);
    });
    return () => unsub?.();
  }, [router]);

  // Killed-state deep linking — getInitialNotificationResponseAsync() returns
  // the tapped notification when the app was fully killed. The routing call is
  // deferred 100ms to ensure the Expo Router Stack is fully mounted before
  // navigation is attempted — calls fired before hydration are silently dropped.
  // This hook is only mounted after isAuthInitialized is true (the loading gate
  // in RootLayout blocks DriverRootLayoutInner until then).
  useEffect(() => {
    if (!canUseNotifications || !Notifications) return;
    let timer: ReturnType<typeof setTimeout>;
    (async () => {
      try {
        const response = await Notifications.getInitialNotificationResponseAsync?.();
        if (response?.notification?.request?.content?.data) {
          const data = response.notification.request.content.data as Record<string, string>;
          console.log('[Push] Driver killed-state notification tap — routing from data:', data);
          // Add a small defer to ensure Stack is mounted
          timer = setTimeout(() => {
            if (data?.type === 'new_ride_assignment') {
              router.push('/driver/' as any);
            } else if (data?.type === 'chat_message' && data?.ride_id) {
              router.push(`/driver/chat?rideId=${data.ride_id}` as any);
            } else if ((data?.type === 'lost_and_found' || data?.type === 'lost_and_found_message') && data?.case_id) {
              router.push({ pathname: '/driver/lost-and-found-chat', params: { caseId: data.case_id } } as any);
            } else {
              router.push('/driver/notifications' as any);
            }
          }, 100);
        }
      } catch (e) {
        console.log('[Push] Driver getInitialNotification failed:', e);
      }
    })();
    return () => clearTimeout(timer);
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

  // Android Auto is registered at JS bundle load in index.js (registerAutoPlay,
  // @iternio/react-native-auto-play) — nothing for the phone layout to do here.
  // iOS CarPlay is dormant (needs an Apple entitlement + scene wiring not present).

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
        setAppCheckTokenProvider(getAppCheckToken);
        await requestNotificationPermission();

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

        // Notifee channel + iOS category setup. Drives the rich heads-up
        // + full-screen-intent ride offer (Uber-style "incoming call" UX).
        if (ensureNotifeeReady) {
          try { await ensureNotifeeReady(); } catch (e) {
            console.log('[Notifee] ensureReady failed:', e);
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
          client_type: 'driver',
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
  }, [isAuthInitialized, authToken]);

  // ── FCM token rotation listener (permanent, not gated on auth token) ──
  // onTokenRefresh must live in its OWN effect with empty deps. If it
  // shared deps with the registration effect above, every 15-minute
  // access-token rotation would trigger cleanup → the listener is
  // unsubscribed → Firebase token rotations are never forwarded to the
  // backend → stale FCM token → push notifications silently fail.
  useEffect(() => {
    const unsubTokenRefresh = onTokenRefresh(async (newToken: string) => {
      try {
        const api = (await import('@shared/api/client')).default;
        await api.post('/notifications/register-token', {
          token: newToken,
          platform: Platform.OS,
          client_type: 'driver',
        });
        console.log('[Push] Refreshed FCM token registered with backend');
      } catch (e) {
        console.log('[Push] Refreshed FCM token registration failed:', e);
      }
    });
    return () => {
      if (typeof unsubTokenRefresh === 'function') unsubTokenRefresh();
    };
  }, []);

  // ── Proactive token refresh on foreground resume + periodic ──
  // Ensures the access token is fresh BEFORE the driver taps a critical
  // action after returning from background. Without this, the first API
  // call after a long background period hits 401, causing a visible
  // 1-2s refresh-then-retry delay.
  useEffect(() => {
    if (!isAuthInitialized || !authToken) return;

    const { ensureFreshToken } = require('@shared/api/client');

    const sub = AppState.addEventListener('change', (nextState: string) => {
      if (nextState === 'active') {
        ensureFreshToken().catch(() => {});
      }
    });

    // With a 15-min token TTL and 2-min refresh buffer, a 60s check
    // guarantees the token is always refreshed before the danger zone.
    const interval = setInterval(() => {
      ensureFreshToken().catch(() => {});
    }, 60_000);

    return () => {
      sub.remove();
      clearInterval(interval);
    };
  }, [isAuthInitialized, authToken]);

  const onLoadingLayout = useCallback(() => {}, []);

  if (!fontsLoaded || fontError || !isAuthInitialized || !isLocationInitialized) {
    return (
      <QueryClientProvider client={queryClient}>
        <ErrorBoundary>
          <BrandSplash onLayout={onLoadingLayout} />
        </ErrorBoundary>
      </QueryClientProvider>
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
          <AlertDialog />
          <Toast />
        </SafeAreaProvider>
      </GestureHandlerRootView>
    </ErrorBoundary>
  );
}

