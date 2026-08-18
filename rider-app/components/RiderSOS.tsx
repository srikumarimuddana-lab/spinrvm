/**
 * Rider SOS: the button plus its Safety panel, bundled.
 *
 * Exists so the five SOSButton call sites across four screens stay one-liners
 * and cannot drift apart — the panel's wiring (location lookup, report-issue
 * navigation, open/close state) lives here once rather than being copy-pasted
 * into each screen.
 *
 * Gesture contract, unchanged from what riders already know:
 *   hold 1.2s -> sends the alert immediately, exactly as before
 *   short tap -> opens the Safety panel (new)
 */
import React, { useCallback, useEffect, useState } from 'react';
import { useRouter } from 'expo-router';
import { SOSButton } from '@shared/components/SOSButton';
import { SafetySheet } from '@shared/components/SafetySheet';
import { getSOSLocation } from '@shared/utils/sosLocation';
import type { SOSTriggerResult } from '@shared/types/safety';

interface RiderSOSProps {
  rideId?: string;
  onTrigger: (
    rideId: string,
    lat?: number,
    lng?: number,
    idempotencyKey?: string,
  ) => Promise<SOSTriggerResult | void>;
  size?: 'small' | 'large';
  t?: (key: string) => string;
}

export function RiderSOS({ rideId, onTrigger, size = 'small', t }: RiderSOSProps) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [coords, setCoords] = useState<{ lat?: number; lng?: number }>({});

  // Resolved only when the panel opens, not on every screen render: this is
  // what picks the service area (and therefore whether a local-authority row
  // appears at all), and it uses the same hard-bounded lookup the alert path
  // uses, so a GPS dead zone can never hang the panel.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    (async () => {
      const c = await getSOSLocation();
      if (!cancelled) setCoords(c);
    })();
    return () => {
      cancelled = true;
    };
  }, [open]);

  const reportIssue = useCallback(() => router.push('/report-safety' as any), [router]);

  return (
    <>
      <SOSButton
        rideId={rideId}
        onTrigger={onTrigger}
        size={size}
        t={t}
        onTap={() => setOpen(true)}
      />
      <SafetySheet
        visible={open}
        onClose={() => setOpen(false)}
        rideId={rideId}
        onTrigger={onTrigger}
        onReportIssue={reportIssue}
        lat={coords.lat}
        lng={coords.lng}
      />
    </>
  );
}

export default RiderSOS;
