/**
 * docs/change-log/2026-08-20-explicit-signup-consent-checkbox.md,
 * revised 2026-08-27 (docs/migration/2026-08-27-legacy-data-full-migration-approach.md §6a).
 *
 * Pins app/login.tsx's explicit consent checkbox, updated for the 2026-08-27
 * fix: the checkbox no longer gates "Send Verification Code" — only phone
 * validity does. The backend only ever reads consent_accepted for a
 * brand-new account (routes/auth.py's verify_otp); gating this screen on it
 * forced every returning user (session expired, logged back in) to re-tick
 * a box with zero effect for them. The checkbox stays visible/toggleable —
 * a proactive new signup can still pre-accept it — and its state is still
 * carried to /otp as a route param either way. otp.tsx now handles the
 * genuine-new-account case inline if the backend comes back with
 * consent_required.
 *  - the checkbox starts unchecked; the "Send Verification Code" button is
 *    enabled once the phone number is valid, regardless of checkbox state
 *  - toggling it (accessibilityRole="checkbox", accessibilityState.checked)
 *    still works, but never itself disables/enables the button
 *  - an invalid phone number still disables continue, checkbox state aside
 *  - a successful continue navigates to /otp carrying consentAccepted
 *    as a route param reflecting whatever the checkbox's state was
 *
 * Uses react-test-renderer directly, matching verifyEmailScreen.test.tsx's
 * conventions.
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
  // signed-out consent-gating behaviour, so a no-op keeps these tests
  // focused without needing to fake navigation focus events.
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

function getConsentCheckbox(renderer: TestRenderer.ReactTestRenderer) {
  return renderer.root.findByProps({ accessibilityRole: 'checkbox' });
}

function toggleConsent(renderer: TestRenderer.ReactTestRenderer) {
  act(() => {
    getConsentCheckbox(renderer).props.onPress();
  });
}

async function tapContinue(renderer: TestRenderer.ReactTestRenderer) {
  await act(async () => {
    await getContinueButton(renderer).props.onPress();
    await Promise.resolve();
  });
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

describe('LoginScreen — explicit consent checkbox', () => {
  it('starts unchecked, with the continue button already enabled for a valid phone number', async () => {
    const renderer = await renderScreen();
    enterPhone(renderer, '3065550199');

    const checkbox = getConsentCheckbox(renderer);
    expect(checkbox.props.accessibilityState).toMatchObject({ checked: false });

    const button = getContinueButton(renderer);
    expect(button.props.accessibilityState).toMatchObject({ disabled: false });
  });

  it('checking the box toggles its own state without changing the continue button', async () => {
    const renderer = await renderScreen();
    enterPhone(renderer, '3065550199');
    toggleConsent(renderer);

    expect(getConsentCheckbox(renderer).props.accessibilityState).toMatchObject({ checked: true });
    expect(getContinueButton(renderer).props.accessibilityState).toMatchObject({ disabled: false });
  });

  it('an invalid phone number keeps continue disabled regardless of consent state', async () => {
    const renderer = await renderScreen();
    enterPhone(renderer, '123'); // too short
    toggleConsent(renderer);

    expect(getContinueButton(renderer).props.accessibilityState).toMatchObject({ disabled: true });
  });

  it('tapping continue while unchecked still calls send-otp and carries consentAccepted:false to /otp', async () => {
    const renderer = await renderScreen();
    enterPhone(renderer, '3065550199');
    await tapContinue(renderer); // consent left unchecked

    expect(mockApiPost).toHaveBeenCalledWith('/auth/send-otp', { phone: '+13065550199' });
    expect(mockPush).toHaveBeenCalledWith(
      expect.objectContaining({
        pathname: '/otp',
        params: expect.objectContaining({ phoneNumber: '+13065550199', consentAccepted: 'false' }),
      }),
    );
  });

  it('navigates to /otp with consentAccepted carried as a route param once checked', async () => {
    const renderer = await renderScreen();
    enterPhone(renderer, '3065550199');
    toggleConsent(renderer);
    await tapContinue(renderer);

    expect(mockApiPost).toHaveBeenCalledWith('/auth/send-otp', { phone: '+13065550199' });
    expect(mockPush).toHaveBeenCalledWith(
      expect.objectContaining({
        pathname: '/otp',
        params: expect.objectContaining({ phoneNumber: '+13065550199', consentAccepted: 'true' }),
      }),
    );
  });

  it('unchecking after checking leaves continue enabled — phone validity is the only gate now', async () => {
    const renderer = await renderScreen();
    enterPhone(renderer, '3065550199');
    toggleConsent(renderer); // check
    toggleConsent(renderer); // uncheck
    expect(getContinueButton(renderer).props.accessibilityState).toMatchObject({ disabled: false });
  });
});
