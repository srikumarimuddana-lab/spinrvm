/**
 * components/DestinationModeBanner.tsx (C136 T2) — persistent "Heading home"
 * strip on the main driver screen. Pins:
 *  - GET /drivers/destination on mount/focus; renders only when active
 *  - shows address + countdown; countdown ticks and the banner hides on expiry
 *  - old-backend fallback: no `active`/expiry but destination_mode → banner, no countdown
 *  - Turn off → DELETE /drivers/destination, hides on success; failure toasts + keeps banner
 *  - fetch failure is silent (no toast, no banner)
 */
import React from 'react';
import { render, fireEvent, act } from '@testing-library/react-native';

import { DestinationModeBanner, DESTINATION_BANNER_TICK_MS } from '../../components/DestinationModeBanner';
import { isDestinationActive, remainingParts } from '../../utils/destinationModeState';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));

jest.mock('expo-router', () => ({
  useFocusEffect: (cb: () => void | (() => void)) => {
    const ReactActual = require('react');
    ReactActual.useEffect(() => cb(), [cb]);
  },
}));

const COLORS = {
  primary: '#EF4444', background: '#FFF', surface: '#FFF', surfaceLight: '#F5F5F5',
  text: '#111', textDim: '#666', border: '#E5E7EB', error: '#DC2626', warning: '#F59E0B',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

// Real English strings so the rendered copy (interpolation included) is
// asserted end-to-end against en.json. Stable `t` reference (see
// destinationModeScreen.test.tsx for why).
jest.mock('../../store/languageStore', () => {
  const { translate } = jest.requireActual('../../i18n');
  const state = { t: (key: string) => translate('en', key) };
  return { useLanguageStore: () => state };
});

const mockApiGet = jest.fn();
const mockApiDelete = jest.fn();
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: {
    get: (...a: any[]) => mockApiGet(...a),
    delete: (...a: any[]) => mockApiDelete(...a),
  },
}));

const mockShowToast = jest.fn();
jest.mock('../../hooks/useToast', () => ({ showToast: (...a: any[]) => mockShowToast(...a) }));

const NOW = new Date('2026-09-24T18:00:00.000Z').getTime();
const inMs = (ms: number) => new Date(NOW + ms).toISOString();
const MIN = 60_000;

const ACTIVE = {
  destination_mode: true,
  destination_address: '1234 Albert St, Regina, SK',
  destination_lat: 50.45,
  destination_lng: -104.6,
  destination_set_at: inMs(-18 * MIN),
  destination_expires_at: inMs(102 * MIN), // 1h 42m
  active: true,
};

const flush = () => act(async () => { await Promise.resolve(); await Promise.resolve(); });

beforeEach(() => {
  jest.useFakeTimers();
  jest.setSystemTime(NOW);
  mockApiGet.mockReset();
  mockApiDelete.mockReset();
  mockShowToast.mockReset();
});
afterEach(() => {
  jest.useRealTimers();
});

