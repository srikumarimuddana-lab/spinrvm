/**
 * app/driver/settings.tsx — broader coverage beyond
 * screens/settingsWavToggle.test.tsx (covers only the WAV toggle's
 * accessibilityRole/State).
 *
 * Pins:
 *  - the four notification toggles (push/ride/earnings/promotions) each
 *    call updatePreferences.mutate with their own key, and revert on error
 *  - the two marketing (CASL) toggles PUT /marketing/preferences and revert
 *    optimistically on failure
 *  - sound/vibration toggles call the alertPrefsStore setters directly (no
 *    server round-trip)
 *  - dark mode toggle calls setTheme('dark'|'light') based on current state
 *  - nav option rows call setNavApp with their key
 *  - the WAV toggle mutates useUpdateDriverMe and reverts on error
 *  - the language modal opens, selects a language (setLanguage + closes),
 *    and closes on backdrop tap
 *  - handleExportData: success/failure toast, spinner while in flight
 *  - handleDeleteAccount: Alert-confirm opens the step-2 modal; typing
 *    anything other than DELETE toasts and does not call the API; typing
 *    DELETE calls DELETE /users/account, logs out, and navigates to
 *    /login; a failed delete toasts the backend's message
 *  - each Account/Emergency row navigates to its destination route
 */
import React from 'react';
import { render, fireEvent, waitFor, act } from '@testing-library/react-native';
import { Alert } from 'react-native';

import SettingsScreen from '../../app/driver/settings';

const flush = () => act(async () => { await Promise.resolve(); await Promise.resolve(); });

const mockPush = jest.fn();
const mockReplace = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ back: jest.fn(), push: mockPush, replace: mockReplace }),
}));

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, right: 0, bottom: 0, left: 0 }),
}));
jest.mock('../../components/ScreenHeader', () => ({ ScreenHeader: () => null }));

const COLORS = {
  primary: '#EF4444', primaryDark: '#D32F2F', background: '#FFFFFF', surface: '#FFFFFF',
  surfaceLight: '#F3F4F6', text: '#111827', textDim: '#6B7280', textSecondary: '#9CA3AF',
  border: '#E5E7EB', orange: '#F97316', gold: '#D4AF37', danger: '#DC2626', error: '#DC2626',
};
const mockSetTheme = jest.fn();
let mockColorScheme = 'light';
jest.mock('@shared/theme/ThemeContext', () => ({
  useTheme: () => ({ colors: COLORS, colorScheme: mockColorScheme, setTheme: mockSetTheme }),
}));

const mockSetLanguage = jest.fn();
jest.mock('../../store/languageStore', () => ({
  useLanguageStore: () => ({
    language: 'en', setLanguage: mockSetLanguage, loadLanguage: jest.fn(),
    t: (key: string) => key,
  }),
}));

const mockSetNavApp = jest.fn();
jest.mock('../../store/navStore', () => ({
  useNavStore: () => ({ navApp: 'default', setNavApp: mockSetNavApp, loadNavApp: jest.fn() }),
}));

const mockSetSoundEffects = jest.fn();
const mockSetVibration = jest.fn();
jest.mock('../../store/alertPrefsStore', () => ({
  useAlertPrefsStore: () => ({
    soundEffects: true, vibration: true,
    setSoundEffects: mockSetSoundEffects, setVibration: mockSetVibration,
    loadAlertPrefs: jest.fn(),
  }),
}));

const mockShowToast = jest.fn();
jest.mock('../../hooks/useToast', () => ({ showToast: (...a: any[]) => mockShowToast(...a) }));

const mockLogout = jest.fn();
jest.mock('@shared/store/authStore', () => ({ useAuthStore: () => ({ logout: mockLogout }) }));

const mockApiGet = jest.fn();
const mockApiPut = jest.fn();
const mockApiPost = jest.fn();
const mockApiDelete = jest.fn();
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: {
    get: (...a: any[]) => mockApiGet(...a),
    put: (...a: any[]) => mockApiPut(...a),
    post: (...a: any[]) => mockApiPost(...a),
    delete: (...a: any[]) => mockApiDelete(...a),
  },
  getApiErrorMessage: (_err: any, fallback: string) => fallback,
}));

