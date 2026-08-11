import { useEffect, useState } from 'react';
import api from '@shared/api/client';

/**
 * Reads the driver_discreet_sos_enabled dark-launch flag (ACTION_ITEMS.md
 * B16) from GET /settings (backend/routes/settings.py). Fails closed to
 * `false` while loading and on any fetch error, so a flaky network call
 * never accidentally shows the new, less-tested discreet-SOS UI in place
 * of the already-shipped SOSButton.
 *
 * Deliberately a local hook, not a new app-wide settings context --
 * driver-app has no existing settings-context precedent to extend, and
 * adding one here would touch a much higher-blast-radius surface than
 * this single feature flag needs.
 */
export function useDriverDiscreetSosFlag(): boolean {
  const [enabled, setEnabled] = useState(false);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        const res = await api.get<{ driver_discreet_sos_enabled?: boolean }>('/settings');
        if (!cancelled) setEnabled(Boolean(res.data?.driver_discreet_sos_enabled));
      } catch {
        if (!cancelled) setEnabled(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  return enabled;
}
