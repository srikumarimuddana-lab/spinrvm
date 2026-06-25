import React, { useCallback, useEffect, useRef, useState } from 'react';

export const StripeKeyContext = React.createContext<string | null>(null);
// Public base URL for the "Share Trip" tracking page, served from
// app_settings.track_base_url via GET /settings. Null while loading or
// until the admin configures it. Consumers should disable the share
// action when null/empty rather than fall back to a hardcoded URL.
export const TrackBaseUrlContext = React.createContext<string | null>(null);
import { Stack, router, usePathname } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { AppState, View, Text, Platform } from 'react-native';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { StripeProvider } from '@stripe/stripe-react-native';
import { useFonts, PlusJakartaSans_400Regular, PlusJakartaSans_500Medium, PlusJakartaSans_600SemiBold, PlusJakartaSans_700Bold } from '@expo-google-fonts/plus-jakarta-sans';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import * as Updates from 'expo-updates';

// Minimum time the branded splash (logo + tagline) stays on screen, even when
// auth/location init finishes sooner — otherwise the tagline animation (which
// only starts ~400ms in) is cut off and the rider barely sees the branding.
// The full intro (logo + tagline + footer loader) settles by ~1.1s, so 1.8s
// shows the branding with a brief beat without the logo sitting idle on a
// white screen long enough to feel stuck.
const SPLASH_MIN_DISPLAY_MS = 1800;
import Constants, { ExecutionEnvironment } from 'expo-constants';
import NetInfo from '@react-native-community/netinfo';
import api from '@shared/api/client';
import { useAuthStore } from '@shared/store/authStore';
import { useLocationStore } from '@shared/store/locationStore';
import { useVehicleTypesSync } from '@shared/store/vehicleTypeStore';
import { useRideStore } from '../store/rideStore';
import { useWorkProfileStore } from '../store/workProfileStore';
import { useRiderSocket } from '../hooks/useRiderSocket';
import * as rideLive from '../services/rideLiveNotification';
import BrandSplash from '../components/BrandSplash';
import { ErrorBoundary } from '@shared/components/ErrorBoundary';
import { OfflineBanner } from '@shared/components/OfflineBanner';
import { ThemeProvider, useTheme } from '@shared/theme/ThemeContext';
import { captureMessage, setUser, initErrorReporting, wrapApp } from '@shared/services/errorReporting';
import Analytics from '@shared/analytics';
import {
  initFirebaseServices,
  requestNotificationPermission,
  requestPushPermissionAndGetToken,
  onForegroundMessage,
  setBackgroundMessageHandler,
  onTokenRefresh,
  getAppCheckToken,
} from '@shared/services/firebase';
import { setAppCheckTokenProvider } from '@shared/api/client';
import { handleScheduledRideReminderFCM } from '../hooks/useScheduledRideReminder';
import { useRideStatusNotification } from '../hooks/useRideStatusNotification';
import ConfirmSheet from '../components/ConfirmSheet';
import type { ConfirmSheetButton } from '../components/ConfirmSheet';
import Toast from '../components/Toast';


function routeFromNotificationData(data: Record<string, string> | undefined) {
  if (!data?.type) return;
  const { type, ride_id, case_id } = data;
  switch (type) {
    case 'driver_accepted':
    case 'driver_arrived':
      if (ride_id) router.push({ pathname: '/driver-arriving', params: { rideId: ride_id } } as any);
      break;
    case 'scheduled_ride_dispatched':
      // A scheduled ride just went live: the dispatcher flipped it
      // scheduled→searching and is now finding a driver. The rider app cleared
      // its currentRide once /(tabs) reloaded (/rides/active excludes
      // 'scheduled'), so route to the finding-driver screen, which fetches the
      // ride by id and re-establishes the live connection.
      router.push({ pathname: '/driver-arriving', params: { rideId: ride_id } } as any);
      break;
    case 'ride_started':
      if (ride_id) router.push({ pathname: '/ride-in-progress', params: { rideId: ride_id } } as any);
      break;
    case 'ride_completed':
      if (ride_id) router.push({ pathname: '/ride-completed', params: { rideId: ride_id } } as any);
      break;
    case 'ride_cancelled':
      router.replace('/(tabs)' as any);
      break;
    case 'chat_message':
      if (ride_id) router.push({ pathname: '/chat-driver', params: { rideId: ride_id } } as any);
      break;
    case 'lost_and_found':
    case 'lost_and_found_message':
      if (case_id) router.push({ pathname: '/lost-and-found-chat', params: { caseId: case_id } } as any);
      break;
    default:
      break;
  }
}

