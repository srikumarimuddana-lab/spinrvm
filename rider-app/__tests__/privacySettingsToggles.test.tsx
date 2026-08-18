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

import PrivacySettingsScreen from '../app/privacy-settings';
import CustomToggle from '../components/CustomToggle';

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
  // The screen routes save failures through getApiErrorMessage; mirror the
  // real fallback contract (no usable detail → caller's message).
  getApiErrorMessage: jest.fn((_err: unknown, fallback: string) => fallback),
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

// Flushes the microtask queue so the mount effect's `api.get(...).then(...)`
// (marketing-preferences hydration) settles inside this test's own `act`,
// instead of resolving after the test — or the whole file — has finished.
// An unflushed renderer previously leaked that resolution into whatever
// test ran next in the same Jest worker (see verifyEmailScreen.test.tsx's
// `flush`/`mountedRenderer` comments for the same failure mode there).
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

// Tracked so afterEach can unmount it — otherwise the leaked mount effect
// above fires its state updates against an unmounted-but-never-torn-down
// renderer, producing "update not wrapped in act" warnings and, worse,
// "import a file after the Jest environment has been torn down" errors in
// later test files sharing this worker process.
let mountedRenderer: TestRenderer.ReactTestRenderer | null = null;

async function renderScreen() {
  let renderer!: TestRenderer.ReactTestRenderer;
  await act(async () => {
    renderer = TestRenderer.create(<PrivacySettingsScreen />);
    await flush();
    await flush();
  });
  mountedRenderer = renderer;
  return renderer;
}

afterEach(() => {
  mountedRenderer?.unmount();
  mountedRenderer = null;
});

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
  it('no longer renders the dead Background Location / Share Live rows', async () => {
    const renderer = await renderScreen();
    const rendered = allText(renderer);
    expect(rendered).not.toContain('privacy.background_location');
    expect(rendered).not.toContain('privacy.share_live');
    // Push + 3 marketing toggles remain.
    expect(renderer.root.findAllByType(CustomToggle)).toHaveLength(4);
  });

  it('hydrates the push toggle from the server preference', async () => {
    mockPrefsData = { push_enabled: false };
    const renderer = await renderScreen();
    expect(pushToggle(renderer).props.value).toBe(false);
  });

  it('writes push_enabled via the shared preferences mutation on toggle', async () => {
    mockPrefsData = { push_enabled: true };
    const renderer = await renderScreen();
    act(() => {
      pushToggle(renderer).props.onValueChange(false);
    });
    expect(mockMutate).toHaveBeenCalledWith({ push_enabled: false }, expect.anything());
  });

  it('reverts the push toggle when the save fails', async () => {
    mockPrefsData = { push_enabled: true };
    const renderer = await renderScreen();
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
