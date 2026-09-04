/**
 * Input — shared labeled-text-field primitive.
 *
 * Code under test: shared/components/Input.tsx
 */
import React from 'react';
import { fireEvent, render } from '@testing-library/react-native';
import { Input } from '../Input';

describe('Input', () => {
  it('renders a label above the field when provided', () => {
    const { getByText, getByPlaceholderText } = render(
      <Input label="Full Name" placeholder="e.g. Sarah Johnson" />,
    );

    expect(getByText('Full Name')).toBeTruthy();
    expect(getByPlaceholderText('e.g. Sarah Johnson')).toBeTruthy();
  });

  it('renders without a label when none is given', () => {
    const { queryByText, getByPlaceholderText } = render(<Input placeholder="No label" />);

    expect(getByPlaceholderText('No label')).toBeTruthy();
    // No stray label text node was rendered.
    expect(queryByText('No label')).toBeNull();
  });

  it('calls onChangeText as the user types', () => {
    const onChangeText = jest.fn();
    const { getByPlaceholderText } = render(
      <Input placeholder="Phone Number" onChangeText={onChangeText} />,
    );

    fireEvent.changeText(getByPlaceholderText('Phone Number'), '(306) 555-1234');

    expect(onChangeText).toHaveBeenCalledWith('(306) 555-1234');
  });

  it('shows the error message and reddens the border when error is set', () => {
    const { getByText, getByPlaceholderText } = render(
      <Input placeholder="Email" error="Enter a valid email" />,
    );

    expect(getByText('Enter a valid email')).toBeTruthy();
    const flatStyle = Object.assign({}, ...[].concat(getByPlaceholderText('Email').props.style));
    // colors.danger in the default (light) palette — see shared/theme/index.ts.
    expect(flatStyle.borderColor).toBe('#DC2626');
  });

  it('respects editable=false (disabled state)', () => {
    const { getByPlaceholderText } = render(
      <Input placeholder="Locked" editable={false} />,
    );

    expect(getByPlaceholderText('Locked').props.editable).toBe(false);
  });

  it('applies labelStyle on top of the default label styling', () => {
    const { getByText } = render(
      <Input label="Full Name" labelStyle={{ fontFamily: 'PlusJakartaSans_600SemiBold' }} />,
    );

    const flatStyle = Object.assign({}, ...[].concat(getByText('Full Name').props.style));
    expect(flatStyle.fontFamily).toBe('PlusJakartaSans_600SemiBold');
    // Default label styling (e.g. fontSize) survives alongside the override.
    expect(flatStyle.fontSize).toBe(13);
  });
});
