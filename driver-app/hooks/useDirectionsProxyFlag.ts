import { useEffect, useState } from 'react';
import api from '@shared/api/client';

/**
 * Reads the directions_proxy_enabled dark-launch flag (R7,
 * docs/audit/ride-experience/ROADMAP.md) from GET /settings
 * (backend/routes/settings.py). Fails closed to `false` while loading and
 * on any fetch error, so a flaky network call never accidentally skips the
 * on-device MapViewDirections fallback before the flag is confirmed on.
 *
 * Deliberately a local hook, not a shared context -- mirrors
 * useDriverDiscreetSosFlag.ts's own precedent (driver-app has no
 * app-wide settings-context the way rider-app's app/_layout.tsx does).
 */
export function useDirectionsProxyFlag(): boolean {
  const [enabled, setEnabled] = useState(false);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        const res = await api.get<{ directions_proxy_enabled?: boolean }>('/settings');
        if (!cancelled) setEnabled(Boolean(res.data?.directions_proxy_enabled));
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
