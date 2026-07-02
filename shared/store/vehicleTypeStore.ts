import { useEffect } from 'react';
import { AppState } from 'react-native';
import { Image as ExpoImage } from 'expo-image';
import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import AsyncStorage from '@react-native-async-storage/async-storage';
import api from '../api/client';

/**
 * Vehicle type config shared by every map surface (rider nearby markers,
 * driver own-car marker, booking screens). One GET /vehicle-types per app
 * open feeds all consumers — never fetch per driver/marker.
 *
 * Persisted so an offline cold start still resolves the last-synced marker
 * variants; refreshed from the API on launch and on foreground (throttled).
 */
export interface VehicleTypeRecord {
  id: string;
  name?: string;
  description?: string;
  capacity?: number;
  image_url?: string;
  // Admin-configured map marker (standard | xl | premium) — see
  // vehicle_types.marker_variant and CarMarker CAR_IMAGES.
  marker_variant?: string;
  // Admin-uploaded custom marker image (transparent PNG, car facing north).
  // Takes precedence over marker_variant; bundled variants remain the
  // fallback when the image is missing or fails to load.
  marker_image_url?: string;
  [key: string]: unknown;
}

interface VehicleTypeState {
  byId: Record<string, VehicleTypeRecord>;
  lastSyncedAt: number | null;
  isLoading: boolean;
  fetchVehicleTypes: () => Promise<void>;
}

export const useVehicleTypeStore = create<VehicleTypeState>()(
  persist(
    (set, get) => ({
      byId: {},
      lastSyncedAt: null,
      isLoading: false,
      fetchVehicleTypes: async () => {
        if (get().isLoading) return;
        set({ isLoading: true });
        try {
          const res = await api.get('/vehicle-types');
          const byId: Record<string, VehicleTypeRecord> = {};
          for (const vt of (res.data || []) as VehicleTypeRecord[]) {
            if (vt && vt.id) byId[vt.id] = vt;
          }
          set({ byId, lastSyncedAt: Date.now(), isLoading: false });
          // Warm the native image disk cache so custom markers render
          // instantly (and usually offline) when the map mounts.
          // Fire-and-forget — failures just defer to the bundled fallback.
          for (const vt of Object.values(byId)) {
            if (vt.marker_image_url) {
              ExpoImage.prefetch(vt.marker_image_url, 'disk').catch(() => {});
            }
          }
        } catch (error) {
          // Display config only — keep the last persisted map and let
          // markers fall back to the standard sedan for unknown types.
          console.log('vehicleTypeStore: refresh failed', error);
          set({ isLoading: false });
        }
      },
    }),
    {
      name: 'spinr-vehicle-types',
      storage: createJSONStorage(() => AsyncStorage),
      partialize: (state) => ({ byId: state.byId, lastSyncedAt: state.lastSyncedAt }),
    },
  ),
);

// Don't hammer the endpoint when the user rapidly flips between apps; the
// config changes at admin cadence, not seconds.
const FOREGROUND_REFRESH_MIN_MS = 60_000;

/**
 * Mount once in each app's root layout. Syncs vehicle types on launch and
 * whenever the app returns to the foreground (throttled).
 */
export function useVehicleTypesSync(): void {
  const fetchVehicleTypes = useVehicleTypeStore((s) => s.fetchVehicleTypes);
  useEffect(() => {
    void fetchVehicleTypes();
    const sub = AppState.addEventListener('change', (state) => {
      if (state !== 'active') return;
      const last = useVehicleTypeStore.getState().lastSyncedAt;
      if (!last || Date.now() - last > FOREGROUND_REFRESH_MIN_MS) {
        void fetchVehicleTypes();
      }
    });
    return () => sub.remove();
  }, [fetchVehicleTypes]);
}
