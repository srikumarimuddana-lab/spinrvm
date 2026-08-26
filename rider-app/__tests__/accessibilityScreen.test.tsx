/**
 * app/accessibility.tsx — single WAV (wheelchair-accessible vehicle)
 * visibility toggle. Pins: reflects rideStore.showWavOption, toggling it
 * calls setShowWavOption, and the back button navigates back. Uses the
 * real rideStore (not mocked) since this screen only touches two plain
 * boolean-state actions on it.
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity } from 'react-native';

jest.mock('@react-native-async-storage/async-storage', () => {
  const store: Record<string, string> = {};
  return {
    __esModule: true,
    default: {
      getItem: jest.fn((k: string) => Promise.resolve(store[k] ?? null)),
      setItem: jest.fn((k: string, v: string) => {
        store[k] = v;
        return Promise.resolve();
      }),
      removeItem: jest.fn((k: string) => {
        delete store[k];
        return Promise.resolve();
      }),
    },
  };
});

import AccessibilityScreen from '../app/accessibility';
import { useRideStore } from '../store/rideStore';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  SafeAreaView: ({ children }: any) => children,
}));

const mockBack = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ back: mockBack }),
}));

const COLORS = {
  primary: '#EF4444', surface: '#FFF', surfaceLight: '#F5F5F5',
  text: '#111', textDim: '#666', border: '#E5E7EB',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

function allText(renderer: TestRenderer.ReactTestRenderer): string {
  return JSON.stringify(renderer.toJSON());
}

let renderer: TestRenderer.ReactTestRenderer | null = null;

beforeEach(() => {
  jest.clearAllMocks();
  useRideStore.setState({ showWavOption: false });
});

afterEach(() => {
  act(() => {
    renderer?.unmount();
  });
  renderer = null;
});

describe('AccessibilityScreen', () => {
  it('renders off when showWavOption is false', () => {
    act(() => {
      renderer = TestRenderer.create(<AccessibilityScreen />);
    });
    expect(allText(renderer!)).toContain('Show wheelchair-accessible options');
  });

  it('toggling calls setShowWavOption and updates the store', () => {
    act(() => {
      renderer = TestRenderer.create(<AccessibilityScreen />);
    });
    const toggle = renderer!.root.findByProps({ value: false });
    act(() => {
      toggle.props.onValueChange(true);
    });
    expect(useRideStore.getState().showWavOption).toBe(true);
  });

  it('navigates back when the back button is pressed', () => {
    act(() => {
      renderer = TestRenderer.create(<AccessibilityScreen />);
    });
    const backBtn = renderer!.root.findAllByType(TouchableOpacity)[0];
    act(() => {
      backBtn.props.onPress();
    });
    expect(mockBack).toHaveBeenCalled();
  });
});
