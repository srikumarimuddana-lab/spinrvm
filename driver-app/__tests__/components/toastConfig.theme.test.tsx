/**
 * toastConfig theme-color regression (ACTION_ITEMS.md UX5).
 *
 * Before this fix, VARIANT_CONFIG hardcoded bg hex values that matched
 * neither shared/theme/index.ts's current nor previous tokens, and never
 * adapted to dark mode. This asserts toast backgrounds now resolve from the
 * live theme in both light and dark mode.
 *
 * Code under test: driver-app/components/toastConfig.tsx
 */
import React from 'react';
import { StyleSheet } from 'react-native';
import { render } from '@testing-library/react-native';
import { toastConfig } from '../../components/toastConfig';
import { ThemeProvider } from '@shared/theme/ThemeContext';
import { lightColors, darkColors } from '@shared/theme/index';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));

// Matches the established structural-type pattern for reading a flattened
// style off a ReactTestInstance (see BrandSplash.test.tsx's styleValue).
function backgroundOf(node: { props: { style?: unknown } }): unknown {
  const flat = StyleSheet.flatten(node.props.style as never) as Record<string, unknown>;
  return flat.backgroundColor;
}

describe('toastConfig — theme-driven colors', () => {
  it('uses the live light-theme tokens, not stale hardcoded hex values', () => {
    const { getByRole: getSuccess } = render(<>{(toastConfig.success as any)({ text1: 'Ride Accepted' })}</>);
    expect(backgroundOf(getSuccess('alert'))).toBe(lightColors.success);

    const { getByRole: getError } = render(<>{(toastConfig.error as any)({ text1: 'Cannot Go Online' })}</>);
    expect(backgroundOf(getError('alert'))).toBe(lightColors.error);

    const { getByRole: getWarning } = render(<>{(toastConfig.warning as any)({ text1: 'Low Balance' })}</>);
    expect(backgroundOf(getWarning('alert'))).toBe(lightColors.warning);

    const { getByRole: getInfo } = render(<>{(toastConfig.info as any)({ text1: 'Heads up' })}</>);
    expect(backgroundOf(getInfo('alert'))).toBe(lightColors.info);
  });

  it('adapts to dark mode when rendered under a dark ThemeProvider', async () => {
    jest.spyOn(require('@react-native-async-storage/async-storage'), 'getItem').mockResolvedValueOnce('dark');

    const { getByRole, findByRole } = render(
      <ThemeProvider>{(toastConfig.error as any)({ text1: 'Cannot Go Online' })}</ThemeProvider>,
    );

    // ThemeProvider resolves the persisted 'dark' preference asynchronously
    // (AsyncStorage.getItem); wait for the dark background to land rather
    // than asserting the pre-hydration light-mode default.
    await findByRole('alert');
    expect(backgroundOf(getByRole('alert'))).toBe(darkColors.error);
  });
});
