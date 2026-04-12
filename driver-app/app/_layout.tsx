import React, { useEffect, useRef, useState } from 'react';
import { Stack } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { View, ActivityIndicator, StyleSheet, Text, Platform } from 'react-native';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { useFonts, PlusJakartaSans_400Regular, PlusJakartaSans_500Medium, PlusJakartaSans_600SemiBold, PlusJakartaSans_700Bold } from '@expo-google-fonts/plus-jakarta-sans';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import Constants, { ExecutionEnvironment } from 'expo-constants';
import { useAuthStore } from '@shared/store/authStore';
import { useLocationStore } from '@shared/store/locationStore';
import SpinrConfig from '@shared/config/spinr.config';
import { ErrorBoundary } from '@shared/components/ErrorBoundary';
import { OfflineBanner } from '@shared/components/OfflineBanner';
import {
  initFirebaseServices,
  requestPushPermissionAndGetToken,
  setBackgroundMessageHandler,
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
//    The OS notification itself is rendered by the Android channel
//    configured below (or APNs on iOS); this handler just keeps the
//    runtime alive long enough to let RN Firebase do its thing.
setBackgroundMessageHandler(async (remoteMessage: any) => {
  console.log('[Push] Background FCM:', remoteMessage?.data?.type || remoteMessage?.notification?.title);
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
  // Guard so we only register the FCM token once per auth session.
  const fcmRegisteredRef = useRef(false);

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
        console.log('[Push] FCM token registered with backend');
      } catch (e) {
        console.log('[Push] FCM token registration failed:', e);
      }
    })();
  }, [isAuthInitialized, authToken]);

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
    <ErrorBoundary>
      <OfflineBanner visible={isOffline} onVisibilityChange={setIsOffline} />
      <GestureHandlerRootView style={{ flex: 1 }}>
        <SafeAreaProvider>
          <StatusBar style={isOffline ? "light" : "dark"} />
          <Stack
            screenOptions={{
              headerShown: false,
              animation: 'slide_from_right',
            }}
          >
            <Stack.Screen name="index" />
            <Stack.Screen name="login" />
            <Stack.Screen name="otp" />
            <Stack.Screen name="profile-setup" options={{ gestureEnabled: false }} />
            <Stack.Screen name="become-driver" options={{ gestureEnabled: false }} />
            <Stack.Screen name="driver" options={{ animation: "fade", gestureEnabled: false }} />
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