// Crash/error reporting (Sentry primary, Crashlytics fallback) — initialised
// at module scope so errors during the first render are captured. No-ops
// when EXPO_PUBLIC_SENTRY_DSN is unset (dev/Expo Go) or on web.
initErrorReporting({
  dsn: process.env.EXPO_PUBLIC_SENTRY_DSN,
  surface: 'rider-app',
});

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

if (Notifications) {
  Notifications.setNotificationHandler({
    handleNotification: async () => ({
      shouldShowBanner: true,
      shouldShowList: true,
      shouldPlaySound: true,
      shouldSetBadge: true,
      shouldShowAlert: true,
    }),
  });
}

setBackgroundMessageHandler(async (remoteMessage: any) => {
  const data = remoteMessage?.data;
  console.log('[Push] Rider background FCM:', data?.type || remoteMessage?.notification?.title);
  // Backend-driven ongoing "live ride" notification. This handler runs headless
  // (app backgrounded/killed), so Notifee renders/updates/cancels the notification
  // here from the data-only message — the only path that works when JS is asleep.
  if (data?.type === 'live_activity') {
    await rideLive.handleFcmData(data);
  }
});


// Maps an active-ride status to the screen that should be showing it. Used by
// the cold-start and foreground-resume handlers so they can skip navigating to
// a screen the rider is already on (re-navigating remounts the screen and its
// bottom sheet — the OTP / ride-in-progress "double load" on app resume).
function targetPathForRideStatus(status: string): string | null {
  if (status === 'searching' || status === 'driver_assigned' || status === 'driver_accepted') {
    return '/driver-arriving';
  }
  if (status === 'driver_arrived') return '/driver-arrived';
  if (status === 'in_progress') return '/ride-in-progress';
  if (status === 'completed') return '/ride-completed';
  return null;
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
  // One GET /vehicle-types per app open feeds every map marker + booking
  // screen via the shared vehicleTypeStore (also refreshes on foreground).
  useVehicleTypesSync();
  const hydrateWorkProfile = useWorkProfileStore(s => s.hydrate);
  const [isOffline, setIsOffline] = useState(false);
  // Hold the branded splash for a minimum duration so the logo + tagline are
  // actually seen — auth/location init can finish in <400ms, cutting off the
  // tagline before it animates in. The loading gate below waits on this too.
  const [minSplashElapsed, setMinSplashElapsed] = useState(false);
  useEffect(() => {
    const t = setTimeout(() => setMinSplashElapsed(true), SPLASH_MIN_DISPLAY_MS);
    return () => clearTimeout(t);
  }, []);
  // True once the loading gate below has cleared and <Stack> is actually mounted,
  // so router navigation targets a live navigator. The cold-start
  // notification-routing and ride-resume effects must wait for this: the branded
  // splash hold keeps <Stack> unmounted for up to SPLASH_MIN_DISPLAY_MS even
  // after auth/location finish, so pushing on isAuthInitialized alone would fire
  // before the navigator exists and the navigation would be silently dropped.
  const navReady =
    fontsLoaded && !fontError && isAuthInitialized && isLocationInitialized && minSplashElapsed;
  const [stripePublishableKey, setStripePublishableKey] = useState<string | null>(null);
  const [trackBaseUrl, setTrackBaseUrl] = useState<string | null>(null);
  const fcmRegisteredRef = useRef(false);
  const backgroundedAtRef = useRef<number | null>(null);
  // Current route, mirrored into a ref so the AppState listener (registered
  // once) and the cold-start timeout read a fresh value instead of a stale
  // closure when deciding whether a resume navigation is redundant.
  const pathname = usePathname();
  const pathnameRef = useRef(pathname);
  pathnameRef.current = pathname;
  const [confirmSheet, setConfirmSheet] = useState<{
    visible: boolean;
    title: string;
    message: string;
    variant: 'info' | 'warning' | 'danger' | 'success';
    buttons: ConfirmSheetButton[];
  }>({ visible: false, title: '', message: '', variant: 'info', buttons: [] });

  const { connectionState: wsState } = useRiderSocket();

  useEffect(() => {
    if (!LogRocket) return;
    try {
      LogRocket.init('gfuign/spinr');
    } catch (e) {
      console.log('[LogRocket] init failed:', e);
    }
  }, []);

  useEffect(() => {
    (async () => {
      try {
        const res = await api.get<{ stripe_publishable_key?: string; track_base_url?: string }>('/settings');
        const key = res.data?.stripe_publishable_key;
        if (key) setStripePublishableKey(key);
        const trackUrl = (res.data?.track_base_url || '').replace(/\/$/, '');
        setTrackBaseUrl(trackUrl.length > 0 ? trackUrl : null);
      } catch (e) {
        console.log('[Settings] Failed to fetch public settings:', e);
      }
    })();
  }, []);

  useEffect(() => {
    const init = async () => {
      try {
        try {
          const AsyncStorage = require('@react-native-async-storage/async-storage').default;
          const saved = await AsyncStorage.getItem('spinr_last_location');
          if (saved) {
            const { lat, lng } = JSON.parse(saved);
            if (typeof lat === 'number' && typeof lng === 'number') {
              useRideStore.getState().setUserLocation({ latitude: lat, longitude: lng });
            }
          }
        } catch (e) { /* non-fatal */ }

        await Promise.all([
          initializeAuth(),
          initializeLocation(),
          hydrateWorkProfile(),
        ]);

        await useRideStore.getState().hydrateActiveRide();
        await initFirebaseServices();
        setAppCheckTokenProvider(getAppCheckToken);
        await requestNotificationPermission();
        captureMessage('rider-app cold start', 'log');

        if (Notifications && Platform.OS === 'android') {
          try {
            await Notifications.setNotificationChannelAsync('ride-updates', {
              name: 'Ride Updates',
              description: 'Status updates for your current ride.',
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
            await Notifications.setNotificationChannelAsync('scheduled-reminders', {
              name: 'Scheduled Ride Reminders',
              description: 'Reminder 15 minutes before your scheduled ride departs.',
              importance: Notifications.AndroidImportance.HIGH,
              sound: 'default',
              vibrationPattern: [0, 250, 150, 250],
              lockscreenVisibility: Notifications.AndroidNotificationVisibility.PUBLIC,
              enableVibrate: true,
            });
            await Notifications.setNotificationChannelAsync('ride-status-live', {
              name: 'Live Ride Status',
              description: 'Shows current ride progress in the notification bar.',
              importance: Notifications.AndroidImportance.LOW,
              sound: null,
              vibrationPattern: [0],
              lockscreenVisibility: Notifications.AndroidNotificationVisibility.PUBLIC,
              enableVibrate: false,
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

    if (Platform.OS === 'web') {
      const script = document.createElement('script');
      const apiKey = Constants.expoConfig?.extra?.EXPO_PUBLIC_GOOGLE_MAPS_API_KEY;
      if (apiKey) {
        script.src = `https://maps.googleapis.com/maps/api/js?key=${apiKey}&libraries=places`;
        script.async = true;
        document.body.appendChild(script);
      }
    }
  }, []);

  // FCM token registration, gated on auth
  useEffect(() => {
    if (!isAuthInitialized || !authToken || fcmRegisteredRef.current) return;

    (async () => {
      try {
        const fcmToken = await requestPushPermissionAndGetToken();
        if (!fcmToken) return;
        await api.post('/notifications/register-token', {
          token: fcmToken,
          platform: Platform.OS,
          client_type: 'rider',
        });
        fcmRegisteredRef.current = true;
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

    const unsubTokenRefresh = onTokenRefresh(async (newToken: string) => {
      try {
        await api.post('/notifications/register-token', {
          token: newToken,
          platform: Platform.OS,
          client_type: 'rider',
        });
      } catch (e) {
        console.log('[Push] Rider refreshed FCM token registration failed:', e);
      }
    });

    return () => {
      if (typeof unsubTokenRefresh === 'function') unsubTokenRefresh();
    };
  }, [isAuthInitialized, authToken]);

  // Live-activity registration (Android): once a driver has accepted, register
  // this device so the backend can update/cancel the ongoing "live ride"
  // notification via data-only FCM even while the app is backgrounded/killed.
  // The local foreground driver (useRideStatusNotification) shows it immediately;
  // this just enables the backend-driven updates. Registered once per ride.
  useEffect(() => {
    if (Platform.OS !== 'android' || !isAuthInitialized || !authToken) return;
    const POST_ACCEPT = new Set(['driver_accepted', 'driver_arrived', 'in_progress']);
    let lastRegisteredRideId: string | null = null;

    const maybeRegister = async (ride: any) => {
      const status = ride?.status as string | undefined;
      if (!ride?.id || !status || !POST_ACCEPT.has(status)) return;
      if (lastRegisteredRideId === ride.id) return;
      lastRegisteredRideId = ride.id;
      try {
        const token = await requestPushPermissionAndGetToken();
        if (!token) return;
        await api.post(`/rides/${ride.id}/live-activity/register`, {
          platform: Platform.OS, // 'android'
          push_token: token,
        });
        console.log('[RideLive] live-activity registered for ride', ride.id);
      } catch (e) {
        console.log('[RideLive] live-activity register failed:', e);
      }
    };

    maybeRegister(useRideStore.getState().currentRide);
    const unsub = useRideStore.subscribe((s) => maybeRegister(s.currentRide));
    return () => unsub();
  }, [isAuthInitialized, authToken]);

  // Foreground FCM message handler
  useEffect(() => {
    if (!isAuthInitialized) return;

    const unsubscribe = onForegroundMessage((remoteMessage: any) => {
      console.log('[Push] Rider foreground FCM:', remoteMessage?.notification?.title);

      const reminderRideId = handleScheduledRideReminderFCM(remoteMessage);
      if (reminderRideId) {
        if (Notifications) {
          const scheduledTime = remoteMessage?.data?.scheduled_time;
          const timeLabel = scheduledTime
            ? new Date(scheduledTime).toLocaleTimeString('en-CA', { hour: '2-digit', minute: '2-digit' })
            : 'soon';
          Notifications.scheduleNotificationAsync({
            content: {
              title: 'Ride in 15 minutes',
              body: `Your scheduled Spinr ride departs at ${timeLabel}. Get ready!`,
              data: { type: 'scheduled_ride_reminder', rideId: reminderRideId },
              sound: 'default',
              ...(Platform.OS === 'android' ? { channelId: 'scheduled-reminders' } : {}),
            },
            trigger: null,
          }).catch(() => {});
        }
        return;
      }

      // Live ride-activity update (data-only): refresh the ongoing notification.
      if (remoteMessage?.data?.type === 'live_activity') {
        rideLive.handleFcmData(remoteMessage.data).catch(() => {});
        return;
      }

      // Scheduled ride going live while the app is foregrounded. The rider's
      // currentRide was cleared after booking (/rides/active excludes
      // 'scheduled'), so proactively reload it and route to the finding-driver
      // screen instead of leaving the rider stranded on home with only a banner.
      if (remoteMessage?.data?.type === 'scheduled_ride_dispatched') {
        const dispatchedRideId = remoteMessage?.data?.ride_id as string | undefined;
        if (dispatchedRideId) {
          useRideStore.getState().fetchRide(dispatchedRideId).catch((e) =>
            console.warn('[Layout] fetchRide on scheduled dispatch failed:', e?.message ?? e),
          );
          router.push({ pathname: '/driver-arriving', params: { rideId: dispatchedRideId } } as any);
        }
        return;
      }

      // Safety check-in
      if (remoteMessage?.data?.type === 'safety_checkin') {
        const checkinRideId = remoteMessage?.data?.ride_id as string | undefined;
        if (checkinRideId) {
          setConfirmSheet({
            visible: true,
            title: 'Safety check-in',
            message: "Just checking in — are you okay?\n\nIf you don't respond, we'll follow up with you shortly.",
            variant: 'info',
            buttons: [
              {
                text: "I'm okay",
                style: 'default',
                onPress: () => {
                  api.post(`/rides/${checkinRideId}/safety-checkin`).catch((e) => console.warn('[Layout] Safety checkin failed:', e?.message ?? e));
                },
              },
            ],
          });
        }
        return;
      }

      // Display a local notification banner — FCM foreground messages are
      // not shown by the OS automatically; we must present them ourselves.
      if (Notifications && remoteMessage?.notification?.title) {
        Notifications.scheduleNotificationAsync({
          content: {
            title: remoteMessage.notification.title,
            body: remoteMessage.notification.body ?? '',
            data: (remoteMessage.data ?? {}) as Record<string, unknown>,
            sound: 'default',
            ...(Platform.OS === 'android' ? { channelId: 'ride-updates' } : {}),
          },
          trigger: null,
        }).catch(() => {});
      }

      const currentRide = useRideStore.getState().currentRide;
      if (currentRide?.id) {
        useRideStore.getState().fetchRide(currentRide.id).catch((e) => console.warn('[Layout] fetchRide on foreground failed:', e?.message ?? e));
      }
    });

    return () => {
      if (typeof unsubscribe === 'function') unsubscribe();
    };
  }, [isAuthInitialized]);

  // Killed-state notification tap routing — gated on navReady so the push lands
  // after <Stack> has mounted (the branded-splash hold delays mount up to 3s).
  useEffect(() => {
    if (!navReady || !canUseNotifications || !Notifications) return;
    let timer: ReturnType<typeof setTimeout>;
    (async () => {
      try {
        const response = await Notifications.getInitialNotificationResponseAsync?.();
        if (response?.notification?.request?.content?.data) {
          const data = response.notification.request.content.data as Record<string, string>;
          timer = setTimeout(() => {
            routeFromNotificationData(data);
          }, 100);
        }
      } catch (e) {
        console.log('[Push] getInitialNotification failed:', e);
      }
    })();
    return () => clearTimeout(timer);
  }, [navReady]);

  // Cold-start ride resume — gated on navReady so the push targets a mounted
  // navigator (the 3s branded-splash hold delays <Stack> mount past auth/location).
  useEffect(() => {
    if (!navReady) return;
    const timer = setTimeout(() => {
      const ride = useRideStore.getState().currentRide;
      if (!ride?.id) return;
      const target = targetPathForRideStatus(ride.status);
      if (!target) return;
      if (target === '/ride-completed' &&
          (ride.payment_status === 'paid' || ride.payment_status === 'waived_admin')) return;
      if (pathnameRef.current === target) return;
      router.push({ pathname: target, params: { rideId: ride.id } } as any);
    }, 200);
    return () => clearTimeout(timer);
  }, [navReady]);

  // Foreground resume: track background duration + re-check active ride
  useEffect(() => {
    if (!isAuthInitialized) return;
    const STALE_THRESHOLD_MS = 5 * 60 * 1000;
    const sub = AppState.addEventListener('change', async (nextState) => {
      if (nextState === 'background' || nextState === 'inactive') {
        backgroundedAtRef.current = Date.now();
        return;
      }
      if (nextState !== 'active') return;

      const bgAt = backgroundedAtRef.current;
      backgroundedAtRef.current = null;
      if (bgAt && Date.now() - bgAt > STALE_THRESHOLD_MS) {
        console.log('[Layout] Long background detected, reloading JS bundle');
        try {
          await Updates.reloadAsync();
        } catch {
          router.replace('/(tabs)' as any);
        }
        return;
      }

      try {
        const result = await useRideStore.getState().fetchActiveRide();
        if (!result?.active || !result.ride) return;
        const target = targetPathForRideStatus(result.ride.status);
        if (!target) return;
        if (target === '/ride-completed') {
          const ps = result.ride.payment_status;
          if (ps === 'paid' || ps === 'waived_admin') return;
        }
        // Already on the destination screen — re-navigating remounts it and its
        // bottom sheet, which the rider sees as the page "double loading" every
        // time the app returns to the foreground. Skip the redundant nav.
        if (pathnameRef.current === target) return;
        router.replace({ pathname: target, params: { rideId: result.ride.id } } as any);
      } catch (e) {
        console.warn('[Layout] Foreground ride check failed:', e);
      }
    });
    return () => sub.remove();
  }, [isAuthInitialized]);

  // Background notification tap routing
  useEffect(() => {
    if (!isAuthInitialized || !canUseNotifications || !Notifications) return;
    const sub = Notifications.addNotificationResponseReceivedListener((response: any) => {
      const data = response?.notification?.request?.content?.data as Record<string, string> | undefined;
      if (data) routeFromNotificationData(data);
    });
    return () => sub?.remove?.();
  }, [isAuthInitialized]);

  // Clear ride state on logout
  const prevAuthTokenRef = useRef(authToken);
  useEffect(() => {
    if (prevAuthTokenRef.current && !authToken) {
      useRideStore.getState().clearRide();
    }
    prevAuthTokenRef.current = authToken;
  }, [authToken]);

  // Network connectivity monitoring
  useEffect(() => {
    const unsubscribe = NetInfo.addEventListener(state => {
      const wasOffline = isOffline;
      const isNowOffline = !state.isConnected || !state.isInternetReachable;

      setIsOffline(isNowOffline);

      if (wasOffline && !isNowOffline && isAuthInitialized) {
        useRideStore.getState().syncOfflineRequests().catch(error => {
          console.error('Failed to sync offline requests:', error);
        });
      }
    });

    return unsubscribe;
  }, [isAuthInitialized, isOffline]);

  const onLoadingLayout = useCallback(() => {}, []);

  if (!fontsLoaded || fontError || !isAuthInitialized || !isLocationInitialized || !minSplashElapsed) {
    return (
      <ErrorBoundary>
        <BrandSplash onLayout={onLoadingLayout} />
      </ErrorBoundary>
    );
  }

  return (
    <ThemeProvider>
      <RootLayoutInner isOffline={isOffline} setIsOffline={setIsOffline} stripePublishableKey={stripePublishableKey} trackBaseUrl={trackBaseUrl} wsState={wsState} confirmSheet={confirmSheet} setConfirmSheet={setConfirmSheet} />
    </ThemeProvider>
  );
}

function GestureRootWrapper({ children }: { children: React.ReactNode }) {
  return <GestureHandlerRootView style={{ flex: 1 }}>{children}</GestureHandlerRootView>;
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
  trackBaseUrl,
  wsState,
  confirmSheet,
  setConfirmSheet,
}: {
  isOffline: boolean;
  setIsOffline: (v: boolean) => void;
  stripePublishableKey: string | null;
  trackBaseUrl: string | null;
  wsState: import('../hooks/useRiderSocket').RiderSocketState;
  confirmSheet: { visible: boolean; title: string; message: string; variant: 'info' | 'warning' | 'danger' | 'success'; buttons: ConfirmSheetButton[] };
  setConfirmSheet: React.Dispatch<React.SetStateAction<typeof confirmSheet>>;
}) {
  const { isDark } = useTheme();
  useRideStatusNotification();
  return (
    <ErrorBoundary>
      <OfflineBanner visible={isOffline} onVisibilityChange={setIsOffline} />
      <GestureRootWrapper>
        <SafeAreaProvider>
          <StatusBar style={isOffline ? "light" : isDark ? "light" : "dark"} />
          {wsState === 'reconnecting' && (
            <View style={{ backgroundColor: '#F59E0B', paddingVertical: 4, alignItems: 'center' }}>
              <Text style={{ color: '#fff', fontSize: 12, fontWeight: '600' }}>
                Reconnecting to ride updates…
              </Text>
            </View>
          )}
          <StripeKeyContext.Provider value={stripePublishableKey}>
          <TrackBaseUrlContext.Provider value={trackBaseUrl}>
          <MaybeStripeProvider publishableKey={stripePublishableKey}>
          <Stack
            screenOptions={{
              headerShown: false,
              animation: 'slide_from_right',
            }}
          >
            <Stack.Screen name="index" options={{ animation: 'none' }} />
            <Stack.Screen name="login" />
            <Stack.Screen name="otp" />
            <Stack.Screen name="profile-setup" options={{ headerShown: false }} />

            <Stack.Screen name="(tabs)" options={{ animation: 'none' }} />

            <Stack.Screen name="search-destination" options={{ animation: 'slide_from_bottom' }} />
            <Stack.Screen name="pick-on-map" options={{ animation: 'slide_from_bottom', headerShown: false }} />
            <Stack.Screen name="confirm-pickup" />
            <Stack.Screen name="ride-options" />
            <Stack.Screen name="payment-confirm" />
            <Stack.Screen name="ride-status" options={{ gestureEnabled: false }} />
            <Stack.Screen name="driver-arriving" options={{ gestureEnabled: false }} />
            <Stack.Screen name="driver-arrived" options={{ gestureEnabled: false }} />
            <Stack.Screen name="ride-in-progress" options={{ gestureEnabled: false }} />
            <Stack.Screen name="ride-completed" options={{ gestureEnabled: false }} />
            <Stack.Screen name="ride-tracking-webview" />
            <Stack.Screen name="chat-driver" />

            <Stack.Screen name="wallet" />
            <Stack.Screen name="manage-cards" />
            <Stack.Screen name="notifications" />
            <Stack.Screen name="saved-places" />
            <Stack.Screen name="scheduled-rides" />
            <Stack.Screen name="promotions" />
            <Stack.Screen name="referral" />
            <Stack.Screen name="loyalty" />
            <Stack.Screen name="support" />
            <Stack.Screen name="ai-assistant" />
            <Stack.Screen name="legal" />
            <Stack.Screen name="emergency-contacts" />
            <Stack.Screen name="report-safety" />
            <Stack.Screen name="privacy-settings" />
            <Stack.Screen name="accessibility" />
            <Stack.Screen name="settings" />
            <Stack.Screen name="ride-details" />
            <Stack.Screen name="work-profile" />
            <Stack.Screen name="work-allowance-request" />
            <Stack.Screen name="become-driver" />
            <Stack.Screen name="lost-and-found" />
            <Stack.Screen name="lost-and-found-chat" />
          </Stack>
          </MaybeStripeProvider>
          </TrackBaseUrlContext.Provider>
          </StripeKeyContext.Provider>
          <ConfirmSheet
            visible={confirmSheet.visible}
            title={confirmSheet.title}
            message={confirmSheet.message}
            variant={confirmSheet.variant}
            buttons={confirmSheet.buttons}
            onClose={() => setConfirmSheet(prev => ({ ...prev, visible: false }))}
          />
          <Toast />
        </SafeAreaProvider>
      </GestureRootWrapper>
    </ErrorBoundary>
  );
}

// Wrap the root with Sentry's error boundary + navigation/touch instrumentation.
// No-op until EXPO_PUBLIC_SENTRY_DSN is set (see initErrorReporting); safe in Expo Go/web.
export default wrapApp(RootLayout);

