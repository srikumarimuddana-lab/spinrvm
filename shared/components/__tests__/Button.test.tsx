/**
 * Button — shared CTA primitive (design-audit follow-up).
 *
 * Code under test: shared/components/Button.tsx
 */
import React from 'react';
import { ActivityIndicator } from 'react-native';
import { fireEvent, render } from '@testing-library/react-native';
import { Button } from '../Button';

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
});
