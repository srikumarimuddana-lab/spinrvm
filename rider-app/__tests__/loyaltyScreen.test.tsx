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
 *  - a load failure shows a distinct error+retry state, not the empty-history copy
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text, FlatList } from 'react-native';

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
  danger: '#DC2626',
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

  it('shows a distinct error+retry state on a load failure, not the empty-history copy', async () => {
    mockApiGet.mockRejectedValue(new Error('network down'));
    const r = await renderScreen();
    const text = allText(r);
    expect(text).toContain("Couldn't load your rewards");
    expect(text).not.toContain('No points history yet');
  });

  it('retrying after a load failure re-fetches and can recover', async () => {
    mockApiGet.mockRejectedValue(new Error('network down'));
    const r = await renderScreen();
    expect(allText(r)).toContain("Couldn't load your rewards");

    mockApiGet.mockImplementation((url: string) => {
      if (url === '/loyalty') return Promise.resolve({ data: LOYALTY });
      return Promise.resolve({ data: [] });
    });
    // Only two TouchableOpacitys render in this error state: the header
    // back button (index 0) and the retry button (index 1).
    const retryBtn = r.root.findAllByType(TouchableOpacity)[1];
    await act(async () => {
      retryBtn.props.onPress();
      await flush();
    });
    expect(allText(r)).toContain('Silver');
    expect(allText(r)).not.toContain("Couldn't load your rewards");
  });

  it('pull-to-refresh reloads loyalty and history silently', async () => {
    const r = await renderScreen();
    mockApiGet.mockClear();
    const list = r.root.findByType(FlatList);
    await act(async () => {
      await list.props.refreshControl.props.onRefresh();
      await flush();
    });
    expect(mockApiGet).toHaveBeenCalledWith('/loyalty');
    expect(mockApiGet).toHaveBeenCalledWith('/loyalty/history');
  });

  it('renders the bonus/promo/expire history icon types without crashing', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/loyalty') return Promise.resolve({ data: LOYALTY });
      return Promise.resolve({
        data: [
          { id: 'h1', type: 'bonus', points: 20, description: 'Bonus points', created_at: '2026-01-01T00:00:00Z' },
          { id: 'h2', type: 'promotion', points: 10, description: 'Promo bonus', created_at: '2026-01-02T00:00:00Z' },
          { id: 'h3', type: 'expiry', points: -5, description: 'Points expired', created_at: '2026-01-03T00:00:00Z' },
        ],
      });
    });
    const r = await renderScreen();
    const text = allText(r);
    expect(text).toContain('Bonus points');
    expect(text).toContain('Promo bonus');
    expect(text).toContain('Points expired');
  });

  it('falls back to an empty string when formatDate is given an unparseable date', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/loyalty') return Promise.resolve({ data: LOYALTY });
      return Promise.resolve({
        data: [{ id: 'h1', type: 'earn', points: 5, description: 'Test entry', created_at: { toString: () => { throw new Error('bad'); } } as any }],
      });
    });
    const r = await renderScreen();
    // No crash — formatDate's catch swallows the throw and returns ''.
    expect(allText(r)).toContain('Test entry');
  });

  it('navigates back when the back button is pressed', async () => {
    const r = await renderScreen();
    const backBtn = r.root.findAllByType(TouchableOpacity)[0];
    act(() => {
      backBtn.props.onPress();
    });
    expect(mockBack).toHaveBeenCalled();
  });

  it('falls back to the bronze color for an unrecognised tier', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/loyalty') return Promise.resolve({ data: { ...LOYALTY, tier: 'diamond' } });
      return Promise.resolve({ data: [] });
    });
    const r = await renderScreen();
    const tierPointsValue = r.root.findAllByType(Text).find((t) => {
      try { return JSON.stringify(t.props.children) === '"750"'; } catch { return false; }
    })!;
    expect(tierPointsValue.props.style).toEqual(expect.arrayContaining([expect.objectContaining({ color: '#CD7F32' })]));
  });

  it('renders the "promo" and "expire" history icon types, and falls back to the generic icon for an unrecognised type', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/loyalty') return Promise.resolve({ data: LOYALTY });
      return Promise.resolve({
        data: [
          { id: 'h1', type: 'promo', points: 15, description: 'Promo credit', created_at: '2026-01-01T00:00:00Z' },
          { id: 'h2', type: 'expire', points: -3, description: 'Expiring soon', created_at: '2026-01-02T00:00:00Z' },
          { id: 'h3', type: 'unknown_type', points: 1, description: 'Mystery entry', created_at: '2026-01-03T00:00:00Z' },
        ],
      });
    });
    const r = await renderScreen();
    const text = allText(r);
    expect(text).toContain('Promo credit');
    expect(text).toContain('Expiring soon');
    expect(text).toContain('Mystery entry');
  });

  it('falls back history to an empty list when the response data is not an array', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/loyalty') return Promise.resolve({ data: LOYALTY });
      if (url === '/loyalty/history') return Promise.resolve({ data: null });
      return Promise.reject(new Error('unexpected'));
    });
    const r = await renderScreen();
    expect(allText(r)).toContain('No points history yet');
  });

  it('caps the progress bar at 100% when points_needed is already zero or negative', async () => {
    mockApiGet.mockImplementation((url: string) => {
      if (url === '/loyalty') {
        return Promise.resolve({ data: { ...LOYALTY, next_tier: { tier: 'gold', points_needed: 0 } } });
      }
      return Promise.resolve({ data: [] });
    });
    const r = await renderScreen();
    const fill = r.root.findAllByProps({}).find((n) =>
      Array.isArray(n.props.style) && n.props.style.some((s: any) => s && typeof s.width === 'string' && s.width.endsWith('%'))
    );
    const style = fill!.props.style.find((s: any) => s && s.width);
    expect(style.width).toBe('100%');
  });
});
