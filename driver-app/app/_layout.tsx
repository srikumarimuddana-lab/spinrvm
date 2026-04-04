import React, { useEffect, useState } from 'react';
import { Stack } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { View, ActivityIndicator, StyleSheet, Text, Platform } from 'react-native';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { useFonts, PlusJakartaSans_400Regular, PlusJakartaSans_500Medium, PlusJakartaSans_600SemiBold, PlusJakartaSans_700Bold } from '@expo-google-fonts/plus-jakarta-sans';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { useAuthStore } from '@shared/store/authStore';
import { useLocationStore } from '@shared/store/locationStore';
import SpinrConfig from '@shared/config/spinr.config';
import { ErrorBoundary } from '@shared/components/ErrorBoundary';
import { OfflineBanner } from '@shared/components/OfflineBanner';
import { initFirebaseServices, requestPushPermissionAndGetToken, onForegroundMessage } from '@shared/services/firebase';

export default function RootLayout() {
  const [fontsLoaded, fontError] = useFonts({
    PlusJakartaSans_400Regular,
    PlusJakartaSans_500Medium,
    PlusJakartaSans_600SemiBold,
    PlusJakartaSans_700Bold,
  });

  const { initialize: initializeAuth, isInitialized: isAuthInitialized } = useAuthStore();
  const { initialize: initializeLocation, isInitialized: isLocationInitialized } = useLocationStore();
  const [isOffline, setIsOffline] = useState(false);

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

        // Firebase: FCM, Crashlytics, App Check
        await initFirebaseServices();
        const fcmToken = await requestPushPermissionAndGetToken();
        if (fcmToken) {
          try {
            const api = (await import('@shared/api/client')).default;
            await api.post('/notifications/register-token', { token: fcmToken, platform: Platform.OS });
          } catch (e) { console.log('FCM token reg failed:', e); }
        }
        onForegroundMessage((msg: any) => {
          console.log('[Push] Driver foreground:', msg.notification?.title);
        });
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