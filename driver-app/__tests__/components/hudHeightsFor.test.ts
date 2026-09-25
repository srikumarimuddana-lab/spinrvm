/**
 * The idle hud's height follows the OS text size (capped at MAX_FONT_SCALE),
 * and the driver map reserves the same space via this helper. At the default
 * size it must equal the original constants exactly.
 */
import {
  hudHeightsFor,
  HUD_EXPANDED_HEIGHT_DP,
  HUD_COLLAPSED_HEIGHT_DP,
} from '../../components/dashboard/DriverIdlePanel';
import { MAX_FONT_SCALE } from '@shared/utils/responsive';

// jest.mock calls are hoisted above the imports.
jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('expo-linear-gradient', () => ({ LinearGradient: 'View' }));
jest.mock('expo-constants', () => ({
  __esModule: true,
  default: { executionEnvironment: 'standalone' },
  ExecutionEnvironment: { StoreClient: 'storeClient', Standalone: 'standalone', Bare: 'bare' },
}));
jest.mock('expo-notifications', () => ({ scheduleNotificationAsync: jest.fn() }));
jest.mock('@react-native-async-storage/async-storage', () => ({
  getItem: jest.fn(() => Promise.resolve('true')),
  setItem: jest.fn(() => Promise.resolve()),
}));

describe('hudHeightsFor', () => {
  it('equals the original constants at the default text size', () => {
    expect(hudHeightsFor(1)).toEqual({
      expanded: HUD_EXPANDED_HEIGHT_DP,
      collapsed: HUD_COLLAPSED_HEIGHT_DP,
    });
  });

  it('never shrinks below the constants for smaller text', () => {
    expect(hudHeightsFor(0.85)).toEqual({
      expanded: HUD_EXPANDED_HEIGHT_DP,
      collapsed: HUD_COLLAPSED_HEIGHT_DP,
    });
  });

  it('grows with larger text', () => {
    const { expanded, collapsed } = hudHeightsFor(1.3);
    expect(expanded).toBeGreaterThan(HUD_EXPANDED_HEIGHT_DP);
    expect(collapsed).toBeGreaterThan(HUD_COLLAPSED_HEIGHT_DP);
  });

  it('stops growing at MAX_FONT_SCALE', () => {
    expect(hudHeightsFor(3)).toEqual(hudHeightsFor(MAX_FONT_SCALE));
  });
});