describe('DestinationModeBanner', () => {
  it('renders address + countdown when active with a future expiry', async () => {
    mockApiGet.mockResolvedValue({ data: ACTIVE });
    const screen = render(<DestinationModeBanner />);
    await flush();
    expect(mockApiGet).toHaveBeenCalledWith('/drivers/destination');
    expect(screen.getByTestId('destination-mode-banner')).toBeTruthy();
    expect(screen.getByText(/Heading home/)).toBeTruthy();
    expect(screen.getByText(' · ends in 1h 42m')).toBeTruthy();
    expect(screen.getByText('Only rides toward 1234 Albert St, Regina, SK')).toBeTruthy();
    const btn = screen.getByTestId('destination-mode-banner-turn-off');
    expect(btn.props.accessibilityRole).toBe('button');
    expect(btn.props.accessibilityLabel).toBe('Turn off destination mode');
  });

  it('is hidden when inactive', async () => {
    mockApiGet.mockResolvedValue({ data: { ...ACTIVE, destination_mode: false, active: false } });
    const screen = render(<DestinationModeBanner />);
    await flush();
    expect(screen.queryByTestId('destination-mode-banner')).toBeNull();
  });

  it('is hidden when the expiry is already past (even if active flag is stale)', async () => {
    mockApiGet.mockResolvedValue({ data: { ...ACTIVE, destination_expires_at: inMs(-1 * MIN) } });
    const screen = render(<DestinationModeBanner />);
    await flush();
    expect(screen.queryByTestId('destination-mode-banner')).toBeNull();
  });

  it('counts down and hides itself when the expiry passes', async () => {
    mockApiGet.mockResolvedValue({ data: { ...ACTIVE, destination_expires_at: inMs(2 * MIN) } });
    const screen = render(<DestinationModeBanner />);
    await flush();
    expect(screen.getByText(' · ends in 2m')).toBeTruthy();
    act(() => { jest.advanceTimersByTime(DESTINATION_BANNER_TICK_MS * 2); }); // +1m
    expect(screen.getByText(' · ends in 1m')).toBeTruthy();
    act(() => { jest.advanceTimersByTime(DESTINATION_BANNER_TICK_MS * 2); }); // +2m → expired
    expect(screen.queryByTestId('destination-mode-banner')).toBeNull();
  });

  it('old backend (no active / no expiry) with destination_mode on → banner without countdown', async () => {
    const { active: _a, destination_expires_at: _e, ...legacy } = ACTIVE;
    mockApiGet.mockResolvedValue({ data: legacy });
    const screen = render(<DestinationModeBanner />);
    await flush();
    expect(screen.getByTestId('destination-mode-banner')).toBeTruthy();
    expect(screen.queryByText(/ends in/)).toBeNull();
  });

  it('turn off calls DELETE /drivers/destination and hides the banner', async () => {
    mockApiGet.mockResolvedValue({ data: ACTIVE });
    mockApiDelete.mockResolvedValue({ data: { success: true, destination_mode: false } });
    const screen = render(<DestinationModeBanner />);
    await flush();
    fireEvent.press(screen.getByTestId('destination-mode-banner-turn-off'));
    await flush();
    expect(mockApiDelete).toHaveBeenCalledTimes(1);
    expect(mockApiDelete).toHaveBeenCalledWith('/drivers/destination');
    expect(screen.queryByTestId('destination-mode-banner')).toBeNull();
    expect(mockShowToast).not.toHaveBeenCalled();
  });

  it('turn off failure shows a non-blocking error and keeps the banner', async () => {
    mockApiGet.mockResolvedValue({ data: ACTIVE });
    mockApiDelete.mockRejectedValue(new Error('network'));
    const screen = render(<DestinationModeBanner />);
    await flush();
    fireEvent.press(screen.getByTestId('destination-mode-banner-turn-off'));
    await flush();
    expect(mockApiDelete).toHaveBeenCalledWith('/drivers/destination');
    expect(screen.getByTestId('destination-mode-banner')).toBeTruthy();
    expect(mockShowToast).toHaveBeenCalledWith('error', "Couldn't turn off", expect.any(String));
  });

  it('fetch failure is silent: no banner, no toast', async () => {
    mockApiGet.mockRejectedValue(new Error('offline'));
    const screen = render(<DestinationModeBanner />);
    await flush();
    expect(screen.queryByTestId('destination-mode-banner')).toBeNull();
    expect(mockShowToast).not.toHaveBeenCalled();
  });
});

describe('destinationModeState helpers', () => {
  it('isDestinationActive follows the C136 decision table', () => {
    expect(isDestinationActive(null, NOW)).toBe(false);
    expect(isDestinationActive({ ...ACTIVE, active: false }, NOW)).toBe(false);
    expect(isDestinationActive(ACTIVE, NOW)).toBe(true);
    expect(isDestinationActive({ ...ACTIVE, active: undefined }, NOW)).toBe(true);
    expect(isDestinationActive({ ...ACTIVE, active: undefined, destination_expires_at: inMs(-MIN) }, NOW)).toBe(false);
    expect(isDestinationActive({ ...ACTIVE, active: undefined, destination_mode: false }, NOW)).toBe(false);
    expect(isDestinationActive({ ...ACTIVE, active: undefined, destination_expires_at: null }, NOW)).toBe(true);
  });

  it('remainingParts rounds up and never shows 0m', () => {
    expect(remainingParts(NOW + 102 * MIN, NOW)).toEqual({ h: 1, m: 42 });
    expect(remainingParts(NOW + 10_000, NOW)).toEqual({ h: 0, m: 1 });
    expect(remainingParts(NOW + 120 * MIN, NOW)).toEqual({ h: 2, m: 0 });
  });
});
