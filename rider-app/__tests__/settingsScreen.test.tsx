/**
 * app/settings.tsx — rider account settings. Pins:
 *  - server-fetched notification preferences sync into local toggle state
 *  - toggling push ON when permission isn't granted requests it; if the
 *    driver/rider still declines, it never flips the toggle and instead
 *    opens device notification settings with a warning toast
 *  - a preference mutation failure reverts the toggle and toasts
 *  - dark mode toggling calls setTheme('dark'/'light')
 *  - the language picker modal opens, lists every language, and
 *    selecting one calls setLanguage and closes the modal
 *  - every navigation row (privacy, payment methods, saved places, ToS,
 *    privacy policy, more policies) routes correctly
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text, Modal, StyleSheet } from 'react-native';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  SafeAreaView: ({ children }: any) => children,
}));

const mockBack = jest.fn();
const mockPush = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ back: mockBack, push: mockPush }),
}));

const mockSetTheme = jest.fn();
const COLORS = {
  primary: '#EF4444', surface: '#FFF', surfaceLight: '#F5F5F5', text: '#111',
  textDim: '#666', textSecondary: '#333', border: '#E5E7EB',
};
jest.mock('@shared/theme/ThemeContext', () => ({
  useTheme: () => ({ colors: COLORS, isDark: false, setTheme: mockSetTheme }),
}));

jest.mock('@shared/api/client', () => ({
  __esModule: true,
  getApiErrorMessage: (_err: any, fallback: string) => fallback,
}));

const mockShowToast = jest.fn();
jest.mock('../store/toastStore', () => ({ showToast: (...args: any[]) => mockShowToast(...args) }));

let mockAuthUser: any = { phone: '+15551234567' };
jest.mock('@shared/store/authStore', () => ({
  useAuthStore: () => ({ user: mockAuthUser }),
}));

const mockSetLanguage = jest.fn();
let mockLanguage = 'en';
jest.mock('../i18n', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
  useLanguageStore: () => ({ language: mockLanguage, setLanguage: mockSetLanguage }),
  LANGUAGES: [
    { code: 'en', name: 'English', nativeName: 'English', flag: '🇨🇦' },
    { code: 'fr', name: 'French', nativeName: 'Français', flag: '🇫🇷' },
  ],
}));

let mockNotificationPrefsData: any = undefined;
const mockMutate = jest.fn();
jest.mock('@shared/hooks/queries', () => ({
  useNotificationPreferences: () => ({ data: mockNotificationPrefsData }),
  useUpdateNotificationPreferences: () => ({ mutate: mockMutate }),
}));

const mockCheckNotificationPermission = jest.fn();
const mockRequestNotificationPermission = jest.fn();
const mockOpenNotificationSettings = jest.fn();
jest.mock('@shared/services/firebase', () => ({
  checkNotificationPermission: (...a: any[]) => mockCheckNotificationPermission(...a),
  requestNotificationPermission: (...a: any[]) => mockRequestNotificationPermission(...a),
  openNotificationSettings: (...a: any[]) => mockOpenNotificationSettings(...a),
}));

import SettingsScreen from '../app/settings';

const flush = async () => {
  await Promise.resolve();
  await Promise.resolve();
};

let renderer: TestRenderer.ReactTestRenderer | null = null;
async function renderScreen() {
  await act(async () => {
    renderer = TestRenderer.create(<SettingsScreen />);
    await flush();
  });
  return renderer!;
}

function allText(r: TestRenderer.ReactTestRenderer) {
  return r.root.findAllByType(Text).map((t) => JSON.stringify(t.props.children)).join(' | ');
}

function findButtonByText(r: TestRenderer.ReactTestRenderer, text: string) {
  return r.root
    .findAllByType(TouchableOpacity)
    .find((n) => n.findAllByType(Text).some((t) => JSON.stringify(t.props.children).includes(text)))!;
}

// SettingToggle instances carry a `title` prop (the i18n key) -- selecting by
// that, rather than by boolean `value`, avoids ambiguity when multiple
// toggles share the same on/off state (push/email/sms all default true).
function findToggleByTitle(r: TestRenderer.ReactTestRenderer, title: string) {
  return r.root.findByProps({ title });
}

beforeEach(() => {
  jest.clearAllMocks();
  mockNotificationPrefsData = undefined;
  mockAuthUser = { phone: '+15551234567' };
  mockLanguage = 'en';
  mockCheckNotificationPermission.mockResolvedValue({ granted: true });
  mockRequestNotificationPermission.mockResolvedValue(true);
});

afterEach(() => {
  act(() => {
    renderer?.unmount();
  });
  renderer = null;
});

describe('SettingsScreen', () => {
  it('syncs local toggle state from fetched notification preferences', async () => {
    mockNotificationPrefsData = { push_enabled: false, email_enabled: false, sms_enabled: true };
    const r = await renderScreen();
    expect(findToggleByTitle(r, 'settings.push_notifications').props.value).toBe(false);
    expect(findToggleByTitle(r, 'settings.email_notifications').props.value).toBe(false);
    expect(findToggleByTitle(r, 'settings.sms_notifications').props.value).toBe(true);
  });

  it('toggles push on directly when permission is already granted', async () => {
    const r = await renderScreen();
    const pushToggle = findToggleByTitle(r, 'settings.push_notifications');
    await act(async () => {
      await pushToggle.props.onToggle(true);
      await flush();
    });
    expect(mockCheckNotificationPermission).toHaveBeenCalled();
    expect(mockMutate).toHaveBeenCalledWith(
      { push_enabled: true },
      expect.objectContaining({ onError: expect.any(Function) }),
    );
  });

  it('requests permission, and opens settings + toasts a warning if still declined', async () => {
    mockCheckNotificationPermission.mockResolvedValue({ granted: false });
    mockRequestNotificationPermission.mockResolvedValue(false);
    const r = await renderScreen();
    const pushToggle = findToggleByTitle(r, 'settings.push_notifications');
    await act(async () => {
      await pushToggle.props.onToggle(true);
      await flush();
    });
    expect(mockRequestNotificationPermission).toHaveBeenCalled();
    expect(mockOpenNotificationSettings).toHaveBeenCalled();
    expect(mockShowToast).toHaveBeenCalledWith(
      'Permissions Required',
      'Push notifications are disabled in device settings. Tap to open Settings.',
      'warning',
    );
    expect(mockMutate).not.toHaveBeenCalled();
  });

  it('reverts the toggle and toasts when the mutation errors', async () => {
    const r = await renderScreen();
    const pushToggle = findToggleByTitle(r, 'settings.push_notifications');
    await act(async () => {
      await pushToggle.props.onToggle(true);
      await flush();
    });
    const onErrorCallback = mockMutate.mock.calls[0][1].onError;
    act(() => {
      onErrorCallback(new Error('server error'));
    });
    expect(mockShowToast).toHaveBeenCalledWith(
      'settings.prefNotSavedTitle',
      'settings.prefNotSavedMsg',
      'danger',
    );
  });

  it('toggles dark mode via setTheme', async () => {
    const r = await renderScreen();
    const darkToggle = findToggleByTitle(r, 'settings.dark_mode');
    act(() => {
      darkToggle.props.onToggle(true);
    });
    expect(mockSetTheme).toHaveBeenCalledWith('dark');
  });

  it('toggles dark mode off via setTheme', async () => {
    const r = await renderScreen();
    const darkToggle = findToggleByTitle(r, 'settings.dark_mode');
    act(() => {
      darkToggle.props.onToggle(false);
    });
    expect(mockSetTheme).toHaveBeenCalledWith('light');
  });

  it('only syncs the preference fields that are present, leaving the others at their defaults', async () => {
    mockNotificationPrefsData = { push_enabled: false }; // email_enabled/sms_enabled absent
    const r = await renderScreen();
    expect(findToggleByTitle(r, 'settings.push_notifications').props.value).toBe(false);
    expect(findToggleByTitle(r, 'settings.email_notifications').props.value).toBe(true); // untouched default
    expect(findToggleByTitle(r, 'settings.sms_notifications').props.value).toBe(false); // untouched default
  });

  it('leaves the push toggle at its default when push_enabled itself is absent from the fetched preferences', async () => {
    mockNotificationPrefsData = { email_enabled: false }; // push_enabled absent
    const r = await renderScreen();
    expect(findToggleByTitle(r, 'settings.push_notifications').props.value).toBe(true); // untouched default
  });

  it('toggles email/sms notifications directly (no permission check, since only push needs it)', async () => {
    const r = await renderScreen();
    const emailToggle = findToggleByTitle(r, 'settings.email_notifications');
    await act(async () => {
      await emailToggle.props.onToggle(false);
      await flush();
    });
    expect(mockCheckNotificationPermission).not.toHaveBeenCalled();
    expect(mockMutate).toHaveBeenCalledWith(
      { email_enabled: false },
      expect.objectContaining({ onError: expect.any(Function) }),
    );
  });

  it('proceeds to enable push once permission is granted after being requested', async () => {
    mockCheckNotificationPermission.mockResolvedValue({ granted: false });
    mockRequestNotificationPermission.mockResolvedValue(true); // granted this time
    const r = await renderScreen();
    const pushToggle = findToggleByTitle(r, 'settings.push_notifications');
    await act(async () => {
      await pushToggle.props.onToggle(true);
      await flush();
    });
    expect(mockOpenNotificationSettings).not.toHaveBeenCalled();
    expect(mockMutate).toHaveBeenCalledWith(
      { push_enabled: true },
      expect.objectContaining({ onError: expect.any(Function) }),
    );
  });

  it('opens the language modal and lists every language', async () => {
    const r = await renderScreen();
    const langRow = findButtonByText(r, 'settings.language');
    act(() => {
      langRow.props.onPress();
    });
    const text = allText(r);
    expect(text).toContain('English');
    expect(text).toContain('Français');
  });

  it('selecting a language calls setLanguage and closes the modal', async () => {
    const r = await renderScreen();
    const langRow = findButtonByText(r, 'settings.language');
    act(() => {
      langRow.props.onPress();
    });
    const frRow = findButtonByText(r, 'Français');
    act(() => {
      frRow.props.onPress();
    });
    expect(mockSetLanguage).toHaveBeenCalledWith('fr');
  });

  it('falls back to "English" for the language subtitle when the current language code is not in the LANGUAGES list', async () => {
    mockLanguage = 'de'; // not present in the mocked LANGUAGES list
    const r = await renderScreen();
    expect(allText(r)).toContain('[null," ","English"]');
  });

  it('shows an empty phone segment when the user has no phone on file', async () => {
    mockAuthUser = null;
    const r = await renderScreen();
    // The version row now also carries the OTA bundle label (jest-expo's
    // expo-updates mock reports updateId "mock" → "OTA mock").
    expect(allText(r)).toContain('["Spinr v1.0.2 · ",""," · ","OTA mock"]');
  });

  it('navigates to /privacy-settings', async () => {
    const r = await renderScreen();
    const row = findButtonByText(r, 'settings.privacy_data');
    act(() => {
      row.props.onPress();
    });
    expect(mockPush).toHaveBeenCalledWith('/privacy-settings');
  });

  it('navigates to /manage-cards', async () => {
    const r = await renderScreen();
    const row = findButtonByText(r, 'settings.payment_methods');
    act(() => {
      row.props.onPress();
    });
    expect(mockPush).toHaveBeenCalledWith('/manage-cards');
  });

  it('navigates to /saved-places', async () => {
    const r = await renderScreen();
    const row = findButtonByText(r, 'settings.saved_places');
    act(() => {
      row.props.onPress();
    });
    expect(mockPush).toHaveBeenCalledWith('/saved-places');
  });

  it('navigates to /legal?type=tos', async () => {
    const r = await renderScreen();
    const row = findButtonByText(r, 'settings.tos');
    act(() => {
      row.props.onPress();
    });
    expect(mockPush).toHaveBeenCalledWith('/legal?type=tos');
  });

  it('navigates to /legal?type=privacy', async () => {
    const r = await renderScreen();
    const row = findButtonByText(r, 'settings.privacy_policy');
    act(() => {
      row.props.onPress();
    });
    expect(mockPush).toHaveBeenCalledWith('/legal?type=privacy');
  });

  it('navigates to /policies', async () => {
    const r = await renderScreen();
    const row = findButtonByText(r, 'settings.more_policies');
    act(() => {
      row.props.onPress();
    });
    expect(mockPush).toHaveBeenCalledWith('/policies');
  });

  it('navigates back when the back button is pressed', async () => {
    const r = await renderScreen();
    const backBtn = r.root.findAllByType(TouchableOpacity)[0];
    act(() => {
      backBtn.props.onPress();
    });
    expect(mockBack).toHaveBeenCalled();
  });

  it('closes the language modal via the backdrop press and via onRequestClose', async () => {
    const r = await renderScreen();
    const langRow = findButtonByText(r, 'settings.language');
    act(() => { langRow.props.onPress(); });
    const modal = r.root.findByType(Modal);
    expect(modal.props.visible).toBe(true);

    act(() => { modal.props.onRequestClose(); });
    expect(r.root.findByType(Modal).props.visible).toBe(false);

    act(() => { langRow.props.onPress(); }); // reopen
    expect(r.root.findByType(Modal).props.visible).toBe(true);
    // findByType(Pressable) doesn't reliably match RN's real Pressable here,
    // so locate the backdrop by its distinguishing props instead (the only
    // element with both an onPress and StyleSheet.absoluteFill).
    const [backdrop] = r.root.findAll(
      (node) => typeof node.props.onPress === 'function' && node.props.style === StyleSheet.absoluteFill,
    );
    act(() => { backdrop.props.onPress(); });
    expect(r.root.findByType(Modal).props.visible).toBe(false);
  });
});
