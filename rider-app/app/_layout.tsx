import React, { useCallback, useEffect, useState } from 'react';
export const StripeKeyContext = React.createContext<string | null>(null);
import { Stack, router } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { View, ActivityIndicator, StyleSheet, Text, Platform } from 'react-native';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { StripeProvider } from '@stripe/stripe-react-native';
import {
  useFonts,
  PlusJakartaSans_400Regular,
  PlusJakartaSans_500Medium,
  PlusJakartaSans_600SemiBold,
  PlusJakartaSans_700Bold,
} from '@expo-google-fonts/plus-jakarta-sans';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import * as SplashScreen from 'expo-splash-screen';

SplashScreen.preventAutoHideAsync().catch(() => {});

import api from '@shared/api/client';
import { useAuthStore } from '@shared/store/authStore';
import { useLocationStore } from '@shared/store/locationStore';
import SpinrConfig from '@shared/config/spinr.config';
import { ErrorBoundary } from '@shared/components/ErrorBoundary';
import { ThemeProvider, useTheme } from '@shared/theme/ThemeContext';
import { useRideStatusNotification } from '../hooks/useRideStatusNotification';
import Toast from '../components/Toast';

export default function RootLayout() {
  const [fontsLoaded, fontError] = useFonts({
    PlusJakartaSans_400Regular,
    PlusJakartaSans_500Medium,
    PlusJakartaSans_600SemiBold,
    PlusJakartaSans_700Bold,
  });

  const { initialize: initializeAuth, isInitialized: isAuthInitialized } = useAuthStore();
  const { initialize: initializeLocation, isInitialized: isLocationInitialized } = useLocationStore();
  const [stripePublishableKey, setStripePublishableKey] = useState<string | null>(null);

  useEffect(() => {
    const init = async () => {
      try {
        await Promise.all([initializeAuth(), initializeLocation()]);
        if (Platform.OS === 'android') {
          try {
            const Notif = require('expo-notifications');
            await Notif.setNotificationChannelAsync('ride-updates', {
              name: 'Ride Updates',
              importance: Notif.AndroidImportance.HIGH,
              sound: 'default',
              vibrationPattern: [0, 300, 150, 300],
              enableVibrate: true,
            });
            await Notif.setNotificationChannelAsync('ride-status-live', {
              name: 'Live Ride Status',
              description: 'Shows current ride progress in the notification bar.',
              importance: Notif.AndroidImportance.LOW,
              sound: null,
              enableVibrate: false,
            });
            await Notif.setNotificationChannelAsync('default', {
              name: 'Default',
              importance: Notif.AndroidImportance.DEFAULT,
              sound: 'default',
            });
          } catch (e) {
            console.log('[Push] Channel setup failed:', e);
          }
        }
      } catch (err) {
        console.error('Initialization error:', err);
      }
    };
    init();
  }, []);

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
      <RootLayoutInner stripePublishableKey={stripePublishableKey} />
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

function RootLayoutInner({ stripePublishableKey }: { stripePublishableKey: string | null }) {
  const { isDark } = useTheme();
  useRideStatusNotification();

  return (
    <ErrorBoundary>
      <GestureHandlerRootView style={{ flex: 1 }}>
        <SafeAreaProvider>
          <StatusBar style={isDark ? 'light' : 'dark'} />
          <StripeKeyContext.Provider value={stripePublishableKey}>
          <MaybeStripeProvider publishableKey={stripePublishableKey}>
          <Stack
            screenOptions={{
              headerShown: false,
              animation: 'slide_from_right',
            }}
          >
            <Stack.Screen name="index" options={{ animation: 'none' }} />
            <Stack.Screen name="(tabs)" options={{ headerShown: false, animation: 'none' }} />
            <Stack.Screen name="login" />
            <Stack.Screen name="otp" />
            <Stack.Screen name="profile-setup" />
            <Stack.Screen name="search-destination" />
            <Stack.Screen name="ride-options" />
            <Stack.Screen name="confirm-pickup" />
            <Stack.Screen name="pick-on-map" />
            <Stack.Screen name="driver-arriving" />
            <Stack.Screen name="driver-arrived" />
            <Stack.Screen name="ride-in-progress" />
            <Stack.Screen name="ride-completed" />
            <Stack.Screen name="ride-details" />
            <Stack.Screen name="ride-status" />
            <Stack.Screen name="ride-tracking-webview" />
            <Stack.Screen name="chat-driver" />
            <Stack.Screen name="wallet" />
            <Stack.Screen name="manage-cards" />
            <Stack.Screen name="payment-confirm" />
            <Stack.Screen name="notifications" />
            <Stack.Screen name="saved-places" />
            <Stack.Screen name="scheduled-rides" />
            <Stack.Screen name="settings" />
            <Stack.Screen name="promotions" />
            <Stack.Screen name="loyalty" />
            <Stack.Screen name="support" />
            <Stack.Screen name="legal" />
            <Stack.Screen name="emergency-contacts" />
            <Stack.Screen name="report-safety" />
            <Stack.Screen name="privacy-settings" />
            <Stack.Screen name="accessibility" />
            <Stack.Screen name="work-profile" />
            <Stack.Screen name="work-allowance-request" />
            <Stack.Screen name="become-driver" />
          </Stack>
          </MaybeStripeProvider>
          </StripeKeyContext.Provider>
          <Toast />
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
