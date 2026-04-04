import React, { useEffect, useRef } from 'react';
import { View, Text, StyleSheet, Animated } from 'react-native';
import { useRouter } from 'expo-router';
import { useAuthStore } from '@shared/store/authStore';
import { useRideStore } from '../store/rideStore';
import SpinrConfig from '@shared/config/spinr.config';

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

    const timer = setTimeout(async () => {
      // Derive profile-complete from row data, not the stored flag alone.
      // See driver-app/app/index.tsx for rationale.
      const hasProfileData = !!(user?.first_name && user?.last_name && user?.email);
      const profileComplete = !!user?.profile_complete || hasProfileData;

      if (!token) {
        router.replace('/login');
      } else if (user && !profileComplete) {
        router.replace('/profile-setup');
      } else {
        // Check for active/pending ride before going to home
        try {
          const result = await useRideStore.getState().fetchActiveRide();
          if (result?.active && result.ride) {
            const status = result.ride.status;
            const rideId = result.ride.id;
            if (status === 'completed') {
              router.replace({ pathname: '/ride-completed', params: { rideId } } as any);
              return;
            } else if (status === 'in_progress') {
              router.replace({ pathname: '/ride-in-progress', params: { rideId } } as any);
              return;
            } else if (status === 'driver_arrived') {
              router.replace({ pathname: '/driver-arrived', params: { rideId } } as any);
              return;
            } else if (status === 'driver_assigned' || status === 'driver_accepted' || status === 'searching') {
              router.replace({ pathname: '/driver-arriving', params: { rideId } } as any);
              return;
            }
          }
        } catch {
          // If check fails, just go to home
        }
        router.replace('/(tabs)');
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
