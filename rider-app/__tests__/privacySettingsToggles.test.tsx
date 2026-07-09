/**
 * Regression tests for the Privacy Settings toggles.
 *
 * Audit findings pinned here:
 *  - The "Background Location" and "Share Live Trip Data" rows were decorative
 *    (bare useState, no persistence) — they must stay removed until they do
 *    something real.
 *  - The Push Notifications toggle used to be a second, divergent copy of the
 *    Settings-screen toggle; it must read and write the same server-side
 *    preference (notification_preferences.push_enabled) with revert-on-error.
 *
 * Uses react-test-renderer directly (matching useExitOnBackPress.test.tsx) —
 * the pinned @testing-library/react-native v12 can't probe RN 0.85 host
 * components.
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('expo-router', () => ({ useRouter: () => ({ back: jest.fn(), replace: jest.fn() }) }));
jest.mock('react-native-safe-area-context', () => {
  const { View } = require('react-native');
  return { SafeAreaView: ({ children }: any) => <View>{children}</View> };
});
jest.mock('../components/ConfirmSheet', () => () => null);
jest.mock('../store/toastStore', () => ({ showToast: jest.fn() }));
jest.mock('../i18n', () => ({ useTranslation: () => ({ t: (k: string) => k }) }));
jest.mock('@shared/store/authStore', () => ({ useAuthStore: () => ({ logout: jest.fn() }) }));
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: {
    get: jest.fn(() => Promise.resolve({ data: {} })),
    put: jest.fn(() => Promise.resolve({ data: {} })),
    post: jest.fn(() => Promise.resolve({ data: {} })),
    delete: jest.fn(() => Promise.resolve({ data: {} })),
  },
}));

const COLORS = {
  primary: '#EF4444', background: '#FFF', surface: '#FFF', surfaceLight: '#F5F5F5',
  text: '#111', textDim: '#666', border: '#E5E7EB',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

const mockMutate = jest.fn();
let mockPrefsData: any = null;
jest.mock('@shared/hooks/queries', () => ({
  useNotificationPreferences: () => ({ data: mockPrefsData }),
  useUpdateNotificationPreferences: () => ({ mutate: mockMutate }),
}));

import PrivacySettingsScreen from '../app/privacy-settings';
import CustomToggle from '../components/CustomToggle';

function renderScreen() {
  let renderer!: TestRenderer.ReactTestRenderer;
  act(() => {
    renderer = TestRenderer.create(<PrivacySettingsScreen />);
  });
  return renderer;
}

function allText(renderer: TestRenderer.ReactTestRenderer): string {
  return JSON.stringify(renderer.toJSON());
}

// The push toggle is the first CustomToggle on the screen (before the three
// marketing-consent toggles).
function pushToggle(renderer: TestRenderer.ReactTestRenderer) {
  return renderer.root.findAllByType(CustomToggle)[0];
}

beforeEach(() => {
  jest.clearAllMocks();
  mockPrefsData = null;
});

describe('PrivacySettingsScreen toggles', () => {
  it('no longer renders the dead Background Location / Share Live rows', () => {
    const renderer = renderScreen();
    const rendered = allText(renderer);
    expect(rendered).not.toContain('privacy.background_location');
    expect(rendered).not.toContain('privacy.share_live');
    // Push + 3 marketing toggles remain.
    expect(renderer.root.findAllByType(CustomToggle)).toHaveLength(4);
  });

  it('hydrates the push toggle from the server preference', () => {
    mockPrefsData = { push_enabled: false };
    const renderer = renderScreen();
    expect(pushToggle(renderer).props.value).toBe(false);
  });

  it('writes push_enabled via the shared preferences mutation on toggle', () => {
    mockPrefsData = { push_enabled: true };
    const renderer = renderScreen();
    act(() => {
      pushToggle(renderer).props.onValueChange(false);
    });
    expect(mockMutate).toHaveBeenCalledWith({ push_enabled: false }, expect.anything());
  });

  it('reverts the push toggle when the save fails', () => {
    mockPrefsData = { push_enabled: true };
    const renderer = renderScreen();
    act(() => {
      pushToggle(renderer).props.onValueChange(false);
    });
    expect(pushToggle(renderer).props.value).toBe(false);
    // Simulate the mutation failing — the optimistic flip must roll back.
    act(() => {
      mockMutate.mock.calls[0][1].onError();
    });
    expect(pushToggle(renderer).props.value).toBe(true);
  });
});
