import React, { useEffect, useRef, useMemo } from 'react';
import { View, Text, Image, StyleSheet, Animated } from 'react-native';
import { useRouter } from 'expo-router';
import { useAuthStore } from '@shared/store/authStore';
import { useRideStore } from '../store/rideStore';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

export default function Index() {
  const router = useRouter();
  const { isInitialized, token, user } = useAuthStore();
  const taglineFade = useRef(new Animated.Value(0)).current;
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);

  useEffect(() => {
    Animated.timing(taglineFade, {
      toValue: 1,
      duration: 700,
      delay: 250,
      useNativeDriver: true,
    }).start();
  }, []);

  useEffect(() => {
    if (!isInitialized) return;

    const timer = setTimeout(async () => {
      const hasProfileData = !!(user?.first_name && user?.last_name && user?.email);
      const profileComplete = !!user?.profile_complete || hasProfileData;

      if (!token) {
        router.replace('/login');
      } else if (user && !profileComplete) {
        router.replace('/profile-setup');
      } else {
        try {
          await useRideStore.getState().hydrateActiveRide();
          const result = await useRideStore.getState().fetchActiveRide();
          if (result?.active && result.ride) {
            const status = result.ride.status;
            const rideId = result.ride.id;
            if (status === 'completed') {
              const ps = result.ride.payment_status;
              if (ps !== 'paid' && ps !== 'waived_admin') {
                router.replace({ pathname: '/ride-completed', params: { rideId } } as any);
                return;
              }
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
      <View style={styles.logoContainer}>
        <Image
          source={require('../assets/images/icon.png')}
          style={styles.logo}
          resizeMode="contain"
        />
        <Animated.Text style={[styles.tagline, { opacity: taglineFade }]}>
          Ride local. Support local.
        </Animated.Text>
      </View>
    </View>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: {
      flex: 1,
      backgroundColor: colors.primary,
      justifyContent: 'center',
      alignItems: 'center',
    },
    logoContainer: {
      alignItems: 'center',
    },
    logo: {
      width: 160,
      height: 160,
    },
    tagline: {
      fontSize: 16,
      fontFamily: 'PlusJakartaSans_400Regular',
      color: 'rgba(255, 255, 255, 0.85)',
      marginTop: 16,
    },
  });
}
