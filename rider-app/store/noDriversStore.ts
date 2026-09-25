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
import api from '@shared/api/client';
import { registerLogoutCallback } from '@shared/store/authStore';
import { useRideStore } from './rideStore';
import { isNoDriversCancellation } from '../utils/noDriversSignal';

export interface NoDriversPlace {
  address: string;
  lat: number;
  lng: number;
}

export interface NoDriversPrompt {
  rideId: string;
  pickup: NoDriversPlace;
  dropoff: NoDriversPlace;
  /** Intermediate stops of the cancelled ride, when it had any. */
  stops?: NoDriversPlace[];
}

interface NoDriversState {
  /**
   * settings.rider_no_drivers_sheet_enabled (GET /settings, set in
   * app/_layout.tsx). Off by default: no sheet, no resume lookup, and the
   * existing toasts, exactly as before this sheet existed.
   */
  enabled: boolean;
  setEnabled: (enabled: boolean) => void;
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
  enabled: false,
  setEnabled: (enabled) => set({ enabled }),
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
  stops?: { address?: string | null; lat?: number | null; lng?: number | null }[] | null;
}

const isCoord = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);

/**
 * Raise the prompt from a snapshot of the cancelled ride. Call it BEFORE
 * clearRide(), which drops the ride. Returns false when the sheet is switched
 * off or the ride lacks the addresses Try again needs, so the caller keeps
 * today's toast instead.
 */
export function offerNoDriversPrompt(ride: RideAddresses | null | undefined): boolean {
  if (!useNoDriversStore.getState().enabled) return false;
  if (!ride?.id) return false;
  if (!isCoord(ride.pickup_lat) || !isCoord(ride.pickup_lng)) return false;
  if (!isCoord(ride.dropoff_lat) || !isCoord(ride.dropoff_lng)) return false;
  const stops: NoDriversPlace[] = (ride.stops ?? [])
    .filter((st): st is { address?: string | null; lat: number; lng: number } => isCoord(st?.lat) && isCoord(st?.lng))
    .map((st) => ({ address: st.address ?? '', lat: st.lat, lng: st.lng }));
  useNoDriversStore.getState().show({
    rideId: ride.id,
    pickup: { address: ride.pickup_address ?? '', lat: ride.pickup_lat, lng: ride.pickup_lng },
    dropoff: { address: ride.dropoff_address ?? '', lat: ride.dropoff_lat, lng: ride.dropoff_lng },
    ...(stops.length > 0 ? { stops } : {}),
  });
  return true;
}

/**
 * Foreground resume: /rides/active only says "no active ride", not why, and
 * fetchActiveRide() then clears the local ride — so a rider who reopens the
 * app after the no-drivers cancel would get no explanation. Given the ride
 * that was searching before that check, look it up once and raise the prompt
 * if it ended for no drivers. Returns true when the prompt was raised.
 * Errors propagate to the caller.
 */
export async function raiseNoDriversPromptAfterResume(
  rideBefore: { id?: string; status?: string } | null | undefined,
): Promise<boolean> {
  if (!useNoDriversStore.getState().enabled) return false;
  if (!rideBefore?.id) return false;
  if (rideBefore.status !== 'searching' && rideBefore.status !== 'driver_assigned') return false;
  // A newer ride replaced it meanwhile — not the ride on screen any more.
  const now = useRideStore.getState().currentRide;
  if (now && now.id !== rideBefore.id) return false;
  const res = await api.get<RideAddresses & { status?: string; cancellation_type?: string | null }>(
    `/rides/${rideBefore.id}`,
  );
  const ride = res?.data;
  if (ride?.status !== 'cancelled' || !isNoDriversCancellation(ride)) return false;
  if (!offerNoDriversPrompt(ride)) return false;
  // The server confirmed the cancel; retire the local copy the same way the
  // ride_cancelled WS path does (fetchActiveRide keeps it when an older
  // ride's cancel latch is still set).
  const rides = useRideStore.getState();
  if (rides.currentRide?.id === rideBefore.id) rides.clearRide();
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
    // Keep a multi-stop trip multi-stop: Try again must not quietly quote a
    // direct pickup-to-dropoff ride instead.
    for (const stop of prompt.stops ?? []) rides.addStop({ ...stop });
  }
  rides.setScheduledTime(null);
  rides.clearEstimates();
}

// Per-session state: the prompt holds the cancelled ride's addresses, so a
// logout must drop it — otherwise the next account on this device could see
// the sheet and rebook the previous rider's trip. Same pattern as rideStore.
registerLogoutCallback(() => {
  useNoDriversStore.setState({ prompt: null, _shownRideId: null, _openScheduleOnArrival: false });
});
