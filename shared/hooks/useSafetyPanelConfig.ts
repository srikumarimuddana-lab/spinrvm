/**
 * Resolves everything the Safety panel needs to decide which rows to render.
 *
 * Two sources, deliberately split (migration 316 / routes/settings.py):
 *   - per service area: the LOCAL AUTHORITY (Calgary 311, SGI, ...) plus the
 *     emergency number, because only these genuinely vary by city
 *   - global app_settings: the Spinr safety-team contacts and the two tile
 *     toggles, which are identical everywhere
 *
 * The area is resolved from the user's own coordinates rather than from a
 * ride's service_area_id on purpose: SOS is most often pressed from the home
 * map, where there is no active ride at all. Point-in-polygon mirrors
 * driver-app/hooks/useAirportZones.ts, which already does this against the
 * same `/service-areas` payload.
 *
 * Everything degrades to "hide that row" — never to a placeholder, and never
 * to a blank 911 button. A failed fetch still leaves a usable panel: 911 is
 * hard-defaulted so the one action that matters always works offline.
 */
import { useEffect, useMemo, useState } from 'react';
import api from '../api/client';

/** Hard floor. Every Canadian service area uses 911; the API also coerces
 *  blank/missing values server-side. Duplicated here so a total fetch failure
 *  still yields a working emergency button. */
export const DEFAULT_EMERGENCY_NUMBER = '911';

export interface SafetyAuthority {
  name: string;
  phone?: string;
  url?: string;
  hours?: string;
}

export interface SafetyPanelConfig {
  emergencyNumber: string;
  /** Null when this area has no authority configured, or none could be resolved. */
  authority: SafetyAuthority | null;
  safetyTeamEmail: string;
  safetyTeamPhone: string;
  showShareTrip: boolean;
  showReportIssue: boolean;
  loading: boolean;
}

interface ServiceAreaRow {
  id: string;
  polygon?: { lat: number; lng: number }[];
  emergency_number?: string | null;
  safety_authority_name?: string | null;
  safety_authority_phone?: string | null;
  safety_authority_url?: string | null;
  safety_authority_hours?: string | null;
}

// Ray casting. Same implementation as useAirportZones' — kept local rather
// than imported because that one lives in driver-app and this hook is shared.
function pointInPolygon(lat: number, lng: number, poly: { lat: number; lng: number }[]): boolean {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const yi = poly[i].lat;
    const xi = poly[i].lng;
    const yj = poly[j].lat;
    const xj = poly[j].lng;
    if (yi > lat !== yj > lat && lng < ((xj - xi) * (lat - yi)) / (yj - yi) + xi) {
      inside = !inside;
    }
  }
  return inside;
}

export function useSafetyPanelConfig(lat?: number, lng?: number, enabled = true): SafetyPanelConfig {
  const [areas, setAreas] = useState<ServiceAreaRow[]>([]);
  const [settings, setSettings] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    setLoading(true);
    (async () => {
      // Independent and both optional: one failing must not blank the other.
      const [areasRes, settingsRes] = await Promise.allSettled([
        api.get<ServiceAreaRow[]>('/service-areas'),
        api.get<Record<string, unknown>>('/settings'),
      ]);
      if (cancelled) return;
      if (areasRes.status === 'fulfilled') setAreas(areasRes.value.data || []);
      if (settingsRes.status === 'fulfilled') setSettings(settingsRes.value.data || null);
      setLoading(false);
    })();
    return () => {
      cancelled = true;
    };
  }, [enabled]);

  return useMemo(() => {
    const matched =
      lat !== undefined && lng !== undefined
        ? areas.find((a) => (a.polygon?.length ?? 0) >= 3 && pointInPolygon(lat, lng, a.polygon!))
        : undefined;

    // A name is the minimum for a meaningful row: a phone or URL with nothing
    // to call it is not something we can put in front of someone in distress.
    const name = matched?.safety_authority_name?.trim();
    const authority: SafetyAuthority | null = name
      ? {
          name,
          phone: matched?.safety_authority_phone?.trim() || undefined,
          url: matched?.safety_authority_url?.trim() || undefined,
          hours: matched?.safety_authority_hours?.trim() || undefined,
        }
      : null;

    return {
      emergencyNumber: matched?.emergency_number?.trim() || DEFAULT_EMERGENCY_NUMBER,
      authority,
      safetyTeamEmail: String(settings?.safety_team_email ?? '').trim(),
      safetyTeamPhone: String(settings?.safety_team_phone ?? '').trim(),
      // Default true so a settings fetch failure hides nothing that exists —
      // both are already-shipped capabilities.
      showShareTrip: settings?.sos_show_share_trip !== false,
      showReportIssue: settings?.sos_show_report_issue !== false,
      loading,
    };
  }, [areas, settings, lat, lng, loading]);
}
