/**
 * R-P3-7: Missing store action tests — applyPromo / clearPromo / setScheduledTime
 *
 * Covers the promo and scheduled-time store actions that had no test coverage.
 */

import { useRideStore } from '../rideStore';

jest.mock('@react-native-async-storage/async-storage', () => ({
  setItem: jest.fn(() => Promise.resolve()),
  getItem: jest.fn(() => Promise.resolve(null)),
  removeItem: jest.fn(() => Promise.resolve()),
}));

jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { post: jest.fn(), get: jest.fn(), put: jest.fn(), patch: jest.fn(), delete: jest.fn() },
}));

jest.mock('@shared/store/authStore', () => ({
  registerLogoutCallback: jest.fn(),
  useAuthStore: { getState: jest.fn(() => ({ user: { id: 'user-abc' } })) },
}));

beforeEach(() => {
  useRideStore.setState({
    appliedPromo: null,
    availablePromos: [],
    scheduledTime: null,
  });
});

// ── applyPromo / clearPromo ──────────────────────────────────────────────────

describe('applyPromo', () => {
  it('sets appliedPromo to the supplied promo object', () => {
    const promo = { id: 'promo-1', code: 'SAVE10', discount: 10, discount_type: 'percentage', discount_value: 10 };
    useRideStore.getState().applyPromo(promo);
    expect(useRideStore.getState().appliedPromo).toEqual(promo);
  });

  it('replaces a previously applied promo', () => {
    const first = { id: 'p1', code: 'FIRST', discount: 5, discount_type: 'percentage', discount_value: 5 };
    const second = { id: 'p2', code: 'SECOND', discount: 15, discount_type: 'percentage', discount_value: 15 };
    useRideStore.getState().applyPromo(first);
    useRideStore.getState().applyPromo(second);
    expect(useRideStore.getState().appliedPromo).toEqual(second);
  });

  it('clears appliedPromo when called with null', () => {
    useRideStore.getState().applyPromo({ id: 'p1', code: 'X', discount: 1, discount_type: 'fixed', discount_value: 1 });
    useRideStore.getState().applyPromo(null);
    expect(useRideStore.getState().appliedPromo).toBeNull();
  });
});

// ── setScheduledTime ─────────────────────────────────────────────────────────

describe('setScheduledTime', () => {
  it('sets scheduledTime to a future Date', () => {
    const future = new Date(Date.now() + 60 * 60 * 1000); // 1 hour from now
    useRideStore.getState().setScheduledTime(future);
    expect(useRideStore.getState().scheduledTime).toEqual(future);
  });

  it('replaces an existing scheduled time', () => {
    const t1 = new Date(Date.now() + 60 * 60 * 1000);
    const t2 = new Date(Date.now() + 2 * 60 * 60 * 1000);
    useRideStore.getState().setScheduledTime(t1);
    useRideStore.getState().setScheduledTime(t2);
    expect(useRideStore.getState().scheduledTime).toEqual(t2);
  });

  it('clears scheduledTime when called with null', () => {
    useRideStore.getState().setScheduledTime(new Date());
    useRideStore.getState().setScheduledTime(null);
    expect(useRideStore.getState().scheduledTime).toBeNull();
  });
});
