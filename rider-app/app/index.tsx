import React, { useEffect } from 'react';
import { View } from 'react-native';
import { useRouter } from 'expo-router';
import { useAuthStore } from '@shared/store/authStore';
import { useRideStore } from '../store/rideStore';

export default function Index() {
  const router = useRouter();
  const { isInitialized, token, user } = useAuthStore();

  useEffect(() => {
    if (!isInitialized) return;

    (async () => {
      const hasProfileData = !!(user?.first_name && user?.last_name && user?.email);
      const profileComplete = !!user?.profile_complete || hasProfileData;

      if (!token) {
        router.replace('/login');
        return;
      }
      if (user && !profileComplete) {
        router.replace('/profile-setup');
        return;
      }

      try {
        await useRideStore.getState().hydrateActiveRide();
        const result = await useRideStore.getState().fetchActiveRide();
        if (result?.active && result.ride) {
          const { status, id: rideId } = result.ride;
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
        // fall through to home on error
      }
      router.replace('/(tabs)');
    })();
    // router is expo-router's stable singleton.
  }, [isInitialized, token, user, router]);

  // Transparent pass-through — BrandSplash (in _layout.tsx) is the only
  // branded loading screen. This screen just routes; it has no visual chrome.
  return <View style={{ flex: 1, backgroundColor: '#FFFFFF' }} />;
}
