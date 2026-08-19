/**
 * Mounts the rider Safety panel once, at the app root.
 *
 * Sits in app/_layout.tsx beside ConfirmSheet and Toast — the same pattern the
 * app already uses for anything that must overlay the whole screen. That
 * placement is the fix for the panel rendering inside the map's absolutely-
 * positioned SOS overlay and inheriting its 20pt left offset (see
 * store/safetySheetStore.ts).
 *
 * Owns the pieces the button can no longer supply now that the two live in
 * different parts of the tree: the emergency trigger, the location lookup
 * that resolves which service area the rider is in, and navigation to the
 * report screen.
 */
import React, { useEffect, useState } from 'react';
import { useRouter } from 'expo-router';
import { SafetySheet } from '@shared/components/SafetySheet';
import { getSOSLocation } from '@shared/utils/sosLocation';
import { useSafetySheetStore } from '../store/safetySheetStore';
import { useRideStore } from '../store/rideStore';

export function SafetySheetHost() {
  const router = useRouter();
  const visible = useSafetySheetStore((s) => s.visible);
  const rideId = useSafetySheetStore((s) => s.rideId);
  const close = useSafetySheetStore((s) => s.close);
  const triggerEmergency = useRideStore((s) => s.triggerEmergency);
  const [coords, setCoords] = useState<{ lat?: number; lng?: number }>({});

  // Resolved only while the panel is open. Picks the service area, and
  // therefore whether a local-authority row applies at all. Uses the same
  // hard-bounded lookup as the alert path, so a GPS dead zone can never hang
  // the panel.
  useEffect(() => {
    if (!visible) return;
    let cancelled = false;
    (async () => {
      const c = await getSOSLocation();
      if (!cancelled) setCoords(c);
    })();
    return () => {
      cancelled = true;
    };
  }, [visible]);

  return (
    <SafetySheet
      visible={visible}
      onClose={close}
      rideId={rideId}
      onTrigger={triggerEmergency}
      onReportIssue={() => router.push('/report-safety' as any)}
      lat={coords.lat}
      lng={coords.lng}
    />
  );
}

export default SafetySheetHost;
