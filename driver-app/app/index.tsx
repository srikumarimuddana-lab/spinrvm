import React, { useEffect, useRef } from 'react';
import { View, Text, StyleSheet, Animated } from 'react-native';
import { useRouter, useNavigationContainerRef } from 'expo-router';
import { useAuthStore } from '@shared/store/authStore';
import SpinrConfig from '@shared/config/spinr.config';

export default function Index() {
  const router = useRouter();
  const navigationRef = useNavigationContainerRef();
  const { isInitialized, token, user } = useAuthStore();
  const fadeAnim = useRef(new Animated.Value(0)).current;
  const scaleAnim = useRef(new Animated.Value(0.5)).current;
  const hasNavigated = useRef(false);

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
    if (!isInitialized || hasNavigated.current) return;
    if (!navigationRef.isReady()) return;

    hasNavigated.current = true;

    if (!token || !user) {
      router.replace('/login' as any);
      return;
    }

    // Routing rule (product decision, 2026-04-04):
    //
    //   1. New user (no first_name/last_name/email)  → /profile-setup
    //   2. Anyone who has already completed profile  → /driver/ (home)
    //
    // The driver onboarding state (documents_required, documents_expired,
    // pending_review, suspended, etc.) is displayed as a BANNER on the home
    // screen — not as a redirect. A driver whose document expired should
    // still land on home and see the re-upload banner there; we never push
    // them out of the dashboard against their will.
    const hasProfileData = !!(user.first_name && user.last_name && user.email);
    const profileComplete = !!user.profile_complete || hasProfileData;

    if (!profileComplete) {
      router.replace('/profile-setup' as any);
    } else {
      router.replace('/driver/' as any);
    }
  }, [isInitialized, token, user, navigationRef.isReady()]);

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
