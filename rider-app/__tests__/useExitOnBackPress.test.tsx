/**
 * useExitOnBackPress — regression for the Android "back on home returns to
 * login" bug. After login the stack is [login, home] (index→/login,
 * /login→/otp push, /otp→home replace), so the hardware back button on home
 * must background the app instead of popping into the auth screens.
 *
 * The hook registers a `hardwareBackPress` listener (Android only) whose
 * handler calls BackHandler.exitApp() and returns true to swallow the default
 * pop. We drive useFocusEffect synchronously and assert that contract.
 */

import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { BackHandler, Platform } from 'react-native';
import { useExitOnBackPress } from '@shared/hooks/useExitOnBackPress';

// Run the focus-effect callback immediately and surface its cleanup so we can
// assert the listener is removed on blur/unmount.
let focusCleanup: undefined | (() => void);
jest.mock('expo-router', () => ({
  useFocusEffect: (cb: () => undefined | (() => void)) => {
    focusCleanup = cb() ?? undefined;
  },
}));

function Harness({ enabled }: { enabled?: boolean }) {
  useExitOnBackPress(enabled);
  return null;
}

describe('useExitOnBackPress', () => {
  const remove = jest.fn();
  let addSpy: jest.SpyInstance;
  let exitSpy: jest.SpyInstance;

  beforeEach(() => {
    focusCleanup = undefined;
    remove.mockClear();
    addSpy = jest
      .spyOn(BackHandler, 'addEventListener')
      .mockReturnValue({ remove } as any);
    exitSpy = jest.spyOn(BackHandler, 'exitApp').mockImplementation(() => {});
  });

  afterEach(() => {
    addSpy.mockRestore();
    exitSpy.mockRestore();
    Platform.OS = 'android';
  });

  it('exits the app on hardware back and swallows the default pop (Android)', () => {
    Platform.OS = 'android';
    act(() => {
      TestRenderer.create(<Harness />);
    });

    expect(addSpy).toHaveBeenCalledWith('hardwareBackPress', expect.any(Function));
    const handler = addSpy.mock.calls[0][1] as () => boolean;
    expect(handler()).toBe(true);
    expect(exitSpy).toHaveBeenCalledTimes(1);

    // Listener is cleaned up when the screen blurs/unmounts.
    focusCleanup?.();
    expect(remove).toHaveBeenCalledTimes(1);
  });

  it('is a no-op on iOS (no hardware back button)', () => {
    Platform.OS = 'ios';
    act(() => {
      TestRenderer.create(<Harness />);
    });
    expect(addSpy).not.toHaveBeenCalled();
  });

  it('does not register when disabled', () => {
    Platform.OS = 'android';
    act(() => {
      TestRenderer.create(<Harness enabled={false} />);
    });
    expect(addSpy).not.toHaveBeenCalled();
  });
});
