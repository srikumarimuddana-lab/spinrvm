/**
 * Unified driver toast host + store (migration 490, UX program W2.3).
 *
 * The host is mounted in app/_layout.tsx and renders only what
 * hooks/useToast.ts routes to the store while driver_unified_toast_enabled is
 * on. It keeps the driver app's 60 px top offset and has no de-duplication.
 *
 * Code under test: driver-app/components/UnifiedToast.tsx (store behaviour:
 * store/__tests__/unifiedToastStore.test.ts)
 */
import React from 'react';
import { AccessibilityInfo, StyleSheet } from 'react-native';
import { render, act } from '@testing-library/react-native';
import UnifiedToast, { UNIFIED_TOAST_TOP_OFFSET } from '../../components/UnifiedToast';
import { useUnifiedToastStore } from '../../store/unifiedToastStore';
import { lightColors } from '@shared/theme/index';
import { MAX_FONT_SCALE } from '@shared/utils/responsive';

jest.mock('@expo/vector-icons', () => ({ Ionicons: () => null }));

function flatStyle(node: { props: { style?: unknown } }): Record<string, unknown> {
  return StyleSheet.flatten(node.props.style as never) as Record<string, unknown>;
}

const show = (title: string, message: string | undefined, variant: 'info' | 'success' | 'warning' | 'danger') =>
  act(() => {
    useUnifiedToastStore.getState().show({ title, message, variant, duration: 3500 });
  });

beforeEach(() => {
  useUnifiedToastStore.setState({ current: null });
  jest.restoreAllMocks();
  jest.spyOn(AccessibilityInfo, 'announceForAccessibility').mockImplementation(() => {});
  // jest-expo already mocks AccessibilityInfo, so restoreAllMocks keeps the
  // same jest.fn across tests; clear its calls explicitly.
  (AccessibilityInfo.announceForAccessibility as jest.Mock).mockClear();
});

describe('UnifiedToast host', () => {
  it('renders nothing while the store is empty (flag off never fills it)', () => {
    const { toJSON } = render(<UnifiedToast />);
    expect(toJSON()).toBeNull();
  });

  it('renders the message as an assertive alert with a combined label for danger', () => {
    const { getByRole, getByText } = render(<UnifiedToast />);
    show('Cannot Go Online', 'Your documents need review.', 'danger');

    const alert = getByRole('alert');
    expect(alert.props.accessibilityLabel).toBe('Cannot Go Online. Your documents need review.');
    expect(alert.props.accessibilityLiveRegion).toBe('assertive');
    expect(getByText('Cannot Go Online')).toBeTruthy();
    expect(getByText('Your documents need review.')).toBeTruthy();
    expect(AccessibilityInfo.announceForAccessibility).toHaveBeenCalledWith(
      'Cannot Go Online. Your documents need review.',
    );
  });

  it('announces non-danger toasts politely and labels a title-only toast with the title', () => {
    const { getByRole } = render(<UnifiedToast />);
    show('Ride Accepted', undefined, 'success');

    const alert = getByRole('alert');
    expect(alert.props.accessibilityLabel).toBe('Ride Accepted');
    expect(alert.props.accessibilityLiveRegion).toBe('polite');
  });

  it('keeps the driver app 60 px top offset (not the safe-area inset)', () => {
    const { getByRole } = render(<UnifiedToast />);
    show('Heads up', undefined, 'info');

    expect(UNIFIED_TOAST_TOP_OFFSET).toBe(60);
    expect(flatStyle(getByRole('alert')).top).toBe(60);
  });

  it('uses the danger token for danger and the live theme for the others', () => {
    const { getByRole } = render(<UnifiedToast />);
    show('Payment Failed', undefined, 'danger');
    expect(flatStyle(getByRole('alert')).backgroundColor).toBe(lightColors.danger);

    show('Saved', undefined, 'success');
    expect(flatStyle(getByRole('alert')).backgroundColor).toBe(lightColors.success);

    show('Low Balance', undefined, 'warning');
    expect(flatStyle(getByRole('alert')).backgroundColor).toBe(lightColors.warning);
  });

  it('lets text scale with the OS setting up to MAX_FONT_SCALE', () => {
    const { getByText } = render(<UnifiedToast />);
    show('Title', 'Body', 'info');

    expect(getByText('Title').props.maxFontSizeMultiplier).toBe(MAX_FONT_SCALE);
    expect(getByText('Body').props.maxFontSizeMultiplier).toBe(MAX_FONT_SCALE);
  });

  it('does not de-duplicate: the same toast twice is two toasts and two announcements', () => {
    render(<UnifiedToast />);
    show('Ride Cancelled', 'The rider cancelled.', 'warning');
    const firstId = useUnifiedToastStore.getState().current!.id;
    show('Ride Cancelled', 'The rider cancelled.', 'warning');

    expect(useUnifiedToastStore.getState().current!.id).not.toBe(firstId);
    expect(AccessibilityInfo.announceForAccessibility).toHaveBeenCalledTimes(2);
  });

  it('clears itself after the toast duration', () => {
    jest.useFakeTimers();
    try {
      render(<UnifiedToast />);
      show('Saved', undefined, 'success');
      expect(useUnifiedToastStore.getState().current).not.toBeNull();

      act(() => {
        jest.advanceTimersByTime(3500 + 1000);
      });
      expect(useUnifiedToastStore.getState().current).toBeNull();
    } finally {
      jest.useRealTimers();
    }
  });
});
