import { useEffect } from 'react';
import { AppState } from 'react-native';
import { useRideStore } from '../store/rideStore';

/** A connected rider socket does not prove the driver's GPS is arriving. */
export function useRideLocationFallback(rideId: string | undefined): void {
  const fetchRide = useRideStore(state => state.fetchRide);
  useEffect(() => {
    if (!rideId) return;
    let fetching = false;
    const refresh = async () => {
      if (fetching) return;
      fetching = true;
      try { await fetchRide(rideId); } finally { fetching = false; }
    };
    void refresh();
    const interval = setInterval(() => {
      const state = useRideStore.getState();
      if (!state.wsConnected || Date.now() - state._lastWsDriverPositionAt >= 10_000) void refresh();
    }, 15_000);
    const sub = AppState.addEventListener('change', state => { if (state === 'active') void refresh(); });
    return () => { clearInterval(interval); sub.remove(); };
  }, [rideId, fetchRide]);
}
