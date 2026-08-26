/**
 * rideVoltraLiveActivity — iOS-only Live Activity lifecycle via Voltra.
 * Pins: iOS-gated availability, the fixed activity name (must match the
 * backend's APNs topic + the template generator), and that every native call
 * failure is swallowed rather than thrown (this runs from background FCM/APNs
 * handlers where an uncaught rejection has no one to catch it).
 */
import { Platform } from 'react-native';

const mockStartLiveActivity = jest.fn().mockResolvedValue(undefined);
const mockEndLiveActivity = jest.fn().mockResolvedValue(undefined);
const mockRemove = jest.fn();
const mockAddVoltraListener = jest.fn().mockReturnValue({ remove: mockRemove });

jest.mock('@use-voltra/ios-client', () => ({
  startLiveActivity: (...args: unknown[]) => mockStartLiveActivity(...args),
  endLiveActivity: (...args: unknown[]) => mockEndLiveActivity(...args),
  addVoltraListener: (...args: unknown[]) => mockAddVoltraListener(...args),
}));

jest.mock('react-native', () => ({
  Platform: { OS: 'ios' },
}));

// `available()` inside the module reads `Platform.OS` fresh on every call,
// so mutating this file's own Platform import is enough -- resetting/
// re-requiring the module would swap in a fresh mocked Platform object via
// the factory above, decoupling it from later OS mutations here.
function loadModule() {
  return require('../rideVoltraLiveActivity');
}

const CONTENT = { status: 'accepted', headline: 'Driver on the way' };

describe('rideVoltraLiveActivity (iOS)', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    (Platform as { OS: string }).OS = 'ios';
  });

  it('exposes the fixed activity name the backend/generator must match', () => {
    const { getActivityName } = loadModule();
    expect(getActivityName()).toBe('spinr-ride');
  });

  it('starts the activity with the fixed name and given content', async () => {
    const { startActivity } = loadModule();
    await startActivity(CONTENT);
    expect(mockStartLiveActivity).toHaveBeenCalledWith({
      activityName: 'spinr-ride',
      attributes: { name: 'spinr-ride' },
      contentState: CONTENT,
    });
  });

  it('ends the activity by its fixed name', async () => {
    const { endActivity } = loadModule();
    await endActivity();
    expect(mockEndLiveActivity).toHaveBeenCalledWith({ activityName: 'spinr-ride' });
  });

  it('is a no-op on a non-iOS platform', async () => {
    (Platform as { OS: string }).OS = 'android';
    const { startActivity, endActivity, onTokenReceived } = loadModule();
    await startActivity(CONTENT);
    await endActivity();
    const unsub = onTokenReceived(jest.fn());
    expect(mockStartLiveActivity).not.toHaveBeenCalled();
    expect(mockEndLiveActivity).not.toHaveBeenCalled();
    expect(mockAddVoltraListener).not.toHaveBeenCalled();
    expect(() => unsub()).not.toThrow();
  });

  it('swallows a start failure instead of throwing', async () => {
    mockStartLiveActivity.mockRejectedValueOnce(new Error('native failure'));
    const { startActivity } = loadModule();
    await expect(startActivity(CONTENT)).resolves.toBeUndefined();
  });

  it('swallows an end failure instead of throwing', async () => {
    mockEndLiveActivity.mockRejectedValueOnce(new Error('native failure'));
    const { endActivity } = loadModule();
    await expect(endActivity()).resolves.toBeUndefined();
  });

  describe('onTokenReceived', () => {
    it('subscribes to activityTokenReceived and forwards the pushToken to the callback', () => {
      const { onTokenReceived } = loadModule();
      const cb = jest.fn();
      onTokenReceived(cb);

      expect(mockAddVoltraListener).toHaveBeenCalledWith(
        'activityTokenReceived',
        expect.any(Function),
      );
      const handler = mockAddVoltraListener.mock.calls[0][1];
      handler({ pushToken: 'abc-token' });
      expect(cb).toHaveBeenCalledWith('abc-token');
    });

    it('falls back to a bare `token` field when pushToken is absent', () => {
      const { onTokenReceived } = loadModule();
      const cb = jest.fn();
      onTokenReceived(cb);
      const handler = mockAddVoltraListener.mock.calls[0][1];
      handler({ token: 'legacy-token' });
      expect(cb).toHaveBeenCalledWith('legacy-token');
    });

    it('does not call back when the event carries no token', () => {
      const { onTokenReceived } = loadModule();
      const cb = jest.fn();
      onTokenReceived(cb);
      const handler = mockAddVoltraListener.mock.calls[0][1];
      handler({});
      expect(cb).not.toHaveBeenCalled();
    });

    it('returns an unsubscribe function that calls remove() on the subscription', () => {
      const { onTokenReceived } = loadModule();
      const unsub = onTokenReceived(jest.fn());
      unsub();
      expect(mockRemove).toHaveBeenCalled();
    });

    it('swallows a listener-registration failure and returns a safe no-op unsubscribe', () => {
      mockAddVoltraListener.mockImplementationOnce(() => {
        throw new Error('native failure');
      });
      const { onTokenReceived } = loadModule();
      const unsub = onTokenReceived(jest.fn());
      expect(() => unsub()).not.toThrow();
    });
  });
});
