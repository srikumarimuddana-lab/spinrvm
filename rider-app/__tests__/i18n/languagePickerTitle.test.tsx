/**
 * app/settings.tsx language picker title (W7.1).
 *
 * The picker sheet's title was the hardcoded English string "Select Language",
 * so French riders saw English in the one sheet that exists to change the
 * language. It now uses the `settings.selectLanguage` key. This renders the
 * real screen against the REAL rider-app i18n module (settingsScreen.test.tsx
 * stubs i18n, so it cannot see this) and checks the title in both languages.
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { TouchableOpacity, Text, Modal } from 'react-native';

jest.mock('@react-native-async-storage/async-storage', () => ({
  getItem: jest.fn(() => Promise.resolve(null)),
  setItem: jest.fn(() => Promise.resolve()),
  removeItem: jest.fn(() => Promise.resolve()),
}));
jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  SafeAreaView: ({ children }: any) => children,
}));
jest.mock('expo-router', () => ({
  useRouter: () => ({ back: jest.fn(), push: jest.fn() }),
}));
jest.mock('@shared/theme/ThemeContext', () => ({
  useTheme: () => ({
    colors: {
      primary: '#EF4444', surface: '#FFF', surfaceLight: '#F5F5F5', text: '#111',
      textDim: '#666', textSecondary: '#333', border: '#E5E7EB',
    },
    isDark: false,
    setTheme: jest.fn(),
  }),
}));
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  getApiErrorMessage: (_err: any, fallback: string) => fallback,
}));
jest.mock('../../store/toastStore', () => ({ showToast: jest.fn() }));
jest.mock('@shared/store/authStore', () => ({
  useAuthStore: () => ({ user: { phone: '+15551234567' } }),
}));
jest.mock('@shared/hooks/queries', () => ({
  useNotificationPreferences: () => ({ data: undefined }),
  useUpdateNotificationPreferences: () => ({ mutate: jest.fn() }),
}));
jest.mock('@shared/services/firebase', () => ({
  checkNotificationPermission: jest.fn(() => Promise.resolve({ granted: true })),
  requestNotificationPermission: jest.fn(() => Promise.resolve(true)),
  openNotificationSettings: jest.fn(),
}));

import SettingsScreen from '../../app/settings';
import { useLanguageStore, type Language } from '../../i18n';

function textsIn(node: TestRenderer.ReactTestInstance): string[] {
  return node.findAllByType(Text).map((t) => {
    const c = t.props.children;
    return Array.isArray(c) ? c.join('') : String(c);
  });
}

async function openPickerIn(language: Language, languageRowLabel: string) {
  useLanguageStore.setState({ language });
  let r!: TestRenderer.ReactTestRenderer;
  await act(async () => {
    r = TestRenderer.create(<SettingsScreen />);
    await Promise.resolve();
  });
  const row = r.root
    .findAllByType(TouchableOpacity)
    .find((n) => textsIn(n).includes(languageRowLabel))!;
  act(() => { row.props.onPress(); });
  const modal = r.root.findByType(Modal);
  expect(modal.props.visible).toBe(true);
  return { r, modalTexts: textsIn(modal) };
}

afterEach(() => {
  useLanguageStore.setState({ language: 'en' });
});

describe('rider-app language picker title', () => {
  it('is French when the app language is French', async () => {
    const { r, modalTexts } = await openPickerIn('fr', 'Langue');
    expect(modalTexts).toContain('Choisir la langue');
    expect(modalTexts).not.toContain('Select Language');
    act(() => r.unmount());
  });

  it('stays "Select Language" in English', async () => {
    const { r, modalTexts } = await openPickerIn('en', 'Language');
    expect(modalTexts).toContain('Select Language');
    act(() => r.unmount());
  });
});
