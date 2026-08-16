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
/**
 * The retry-wrapped emergency POST, as a plain function.
 *
 * Extracted from the hook so a non-React caller can reach the SAME code path —
 * specifically the Android Auto surface (`lib/androidAuto/register.ts`), which
 * runs outside the component tree and cannot call a hook. Duplicating this
 * would mean two retry policies on an emergency path that must not drift, and
 * the double-wrapping hazard in the docstring above is exactly what that kind
 * of drift causes.
 *
 * Callers get ONE retry policy: 3 attempts, 1000ms/2000ms backoff. Never wrap
 * this in another retry.
 */
export async function triggerDriverEmergency(
  rideId: string,
  lat?: number,
  lng?: number,
): Promise<DriverSafetyTriggerResult> {
  let lastError: unknown;
  for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt++) {
    if (attempt > 0) {
      const delay = RETRY_DELAYS_MS[attempt - 1] ?? RETRY_DELAYS_MS[RETRY_DELAYS_MS.length - 1];
      await new Promise<void>((resolve) => setTimeout(resolve, delay));
    }
    try {
      const res = await api.post<{ contacts?: DriverSafetyContactStatus[] }>(
        `/rides/${rideId}/emergency`,
        { latitude: lat, longitude: lng },
      );
      return { contacts: res.data?.contacts || [] };
    } catch (err) {
      lastError = err;
    }
  }
  throw lastError;
}

export function useDriverSafetyTrigger() {
  const trigger = useCallback(
    (rideId: string, lat?: number, lng?: number): Promise<DriverSafetyTriggerResult> =>
      triggerDriverEmergency(rideId, lat, lng),
    [],
  );

  return { trigger };
}
