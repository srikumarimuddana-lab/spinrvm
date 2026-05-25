import React, { useCallback, useEffect, useRef, useState } from 'react';

export const StripeKeyContext = React.createContext<string | null>(null);
// Public base URL for the "Share Trip" tracking page, served from
// app_settings.track_base_url via GET /settings. Null while loading or
// until the admin configures it. Consumers should disable the share
// action when null/empty rather than fall back to a hardcoded URL.
export const TrackBaseUrlContext = React.createContext<string | null>(null);
import { Stack, router } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { AppState, View, ActivityIndicator, StyleSheet, Text, Platform } from 'react-native';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { StripeProvider } from '@stripe/stripe-react-native';
import { useFonts, PlusJakartaSans_400Regular, PlusJakartaSans_500Medium, PlusJakartaSans_600SemiBold, PlusJakartaSans_700Bold } from '@expo-google-fonts/plus-jakarta-sans';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import * as SplashScreen from 'expo-splash-screen';
import * as Updates from 'expo-updates';

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
  requestNotificationPermission,
  requestPushPermissionAndGetToken,
  onForegroundMessage,
  setBackgroundMessageHandler,
  onTokenRefresh,
} from '@shared/services/firebase';
import { handleScheduledRideReminderFCM } from '../hooks/useScheduledRideReminder';
import { useRideStatusNotification } from '../hooks/useRideStatusNotification';
import ConfirmSheet from '../components/ConfirmSheet';
import type { ConfirmSheetButton } from '../components/ConfirmSheet';
import Toast from '../components/Toast';


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
    case 'chat_message':
      if (data?.ride_id) {
        router.push({ pathname: '/chat-driver', params: { rideId: ride_id } } as any);
      }
      break;
    default:
      break;
  }
}

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

let LogRocket: any = null;
if (!isExpoGo && Platform.OS !== 'web') {
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
  const [stripePublishableKey, setStripePublishableKey] = useState<string | null>(null);
  const [trackBaseUrl, setTrackBaseUrl] = useState<string | null>(null);
  const fcmRegisteredRef = useRef(false);
  const backgroundedAtRef = useRef<number | null>(null);
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

  // Killed-state notification tap routing
  useEffect(() => {
    if (!isAuthInitialized || !canUseNotifications || !Notifications) return;
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
  }, [isAuthInitialized]);

  // Cold-start ride resume
  useEffect(() => {
    if (!isAuthInitialized || !isLocationInitialized) return;
    const timer = setTimeout(() => {
      const ride = useRideStore.getState().currentRide;
      if (!ride?.id) return;
      const s = ride.status;
      if (s === 'searching' || s === 'driver_assigned' || s === 'driver_accepted') {
        router.push({ pathname: '/driver-arriving', params: { rideId: ride.id } } as any);
      } else if (s === 'driver_arrived') {
        router.push({ pathname: '/driver-arrived', params: { rideId: ride.id } } as any);
      } else if (s === 'in_progress') {
        router.push({ pathname: '/ride-in-progress', params: { rideId: ride.id } } as any);
      } else if (s === 'completed') {
        if (ride.payment_status !== 'paid' && ride.payment_status !== 'waived_admin') {
          router.push({ pathname: '/ride-completed', params: { rideId: ride.id } } as any);
        }
      }
    }, 200);
    return () => clearTimeout(timer);
  }, [isAuthInitialized, isLocationInitialized]);

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
        const s = result.ride.status;
        if (s === 'searching' || s === 'driver_assigned' || s === 'driver_accepted') {
          router.replace({ pathname: '/driver-arriving', params: { rideId: result.ride.id } } as any);
        } else if (s === 'driver_arrived') {
          router.replace({ pathname: '/driver-arrived', params: { rideId: result.ride.id } } as any);
        } else if (s === 'in_progress') {
          router.replace({ pathname: '/ride-in-progress', params: { rideId: result.ride.id } } as any);
        } else if (s === 'completed') {
          const ps = result.ride.payment_status;
          if (ps !== 'paid' && ps !== 'waived_admin') {
            router.replace({ pathname: '/ride-completed', params: { rideId: result.ride.id } } as any);
          }
        }
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
            <Stack.Screen name="loyalty" />
            <Stack.Screen name="support" />
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
