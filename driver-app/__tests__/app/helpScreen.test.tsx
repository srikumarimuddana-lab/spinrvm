/**
 * app/driver/help.tsx — thin wrapper around shared/components/SupportScreen
 * for the driver role. Pins: role='driver' is always passed, with no other
 * props (unlike rider-app's support.tsx, which pre-fills a payment-issue
 * ticket from query params -- this screen has no such deep-link entry).
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';

import HelpScreen from '../../app/driver/help';

const mockSupportScreen = jest.fn((_props: any) => null);
jest.mock('@shared/components/SupportScreen', () => (props: any) => {
  mockSupportScreen(props);
  return null;
});

beforeEach(() => {
  jest.clearAllMocks();
});

describe('HelpScreen (driver-app)', () => {
  it('renders SupportScreen with role="driver" and no other props', () => {
    act(() => {
      TestRenderer.create(<HelpScreen />);
    });
    expect(mockSupportScreen).toHaveBeenCalledWith({ role: 'driver' });
  });
});
