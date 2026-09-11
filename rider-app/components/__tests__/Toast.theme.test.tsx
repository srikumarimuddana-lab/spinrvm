/**
 * Toast theme-color regression (ACTION_ITEMS.md UX6, follow-up to UX5).
 *
 * Before this fix, VARIANT_CONFIG hardcoded bg hex values that matched
 * neither shared/theme/index.ts's current nor previous tokens, and never
 * adapted to dark mode — the same bug UX5 fixed in
 * driver-app/components/toastConfig.tsx. This asserts toast backgrounds now
 * resolve from the live theme in both light and dark mode.
 *
 * Code under test: rider-app/components/Toast.tsx
 */
import React from 'react';
import { StyleSheet } from 'react-native';
import { render, act } from '@testing-library/react-native';
import Toast from '../Toast';
import { useToastStore, showToast } from '../../store/toastStore';
import { ThemeProvider } from '@shared/theme/ThemeContext';
import { lightColors, darkColors } from '@shared/theme/index';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));
jest.mock('react-native-safe-area-context', () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 }),
}));

// Matches the established structural-type pattern for reading a flattened
// style off a ReactTestInstance — same helper as
// driver-app/__tests__/components/toastConfig.theme.test.tsx's backgroundOf.
function backgroundOf(node: { props: { style?: unknown } }): unknown {
  const flat = StyleSheet.flatten(node.props.style as never) as Record<string, unknown>;
  return flat.backgroundColor;
}

beforeEach(() => {
  useToastStore.setState({ current: null });
});

describe('Toast — theme-driven colors', () => {
  it('uses the live light-theme tokens, not stale hardcoded hex values', () => {
    const { getByRole } = render(<Toast />);

    act(() => {
      showToast('Ride Accepted', undefined, 'success');
    });
    expect(backgroundOf(getByRole('alert'))).toBe(lightColors.success);

    act(() => {
      showToast('Payment Failed', 'Your card was declined.', 'danger');
    });
    expect(backgroundOf(getByRole('alert'))).toBe(lightColors.danger);

    act(() => {
      showToast('Low Balance', undefined, 'warning');
    });
    expect(backgroundOf(getByRole('alert'))).toBe(lightColors.warning);

    act(() => {
      showToast('Heads up', undefined, 'info');
    });
    expect(backgroundOf(getByRole('alert'))).toBe(lightColors.info);
  });

  it('adapts to dark mode when rendered under a dark ThemeProvider', async () => {
    jest.spyOn(require('@react-native-async-storage/async-storage'), 'getItem').mockResolvedValueOnce('dark');

    const { getByRole, findByRole } = render(
      <ThemeProvider>
        <Toast />
      </ThemeProvider>,
    );

    act(() => {
      showToast('Payment Failed', 'Your card was declined.', 'danger');
    });

    // ThemeProvider resolves the persisted 'dark' preference asynchronously
    // (AsyncStorage.getItem); wait for the dark background to land rather
    // than asserting the pre-hydration light-mode default.
    await findByRole('alert');
    expect(backgroundOf(getByRole('alert'))).toBe(darkColors.danger);
  });
});
