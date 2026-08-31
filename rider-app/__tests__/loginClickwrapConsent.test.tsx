/**
 * docs/change-log/2026-08-30-rider-login-clickwrap-consent.md.
 *
 * Pins app/login.tsx's consent gesture after the checkbox was replaced by a
 * clickwrap disclosure, mirroring the driver app (2026-08-28, commit 7ca0ddf):
 * the "By continuing, you agree to..." sentence sits under the button, and
 * tapping "Send Verification Code" IS the acceptance.
 *
 * History: passive text -> explicit checkbox (2026-08-20, closing the
 * consent-evidence gap in ACTION_ITEMS.md A41) -> checkbox stops gating the
 * button and is scoped to first login (2026-08-27) -> clickwrap (this file).
 * The backend still refuses a brand-new account without consent_accepted and
 * still stamps consent_version / consent_accepted_at, so what is recorded is
 * unchanged; only the gesture that produces it moved.
 *  - no checkbox is rendered any more (the whole point of the change)
 *  - the disclosure and BOTH legal links are present and individually
 *    reachable — this screen is the only pre-account path to those
 *    documents, so losing them is an accessibility blocker
 *  - a successful continue always carries consentAccepted:'true' to /otp
 *  - phone validity alone still gates the button
 *
 * Uses react-test-renderer directly, matching this app's existing login-test
 * conventions (the driver app's equivalent uses @testing-library).
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';

import LoginScreen from '../app/login';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));

const mockPush = jest.fn();
const mockReplace = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ push: mockPush, replace: mockReplace, back: jest.fn() }),
  // login.tsx only uses this to bounce away when already authenticated
  // (useAuthStore.getState().token truthy) — irrelevant to this screen's
  // signed-out consent behaviour.
  useFocusEffect: () => {},
}));

const COLORS = {
  primary: '#EF4444', primaryDark: '#D32F2F', background: '#FFF', surface: '#FFF',
  surfaceLight: '#F5F5F5', text: '#111', textDim: '#666', border: '#E5E7EB', success: '#22C55E',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

const mockShowToast = jest.fn();
jest.mock('../store/toastStore', () => ({ showToast: (...args: any[]) => mockShowToast(...args) }));

const mockApiPost = jest.fn();
jest.mock('@shared/api/client', () => {
  const actual = jest.requireActual('@shared/api/client');
  return {
    __esModule: true,
    ...actual,
    default: { post: (...args: any[]) => mockApiPost(...args) },
  };
});

let mountedRenderer: TestRenderer.ReactTestRenderer | null = null;

async function renderScreen() {
  let renderer!: TestRenderer.ReactTestRenderer;
  await act(async () => {
    renderer = TestRenderer.create(<LoginScreen />);
    await Promise.resolve();
  });
  mountedRenderer = renderer;
  return renderer;
}

function enterPhone(renderer: TestRenderer.ReactTestRenderer, digits: string) {
  const input = renderer.root.findByProps({ accessibilityLabel: 'Phone number' });
  act(() => {
    input.props.onChangeText(digits);
  });
}

function getContinueButton(renderer: TestRenderer.ReactTestRenderer) {
  return renderer.root.findByProps({ accessibilityLabel: 'Send verification code' });
}

async function tapContinue(renderer: TestRenderer.ReactTestRenderer) {
  await act(async () => {
    await getContinueButton(renderer).props.onPress();
    await Promise.resolve();
  });
}

function allText(renderer: TestRenderer.ReactTestRenderer): string {
  return JSON.stringify(renderer.toJSON());
}

afterEach(() => {
  act(() => {
    mountedRenderer?.unmount();
  });
  mountedRenderer = null;
  jest.clearAllMocks();
});

beforeEach(() => {
  mockApiPost.mockResolvedValue({ data: { success: true } });
});

describe('LoginScreen — clickwrap consent', () => {
  it('renders no consent checkbox', async () => {
    const renderer = await renderScreen();
    expect(renderer.root.findAllByProps({ accessibilityRole: 'checkbox' })).toHaveLength(0);
  });

  it('shows the "by continuing" disclosure with both legal links reachable', async () => {
    const renderer = await renderScreen();
    expect(allText(renderer)).toContain('By continuing, you agree to our');

    // findAllByProps matches both the composite Text and its host node, so
    // assert presence and distinctness rather than an exact count.
    const tos = renderer.root.findAllByProps({ accessibilityLabel: 'Terms of Service' });
    const privacy = renderer.root.findAllByProps({ accessibilityLabel: 'Privacy Policy' });
    expect(tos.length).toBeGreaterThan(0);
    expect(privacy.length).toBeGreaterThan(0);
    expect(tos[0].props.accessibilityRole).toBe('link');
    expect(privacy[0].props.accessibilityRole).toBe('link');
  });

  it('each legal link opens its own document', async () => {
    const renderer = await renderScreen();
    act(() => {
      renderer.root.findAllByProps({ accessibilityLabel: 'Terms of Service' })[0].props.onPress();
    });
    expect(mockPush).toHaveBeenCalledWith(
      expect.objectContaining({ pathname: '/legal', params: { type: 'tos' } }),
    );
    act(() => {
      renderer.root.findAllByProps({ accessibilityLabel: 'Privacy Policy' })[0].props.onPress();
    });
    expect(mockPush).toHaveBeenCalledWith(
      expect.objectContaining({ pathname: '/legal', params: { type: 'privacy' } }),
    );
  });

  it('continuing is itself the consent gesture — carries consentAccepted:true to /otp', async () => {
    const renderer = await renderScreen();
    enterPhone(renderer, '3065550199');
    await tapContinue(renderer);

    expect(mockApiPost).toHaveBeenCalledWith('/auth/send-otp', { phone: '+13065550199' });
    expect(mockPush).toHaveBeenCalledWith(
      expect.objectContaining({
        pathname: '/otp',
        params: expect.objectContaining({ phoneNumber: '+13065550199', consentAccepted: 'true' }),
      }),
    );
  });

  it('a valid phone number alone enables continue', async () => {
    const renderer = await renderScreen();
    enterPhone(renderer, '3065550199');
    expect(getContinueButton(renderer).props.accessibilityState).toMatchObject({ disabled: false });
  });

  it('an invalid phone number still disables continue', async () => {
    const renderer = await renderScreen();
    enterPhone(renderer, '123');
    expect(getContinueButton(renderer).props.accessibilityState).toMatchObject({ disabled: true });
  });

  it('greets a first-time rider without claiming they have been here before', async () => {
    // "Welcome back" was shown unconditionally, including to someone creating
    // an account — the same mismatch the driver app fixed in 7ca0ddf.
    const renderer = await renderScreen();
    const text = allText(renderer);
    expect(text).toContain('Welcome to Spinr');
    expect(text).not.toContain('Welcome back');
  });
});
