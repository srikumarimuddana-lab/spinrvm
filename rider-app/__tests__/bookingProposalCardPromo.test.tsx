/**
 * Regression test for the in-chat booking card's promo invariant:
 *
 *   THE TOTAL THE CARD DISPLAYS IS THE TOTAL THAT GETS CHARGED.
 *
 * The card renders displayFareWithPromo(estimate, appliedPromo) while
 * createRide bills `appliedPromo?.code` straight off the store. handleConfirm
 * used to call applyPromo(proposedPromo) immediately before createRide, which
 * overwrote the promo fetchAvailablePromos had resolved:
 *
 *   - proposal carries no promo_code  → proposedPromo is null → the auto-picked
 *     promo was wiped → card showed a discount, ride booked at full fare.
 *   - proposal's promo_code is gone   → the zero-discount placeholder replaced
 *     the eligible substitute → card showed the substitute's discount, ride
 *     booked with a code the server had just rejected.
 *
 * Both charged the rider MORE than the card displayed. These tests pin the
 * displayed total against the promo code handed to createRide.
 *
 * Uses react-test-renderer directly (matching privacySettingsToggles.test.tsx)
 * — the pinned @testing-library/react-native v12 can't probe RN 0.85 hosts.
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';

import BookingProposalCard from '../components/BookingProposalCard';
import { displayFareWithPromo } from '../components/bookingProposal';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('expo-router', () => ({ useRouter: () => ({ push: jest.fn(), replace: jest.fn() }) }));

const COLORS = {
  primary: '#EF4444', background: '#FFF', surface: '#FFF', surfaceLight: '#F5F5F5',
  text: '#111', textDim: '#666', border: '#E5E7EB', success: '#16A34A', warning: '#F59E0B',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

/**
 * Minimal stand-in for rideStore that keeps the two behaviours this bug lives
 * between: fetchAvailablePromos auto-applies an eligible promo when none is
 * applied (rideStore.ts:543-545), and createRide bills appliedPromo?.code
 * (rideStore.ts:708).
 */
const ESTIMATE = {
  vehicle_type: { id: 'vt-1', name: 'Economy' },
  base_fare: '3.50',
  distance_fare: '18.17',
  time_fare: '7.25',
  total_fare: '28.92',
  grand_total: '30.92',
  available: true,
};

let mockStore: any;
const createRideSpy = jest.fn();

const makeStore = (promosFromServer: any[]) => ({
  estimates: [ESTIMATE],
  appliedPromo: null as any,
  setPickup: jest.fn(),
  setDropoff: jest.fn(),
  clearStops: jest.fn(),
  setScheduledTime: jest.fn(),
  selectVehicle: jest.fn(),
  fetchEstimates: jest.fn(),
  applyPromo: (promo: any) => { mockStore.appliedPromo = promo; },
  // Mirrors rideStore: re-validate the applied promo against the fresh list,
  // else auto-apply the first eligible one.
  fetchAvailablePromos: jest.fn(async () => {
    const current = mockStore.appliedPromo;
    const match = current ? promosFromServer.find((p) => p.code === current.code) : null;
    mockStore.appliedPromo = match ?? promosFromServer.find((p) => p.eligible !== false) ?? null;
  }),
  // Mirrors rideStore: the promo billed is whatever sits in the store NOW.
  createRide: jest.fn(async () => {
    createRideSpy({ promo_code: mockStore.appliedPromo?.code ?? null });
    return { id: 'ride-1' };
  }),
});

jest.mock('../store/rideStore', () => ({
  useRideStore: (selector: any) => selector(mockStore),
}));

// Not under test here — stub so BookingProposalCard's workProfileStore import
// doesn't pull in the native AsyncStorage module under Jest.
jest.mock('../store/workProfileStore', () => ({
  useWorkProfileStore: (selector: any) => selector({
    workModeEnabled: false,
    activeCompanyId: null,
    profiles: [],
    fetchPolicy: jest.fn(),
    checkRide: jest.fn(() => ({ ok: true, reasons: [] })),
  }),
}));

const PROPOSAL: any = {
  pickup_lat: 50.4079, pickup_lng: -104.6501, pickup_address: '4500 Gordon Rd, Regina',
  dropoff_lat: 50.4497, dropoff_lng: -104.5345, dropoff_address: '4325 Wakeling St, Regina',
  vehicle_type_id: 'vt-1',
  payment_method: 'wallet', // wallet books inline; card hands off to /ride-options
};

