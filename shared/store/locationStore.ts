import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import { Platform } from 'react-native';

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

          // Start watching position with platform-specific options
          const watchOptions: PositionOptions = {
            enableHighAccuracy: true,
            timeout: 10000,
            maximumAge: 5000
          };

          // Only add distanceFilter for non-web platforms (React Native)
          if (Platform.OS !== 'web') {
            (watchOptions as any).distanceFilter = 10;
          }

          navigator.geolocation.watchPosition(
            (position) => {
              set({
                currentLocation: {
                  latitude: position.coords.latitude,
                  longitude: position.coords.longitude,
                  timestamp: position.timestamp,
                },
              });
            },
            (error) => {
              set({ error: error.message });
            },
            watchOptions
          );
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
      storage: createJSONStorage(() => localStorage),
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