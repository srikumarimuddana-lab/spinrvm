import React, { useEffect } from 'react';
import { Stack } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { View, ActivityIndicator, StyleSheet, Text, Platform } from 'react-native';
import { useFonts, PlusJakartaSans_400Regular, PlusJakartaSans_500Medium, PlusJakartaSans_600SemiBold, PlusJakartaSans_700Bold } from '@expo-google-fonts/plus-jakarta-sans';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { useAuthStore } from '@shared/store/authStore';
import SpinrConfig from '@shared/config/spinr.config';

export default function RootLayout() {
  const [fontsLoaded, fontError] = useFonts({
    PlusJakartaSans_400Regular,
    PlusJakartaSans_500Medium,
    PlusJakartaSans_600SemiBold,
    PlusJakartaSans_700Bold,
  });

  const { initialize, isInitialized } = useAuthStore();

  useEffect(() => {
    const init = async () => {
      try {
        await initialize();
      } catch (err: any) {
        console.log('Init error:', err);
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

  if (!fontsLoaded || !isInitialized) {
    return (
      <View style={styles.loadingContainer}>
        <Text style={styles.logoText}>Spinr</Text>
        <ActivityIndicator size="large" color="#FFFFFF" style={{ marginTop: 20 }} />
      </View>
    );
  }

  return (
    <SafeAreaProvider>
      <StatusBar style="dark" />
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
        <Stack.Screen name="vehicle-info" />
        <Stack.Screen name="documents" />
      </Stack>
    </SafeAreaProvider>
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
