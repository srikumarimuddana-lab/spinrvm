/**
 * Ranked blocker #22 (audit finding, baseline row #16 / N6 group): the
 * back button on Report Safety Issue was an icon-only TouchableOpacity
 * with no accessibilityLabel — a screen-reader user landing on this
 * high-traffic safety screen had no way to know what the control did.
 */
import React from 'react';
import { render } from '@testing-library/react-native';
import ReportSafetyScreen from '../app/report-safety';

const mockBack = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ back: mockBack }),
}));
jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  SafeAreaView: ({ children }: any) => children,
}));

const COLORS = {
  primary: '#EF4444', primaryDark: '#D32F2F', background: '#FFF', surface: '#FFF',
  surfaceLight: '#F5F5F5', text: '#111', textDim: '#666', border: '#E5E7EB',
};
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));
jest.mock('../store/toastStore', () => ({ showToast: jest.fn() }));
jest.mock('@shared/api/client', () => ({
  __esModule: true,
  default: { post: jest.fn() },
  getApiErrorMessage: () => 'error',
}));

describe('ReportSafetyScreen — back button accessibility', () => {
  it('has a non-empty accessibilityLabel announcing itself as a button', () => {
    const screen = render(<ReportSafetyScreen />);
    const backButton = screen.getByLabelText('Go back');
    expect(backButton.props.accessibilityRole).toBe('button');
  });
});