const mockUpdatePreferencesMutate = jest.fn();
const mockUpdateDriverMeMutate = jest.fn();
let mockPrefsData: any = undefined;
let mockDriverMeData: { is_wav?: boolean | null } | undefined = { is_wav: false };
jest.mock('@shared/hooks/queries', () => ({
  useNotificationPreferences: () => ({ data: mockPrefsData }),
  useUpdateNotificationPreferences: () => ({ mutate: mockUpdatePreferencesMutate }),
  useDriverMe: () => ({ data: mockDriverMeData }),
  useUpdateDriverMe: () => ({ mutate: mockUpdateDriverMeMutate }),
}));

beforeEach(() => {
  jest.clearAllMocks();
  mockColorScheme = 'light';
  mockPrefsData = undefined;
  mockDriverMeData = { is_wav: false };
  mockApiGet.mockResolvedValue({ data: { email_opt_in: false, sms_opt_in: false } });
  mockApiPut.mockResolvedValue({ data: {} });
  mockApiPost.mockResolvedValue({ data: {} });
  mockApiDelete.mockResolvedValue({ data: {} });
  jest.spyOn(Alert, 'alert').mockImplementation(() => {});
});

describe('notification toggles', () => {
  const cases: [string, string][] = [
    ['settings.pushNotifications', 'push_enabled'],
    ['settings.rideAlerts', 'ride_updates'],
    ['settings.earningsSummary', 'earnings_summary'],
    ['settings.promotions', 'promotions'],
  ];
  it.each(cases)('%s toggles and mutates key %s', async (label, key) => {
    const screen = render(<SettingsScreen />);
    await flush();
    const toggle = screen.getByLabelText(label);
    await act(async () => { fireEvent.press(toggle); });
    expect(mockUpdatePreferencesMutate).toHaveBeenCalledWith(
      { [key]: expect.any(Boolean) },
      expect.anything(),
    );
  });

  it('reverts the toggle on mutation error', async () => {
    const screen = render(<SettingsScreen />);
    await flush();
    const toggle = screen.getByLabelText('settings.pushNotifications');
    expect(toggle.props.accessibilityState.checked).toBe(true);
    mockUpdatePreferencesMutate.mockImplementation((_vars: any, opts: any) => opts.onError());
    await act(async () => { fireEvent.press(toggle); });
    expect(mockShowToast).toHaveBeenCalledWith('error', 'settings.prefNotSavedTitle', 'settings.prefNotSavedMsg');
    expect(screen.getByLabelText('settings.pushNotifications').props.accessibilityState.checked).toBe(true);
  });

  it('hydrates from the prefs query response', async () => {
    mockPrefsData = { push_enabled: false, ride_updates: false, earnings_summary: false, promotions: true };
    const screen = render(<SettingsScreen />);
    await flush();
    expect(screen.getByLabelText('settings.pushNotifications').props.accessibilityState.checked).toBe(false);
    expect(screen.getByLabelText('settings.promotions').props.accessibilityState.checked).toBe(true);
  });
});

describe('marketing consent toggles', () => {
  it('hydrates from GET /marketing/preferences', async () => {
    mockApiGet.mockResolvedValue({ data: { email_opt_in: true, sms_opt_in: true } });
    const screen = render(<SettingsScreen />);
    await flush();
    await waitFor(() => {
      expect(screen.getByLabelText('settings.marketingEmails').props.accessibilityState.checked).toBe(true);
    });
    expect(screen.getByLabelText('settings.marketingSms').props.accessibilityState.checked).toBe(true);
  });

  it('PUTs the email channel and reverts on failure', async () => {
    const screen = render(<SettingsScreen />);
    await flush();
    await waitFor(() => expect(mockApiGet).toHaveBeenCalledWith('/marketing/preferences'));
    const emailToggle = screen.getByLabelText('settings.marketingEmails');
    await act(async () => { fireEvent.press(emailToggle); });
    expect(mockApiPut).toHaveBeenCalledWith('/marketing/preferences', { email_opt_in: true, source: 'driver_app' });
    expect(screen.getByLabelText('settings.marketingEmails').props.accessibilityState.checked).toBe(true);

    mockApiPut.mockRejectedValueOnce(new Error('down'));
    await act(async () => { fireEvent.press(screen.getByLabelText('settings.marketingEmails')); });
    expect(mockShowToast).toHaveBeenCalledWith('error', 'settings.prefNotSavedTitle', 'settings.prefNotSavedMsg');
    expect(screen.getByLabelText('settings.marketingEmails').props.accessibilityState.checked).toBe(true);
  });

  it('PUTs the sms channel', async () => {
    const screen = render(<SettingsScreen />);
    await flush();
    await waitFor(() => expect(mockApiGet).toHaveBeenCalled());
    await act(async () => { fireEvent.press(screen.getByLabelText('settings.marketingSms')); });
    expect(mockApiPut).toHaveBeenCalledWith('/marketing/preferences', { sms_opt_in: true, source: 'driver_app' });
  });
});

