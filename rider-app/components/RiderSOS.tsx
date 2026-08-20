/**
 * Rider SOS button.
 *
 * Renders ONLY the button. The Safety panel it opens is mounted at the app
 * root by SafetySheetHost — see store/safetySheetStore.ts for why that
 * separation is load-bearing rather than stylistic (the panel used to render
 * inside the map's absolutely-positioned SOS overlay and inherited its
 * offset, pushing itself off the right edge of the screen).
 *
 * Gesture contract, unchanged from what riders already know:
 *   hold 1.2s -> sends the alert immediately, exactly as before
 *   short tap -> opens the Safety panel
 */
import React from 'react';
import { SOSButton } from '@shared/components/SOSButton';
import { useSafetySheetStore } from '../store/safetySheetStore';
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
  /**
   * Ride-less SOS (ACTION_ITEMS.md B15(c)). Both optional and forwarded
   * as-is to SOSButton — omitted at every RiderSOS mount site except the
   * home screen, so those stay byte-identical.
   */
  ridelessSosEnabled?: boolean;
  onTriggerRideless?: (
    lat?: number,
    lng?: number,
    idempotencyKey?: string,
  ) => Promise<SOSTriggerResult | void>;
}

export function RiderSOS({
  rideId,
  onTrigger,
  size = 'small',
  t,
  ridelessSosEnabled,
  onTriggerRideless,
}: RiderSOSProps) {
  const openSheet = useSafetySheetStore((s) => s.open);

  return (
    <SOSButton
      rideId={rideId}
      onTrigger={onTrigger}
      size={size}
      t={t}
      onTap={() => openSheet(rideId)}
      ridelessSosEnabled={ridelessSosEnabled}
      onTriggerRideless={onTriggerRideless}
    />
  );
}

export default RiderSOS;
