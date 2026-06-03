import React, { useEffect, useRef } from 'react';
import { View } from 'react-native';
import { useRouter, useNavigationContainerRef } from 'expo-router';
import { useAuthStore } from '@shared/store/authStore';
import { createLogger } from '@shared/utils/logger';

const log = createLogger('Index');

export default function Index() {
  const router = useRouter();
  const navigationRef = useNavigationContainerRef();
  const { isInitialized, token, user, logout } = useAuthStore();
  const hasNavigated = useRef(false);

  useEffect(() => {
    if (!isInitialized || hasNavigated.current) return;
    if (!navigationRef.isReady()) return;

    hasNavigated.current = true;

    if (!token || !user) {
      router.replace('/login' as any);
      return;
    }

    const hasProfileData = !!(user.first_name && user.last_name && user.email);
    const profileComplete = !!user.profile_complete || hasProfileData;

    if (!profileComplete) {
      log.info('Profile incomplete, clearing session → /login');
      logout().then(() => {
        router.replace('/login' as any);
      });
    } else {
      router.replace('/driver/' as any);
    }
  }, [isInitialized, token, user, navigationRef.isReady()]);

  // Transparent pass-through — BrandSplash (in _layout.tsx) is the only
  // branded loading screen. This screen just routes; it has no visual chrome.
  return <View style={{ flex: 1, backgroundColor: '#FFFFFF' }} />;
}
