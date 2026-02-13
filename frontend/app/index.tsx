import React, { useEffect, useRef } from 'react';
import { View, Text, StyleSheet, Animated } from 'react-native';
import { useRouter } from 'expo-router';
import Constants from 'expo-constants';
import { useAuthStore } from '../store/authStore';
import SpinrConfig from '../config/spinr.config';

export default function Index() {
  const router = useRouter();
  const { isInitialized, token, user } = useAuthStore();
  const fadeAnim = useRef(new Animated.Value(0)).current;
  const scaleAnim = useRef(new Animated.Value(0.5)).current;

  useEffect(() => {
    Animated.parallel([
      Animated.timing(fadeAnim, {
        toValue: 1,
        duration: 1000,
        useNativeDriver: true,
      }),
      Animated.spring(scaleAnim, {
        toValue: 1,
        friction: 8,
        useNativeDriver: true,
      }),
    ]).start();
  }, []);

  useEffect(() => {
    if (!isInitialized) return;

    const timer = setTimeout(() => {
      if (!token) {
        router.replace('/login');
      } else if (user && !user.profile_complete) {
        router.replace('/profile-setup');
      } else {
        // Profile is complete, check App Variant
        const appVariant = Constants.expoConfig?.extra?.APP_VARIANT;

        if (appVariant === 'driver') {
          if (user?.is_driver) {
            router.replace('/(driver)');
          } else {
            router.replace('/become-driver');
          }
        } else {
          // Default / Rider App
          router.replace('/(tabs)');
        }
      }
    }, 1500);

    return () => clearTimeout(timer);
  }, [isInitialized, token, user]);

  return (
    <View style={styles.container}>
      <Animated.View
        style={[
          styles.logoContainer,
          {
            opacity: fadeAnim,
            transform: [{ scale: scaleAnim }],
          },
        ]}
      >
        <Text style={styles.logo}>Spinr</Text>
        <Text style={styles.tagline}>Ride local. Support local.</Text>
      </Animated.View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: SpinrConfig.theme.colors.primary,
    justifyContent: 'center',
    alignItems: 'center',
  },
  logoContainer: {
    alignItems: 'center',
  },
  logo: {
    fontSize: 64,
    fontFamily: 'PlusJakartaSans_700Bold',
    color: '#FFFFFF',
    letterSpacing: -2,
  },
  tagline: {
    fontSize: 16,
    fontFamily: 'PlusJakartaSans_400Regular',
    color: 'rgba(255, 255, 255, 0.8)',
    marginTop: 8,
  },
});