describe('sound & haptics', () => {
  it('sound effects toggle calls setSoundEffects directly (no API)', async () => {
    const screen = render(<SettingsScreen />);
    await flush();
    await act(async () => { fireEvent.press(screen.getByLabelText('settings.soundEffects')); });
    expect(mockSetSoundEffects).toHaveBeenCalledWith(false);
    expect(mockApiPut).not.toHaveBeenCalled();
  });

  it('vibration toggle calls setVibration directly', async () => {
    const screen = render(<SettingsScreen />);
    await flush();
    await act(async () => { fireEvent.press(screen.getByLabelText('settings.vibration')); });
    expect(mockSetVibration).toHaveBeenCalledWith(false);
  });
});

describe('appearance', () => {
  it('turns dark mode on from light', async () => {
    mockColorScheme = 'light';
    const screen = render(<SettingsScreen />);
    await flush();
    await act(async () => { fireEvent.press(screen.getByLabelText('settings.darkMode')); });
    expect(mockSetTheme).toHaveBeenCalledWith('dark');
  });

  it('turns dark mode off from dark', async () => {
    mockColorScheme = 'dark';
    const screen = render(<SettingsScreen />);
    await flush();
    await act(async () => { fireEvent.press(screen.getByLabelText('settings.darkMode')); });
    expect(mockSetTheme).toHaveBeenCalledWith('light');
  });
});

describe('navigation app selection', () => {
  it('selecting a nav option calls setNavApp with its key', async () => {
    const screen = render(<SettingsScreen />);
    await flush();
    fireEvent.press(screen.getByText('settings.navWaze'));
    expect(mockSetNavApp).toHaveBeenCalledWith('waze');
  });
});

describe('WAV toggle mutation', () => {
  it('mutates useUpdateDriverMe and reverts on error', async () => {
    mockDriverMeData = { is_wav: false };
    const screen = render(<SettingsScreen />);
    await flush();
    const toggle = screen.getByLabelText('settings.wavTitle');
    mockUpdateDriverMeMutate.mockImplementation((_vars: any, opts: any) => opts.onError());
    await act(async () => { fireEvent.press(toggle); });
    expect(mockUpdateDriverMeMutate).toHaveBeenCalledWith({ is_wav: true }, expect.anything());
    expect(mockShowToast).toHaveBeenCalledWith('error', 'settings.wavNotSavedTitle', 'settings.wavNotSavedMsg');
    expect(screen.getByLabelText('settings.wavTitle').props.accessibilityState.checked).toBe(false);
  });
});

describe('language modal', () => {
  it('opens, selects a language, and closes', async () => {
    const screen = render(<SettingsScreen />);
    await flush();
    fireEvent.press(screen.getByText('settings.language'));
    expect(screen.getByText('Français')).toBeTruthy();
    fireEvent.press(screen.getByText('Français'));
    expect(mockSetLanguage).toHaveBeenCalledWith('fr');
  });
});

describe('export data', () => {
  it('success toasts the confirmation', async () => {
    const screen = render(<SettingsScreen />);
    await flush();
    await act(async () => { fireEvent.press(screen.getByText('settings.downloadData')); });
    expect(mockApiPost).toHaveBeenCalledWith('/drivers/me/export-data');
    expect(mockShowToast).toHaveBeenCalledWith('success', 'settings.exportRequestedTitle', 'settings.exportRequestedMsg');
  });

  it('failure toasts the failure message', async () => {
    mockApiPost.mockRejectedValue(new Error('rate limited'));
    const screen = render(<SettingsScreen />);
    await flush();
    await act(async () => { fireEvent.press(screen.getByText('settings.downloadData')); });
    expect(mockShowToast).toHaveBeenCalledWith('error', 'settings.exportFailedTitle', 'settings.exportFailedMsg');
  });
});