/** Every string rendered anywhere in the tree. */
const texts = (r: TestRenderer.ReactTestRenderer): string[] =>
  r.root.findAllByType(require('react-native').Text)
    .map((n) => (Array.isArray(n.props.children) ? n.props.children.join('') : String(n.props.children ?? '')));

async function renderAndConfirm(proposal: any) {
  const { TouchableOpacity } = require('react-native');
  let renderer!: TestRenderer.ReactTestRenderer;
  await act(async () => {
    renderer = TestRenderer.create(<BookingProposalCard proposal={proposal} />);
  });
  // Let the promo fetch settle so the card shows its final total.
  await act(async () => { await Promise.resolve(); });

  // Snapshot the ready-state copy BEFORE confirming — the card swaps to a
  // "Booked!" state afterwards and the fare/promo rows unmount.
  const readyTexts = texts(renderer);
  // The last $ amount is the live (post-promo) total; a struck-through
  // pre-promo total may precede it.
  const shown = readyTexts.filter((t) => /^\$\d+\.\d{2}$/.test(t));
  const finalTotal = shown[shown.length - 1];

  const confirm = renderer.root.findAllByType(TouchableOpacity)[0];
  await act(async () => { await confirm.props.onPress(); });

  return { finalTotal, readyTexts, renderer };
}

beforeEach(() => {
  createRideSpy.mockClear();
});

describe('booking card promo invariant: displayed total == charged total', () => {
  it('books with the auto-picked promo it displayed (proposal carries no promo_code)', async () => {
    const autoPicked = { code: 'AUTO10', discount_type: 'flat', discount_value: 10, eligible: true };
    mockStore = makeStore([autoPicked]);

    const { finalTotal } = await renderAndConfirm(PROPOSAL);

    // The card discounted the fare by the auto-picked promo...
    expect(finalTotal).toBe(`$${displayFareWithPromo(ESTIMATE as any, autoPicked as any)}`);
    expect(finalTotal).toBe('$20.92');
    // ...so the ride must be booked with that same promo, not null.
    expect(createRideSpy).toHaveBeenCalledWith({ promo_code: 'AUTO10' });
  });

  it('books with the substitute promo when the proposed code is gone', async () => {
    const substitute = { code: 'SAVE50', discount_type: 'flat', discount_value: 5, eligible: true };
    mockStore = makeStore([substitute]); // SAVE75 is NOT in the server's list

    const { finalTotal, readyTexts } = await renderAndConfirm({ ...PROPOSAL, promo_code: 'SAVE75' });

    expect(finalTotal).toBe(`$${displayFareWithPromo(ESTIMATE as any, substitute as any)}`);
    expect(finalTotal).toBe('$25.92');
    // The old code would have billed the dead 'SAVE75' here.
    expect(createRideSpy).toHaveBeenCalledWith({ promo_code: 'SAVE50' });

    // ...and the card must name the substitute rather than claim the total is
    // undiscounted while showing a discounted one (the chip used to be
    // suppressed whenever the applied code differed from the proposed one).
    expect(readyTexts).toContain('Promo SAVE50');
    expect(readyTexts).toContain('Promo SAVE75 is no longer available — SAVE50 applied instead.');
  });

  it('books with the proposed promo when the server still honours it', async () => {
    const proposed = { code: 'SAVE75', discount_type: 'flat', discount_value: 10, eligible: true };
    mockStore = makeStore([proposed]);

    const { finalTotal } = await renderAndConfirm({ ...PROPOSAL, promo_code: 'SAVE75' });

    expect(finalTotal).toBe('$20.92');
    expect(createRideSpy).toHaveBeenCalledWith({ promo_code: 'SAVE75' });
  });

  it('books with no promo when none is eligible, and shows the undiscounted total', async () => {
    mockStore = makeStore([]);

    const { finalTotal } = await renderAndConfirm({ ...PROPOSAL, promo_code: 'SAVE75' });

    expect(finalTotal).toBe('$30.92');
    expect(createRideSpy).toHaveBeenCalledWith({ promo_code: null });
  });
});
