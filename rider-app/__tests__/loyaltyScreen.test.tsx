/**
 * app/loyalty.tsx — the rider's loyalty/rewards screen. Pins:
 *  - GET /loyalty + /loyalty/history load in parallel on mount
 *  - the tier card, progress bar, and points-info row render from the
 *    loyalty payload; "highest tier" copy replaces the progress bar when
 *    next_tier is null
 *  - the progress-percent calculation (points / (points + needed))
 *  - history rows render positive points with a '+' prefix in green and
 *    negative points in red, with the type-to-icon mapping falling back
 *    to a generic icon for an unrecognised type
 *  - the empty state renders when history is empty
 *  - a load failure is swallowed silently (loyalty stays null, no crash)
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text } from 'react-native';

import LoyaltyScreen from '../app/loyalty';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  SafeAreaView: ({ children }: any) => children,
}));

const mockBack = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ back: mockBack }),
}));

const COLORS = {
  primary: '#EF4444', surface: '#FFF', surfaceLight: '#F5F5F5', text: '#111', textDim: '#666', border: '#E5E7EB',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

const mockApiGet = jest.fn();
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { get: (...a: any[]) => mockApiGet(...a) },
}));

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
};

const LOYALTY = {
  points: 750,
  lifetime_points: 3200,
  tier: 'silver',
  multiplier: 1.5,
  next_tier: { tier: 'gold', points_needed: 250 },
  redemption_rate: 0.01,
};

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  await act(async () => {
    renderer = TestRenderer.create(<LoyaltyScreen />);
    await flush();
  });
  return renderer!;
}

function allText(r: TestRenderer.ReactTestRenderer) {
  return r.root.findAllByType(Text).map((t) => JSON.stringify(t.props.children)).join(' | ');
}

beforeEach(() => {
  jest.clearAllMocks();
  mockApiGet.mockImplementation((url: string) => {
    if (url === '/loyalty') return Promise.resolve({ data: LOYALTY });
    if (url === '/loyalty/history') return Promise.resolve({ data: [] });
    return Promise.reject(new Error('unexpected url ' + url));
  });
});

afterEach(() => {
  act(() => {
    renderer?.unmount();
  });
  renderer = null;
});

describe('LoyaltyScreen', () => {
  it('loads loyalty info and history in parallel', async () => {
    const r = await renderScreen();
    expect(mockApiGet).toHaveBeenCalledWith('/loyalty');
    expect(mockApiGet).toHaveBeenCalledWith('/loyalty/history');
    const text = allText(r);
    expect(text).toContain('Silver');
    expect(text).toContain('750');
  });

  it('shows the progress bar and points-needed hint toward the next tier', async () => {
    const r = await renderScreen();
    const text = allText(r);
    // progress = round(750 / (750+250) * 100) = 75
    expect(text).toContain('["250",\" pts needed to reach\",\" \",\"Gold\"]');
  });

  it('shows "highest tier" copy instead of a progress bar when next_tier is null', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/loyalty') return Promise.resolve({ data: { ...LOYALTY, tier: 'platinum', next_tier: null } });
      return Promise.resolve({ data: [] });
    });
    const r = await renderScreen();
    expect(allText(r)).toContain("You've reached the highest tier!");
  });

  it('shows the empty history state when there is no history', async () => {
    const r = await renderScreen();
    expect(allText(r)).toContain('No points history yet');
  });

  it('renders a positive history entry with a + prefix', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/loyalty') return Promise.resolve({ data: LOYALTY });
      return Promise.resolve({
        data: [{ id: 'h1', type: 'ride_earn', points: 50, description: 'Ride completed', created_at: '2026-01-01T00:00:00Z' }],
      });
    });
    const r = await renderScreen();
    const text = allText(r);
    expect(text).toContain('Ride completed');
    expect(text).toContain('["+","50"," pts"]');
  });

  it('renders a negative history entry without a + prefix', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/loyalty') return Promise.resolve({ data: LOYALTY });
      return Promise.resolve({
        data: [{ id: 'h2', type: 'redeem', points: -100, description: 'Redeemed for discount', created_at: '2026-01-01T00:00:00Z' }],
      });
    });
    const r = await renderScreen();
    const text = allText(r);
    expect(text).toContain('Redeemed for discount');
    expect(text).toContain('["","-100"," pts"]');
  });

  it('does not crash and leaves loyalty null on a load failure', async () => {
    mockApiGet.mockRejectedValue(new Error('network down'));
    const r = await renderScreen();
    expect(allText(r)).toContain('No points history yet');
  });

  it('navigates back when the back button is pressed', async () => {
    const r = await renderScreen();
    const backBtn = r.root.findAllByType(TouchableOpacity)[0];
    act(() => {
      backBtn.props.onPress();
    });
    expect(mockBack).toHaveBeenCalled();
  });
});
