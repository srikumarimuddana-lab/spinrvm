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

    if (!token) {
      router.replace('/login' as any);
      return;
    }
    if (!user) {
      router.replace('/login' as any);
      return;
    }

    // Route based on the driver onboarding state machine returned by the
    // backend in /auth/me. This is the authoritative source — the client
    // should not second-guess it. Each state has a canonical next screen.
    //
    //   profile_incomplete  → /profile-setup
    //   vehicle_required    → /become-driver
    //   documents_required  → /documents
    //   documents_rejected  → /documents  (banner inside explains)
    //   documents_expired   → /documents  (banner inside explains)
    //   pending_review      → /driver     (dashboard, "under review" state)
    //   verified            → /driver     (fully unlocked)
    //   suspended           → /driver     (dashboard, suspended banner)
    //
    // If the backend didn't send a status (non-driver, legacy build, or
    // error), fall back to the legacy boolean + is_driver branching.
    const status = user.driver_onboarding_status;
    if (status) {
      const next = user.driver_onboarding_next_screen || '/driver';
      router.replace(next as any);
      return;
    }

    // Legacy fallback (rider / pre-state-machine backend).
    const hasProfileData = !!(user.first_name && user.last_name && user.email);
    const profileComplete = !!user.profile_complete || hasProfileData;
    if (!profileComplete) {
      router.replace('/profile-setup' as any);
    } else if (user.is_driver) {
      router.replace('/driver/' as any);
    } else {
      router.replace('/become-driver' as any);
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
