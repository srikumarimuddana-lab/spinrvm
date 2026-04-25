import React, { useEffect, useRef, useMemo } from 'react';
import { View, Image, StyleSheet, Animated } from 'react-native';
import { useRouter, useNavigationContainerRef } from 'expo-router';
import { useAuthStore } from '@shared/store/authStore';
import { createLogger } from '@shared/utils/logger';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

const log = createLogger('Index');

// The native splash (configured in app.config.ts) shows the Spinr icon at
// 160px on the brand red background. The React-rendered splash below mirrors
// the EXACT same image at the EXACT same size on the SAME background, so the
// native→React handoff looks like a single continuous screen. The tagline
// fades in beneath the static logo to give a subtle hint of motion.
export default function Index() {
  const router = useRouter();
  const navigationRef = useNavigationContainerRef();
  const { isInitialized, token, user, driver, logout } = useAuthStore();
  const taglineFade = useRef(new Animated.Value(0)).current;
  const hasNavigated = useRef(false);
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
    if (!isInitialized || hasNavigated.current) return;
    if (!navigationRef.isReady()) return;

    hasNavigated.current = true;

    if (!token || !user) {
      router.replace('/login' as any);
      return;
    }

    // Routing rule:
    //   1. Profile complete → /driver/ (home)
    //   2. Profile incomplete → clear session → /login (phone screen)
    //
    // This ensures the phone number screen is ALWAYS the first screen
    // for users who haven't completed their profile. After phone + OTP
    // verification, otp.tsx routes to /profile-setup or /driver based
    // on the profile state.
    //
    // The driver onboarding state (documents_required, documents_expired,
    // pending_review, suspended, etc.) is displayed as a BANNER on the home
    // screen — not as a redirect.
    const hasProfileData = !!(user.first_name && user.last_name && user.email);
    const profileComplete = !!user.profile_complete || hasProfileData;

    if (!profileComplete) {
      // Profile incomplete — clear stale session and send to login.
      // The user will enter their phone, verify OTP, then get routed
      // to /profile-setup by otp.tsx.
      log.info('Profile incomplete, clearing session → /login');
      logout().then(() => {
        router.replace('/login' as any);
      });
    } else {
      // Profile complete — go straight to the driver home.
      // The driver record is auto-created by authStore if missing;
      // the home screen handles the no-driver-record state gracefully.
      router.replace('/driver/' as any);
    }
  }, [isInitialized, token, user, navigationRef.isReady()]);

  return (
    <View style={styles.container}>
      <View style={styles.logoContainer}>
        <Image
          source={require('../assets/images/icon.png')}
          style={styles.logo}
          resizeMode="contain"
        />
        <Animated.Text style={[styles.tagline, { opacity: taglineFade }]}>
          Drive local. Earn local.
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
