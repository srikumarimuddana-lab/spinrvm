import { renderHook } from '@testing-library/react-native';
import { setLogRocketInstance } from '../../services/logRocketInstance';
import {
  setPostHogReplayClient,
  type PostHogReplayClient,
} from '../../services/posthogReplay';
import { useLogRocketPrivacyScreen } from '../useLogRocketPrivacyScreen';

jest.mock('expo-router', () => ({
  useRouter: () => ({ push: jest.fn() }),
}));

describe('useLogRocketPrivacyScreen', () => {
  afterEach(() => {
    setLogRocketInstance(null);
    setPostHogReplayClient(null);
  });

  it('pauses LogRocket and PostHog on mount and resumes on unmount', () => {
    const pauseViewCapture = jest.fn();
    const unpauseViewCapture = jest.fn();
    setLogRocketInstance({ pauseViewCapture, unpauseViewCapture });

    const stopSessionRecording = jest.fn();
    const startSessionRecording = jest.fn();
    const posthog: PostHogReplayClient = {
      identify: jest.fn(),
      stopSessionRecording,
      startSessionRecording,
    };
    setPostHogReplayClient(posthog);

    const { unmount } = renderHook(() => useLogRocketPrivacyScreen());
    expect(pauseViewCapture).toHaveBeenCalledTimes(1);
    expect(stopSessionRecording).toHaveBeenCalledTimes(1);

    unmount();
    expect(unpauseViewCapture).toHaveBeenCalledTimes(1);
    expect(startSessionRecording).toHaveBeenCalledWith(true);
  });

  it('still pauses PostHog when LogRocket is not initialised', () => {
    const stopSessionRecording = jest.fn();
    setPostHogReplayClient({
      identify: jest.fn(),
      stopSessionRecording,
      startSessionRecording: jest.fn(),
    });

    renderHook(() => useLogRocketPrivacyScreen());
    expect(stopSessionRecording).toHaveBeenCalledTimes(1);
  });
});
