/**
 * app/subscription/cancel.tsx — Stripe Checkout cancel landing screen
 * (the deep-link target for `spinr-driver://subscription/cancel`; without
 * this file the router would 404). Pins: redirects to
 * /driver/subscription after a 600ms delay, exactly once even if the
 * effect were to re-run (StrictMode/re-mount guard).
 */
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';

import SubscriptionCancelScreen from '../../app/subscription/cancel';

const mockReplace = jest.fn();
jest.mock('expo-router', () => ({
  useRouter: () => ({ replace: mockReplace }),
}));

const COLORS = { primary: '#EF4444', surface: '#FFF', textDim: '#666' };
jest.mock('@shared/theme/ThemeContext', () => ({ useTheme: () => ({ colors: COLORS, isDark: false }) }));

beforeEach(() => {
  jest.clearAllMocks();
  jest.useFakeTimers();
});

afterEach(() => {
  jest.useRealTimers();
});

describe('SubscriptionCancelScreen', () => {
  it('redirects to /driver/subscription after the delay', async () => {
    let renderer!: TestRenderer.ReactTestRenderer;
    act(() => {
      renderer = TestRenderer.create(<SubscriptionCancelScreen />);
    });
    expect(mockReplace).not.toHaveBeenCalled();
    act(() => {
      jest.advanceTimersByTime(600);
    });
    expect(mockReplace).toHaveBeenCalledWith('/driver/subscription');
    act(() => {
      renderer.unmount();
    });
  });

  it('never redirects twice even across multiple timer advances', async () => {
    let renderer!: TestRenderer.ReactTestRenderer;
    act(() => {
      renderer = TestRenderer.create(<SubscriptionCancelScreen />);
    });
    act(() => {
      jest.advanceTimersByTime(600);
      jest.advanceTimersByTime(600);
    });
    expect(mockReplace).toHaveBeenCalledTimes(1);
    act(() => {
      renderer.unmount();
    });
  });
});
