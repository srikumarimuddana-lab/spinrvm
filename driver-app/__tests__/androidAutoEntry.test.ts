/**
 * Unit tests for the Android Auto JS entry point (car/androidAutoEntry.tsx).
 * Verifies it registers the "AndroidAuto" AppRegistry root that the fork's
 * CarPlaySession.runApplication("AndroidAuto") requires — on Android only.
 */

// The fork's headless-task subpath is required defensively during registration; stub it.
jest.mock('@g4rb4g3/react-native-carplay/lib/CarPlayHeadlessJsTask', () => ({
  default: jest.fn(),
}));

import { AppRegistry, Platform } from 'react-native';
import { registerAndroidAuto, AndroidAutoRoot } from '../car/androidAutoEntry';

const setOS = (os: string) => {
  (Platform as unknown as { OS: string }).OS = os;
};

describe('registerAndroidAuto', () => {
  let spy: jest.SpyInstance;

  beforeEach(() => {
    spy = jest
      .spyOn(AppRegistry, 'registerComponent')
      .mockImplementation((name: string) => name);
  });

  afterEach(() => {
    spy.mockRestore();
    setOS('ios');
  });

  it('registers the AndroidAuto + AndroidAutoCluster roots on Android', () => {
    setOS('android');

    registerAndroidAuto();

    const names = spy.mock.calls.map((c) => c[0]);
    expect(names).toContain('AndroidAuto');
    expect(names).toContain('AndroidAutoCluster');

    // the registered factory yields our car root
    const factory = spy.mock.calls.find((c) => c[0] === 'AndroidAuto')?.[1];
    expect(factory()).toBe(AndroidAutoRoot);
  });

  it('is a no-op on iOS (the phone tree drives CarPlay there)', () => {
    setOS('ios');

    registerAndroidAuto();

    expect(spy).not.toHaveBeenCalled();
  });
});
