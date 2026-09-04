/**
 * Card — shared surface-container primitive.
 *
 * Code under test: shared/components/Card.tsx
 */
import React from 'react';
import { Text } from 'react-native';
import { render } from '@testing-library/react-native';
import { Card } from '../Card';

describe('Card', () => {
  it('renders its children', () => {
    const { getByText } = render(
      <Card>
        <Text>Card content</Text>
      </Card>,
    );

    expect(getByText('Card content')).toBeTruthy();
  });

  it('applies the requested padding size without throwing', () => {
    const { getByText, rerender } = render(
      <Card padding="sm">
        <Text>sm</Text>
      </Card>,
    );
    expect(getByText('sm')).toBeTruthy();

    rerender(
      <Card padding="lg">
        <Text>lg</Text>
      </Card>,
    );
    expect(getByText('lg')).toBeTruthy();
  });

  it('omits the border when bordered=false', () => {
    const { getByTestId } = render(
      <Card bordered={false} testID="card">
        <Text>content</Text>
      </Card>,
    );

    const flatStyle = Object.assign({}, ...[].concat(getByTestId('card').props.style));
    expect(flatStyle.borderWidth).toBeUndefined();
  });

  it('includes the border by default', () => {
    const { getByTestId } = render(
      <Card testID="card">
        <Text>content</Text>
      </Card>,
    );

    const flatStyle = Object.assign({}, ...[].concat(getByTestId('card').props.style));
    expect(flatStyle.borderWidth).toBe(1);
  });
});
