/**
 * useBottomSheetGuard — regression for the "Home screen loses the bottom
 * sheet" bug. @gorhom/bottom-sheet resolves percentage snap points against
 * its container height; a transient 0-height layout pass (cold start, tab
 * detach/re-attach) can resolve the sheet to index -1 (closed) and it then
 * stays off-screen forever because pan-down-to-close is disabled and nothing
 * re-opens it.
 *
 * The guard must: snap on first real-height layout, treat onChange(-1) as
 * the layout race and snap back to the last user-chosen index, and re-assert
 * on layout/focus while the sheet reports closed — without touching the
 * sheet when it is healthy.
 */

import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { useBottomSheetGuard } from '../useBottomSheetGuard';

let focusCallback: undefined | (() => void);
jest.mock('expo-router/react-navigation', () => ({
  useFocusEffect: (cb: () => void) => {
    focusCallback = cb;
  },
}));

// Run rAF-deferred snaps synchronously so assertions see them immediately.
beforeAll(() => {
  jest.spyOn(globalThis, 'requestAnimationFrame').mockImplementation((cb: FrameRequestCallback) => {
    cb(0);
    return 0;
  });
});
afterAll(() => (globalThis.requestAnimationFrame as jest.Mock).mockRestore());

const layoutEvent = (height: number) => ({ nativeEvent: { layout: { height } } });

function setup() {
  const snapToIndex = jest.fn();
  const sheetRef = { current: { snapToIndex } as any };
  let handlers: ReturnType<typeof useBottomSheetGuard>;
  function Harness() {
    handlers = useBottomSheetGuard(sheetRef);
    return null;
  }
  let renderer!: TestRenderer.ReactTestRenderer;
  act(() => {
    renderer = TestRenderer.create(<Harness />);
  });
  return { snapToIndex, handlers: handlers!, renderer };
}

beforeEach(() => {
  focusCallback = undefined;
});

describe('useBottomSheetGuard', () => {
  it('snaps to the initial index on the first real-height layout only', () => {
    const { snapToIndex, handlers } = setup();

    handlers.handleContainerLayout(layoutEvent(0));
    expect(snapToIndex).not.toHaveBeenCalled();

    handlers.handleContainerLayout(layoutEvent(800));
    expect(snapToIndex).toHaveBeenCalledTimes(1);
    expect(snapToIndex).toHaveBeenCalledWith(0);

    // Healthy sheet + repeat layouts → no re-snap churn.
    handlers.handleSheetChange(0);
    handlers.handleContainerLayout(layoutEvent(800));
    expect(snapToIndex).toHaveBeenCalledTimes(1);
  });

  it('recovers immediately when onChange reports -1, restoring the last user index', () => {
    const { snapToIndex, handlers } = setup();
    handlers.handleContainerLayout(layoutEvent(800));
    snapToIndex.mockClear();

    // User expanded to the 45% detent (index 1), then the layout race closes it.
    handlers.handleSheetChange(1);
    handlers.handleSheetChange(-1);
    expect(snapToIndex).toHaveBeenCalledWith(1);
  });

  it('re-asserts on a real-height layout while the sheet reports closed', () => {
    const { snapToIndex, handlers } = setup();
    handlers.handleContainerLayout(layoutEvent(800));
    handlers.handleSheetChange(0);
    snapToIndex.mockClear();

    handlers.handleSheetChange(-1); // race closed it (snap attempt may be dropped)
    snapToIndex.mockClear();

    handlers.handleContainerLayout(layoutEvent(800));
    expect(snapToIndex).toHaveBeenCalledWith(0);
  });

  it('re-asserts on tab focus while closed, but not while healthy', () => {
    const { snapToIndex, handlers } = setup();
    handlers.handleContainerLayout(layoutEvent(800));
    handlers.handleSheetChange(0);
    snapToIndex.mockClear();

    focusCallback?.();
    expect(snapToIndex).not.toHaveBeenCalled();

    handlers.handleSheetChange(-1);
    snapToIndex.mockClear();
    focusCallback?.();
    expect(snapToIndex).toHaveBeenCalledWith(0);
  });

  it('does nothing on focus before the container has ever measured', () => {
    const { snapToIndex } = setup();
    focusCallback?.();
    expect(snapToIndex).not.toHaveBeenCalled();
  });
});
