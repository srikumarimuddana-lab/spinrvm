import { useEffect, useState } from 'react';
import api from '@shared/api/client';

/**
 * Reads the directions_proxy_enabled dark-launch flag (R7,
 * docs/audit/ride-experience/ROADMAP.md) from GET /settings
 * (backend/routes/settings.py). Fails closed to `false` while loading and
 * on any fetch error, so a flaky network call never accidentally skips the
 * on-device MapViewDirections fallback before the flag is confirmed on.
 *
 * `loaded` distinguishes "confirmed off" from "not answered yet" -- a
 * caller that also has an on-device fallback path (the driver dashboard's
 * MapViewDirections) must gate that fallback on `loaded`, not just
 * `!enabled`. Without it, a caller mounted mid-flow (e.g. app relaunch
 * while already navigating to pickup) would mount the on-device path on
 * the `enabled=false` default the instant before the real value (`true`)
 * arrives, firing both the proxy and the on-device Directions call for the
 * same generation with no way to cancel the on-device one.
 *
 * Deliberately a local hook, not a shared context -- mirrors
 * useDriverDiscreetSosFlag.ts's own precedent (driver-app has no
 * app-wide settings-context the way rider-app's app/_layout.tsx does).
 */
export function useDirectionsProxyFlag(): { enabled: boolean; loaded: boolean } {
  const [enabled, setEnabled] = useState(false);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        const res = await api.get<{ directions_proxy_enabled?: boolean }>('/settings');
        if (!cancelled) setEnabled(Boolean(res.data?.directions_proxy_enabled));
      } catch {
        if (!cancelled) setEnabled(false);
      } finally {
        if (!cancelled) setLoaded(true);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  return { enabled, loaded };
}
