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
import Constants from 'expo-constants';
import { ErrorBoundary } from '@shared/components/ErrorBoundary';
import { OfflineBanner } from '@shared/components/OfflineBanner';
import { initFirebaseServices, requestPushPermissionAndGetToken, onForegroundMessage, setCrashlyticsUser } from '@shared/services/firebase';

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
        await Promise.all([initializeAuth(), initializeLocation()]);

        // Initialize Firebase (FCM, Crashlytics, App Check)
        await initFirebaseServices();

        // Request push permission and register FCM token
        const fcmToken = await requestPushPermissionAndGetToken();
        if (fcmToken) {
          try {
            const api = (await import('@shared/api/client')).default;
            await api.post('/notifications/register-token', { token: fcmToken, platform: Platform.OS });
          } catch (e) {
            console.log('FCM token registration failed:', e);
          }
        }

        // Handle foreground push messages
        onForegroundMessage((message: any) => {
          console.log('[Push] Foreground message:', message.notification?.title);
          // Could show in-app toast here
        });
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
      <GestureHandlerRootView>
        <View style={{ flex: 1 }}>
          <SafeAreaProvider>
            <StatusBar style={isOffline ? "light" : "dark"} />
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