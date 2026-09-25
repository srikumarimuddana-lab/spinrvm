import React from 'react';
import { render } from '@testing-library/react-native';
import { Animated } from 'react-native';
import SkeletonBox from '../SkeletonBox';

// Controls the OS Reduce Motion setting as seen through the shared hook.
let mockReduceMotion = false;
jest.mock('@shared/hooks/useReduceMotion', () => ({
  useReduceMotion: () => mockReduceMotion,
}));

describe('SkeletonBox — Reduce Motion', () => {
  let loopSpy: jest.SpyInstance;

  beforeEach(() => {
    loopSpy = jest.spyOn(Animated, 'loop');
  });
  afterEach(() => {
    loopSpy.mockRestore();
    mockReduceMotion = false;
  });

  it('pulses when Reduce Motion is off', () => {
    render(<SkeletonBox />);
    expect(loopSpy).toHaveBeenCalledTimes(1);
  });

  it('renders a static placeholder with no loop when Reduce Motion is on', () => {
    mockReduceMotion = true;
    const { toJSON } = render(<SkeletonBox />);
    expect(loopSpy).not.toHaveBeenCalled();
    expect(toJSON()).not.toBeNull();
  });

  it('stops a running pulse when Reduce Motion turns on mid-session', () => {
    const { rerender } = render(<SkeletonBox />);
    const running = loopSpy.mock.results[0].value;
    const stopSpy = jest.spyOn(running, 'stop');

    mockReduceMotion = true;
    rerender(<SkeletonBox />);
    expect(stopSpy).toHaveBeenCalled();
    expect(loopSpy).toHaveBeenCalledTimes(1);
  });
});
