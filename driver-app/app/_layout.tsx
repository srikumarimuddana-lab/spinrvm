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
import { toastConfig } from '../components/toastConfig';

// Minimum time the branded splash (logo + tagline) stays on screen, even when
// auth/location init finishes sooner — otherwise the tagline animation (which
// only starts ~400ms in) is cut off and the driver barely sees the branding.
const SPLASH_MIN_DISPLAY_MS = 3000;
import Constants, { ExecutionEnvironment } from 'expo-constants';
import { useAuthStore } from '@shared/store/authStore';
import { useLocationStore } from '@shared/store/locationStore';
import { useVehicleTypesSync } from '@shared/store/vehicleTypeStore';
import { useDriverStore } from '../store/driverStore';
import BrandSplash from '../components/BrandSplash';
import { ErrorBoundary } from '@shared/components/ErrorBoundary';
import { OfflineBanner } from '@shared/components/OfflineBanner';
import { ThemeProvider, useTheme } from '@shared/theme/ThemeContext';
import { QueryClientProvider } from '@tanstack/react-query';
import { PersistQueryClientProvider } from '@tanstack/react-query-persist-client';
import { queryClient, asyncStoragePersister, QUERY_CACHE_BUSTER } from '@shared/api/queryClient';
import { captureMessage, setUser, initErrorReporting, wrapApp } from '@shared/services/errorReporting';

// EAS Observe (SDK 55 API: AppMetricsRoot / AppMetrics). Native module —
// present only in binaries built with it, so the guarded require keeps older
// installed builds and Expo Go booting with metrics simply off.
let ObserveMetricsRoot: any = null;
let ObserveMetrics: any = null;
try {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const _observe = require('expo-observe');
  ObserveMetricsRoot = _observe.AppMetricsRoot ?? null;
  ObserveMetrics = _observe.AppMetrics ?? null;
} catch {
  ObserveMetricsRoot = null;
  ObserveMetrics = null;
}
import {
  initFirebaseServices,
  requestNotificationPermission,
  requestPushPermissionAndGetToken,
  onTokenRefresh,
  getAppCheckToken,
} from '@shared/services/firebase';
import { setAppCheckTokenProvider } from '@shared/api/client';
// Notifee — rich notifications (heads-up + full-screen intent + Accept/Decline
// action buttons). Lazy-required because the native module isn't linked in
// Expo Go / web; we treat the import failure the same as Expo Go: no-op.
let notifee: any = null;
let parseRideOfferEvent: any = null;
let dismissRideOfferNotification: any = null;
let ensureNotifeeReady: any = null;
if (Platform.OS === 'android' || Platform.OS === 'ios') {
  try {
    notifee = require('@notifee/react-native').default;
    const svc = require('../services/notifeeService');
    parseRideOfferEvent = svc.parseRideOfferEvent;
    dismissRideOfferNotification = svc.dismissRideOfferNotification;
    ensureNotifeeReady = svc.ensureNotifeeReady;
  } catch (e) {
    console.log('[Notifee] native module not available — falling back to expo-notifications only');
  }
}

// Crash/error reporting (Sentry primary, Crashlytics fallback) — initialised
// at module scope so errors during the first render are captured. No-ops
// when EXPO_PUBLIC_SENTRY_DSN is unset (dev/Expo Go) or on web.
initErrorReporting({
  dsn: process.env.EXPO_PUBLIC_SENTRY_DSN,
  surface: 'driver-app',
});

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
//
// Android is DISABLED by default: LogRocket's session-replay view serializer
// reflects into hidden framework fields (e.g. PorterDuffColorFilter.mColor)
// that Android 16 / targetSdk 36 blocks ("hiddenapi ... denied"). On Android 16
// devices (e.g. Samsung S26) that denial hung the UI thread during startup, so
// the first React frame never rendered and the app stuck on the native splash.
// Override per build with EXPO_PUBLIC_ENABLE_LOGROCKET ('true'/'false'); leave
// unset for the safe default (iOS on, Android off). Re-enable Android once
// LogRocket ships an Android-16-safe SDK.
// Only the literal strings 'true'/'false' override; any other inlined value
// (undefined / '' when unset) falls through to the platform default.
const lrFlag = process.env.EXPO_PUBLIC_ENABLE_LOGROCKET;
const LOGROCKET_ENABLED =
  lrFlag === 'true' ? true : lrFlag === 'false' ? false : Platform.OS === 'ios';
