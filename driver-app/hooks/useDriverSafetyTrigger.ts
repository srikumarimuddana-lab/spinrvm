import { useCallback } from 'react';
import api from '@shared/api/client';

// Same retry policy SOSButton.tsx already uses (3 total attempts,
// 1000ms/2000ms backoff) -- reimplemented as local constants rather than
// imported from SOSButton.tsx, which stays untouched by B16.
const MAX_ATTEMPTS = 3;
const RETRY_DELAYS_MS = [1000, 2000];

export interface DriverSafetyContactStatus {
  id: string;
  name: string;
  notified: boolean;
}

export interface DriverSafetyTriggerResult {
  contacts: DriverSafetyContactStatus[];
}

/**
 * Retry-wrapped POST /rides/{rideId}/emergency for the driver Safety
 * shield/overlay (ACTION_ITEMS.md B16).
 *
 * IMPORTANT: this must be used ONLY as SafetyShield/SafetyOverlay's
 * onTrigger -- they have no retry logic of their own. Never pass it to the
 * legacy shared SOSButton, which already retries 3x internally; wrapping it
 * a second time would reproduce the rider-app bug where a single press can
 * issue up to 9 actual POSTs (SOSButton's 3 outer attempts x rideStore's 3
 * inner attempts). The flag-off legacy driver-app path keeps its own
 * separate, single-attempt-plus-rethrow onTrigger (see (tabs)/index.tsx).
 */
export function useDriverSafetyTrigger() {
  const trigger = useCallback(
    async (rideId: string, lat?: number, lng?: number): Promise<DriverSafetyTriggerResult> => {
      // One key per trigger, generated outside the loop so all attempts share
      // it. The backend returns the original incident rather than inserting a
      // duplicate and re-sending emergency-contact SMS when a retry follows a
      // lost response (migration 315). This hook owns its own retry ladder, so
      // it owns its own key -- same contract SOSButton uses on the rider side.
      const idempotencyKey = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
      let lastError: unknown;
      for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt++) {
        if (attempt > 0) {
          const delay = RETRY_DELAYS_MS[attempt - 1] ?? RETRY_DELAYS_MS[RETRY_DELAYS_MS.length - 1];
          await new Promise<void>((resolve) => setTimeout(resolve, delay));
        }
        try {
          const res = await api.post<{ contacts?: DriverSafetyContactStatus[] }>(
            `/rides/${rideId}/emergency`,
            { latitude: lat, longitude: lng, idempotency_key: idempotencyKey },
          );
          return { contacts: res.data?.contacts || [] };
        } catch (err) {
          lastError = err;
        }
      }
      throw lastError;
    },
    [],
  );

  return { trigger };
}
