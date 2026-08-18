/**
 * The missing-native-module guard in registerAutoPlay, tested in ISOLATION.
 *
 * Simulates Expo Go / a binary that predates the @iternio dependency: importing
 * @iternio/react-native-auto-play instantiates its Nitro HybridObject natively,
 * which throws when the native side is absent. registerAutoPlay runs at bundle
 * load (index.js), so an unguarded throw would crash the whole phone app.
 *
 * This lives in its own file (not register.test.ts) on purpose: the throwing
 * @iternio mock must be the file's hoisted module factory. Overriding it inside
 * the shared file via doMock/dontMock leaves that registry without its baseline
 * mock for any test appended afterwards — an order-dependent trap.
 */
import { Platform } from 'react-native';

const mockRecordNonFatal = jest.fn();

jest.mock('../../../utils/crashlytics', () => ({
  recordNonFatal: (...args: unknown[]) => mockRecordNonFatal(...args),
}));
jest.mock('../../../store/driverStore', () => ({
  useDriverStore: { getState: jest.fn(), subscribe: jest.fn() },
}));
jest.mock('../carSurface', () => ({ CarMapSurface: () => null }));
// Keeps the real module's transitive utils/backgroundLocation import (SQLite
// recorder, Firebase App Check) out of a suite that only exercises the
// missing-native-module guard.
jest.mock('../carLocationTask', () => ({
  startCarLocationService: jest.fn().mockResolvedValue('started'),
  stopCarLocationService: jest.fn().mockResolvedValue(undefined),
}));
jest.mock('../carSession', () => ({
  startCarSession: jest.fn().mockResolvedValue(undefined),
  stopCarSession: jest.fn(),
}));
jest.mock('../carMapCamera', () => ({
  useCarMapCamera: { getState: () => ({ reset: jest.fn() }) },
}));
jest.mock('@iternio/react-native-auto-play', () => {
  throw new Error('Nitro HybridObject "AutoPlay" is not registered');
});

// eslint-disable-next-line import/first -- must follow the jest.mock() calls above
import registerAutoPlay from '../register';

it('degrades to no-car-support instead of throwing when the native module is missing', () => {
  (Platform as unknown as { OS: string }).OS = 'android';
  const errSpy = jest.spyOn(console, 'error').mockImplementation(() => {});

  expect(() => registerAutoPlay()).not.toThrow();

  // Loud local log for a developer at the Metro console…
  expect(errSpy).toHaveBeenCalledWith(
    expect.stringContaining('native module unavailable'),
    expect.any(Error),
  );
  // …and a remotely visible Crashlytics non-fatal for the team dashboard.
  expect(mockRecordNonFatal).toHaveBeenCalledWith(expect.any(Error), {
    module: 'androidAuto',
    reason: 'native_module_unavailable',
  });

  errSpy.mockRestore();
});
