import React, { useEffect, useState } from 'react';
import { Text } from '@shared/components/Text';

/** Age comes from sensor capture, never from a polling response arrival. */
export function DriverLocationStatus({ capturedAt, color }: { capturedAt?: string | null; color: string }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 5000);
    return () => clearInterval(timer);
  }, []);
  const captured = Date.parse(capturedAt ?? '');
  const unknown = !Number.isFinite(captured) || captured > now + 5000;
  if (!unknown && now - captured <= 20_000) return null;
  return <Text accessibilityLiveRegion="polite" style={{ color, fontSize: 12, marginVertical: 6 }}>
    {unknown ? 'Waiting for driver location' : 'Location updates delayed · showing last known position'}
  </Text>;
}
