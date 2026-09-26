/**
 * Unified driver toast store (migration 490, UX program W2.3).
 *
 * Code under test: driver-app/store/unifiedToastStore.ts
 */
import {
  isUnifiedToastEnabled,
  setUnifiedToastEnabled,
  useUnifiedToastStore,
} from '../unifiedToastStore';

beforeEach(() => {
  useUnifiedToastStore.setState({ current: null });
  setUnifiedToastEnabled(false);
});

describe('unifiedToastStore', () => {
  it('does not de-duplicate: the same toast twice gets a new id', () => {
    const { show } = useUnifiedToastStore.getState();
    show({ title: 'Ride Cancelled', message: 'The rider cancelled.', variant: 'warning', duration: 3500 });
    const firstId = useUnifiedToastStore.getState().current!.id;
    show({ title: 'Ride Cancelled', message: 'The rider cancelled.', variant: 'warning', duration: 3500 });

    expect(useUnifiedToastStore.getState().current!.id).not.toBe(firstId);
  });

  it('dismiss(id) leaves a newer toast in place; dismiss() clears', () => {
    const { show, dismiss } = useUnifiedToastStore.getState();
    show({ title: 'Old', variant: 'info', duration: 3500 });
    const oldId = useUnifiedToastStore.getState().current!.id;
    show({ title: 'New', variant: 'info', duration: 3500 });

    dismiss(oldId);
    expect(useUnifiedToastStore.getState().current?.title).toBe('New');

    dismiss();
    expect(useUnifiedToastStore.getState().current).toBeNull();
  });
});

describe('driver_unified_toast_enabled', () => {
  it('defaults off and follows setUnifiedToastEnabled', () => {
    jest.isolateModules(() => {
      // A freshly loaded module (cold start, before /drivers/config) is off.
      const fresh = require('../unifiedToastStore') as typeof import('../unifiedToastStore');
      expect(fresh.isUnifiedToastEnabled()).toBe(false);
    });
    setUnifiedToastEnabled(true);
    expect(isUnifiedToastEnabled()).toBe(true);
    setUnifiedToastEnabled(false);
    expect(isUnifiedToastEnabled()).toBe(false);
  });
});
