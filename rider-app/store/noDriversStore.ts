/**
 * "No drivers available right now" prompt state.
 *
 * Raised when the backend auto-cancels the ride on screen because no driver
 * accepted (cancellation_type 'no_drivers_found'). The cancel can reach the
 * app three ways — the ride_cancelled WS message, a push tap, or the searching
 * screen's own ride poll — so the state lives here and one root-mounted host
 * (components/NoDriversSheetHost.tsx) renders it, the same pattern as
 * store/safetySheetStore.ts.
 *
 * Callers must only raise it for the ride currently on screen; the existing
 * guards (shouldLeaveScreenForRideCancelled, the searching screen's
 * `currentRide.id === rideId` check) do that before calling in here.
 */
import { create } from 'zustand';
import { useRideStore } from './rideStore';

export interface NoDriversPlace {
  address: string;
  lat: number;
  lng: number;
}

export interface NoDriversPrompt {
  rideId: string;
  pickup: NoDriversPlace;
  dropoff: NoDriversPlace;
}

interface NoDriversState {
  prompt: NoDriversPrompt | null;
  /**
   * The last ride a prompt was raised for. The WS message, the push and the
   * poll can all report the same cancel; only the first one shows the sheet,
   * and a late duplicate after the rider dismissed it does not bring it back.
   */
  _shownRideId: string | null;
  /** Set by "Schedule for later"; ride-options consumes it once and opens its picker. */
  _openScheduleOnArrival: boolean;
  show: (prompt: NoDriversPrompt) => void;
  dismiss: () => void;
  requestScheduleOnArrival: () => void;
  /** Returns true once per request, then resets. */
  consumeScheduleOnArrival: () => boolean;
}

export const useNoDriversStore = create<NoDriversState>((set, get) => ({
  prompt: null,
  _shownRideId: null,
  _openScheduleOnArrival: false,
  show: (prompt) => {
    if (get()._shownRideId === prompt.rideId) return;
    set({ prompt, _shownRideId: prompt.rideId });
  },
  dismiss: () => set({ prompt: null }),
  requestScheduleOnArrival: () => set({ _openScheduleOnArrival: true }),
  consumeScheduleOnArrival: () => {
    if (!get()._openScheduleOnArrival) return false;
    set({ _openScheduleOnArrival: false });
    return true;
  },
}));

interface RideAddresses {
  id?: string | null;
  pickup_address?: string | null;
  pickup_lat?: number | null;
  pickup_lng?: number | null;
  dropoff_address?: string | null;
  dropoff_lat?: number | null;
  dropoff_lng?: number | null;
}

const isCoord = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);

/**
 * Raise the prompt from a snapshot of the cancelled ride. Call it BEFORE
 * clearRide(), which drops the ride. Returns false when the ride lacks the
 * addresses Try again needs, so the caller keeps today's toast instead.
 */
export function offerNoDriversPrompt(ride: RideAddresses | null | undefined): boolean {
  if (!ride?.id) return false;
  if (!isCoord(ride.pickup_lat) || !isCoord(ride.pickup_lng)) return false;
  if (!isCoord(ride.dropoff_lat) || !isCoord(ride.dropoff_lng)) return false;
  useNoDriversStore.getState().show({
    rideId: ride.id,
    pickup: { address: ride.pickup_address ?? '', lat: ride.pickup_lat, lng: ride.pickup_lng },
    dropoff: { address: ride.dropoff_address ?? '', lat: ride.dropoff_lat, lng: ride.dropoff_lng },
  });
  return true;
}

const COORD_EPSILON = 1e-6;
function samePlace(a: { lat: number; lng: number } | null | undefined, b: NoDriversPlace): boolean {
  return !!a && Math.abs(a.lat - b.lat) < COORD_EPSILON && Math.abs(a.lng - b.lng) < COORD_EPSILON;
}

/**
 * Put the cancelled trip back into the booking draft for Try again /
 * Schedule for later. Never books anything: it only restores the addresses
 * and wipes the old quote, so ride-options fetches a fresh one and the rider
 * sees the current price (and any surge) before confirming.
 */
export function prepareRebookDraft(prompt: NoDriversPrompt): void {
  const rides = useRideStore.getState();
  // The push-tap path leaves the cancelled ride in the store; retire it so the
  // booking screen's active-ride check does not treat it as live.
  if (rides.currentRide?.id === prompt.rideId) rides.clearRide();
  // The draft normally still holds this trip (clearRide keeps it). Keep it —
  // stops and vehicle choice included — when it matches; otherwise (e.g. the
  // app restarted mid-search) rebuild pickup/dropoff from the ride.
  if (!samePlace(rides.pickup, prompt.pickup) || !samePlace(rides.dropoff, prompt.dropoff)) {
    rides.setPickup({ ...prompt.pickup });
    rides.setDropoff({ ...prompt.dropoff });
    rides.clearStops();
  }
  rides.setScheduledTime(null);
  rides.clearEstimates();
}
