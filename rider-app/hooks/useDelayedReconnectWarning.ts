import { useEffect, useState } from 'react';

/** Keep routine socket handoffs from showing a customer-facing warning. */
export const RIDER_RECONNECT_WARNING_DELAY_MS = 10_000;

export function useDelayedReconnectWarning(isReconnecting: boolean, isOffline: boolean): boolean {
  const shouldWarn = isReconnecting && !isOffline;
  const [delayElapsed, setDelayElapsed] = useState(false);

  useEffect(() => {
    if (!shouldWarn) {
      // Reset between reconnect episodes so each one gets its own grace period.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setDelayElapsed(false);
      return;
    }

    const timeout = setTimeout(() => setDelayElapsed(true), RIDER_RECONNECT_WARNING_DELAY_MS);
    return () => clearTimeout(timeout);
  }, [shouldWarn]);

  return shouldWarn && delayElapsed;
}