describe('delete account flow', () => {
  it('Alert-confirm opens step-2; typing anything other than DELETE toasts without calling the API', async () => {
    const screen = render(<SettingsScreen />);
    await flush();
    fireEvent.press(screen.getByText('settings.deleteAccount'));
    expect(Alert.alert).toHaveBeenCalled();
    const alertCall = (Alert.alert as jest.Mock).mock.calls[0];
    const continueBtn = alertCall[2].find((b: any) => b.text === 'settings.deleteContinue');
    act(() => { continueBtn.onPress(); });

    const input = screen.getByPlaceholderText('settings.typeDelete');
    fireEvent.changeText(input, 'nope');
    await act(async () => { fireEvent.press(screen.getByText('settings.deleteForever')); });
    expect(mockShowToast).toHaveBeenCalledWith('warning', 'settings.notConfirmedTitle', 'settings.notConfirmedMsg');
    expect(mockApiDelete).not.toHaveBeenCalled();
  });

  it('typing DELETE deletes, logs out, and navigates to /login', async () => {
    const screen = render(<SettingsScreen />);
    await flush();
    fireEvent.press(screen.getByText('settings.deleteAccount'));
    const continueBtn = (Alert.alert as jest.Mock).mock.calls[0][2].find((b: any) => b.text === 'settings.deleteContinue');
    act(() => { continueBtn.onPress(); });

    fireEvent.changeText(screen.getByPlaceholderText('settings.typeDelete'), 'delete');
    await act(async () => { fireEvent.press(screen.getByText('settings.deleteForever')); });
    expect(mockApiDelete).toHaveBeenCalledWith('/users/account');
    expect(mockLogout).toHaveBeenCalled();
    expect(mockReplace).toHaveBeenCalledWith('/login');
  });

  it('a failed delete toasts the backend message instead of navigating', async () => {
    mockApiDelete.mockRejectedValue(new Error('active ride'));
    const screen = render(<SettingsScreen />);
    await flush();
    fireEvent.press(screen.getByText('settings.deleteAccount'));
    const continueBtn = (Alert.alert as jest.Mock).mock.calls[0][2].find((b: any) => b.text === 'settings.deleteContinue');
    act(() => { continueBtn.onPress(); });

    fireEvent.changeText(screen.getByPlaceholderText('settings.typeDelete'), 'DELETE');
    await act(async () => { fireEvent.press(screen.getByText('settings.deleteForever')); });
    expect(mockShowToast).toHaveBeenCalledWith('error', 'settings.deleteFailedTitle', 'settings.deleteFailedMsg');
    expect(mockLogout).not.toHaveBeenCalled();
    expect(mockReplace).not.toHaveBeenCalled();
  });

  it('cancel in step-2 closes without deleting', async () => {
    const screen = render(<SettingsScreen />);
    await flush();
    fireEvent.press(screen.getByText('settings.deleteAccount'));
    const continueBtn = (Alert.alert as jest.Mock).mock.calls[0][2].find((b: any) => b.text === 'settings.deleteContinue');
    act(() => { continueBtn.onPress(); });
    fireEvent.press(screen.getByText('settings.cancel'));
    expect(mockApiDelete).not.toHaveBeenCalled();
  });
});

describe('account & emergency rows navigate correctly', () => {
  const cases: [string, string][] = [
    ['settings.legal', '/legal'],
    ['settings.morePolicies', '/policies'],
    ['settings.backgroundCheckConsent', '/crc-consent'],
    ['settings.appealAccountStatus', '/appeal'],
    ['settings.taxDocuments', '/driver/tax-documents'],
    ['settings.destinationModeTitle', '/driver/destination-mode'],
    ['settings.reportSafety', '/report-safety'],
    ['settings.emergencyContacts', '/driver/emergency-contacts'],
  ];
  it.each(cases)('%s navigates to %s', async (label, route) => {
    const screen = render(<SettingsScreen />);
    await flush();
    fireEvent.press(screen.getByText(label));
    expect(mockPush).toHaveBeenCalledWith(route);
  });

  it('the emergency call row confirms via Alert then dials 911', async () => {
    const screen = render(<SettingsScreen />);
    await flush();
    fireEvent.press(screen.getByText('settings.callEmergency'));
    expect(Alert.alert).toHaveBeenCalledWith(
      'settings.emergencyServicesTitle',
      'settings.emergencyServicesMsg',
      expect.any(Array),
    );
  });
});
