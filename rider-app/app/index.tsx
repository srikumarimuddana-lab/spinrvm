import React, { useEffect, useRef } from 'react';
import { View, Text, StyleSheet, Animated } from 'react-native';
import { useRouter } from 'expo-router';
import { useAuthStore } from '@shared/store/authStore';
import SpinrConfig from '@shared/config/spinr.config';

export default function Index() {
  const router = useRouter();
  const { isInitialized, token, user } = useAuthStore();
  const taglineFade = useRef(new Animated.Value(0)).current;

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

    const timer = setTimeout(() => {
      if (!token) {
        router.replace('/login');
        return;
      }

      const profileComplete = user?.first_name && user?.last_name && user?.email;
      if (!profileComplete) {
        router.replace('/profile-setup');
        return;
      }

      router.replace('/(tabs)' as any);
    }, 1500);

    return () => clearTimeout(timer);
  }, [isInitialized]);

  return (
    <View style={styles.container}>
      <Text style={styles.logo}>Spinr</Text>
      <Animated.Text style={[styles.tagline, { opacity: taglineFade }]}>
        Ride local. Support local.
      </Animated.Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: SpinrConfig.theme.colors.primary,
  },
  logo: {
    fontSize: 56,
    fontWeight: '700',
    color: '#FFFFFF',
  },
  tagline: {
    fontSize: 16,
    color: '#FFFFFF',
    marginTop: 8,
    fontFamily: 'PlusJakartaSans_400Regular',
  },
});
