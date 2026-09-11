/**
 * Button — shared CTA primitive (design-audit follow-up).
 *
 * Code under test: shared/components/Button.tsx
 */
import React from 'react';
import { ActivityIndicator } from 'react-native';
import { fireEvent, render } from '@testing-library/react-native';
import { Button } from '../Button';

// Real Ionicons (@expo/vector-icons) does an async font-load `setState` on
// mount that logs an "not wrapped in act(...)" warning under
// react-test-renderer — harmless, but noisy, and unrelated to anything this
// suite verifies. Mocked the same way driver-app's ActivityView.test.tsx
// already mocks it, so `findAllByType(Ionicons)` below still matches the
// (now-mocked) component instance Button.tsx renders.
jest.mock('@expo/vector-icons', () => ({
  Ionicons: () => null,
}));

describe('Button', () => {
  it('renders its label for each variant', () => {
    const { getByText, rerender } = render(<Button variant="primary">Primary</Button>);
    expect(getByText('Primary')).toBeTruthy();

    rerender(<Button variant="secondary">Secondary</Button>);
    expect(getByText('Secondary')).toBeTruthy();

    rerender(<Button variant="danger">Danger</Button>);
    expect(getByText('Danger')).toBeTruthy();
  });

  it('renders at each size without throwing', () => {
    const { getByText, rerender } = render(<Button size="sm">Small</Button>);
    expect(getByText('Small')).toBeTruthy();

    rerender(<Button size="md">Medium</Button>);
    expect(getByText('Medium')).toBeTruthy();

    rerender(<Button size="lg">Large</Button>);
    expect(getByText('Large')).toBeTruthy();
  });

  it('calls onPress when tapped', () => {
    const onPress = jest.fn();
    const { getByRole } = render(<Button onPress={onPress}>Tap me</Button>);

    fireEvent.press(getByRole('button'));

    expect(onPress).toHaveBeenCalledTimes(1);
  });

  it('does not call onPress when disabled', () => {
    const onPress = jest.fn();
    const { getByRole } = render(
      <Button onPress={onPress} disabled>
        Disabled
      </Button>,
    );

    fireEvent.press(getByRole('button'));

    expect(onPress).not.toHaveBeenCalled();
    expect(getByRole('button').props.accessibilityState.disabled).toBe(true);
  });

  it('shows a spinner and blocks onPress while loading, hiding the label', () => {
    const onPress = jest.fn();
    const { getByRole, queryByText, UNSAFE_getByType } = render(
      <Button onPress={onPress} loading>
        Submit Report
      </Button>,
    );

    fireEvent.press(getByRole('button'));

    expect(onPress).not.toHaveBeenCalled();
    expect(queryByText('Submit Report')).toBeNull();
    expect(UNSAFE_getByType(ActivityIndicator)).toBeTruthy();
    expect(getByRole('button').props.accessibilityState.busy).toBe(true);
  });

  // UX3 (ACTION_ITEMS.md, 2026-09-10): `icon` added for driver-app's
  // ActivityView "Try Again" retry pill / documents.tsx "Re-upload
  // Document" — the first two real consumers that needed a leading glyph.
  it('renders a leading icon before the label when `icon` is provided', () => {
    const { Ionicons } = require('@expo/vector-icons');
    const { getByText, UNSAFE_root } = render(<Button icon="refresh">Try Again</Button>);

    expect(getByText('Try Again')).toBeTruthy();
    expect(UNSAFE_root.findAllByType(Ionicons)).toHaveLength(1);
  });

  it('omits the icon for every pre-existing consumer that does not pass one', () => {
    const { Ionicons } = require('@expo/vector-icons');
    const { getByText, UNSAFE_root } = render(<Button>Plain Label</Button>);

    expect(getByText('Plain Label')).toBeTruthy();
    expect(UNSAFE_root.findAllByType(Ionicons)).toHaveLength(0);
  });

  it('hides the icon (along with the label) while loading, same as an icon-less button', () => {
    const { queryByText, UNSAFE_root } = render(
      <Button icon="refresh" loading>
        Try Again
      </Button>,
    );
    const { Ionicons } = require('@expo/vector-icons');

    expect(queryByText('Try Again')).toBeNull();
    expect(UNSAFE_root.findAllByType(Ionicons)).toHaveLength(0);
    expect(UNSAFE_root.findAllByType(ActivityIndicator)).toHaveLength(1);
  });
});
