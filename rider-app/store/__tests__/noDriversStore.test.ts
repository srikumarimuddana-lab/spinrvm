/**
 * No-drivers prompt store + Try again draft preparation.
 *
 * Pins:
 *   - one prompt per ride (WS, push and poll can all report the same cancel)
 *   - a ride without coordinates does not raise the prompt (caller keeps the toast)
 *   - Try again never books: it restores the trip, wipes the old quote, and
 *     leaves the fresh fetch to ride-options
 *   - the cancelled ride still in the store is retired, a different ride is not
 *
 * Code under test: rider-app/store/noDriversStore.ts
 */
import api from '@shared/api/client';
import { useRideStore } from '../rideStore';
import {
  useNoDriversStore,
  offerNoDriversPrompt,
  prepareRebookDraft,
  raiseNoDriversPromptAfterResume,
  type NoDriversPrompt,
} from '../noDriversStore';
import { registerLogoutCallback } from '@shared/store/authStore';

// Captured at import time, before beforeEach's clearAllMocks wipes the calls.
const onLogout = (registerLogoutCallback as jest.Mock).mock.calls[0]?.[0] as (() => void) | undefined;

jest.mock('@react-native-async-storage/async-storage', () => ({
  setItem: jest.fn(() => Promise.resolve()),
  getItem: jest.fn(() => Promise.resolve(null)),
  removeItem: jest.fn(() => Promise.resolve()),
}));

jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { post: jest.fn(), get: jest.fn(), put: jest.fn(), patch: jest.fn(), delete: jest.fn() },
  hasAuthToken: jest.fn(() => true),
  SpinrApiError: class SpinrApiError extends Error {},
}));

jest.mock('@shared/store/authStore', () => ({
  registerLogoutCallback: jest.fn(),
  useAuthStore: { getState: jest.fn(() => ({ user: { id: 'user-abc' } })) },
}));

jest.mock('expo-router', () => ({ router: { push: jest.fn(), replace: jest.fn() } }));

const mockApi = api as jest.Mocked<typeof api>;

const ride = (id = 'ride-1') => ({
  id,
  status: 'cancelled',
  cancellation_type: 'no_drivers_found',
  pickup_address: '100 Queen St',
  pickup_lat: 52.13,
  pickup_lng: -106.67,
  dropoff_address: '200 King St',
  dropoff_lat: 52.12,
  dropoff_lng: -106.65,
});

const prompt: NoDriversPrompt = {
  rideId: 'ride-1',
  pickup: { address: '100 Queen St', lat: 52.13, lng: -106.67 },
  dropoff: { address: '200 King St', lat: 52.12, lng: -106.65 },
};

beforeEach(() => {
  jest.clearAllMocks();
  useNoDriversStore.setState({ enabled: true, prompt: null, _shownRideId: null, _openScheduleOnArrival: false });
  useRideStore.setState({
    currentRide: null,
    _clearedRideId: null,
    pickup: null,
    dropoff: null,
    stops: [],
    estimates: [],
    scheduledTime: null,
    isLoading: false,
  } as any);
});

describe('offerNoDriversPrompt', () => {
  it('raises the prompt from the ride snapshot', () => {
    expect(offerNoDriversPrompt(ride())).toBe(true);
    expect(useNoDriversStore.getState().prompt).toEqual(prompt);
  });

  it('shows once per ride, even after dismissal', () => {
    offerNoDriversPrompt(ride());
    useNoDriversStore.getState().dismiss();
    offerNoDriversPrompt(ride());
    expect(useNoDriversStore.getState().prompt).toBeNull();
  });

  it('shows again for a different ride', () => {
    offerNoDriversPrompt(ride('ride-1'));
    useNoDriversStore.getState().dismiss();
    offerNoDriversPrompt(ride('ride-2'));
    expect(useNoDriversStore.getState().prompt?.rideId).toBe('ride-2');
  });

  it('carries the ride\'s stops (only ones with coordinates) into the prompt', () => {
    offerNoDriversPrompt({
      ...ride(),
      stops: [{ address: 'Stop', lat: 52.125, lng: -106.66 }, { address: 'Bad', lat: null, lng: null }],
    });
    expect(useNoDriversStore.getState().prompt?.stops).toEqual([{ address: 'Stop', lat: 52.125, lng: -106.66 }]);
  });

  it('returns false and raises nothing with the sheet switched off', () => {
    useNoDriversStore.setState({ enabled: false });
    expect(offerNoDriversPrompt(ride())).toBe(false);
    expect(useNoDriversStore.getState().prompt).toBeNull();
  });

  it('refuses a ride without coordinates', () => {
    expect(offerNoDriversPrompt({ ...ride(), pickup_lat: undefined })).toBe(false);
    expect(offerNoDriversPrompt(null)).toBe(false);
    expect(useNoDriversStore.getState().prompt).toBeNull();
  });
});