let LogRocket: any = null;
if (!isExpoGo && Platform.OS !== 'web' && LOGROCKET_ENABLED) {
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

// 2. Background FCM + Notifee killed-state handlers are registered in
//    index.js via services/backgroundMessaging.ts — they MUST live at the
//    real bundle entry. When the app is killed, Android delivers data-only
//    ride offers through a headless JS launch where route modules (this
//    file) never execute, so a handler registered here would never exist
//    and the offer would be silently dropped. PENDING_ACTION_KEY is shared
//    with the foreground listeners below.

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
      // The app is foreground/active here, so execute accept/decline
      // immediately. Stashing would NOT be replayed: the dashboard's
      // pending-action consumer only runs on mount or an AppState→active
      // transition, and an already-mounted, already-active dashboard sees
      // neither — so a foreground action tap would otherwise sit unread and
      // the offer would time out. A body tap just routes to the offer panel.
      try {
        const store = useDriverStore.getState();
        if (parsed.action === 'accept') {
          await store.acceptRide(parsed.ride_id);
        } else if (parsed.action === 'decline') {
          await store.declineRide(parsed.ride_id);
        }
        if (dismissRideOfferNotification) await dismissRideOfferNotification();
      } catch (e) {
        console.warn('[Notifee] foreground action failed:', e);
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

function RootLayout() {
  const [fontsLoaded, fontError] = useFonts({
    PlusJakartaSans_400Regular,
    PlusJakartaSans_500Medium,
    PlusJakartaSans_600SemiBold,
    PlusJakartaSans_700Bold,
  });

  const { initialize: initializeAuth, isInitialized: isAuthInitialized, token: authToken } = useAuthStore();
  const { initialize: initializeLocation, isInitialized: isLocationInitialized } = useLocationStore();
  // One GET /vehicle-types per app open feeds the own-car map marker via
  // the shared vehicleTypeStore (also refreshes on foreground).
  useVehicleTypesSync();
  const [isOffline, setIsOffline] = useState(false);
  // Hold the branded splash for a minimum duration so the logo + tagline are
  // actually seen — auth/location init can finish in <400ms, cutting off the
  // tagline before it animates in. The loading gate below waits on this too.
  const [minSplashElapsed, setMinSplashElapsed] = useState(false);
  useEffect(() => {
    const t = setTimeout(() => setMinSplashElapsed(true), SPLASH_MIN_DISPLAY_MS);
    return () => clearTimeout(t);
  }, []);
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

  // Foreground ride offers (app open) are surfaced by useDriverDashboard,
  // which already subscribes to onForegroundMessage and renders the offer
  // via _surfaceOfferNotification (silent Notifee banner + in-app MP3 loop).
  // A second root-level subscriber here would double-handle the same FCM
  // payload and race on the shared notification id, so it lives there only.

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

  // EAS Observe: Time-to-Interactive ends when the loading gate below clears
  // — the first moment the driver can actually act.
  const appInteractive =
    fontsLoaded && !fontError && isAuthInitialized && isLocationInitialized && minSplashElapsed;
  useEffect(() => {
    if (appInteractive) ObserveMetrics?.markInteractive?.();
  }, [appInteractive]);

  if (!fontsLoaded || fontError || !isAuthInitialized || !isLocationInitialized || !minSplashElapsed) {
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
          <Toast config={toastConfig} />
        </SafeAreaProvider>
      </GestureHandlerRootView>
    </ErrorBoundary>
  );
}

// Wrap the root with Sentry's error boundary + navigation/touch instrumentation.
// No-op until EXPO_PUBLIC_SENTRY_DSN is set (see initErrorReporting); safe in Expo Go/web.
// Sentry (wrapApp) stays outermost so it also catches the Observe wrapper.
export default wrapApp(ObserveMetricsRoot ? ObserveMetricsRoot.wrap(RootLayout) : RootLayout);

