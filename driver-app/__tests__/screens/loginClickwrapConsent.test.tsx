/**
 * docs/change-log/2026-08-28-driver-login-clickwrap-consent.md.
 *
 * Pins app/login.tsx's consent gesture after the checkbox was replaced by a
 * clickwrap disclosure: the "By continuing, you agree to..." sentence sits
 * under the button, and tapping "Send Verification Code" IS the acceptance.
 * History: passive text -> explicit checkbox (2026-08-20, closing the
 * consent-evidence gap in ACTION_ITEMS.md A41) -> checkbox stops gating the
 * button (2026-08-27) -> clickwrap (this file). The backend still refuses a
 * brand-new account without consent_accepted and still stamps
 * consent_version / consent_accepted_at, so what is recorded is unchanged;
 * only the gesture that produces it moved.
 *  - no checkbox is rendered any more (the whole point of the change)
 *  - the disclosure and BOTH legal links are present and individually
 *    reachable — this screen is the only pre-account path to those
 *    documents, so losing them is an accessibility blocker
 *  - a successful continue always carries consentAccepted:'true' to /otp
 *  - phone validity alone still gates the button
 *
 * Mirrors screens/settingsWavToggle.test.tsx's mocking conventions
 * (@testing-library/react-native, `t: (key) => key`).
 */
import React from 'react';
import { render, fireEvent, act, waitFor } from '@testing-library/react-native';
import LoginScreen from '../../app/login';

const mockPush = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ back: jest.fn(), push: mockPush, replace: jest.fn() }),
}));

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, right: 0, bottom: 0, left: 0 }),
}));

jest.mock('../../store/languageStore', () => ({
  useLanguageStore: () => ({
    language: 'en',
    setLanguage: jest.fn(),
    loadLanguage: jest.fn(),
    t: (key: string) => key,
  }),
}));

const mockShowToast = jest.fn();
jest.mock('../../hooks/useToast', () => ({ showToast: (...args: any[]) => mockShowToast(...args) }));

jest.mock('@shared/theme/ThemeContext', () => ({
  useTheme: () => ({
    colors: {
      primary: '#EF4444', primaryDark: '#D32F2F', background: '#FFFFFF', surface: '#FFFFFF',
      surfaceLight: '#F3F4F6', text: '#111827', textDim: '#6B7280', border: '#E5E7EB',
      success: '#22C55E',
    },
    isDark: false,
  }),
}));

const mockApiPost = jest.fn().mockResolvedValue({ data: { success: true } });
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { get: jest.fn(), post: (...args: any[]) => mockApiPost(...args), put: jest.fn(), delete: jest.fn() },
  getApiErrorMessage: (_err: unknown, fallback: string) => fallback,
}));

function findContinueButton(screen: ReturnType<typeof render>) {
  return screen.getByLabelText('Send verification code');
}

afterEach(() => jest.clearAllMocks());

describe('Driver LoginScreen — clickwrap consent', () => {
  it('renders no consent checkbox', () => {
    const screen = render(<LoginScreen />);
    expect(screen.UNSAFE_queryAllByProps({ accessibilityRole: 'checkbox' })).toHaveLength(0);
    expect(screen.queryByTestId('consent-checkbox')).toBeNull();
  });

  it('shows the "by continuing" disclosure with both legal links reachable', () => {
    const screen = render(<LoginScreen />);
    // `t` is mocked to echo the key, so these assert the keys the screen uses.
    expect(screen.getByText(/login\.termsPrefix/)).toBeTruthy();
    const tos = screen.getByLabelText('login.termsOfService');
    const privacy = screen.getByLabelText('login.privacyPolicy');
    // Separate nodes, each its own link — not collapsed into one row.
    expect(tos.props.accessibilityRole).toBe('link');
    expect(privacy.props.accessibilityRole).toBe('link');
    expect(tos).not.toBe(privacy);
  });

  it('each legal link opens its own document', () => {
    const screen = render(<LoginScreen />);
    fireEvent.press(screen.getByLabelText('login.termsOfService'));
    expect(mockPush).toHaveBeenCalledWith(
      expect.objectContaining({ pathname: '/legal', params: { type: 'tos' } }),
    );
    fireEvent.press(screen.getByLabelText('login.privacyPolicy'));
    expect(mockPush).toHaveBeenCalledWith(
      expect.objectContaining({ pathname: '/legal', params: { type: 'privacy' } }),
    );
  });

  it('continuing is itself the consent gesture — carries consentAccepted:true to /otp', async () => {
    const screen = render(<LoginScreen />);
    fireEvent.changeText(screen.getByTestId('phone-input'), '3065550199');
    await act(async () => {
      fireEvent.press(findContinueButton(screen));
    });

    await waitFor(() => expect(mockApiPost).toHaveBeenCalledWith('/auth/send-otp', { phone: '+13065550199' }));
    expect(mockPush).toHaveBeenCalledWith(
      expect.objectContaining({
        pathname: '/otp',
        params: expect.objectContaining({ phoneNumber: '+13065550199', consentAccepted: 'true' }),
      }),
    );
  });

  it('a valid phone number alone enables continue', () => {
    const screen = render(<LoginScreen />);
    fireEvent.changeText(screen.getByTestId('phone-input'), '3065550199');
    expect(findContinueButton(screen).props.accessibilityState).toEqual(
      expect.objectContaining({ disabled: false }),
    );
  });

  it('an invalid phone number keeps continue disabled', () => {
    const screen = render(<LoginScreen />);
    fireEvent.changeText(screen.getByTestId('phone-input'), '123'); // too short
    expect(findContinueButton(screen).props.accessibilityState).toEqual(
      expect.objectContaining({ disabled: true }),
    );
  });
});
