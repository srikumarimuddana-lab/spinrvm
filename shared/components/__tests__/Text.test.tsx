/**
 * Text — themed Text wrapper (ACTION_ITEMS.md UX1).
 *
 * Code under test: shared/components/Text.tsx
 */
import React from 'react';
import { StyleSheet } from 'react-native';
import { render } from '@testing-library/react-native';
import { Text } from '../Text';

describe('Text', () => {
  it('renders its children', () => {
    const { getByText } = render(<Text>Hello</Text>);
    expect(getByText('Hello')).toBeTruthy();
  });

  it('defaults to Regular when no style is given', () => {
    const { getByText } = render(<Text>Plain</Text>);
    const flattened = StyleSheet.flatten(getByText('Plain').props.style);
    expect(flattened.fontFamily).toBe('PlusJakartaSans_400Regular');
  });

  it.each([
    ['400', 'PlusJakartaSans_400Regular'],
    ['normal', 'PlusJakartaSans_400Regular'],
    ['500', 'PlusJakartaSans_500Medium'],
    ['600', 'PlusJakartaSans_600SemiBold'],
    ['700', 'PlusJakartaSans_700Bold'],
    ['bold', 'PlusJakartaSans_700Bold'],
  ] as const)('maps fontWeight %s to %s', (fontWeight, expectedFamily) => {
    const { getByText } = render(<Text style={{ fontWeight }}>Weighted</Text>);
    const flattened = StyleSheet.flatten(getByText('Weighted').props.style);
    expect(flattened.fontFamily).toBe(expectedFamily);
    // The caller's fontWeight itself is left untouched.
    expect(flattened.fontWeight).toBe(fontWeight);
  });

  it.each([
    ['100', 'PlusJakartaSans_400Regular'],
    ['300', 'PlusJakartaSans_400Regular'],
    ['800', 'PlusJakartaSans_700Bold'],
    ['900', 'PlusJakartaSans_700Bold'],
  ] as const)('snaps unloaded weight %s to the nearest loaded family (%s)', (fontWeight, expectedFamily) => {
    const { getByText } = render(<Text style={{ fontWeight }}>Weighted</Text>);
    const flattened = StyleSheet.flatten(getByText('Weighted').props.style);
    expect(flattened.fontFamily).toBe(expectedFamily);
  });

  it('lets an explicit fontFamily in style override the default', () => {
    const { getByText } = render(
      <Text style={{ fontWeight: '700', fontFamily: 'CustomIconFont' }}>Override</Text>,
    );
    const flattened = StyleSheet.flatten(getByText('Override').props.style);
    expect(flattened.fontFamily).toBe('CustomIconFont');
  });

  it('accepts a style array, applying its default underneath', () => {
    const { getByText } = render(
      <Text style={[{ color: 'red' }, { fontWeight: '600' }]}>Arr</Text>,
    );
    const flattened = StyleSheet.flatten(getByText('Arr').props.style);
    expect(flattened.fontFamily).toBe('PlusJakartaSans_600SemiBold');
    expect(flattened.color).toBe('red');
  });

  it('passes through other Text props unchanged', () => {
    const { getByText } = render(<Text numberOfLines={2}>Truncated</Text>);
    expect(getByText('Truncated').props.numberOfLines).toBe(2);
  });
});
