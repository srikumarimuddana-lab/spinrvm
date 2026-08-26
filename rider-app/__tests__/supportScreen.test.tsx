/**
 * app/support.tsx — thin wrapper around shared/components/SupportScreen.
 * Pins: role='rider' always; a ?topic=payment_failed deep link (used by the
 * stuck end-of-ride payment screen) pre-fills the Contact tab with a
 * payment-issue ticket, including the ride id when present; any other/no
 * topic defaults to the FAQ tab with no pre-fill.
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';

import RiderSupportScreen from '../app/support';

let mockParams: Record<string, string | undefined> = {};
jest.mock('expo-router', () => ({
  useLocalSearchParams: () => mockParams,
}));

const mockSupportScreen = jest.fn((_props: any) => null);
jest.mock('@shared/components/SupportScreen', () => (props: any) => {
  mockSupportScreen(props);
  return null;
});

beforeEach(() => {
  jest.clearAllMocks();
  mockParams = {};
});

describe('RiderSupportScreen', () => {
  it('defaults to the FAQ tab with no pre-fill when there is no topic param', () => {
    act(() => {
      TestRenderer.create(<RiderSupportScreen />);
    });
    expect(mockSupportScreen).toHaveBeenCalledWith(
      expect.objectContaining({
        role: 'rider',
        initialTab: 'faq',
        initialCategory: 'general',
        initialIssue: '',
      }),
    );
  });

  it('pre-fills a payment-issue contact ticket with the ride id when topic=payment_failed', () => {
    mockParams = { rideId: 'ride-42', topic: 'payment_failed' };
    act(() => {
      TestRenderer.create(<RiderSupportScreen />);
    });
    expect(mockSupportScreen).toHaveBeenCalledWith(
      expect.objectContaining({
        role: 'rider',
        initialTab: 'contact',
        initialCategory: 'payment_failed',
        initialIssue: expect.stringContaining('ride ride-42'),
      }),
    );
  });

  it('omits the ride reference when topic=payment_failed but no rideId is given', () => {
    mockParams = { topic: 'payment_failed' };
    act(() => {
      TestRenderer.create(<RiderSupportScreen />);
    });
    const call = mockSupportScreen.mock.calls[0][0];
    expect(call.initialIssue).not.toContain('(ride');
    expect(call.initialIssue).toContain("couldn't complete payment");
  });
});
