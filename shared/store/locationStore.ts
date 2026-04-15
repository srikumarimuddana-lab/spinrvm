import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import { Platform } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';

interface LocationState {
  currentLocation: {
    latitude: number | null;
    longitude: number | null;
    timestamp: number | null;
  };
  lastKnownLocation: {
    latitude: number | null;
    longitude: number | null;
    timestamp: number | null;
  };
  isInitialized: boolean;
  error: string | null;
  setCurrentLocation: (location: { latitude: number; longitude: number; timestamp: number }) => void;
  setLastKnownLocation: (location: { latitude: number; longitude: number; timestamp: number }) => void;
  setError: (error: string) => void;
  initialize: () => Promise<void>;
}

export const useLocationStore = create<LocationState>()(
  persist(
    (set) => ({
      currentLocation: {
        latitude: null,
        longitude: null,
        timestamp: null,
      },
      lastKnownLocation: {
        latitude: null,
        longitude: null,
        timestamp: null,
      },
      isInitialized: false,
      error: null,
      setCurrentLocation: (location: { latitude: number; longitude: number; timestamp: number }) =>
        set({
          currentLocation: {
            latitude: location.latitude,
            longitude: location.longitude,
            timestamp: location.timestamp,
          },
        }),
      setLastKnownLocation: (location: { latitude: number; longitude: number; timestamp: number }) =>
        set({
          lastKnownLocation: {
            latitude: location.latitude,
            longitude: location.longitude,
            timestamp: location.timestamp,
          },
        }),
      setError: (error: string) => set({ error }),
      initialize: async () => {
        try {
          // Request location permissions
          let status = 'granted';
          if (navigator.permissions && navigator.permissions.query) {
            const permissionStatus = await navigator.permissions.query({ name: 'geolocation' });
            status = permissionStatus.state;
          }
          
          if (status !== 'granted') {
            throw new Error('Location permission denied');
          }

          // Get initial location
          const position = await new Promise<GeolocationPosition>((resolve, reject) => {
            navigator.geolocation.getCurrentPosition(
              resolve,
              reject,
              { enableHighAccuracy: true, timeout: 5000, maximumAge: 0 }
            );
          });

          set({
            currentLocation: {
              latitude: position.coords.latitude,
              longitude: position.coords.longitude,
              timestamp: position.timestamp,
            },
            lastKnownLocation: {
              latitude: position.coords.latitude,
              longitude: position.coords.longitude,
              timestamp: position.timestamp,
            },
            isInitialized: true,
            error: null,
          });

          // NOTE: watchPosition was removed here. Previously this store
          // started a continuous geolocation watcher that ran for the
          // entire app session. Neither the rider-app nor the driver-app
          // consumed the resulting `currentLocation` updates — the
          // driver-app uses its own high-accuracy watcher in
          // useDriverDashboard.ts, and the rider-app reads a one-shot
          // position from expo-location for pickup selection. The
          // continuous watcher just drained battery on both platforms
          // with no consumer.
          //
          // If a new feature needs continuous background position in
          // the shared store, add an explicit `startWatching()` action
          // that callers opt into rather than running on init.
        } catch (err: any) {
          set({ 
            error: err.message || 'Failed to initialize location services',
            isInitialized: true 
          });
        }
      },
    }),
    {
      name: 'location-storage',
      storage: createJSONStorage(() => Platform.OS === 'web' ? localStorage : AsyncStorage),
    }
  )
);

// Mock implementation for server-side rendering
if (typeof window === 'undefined') {
  Object.assign(navigator, {
    geolocation: {
      getCurrentPosition: () => {},
      watchPosition: () => {},
    },
  });
}