describe('prepareRebookDraft', () => {
  it('keeps a matching draft (with its stops) and wipes the old quote', () => {
    const stop = { address: 'Stop', lat: 52.125, lng: -106.66 };
    useRideStore.setState({
      pickup: { address: '100 Queen St', lat: 52.13, lng: -106.67 },
      dropoff: { address: '200 King St', lat: 52.12, lng: -106.65 },
      stops: [stop],
      estimates: [{ vehicle_type: { id: 'vt' }, total_fare: '12.00', estimate_token: 'old' }],
      appliedPromo: { id: 'p', code: 'X', discount_type: 'flat', discount_value: 1 },
    } as any);

    prepareRebookDraft(prompt);

    const s = useRideStore.getState();
    expect(s.estimates).toEqual([]);
    expect(s.appliedPromo).toBeNull();
    expect(s.isLoading).toBe(true); // ride-options' mount fetch fills it
    expect(s.stops).toEqual([stop]);
    expect(s.pickup?.lat).toBe(52.13);
    expect(s.scheduledTime).toBeNull();
  });

  it('rebuilds pickup/dropoff from the ride when the draft is gone', () => {
    prepareRebookDraft(prompt);
    const s = useRideStore.getState();
    expect(s.pickup).toEqual(prompt.pickup);
    expect(s.dropoff).toEqual(prompt.dropoff);
    expect(s.stops).toEqual([]);
  });

  it('restores the cancelled ride\'s stops when rebuilding the trip', () => {
    const stop = { address: 'Stop', lat: 52.125, lng: -106.66 };
    prepareRebookDraft({ ...prompt, stops: [stop] });
    expect(useRideStore.getState().stops).toEqual([stop]);
  });

  it('never books or quotes by itself', () => {
    prepareRebookDraft(prompt);
    expect(mockApi.post).not.toHaveBeenCalled();
    expect(mockApi.get).not.toHaveBeenCalled();
  });

  it('retires the cancelled ride if it is still in the store', () => {
    useRideStore.setState({ currentRide: { ...ride(), status: 'searching' } } as any);
    prepareRebookDraft(prompt);
    expect(useRideStore.getState().currentRide).toBeNull();
    expect(useRideStore.getState()._clearedRideId).toBe('ride-1');
  });

  it('leaves a different current ride alone', () => {
    useRideStore.setState({ currentRide: { ...ride('ride-9'), status: 'searching' } } as any);
    prepareRebookDraft(prompt);
    expect(useRideStore.getState().currentRide?.id).toBe('ride-9');
  });
});

describe('raiseNoDriversPromptAfterResume', () => {
  const searching = { ...ride(), status: 'searching', cancellation_type: null };

  it('does nothing, and makes no request, with the sheet switched off', async () => {
    useNoDriversStore.setState({ enabled: false });
    useRideStore.setState({ currentRide: searching } as any);

    await expect(raiseNoDriversPromptAfterResume(searching)).resolves.toBe(false);

    expect(mockApi.get).not.toHaveBeenCalled();
    expect(useNoDriversStore.getState().prompt).toBeNull();
  });

  it('raises the prompt and retires the ride when it ended for no drivers', async () => {
    useRideStore.setState({ currentRide: searching } as any);
    mockApi.get.mockResolvedValueOnce({ data: ride(), status: 200 } as any);

    await expect(raiseNoDriversPromptAfterResume(searching)).resolves.toBe(true);

    expect(mockApi.get).toHaveBeenCalledWith('/rides/ride-1');
    expect(useNoDriversStore.getState().prompt?.rideId).toBe('ride-1');
    expect(useRideStore.getState().currentRide).toBeNull();
  });

  it('works when fetchActiveRide already cleared the local ride', async () => {
    mockApi.get.mockResolvedValueOnce({ data: ride(), status: 200 } as any);
    await expect(raiseNoDriversPromptAfterResume(searching)).resolves.toBe(true);
    expect(useNoDriversStore.getState().prompt?.rideId).toBe('ride-1');
  });

  it('does nothing for a rider cancel', async () => {
    mockApi.get.mockResolvedValueOnce({
      data: { ...ride(), cancellation_type: 'rider_cancel' }, status: 200,
    } as any);
    await expect(raiseNoDriversPromptAfterResume(searching)).resolves.toBe(false);
    expect(useNoDriversStore.getState().prompt).toBeNull();
  });

  it('skips the lookup when nothing was searching, or a newer ride took over', async () => {
    await expect(raiseNoDriversPromptAfterResume(null)).resolves.toBe(false);
    await expect(raiseNoDriversPromptAfterResume({ ...searching, status: 'driver_accepted' })).resolves.toBe(false);
    useRideStore.setState({ currentRide: { ...searching, id: 'ride-9' } } as any);
    await expect(raiseNoDriversPromptAfterResume(searching)).resolves.toBe(false);
    expect(mockApi.get).not.toHaveBeenCalled();
  });
});

describe('schedule-on-arrival request', () => {
  it('is consumed exactly once', () => {
    const s = useNoDriversStore.getState();
    expect(s.consumeScheduleOnArrival()).toBe(false);
    s.requestScheduleOnArrival();
    expect(useNoDriversStore.getState().consumeScheduleOnArrival()).toBe(true);
    expect(useNoDriversStore.getState().consumeScheduleOnArrival()).toBe(false);
  });
});

describe('logout', () => {
  it('registers a logout callback that drops the prompt and its addresses', () => {
    expect(onLogout).toBeInstanceOf(Function);
    offerNoDriversPrompt(ride());
    useNoDriversStore.getState().requestScheduleOnArrival();

    onLogout!();

    const s = useNoDriversStore.getState();
    expect(s.prompt).toBeNull();
    expect(s._shownRideId).toBeNull();
    expect(s._openScheduleOnArrival).toBe(false);
  });
});
