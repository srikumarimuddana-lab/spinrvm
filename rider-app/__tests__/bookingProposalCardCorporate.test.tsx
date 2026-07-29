/**
 * Regression test for ACTION_ITEMS.md B-AI1: the in-chat booking card used to
 * always call createRide(paymentMethod, undefined, ...), so a wallet-payment
 * proposal from a corporate rider with Work Mode on booked personally instead
 * of billing the company — corporate policy checks in
 * backend/routes/rides/booking.py never ran.
 *
 * Fix mirrors /ride-options.tsx's own default: if the rider's Work Mode
 * toggle (useWorkProfileStore) is on and has an active company, the card now
 * passes that company id to createRide and runs the same client-side policy
 * pre-check (checkRide) that /ride-options runs before booking.
 *
 * Uses react-test-renderer directly, matching bookingProposalCardPromo.test.tsx.
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('expo-router', () => ({ useRouter: () => ({ push: jest.fn(), replace: jest.fn() }) }));

const COLORS = {
  primary: '#EF4444', background: '#FFF', surface: '#FFF', surfaceLight: '#F5F5F5',
  text: '#111', textDim: '#666', border: '#E5E7EB', success: '#16A34A', warning: '#F59E0B',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

const ESTIMATE = {
  vehicle_type: { id: 'vt-1', name: 'Economy' },
  base_fare: '3.50',
  distance_fare: '18.17',
  time_fare: '7.25',
  total_fare: '28.92',
  grand_total: '30.92',
  available: true,
};

let mockRideStore: any;
let mockWorkStore: any;
const createRideSpy = jest.fn();

const makeRideStore = () => ({
  estimates: [ESTIMATE],
  appliedPromo: null as any,
  setPickup: jest.fn(),
  setDropoff: jest.fn(),
  clearStops: jest.fn(),
  setScheduledTime: jest.fn(),
  selectVehicle: jest.fn(),
  fetchEstimates: jest.fn(),
  applyPromo: jest.fn(),
  fetchAvailablePromos: jest.fn(async () => {}),
  createRide: jest.fn(async (paymentMethod: any, corporateAccountId: any) => {
    createRideSpy(paymentMethod, corporateAccountId);
    return { id: 'ride-1' };
  }),
});

const makeWorkStore = (opts: {
  workModeEnabled: boolean;
  activeCompanyId: string | null;
  profiles?: any[];
  checkRideResult?: { ok: boolean; reasons: string[] };
}) => ({
  workModeEnabled: opts.workModeEnabled,
  activeCompanyId: opts.activeCompanyId,
  profiles: opts.profiles ?? [],
  fetchPolicy: jest.fn(async () => {}),
  checkRide: jest.fn(() => opts.checkRideResult ?? { ok: true, reasons: [] }),
});

jest.mock('../store/rideStore', () => ({
  useRideStore: (selector: any) => selector(mockRideStore),
}));
jest.mock('../store/workProfileStore', () => ({
  useWorkProfileStore: (selector: any) => selector(mockWorkStore),
}));

import BookingProposalCard from '../components/BookingProposalCard';

const PROPOSAL: any = {
  pickup_lat: 50.4079, pickup_lng: -104.6501, pickup_address: '4500 Gordon Rd, Regina',
  dropoff_lat: 50.4497, dropoff_lng: -104.5345, dropoff_address: '4325 Wakeling St, Regina',
  vehicle_type_id: 'vt-1',
  payment_method: 'wallet',
};

const texts = (r: TestRenderer.ReactTestRenderer): string[] =>
  r.root.findAllByType(require('react-native').Text)
    .map((n) => (Array.isArray(n.props.children) ? n.props.children.join('') : String(n.props.children ?? '')));

async function renderAndConfirm(proposal: any = PROPOSAL) {
  const { TouchableOpacity } = require('react-native');
  let renderer!: TestRenderer.ReactTestRenderer;
  await act(async () => {
    renderer = TestRenderer.create(<BookingProposalCard proposal={proposal} />);
  });
  await act(async () => { await Promise.resolve(); });

  const readyTexts = texts(renderer);
  const confirm = renderer.root.findAllByType(TouchableOpacity)[0];
  await act(async () => { await confirm.props.onPress(); });

  return { renderer, readyTexts, afterConfirmTexts: texts(renderer) };
}

beforeEach(() => {
  createRideSpy.mockClear();
  mockRideStore = makeRideStore();
});

describe('booking card corporate-billing parity (B-AI1)', () => {
  it('books personally when Work Mode is off, as before', async () => {
    mockWorkStore = makeWorkStore({ workModeEnabled: false, activeCompanyId: null });

    await renderAndConfirm();

    expect(createRideSpy).toHaveBeenCalledWith('wallet', null);
  });

  it('books to the active company when Work Mode is on, matching /ride-options default', async () => {
    mockWorkStore = makeWorkStore({
      workModeEnabled: true,
      activeCompanyId: 'co-1',
      profiles: [{ membership: { id: 'm1', company_id: 'co-1', role: 'member', status: 'active' }, company: { id: 'co-1', name: 'Acme Inc' } }],
    });

    const { readyTexts } = await renderAndConfirm();

    expect(createRideSpy).toHaveBeenCalledWith('wallet', 'co-1');
    expect(readyTexts).toContain('Charged to Acme Inc');
  });

  it('does not book, and shows the policy reason, when the company policy check fails', async () => {
    mockWorkStore = makeWorkStore({
      workModeEnabled: true,
      activeCompanyId: 'co-1',
      profiles: [{ membership: { id: 'm1', company_id: 'co-1', role: 'member', status: 'active' }, company: { id: 'co-1', name: 'Acme Inc' } }],
      checkRideResult: { ok: false, reasons: ['Fare exceeds company cap of $20.00.'] },
    });

    const { afterConfirmTexts } = await renderAndConfirm();

    expect(createRideSpy).not.toHaveBeenCalled();
    expect(afterConfirmTexts.some((t) => t.includes('Fare exceeds company cap'))).toBe(true);
  });

  it('does not affect a Work Mode rider paying by card (already deep-links to /ride-options)', async () => {
    mockWorkStore = makeWorkStore({ workModeEnabled: true, activeCompanyId: 'co-1' });

    await renderAndConfirm({ ...PROPOSAL, payment_method: 'card' });

    // Card path never calls createRide from this card at all.
    expect(createRideSpy).not.toHaveBeenCalled();
  });
});
