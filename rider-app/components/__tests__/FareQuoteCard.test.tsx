/**
 * AI17/F4: a priced-but-unavailable option (no drivers online right now)
 * must be shown with its real price, dimmed, and never tappable/bookable —
 * matching rider-app/app/ride-options.tsx's existing disabled-card pattern.
 *
 * Code under test: rider-app/components/FareQuoteCard.tsx
 */
import React from 'react';
import { render, fireEvent } from '@testing-library/react-native';
import FareQuoteCard from '../FareQuoteCard';
import type { AiAction } from '@shared/types/ai';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));

type FareQuoteAction = Extract<AiAction, { type: 'fare_quote' }>;

const QUOTE: FareQuoteAction = {
  type: 'fare_quote',
  distance_km: 6.4,
  duration_minutes: 17,
  currency: 'CAD',
  recommended_vehicle_type_id: 'vt-1',
  quotes: [
    {
      vehicle_type_id: 'vt-1',
      vehicle_type: 'Economy',
      capacity: 4,
      eta_minutes: 4,
      closest_driver_km: 1.8,
      total: '18.48',
      final_total: '18.48',
      available: true,
    },
    {
      vehicle_type_id: 'vt-2',
      vehicle_type: 'XL',
      capacity: 6,
      total: '25.00',
      final_total: '25.00',
      available: false,
    },
  ],
};

describe('FareQuoteCard — availability parity (AI17/F4)', () => {
  it('shows a real price for an unavailable option but disables booking it', () => {
    const onSelect = jest.fn();
    const { getByText, getByLabelText } = render(<FareQuoteCard quote={QUOTE} onSelect={onSelect} />);

    // Price is still shown, not hidden or replaced with a placeholder.
    expect(getByText('$25.00')).toBeTruthy();
    expect(getByText('No drivers nearby')).toBeTruthy();

    const unavailableButton = getByLabelText(/XL.*\$25\.00.*not bookable/i);
    expect(unavailableButton.props.accessibilityState?.disabled).toBe(true);

    fireEvent.press(unavailableButton);
    expect(onSelect).not.toHaveBeenCalled();
  });

  it('still lets an available option be tapped and booked', () => {
    const onSelect = jest.fn();
    const { getByLabelText } = render(<FareQuoteCard quote={QUOTE} onSelect={onSelect} />);

    const availableButton = getByLabelText('Book Economy for $18.48');
    expect(availableButton.props.accessibilityState?.disabled).toBeFalsy();

    fireEvent.press(availableButton);
    expect(onSelect).toHaveBeenCalledWith(QUOTE.quotes[0]);
  });

  it('treats an option with no `available` field as available (pre-F4 quotes)', () => {
    const onSelect = jest.fn();
    const legacyQuote: FareQuoteAction = {
      ...QUOTE,
      quotes: [{ ...QUOTE.quotes[0], available: undefined }],
    };
    const { getByLabelText } = render(<FareQuoteCard quote={legacyQuote} onSelect={onSelect} />);

    const button = getByLabelText('Book Economy for $18.48');
    expect(button.props.accessibilityState?.disabled).toBeFalsy();
    fireEvent.press(button);
    expect(onSelect).toHaveBeenCalledWith(legacyQuote.quotes[0]);
  });
});
