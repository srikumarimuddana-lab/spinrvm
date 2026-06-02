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
import { registerAndroidAuto, AndroidAutoRoot, AndroidAutoClusterRoot } from '../car/androidAutoEntry';

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

    // the main car surface yields the nav-driving root
    const autoFactory = spy.mock.calls.find((c) => c[0] === 'AndroidAuto')?.[1];
    expect(autoFactory()).toBe(AndroidAutoRoot);

    // the cluster surface yields a SEPARATE no-op root (not AndroidAutoRoot), so
    // initCarNav doesn't run twice and double-fire nav hand-offs.
    const clusterFactory = spy.mock.calls.find((c) => c[0] === 'AndroidAutoCluster')?.[1];
    expect(clusterFactory()).toBe(AndroidAutoClusterRoot);
    expect(clusterFactory()).not.toBe(AndroidAutoRoot);
    expect(AndroidAutoClusterRoot()).toBeNull();
  });

  it('is a no-op on iOS (the phone tree drives CarPlay there)', () => {
    setOS('ios');

    registerAndroidAuto();

    expect(spy).not.toHaveBeenCalled();
  });
});
