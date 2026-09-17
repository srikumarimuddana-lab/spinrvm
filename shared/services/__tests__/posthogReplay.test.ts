/**
 * Fail-closed PostHog session-replay gate. Does not import the native SDK.
 */

import {
  identifyPostHogUser,
  initPostHogReplayFromSettings,
  isAllowedPosthogHost,
  pausePostHogReplay,
  posthogHostFromSettings,
  resetPostHogReplay,
  resumePostHogReplay,
  setPostHogReplayClient,
  shouldInitPostHogReplay,
  type PostHogReplayClient,
} from '../posthogReplay';

describe('shouldInitPostHogReplay', () => {
  it('is false when settings are missing', () => {
    expect(shouldInitPostHogReplay(undefined)).toBe(false);
    expect(shouldInitPostHogReplay(null)).toBe(false);
    expect(shouldInitPostHogReplay({})).toBe(false);
  });

  it('is false when the flag is on but the key is empty', () => {
    expect(
      shouldInitPostHogReplay({
        posthog_session_replay_enabled: true,
        posthog_api_key: '   ',
      }),
    ).toBe(false);
  });

  it('is false when a key is set but the flag is off', () => {
    expect(
      shouldInitPostHogReplay({
        posthog_session_replay_enabled: false,
        posthog_api_key: 'phc_test',
      }),
    ).toBe(false);
  });

  it('is true only when the flag is on and the key is a phc_ project key', () => {
    expect(
      shouldInitPostHogReplay({
        posthog_session_replay_enabled: true,
        posthog_api_key: 'phc_test',
      }),
    ).toBe(true);
  });

  it('is false for a personal API key (phx_) even when the flag is on', () => {
    expect(
      shouldInitPostHogReplay({
        posthog_session_replay_enabled: true,
        posthog_api_key: 'phx_personal',
      }),
    ).toBe(false);
  });
});

describe('posthogHostFromSettings', () => {
  it('defaults to US ingest when host is blank', () => {
    expect(posthogHostFromSettings({})).toBe('https://us.i.posthog.com');
    expect(posthogHostFromSettings({ posthog_host: '  ' })).toBe('https://us.i.posthog.com');
  });

  it('uses the configured host', () => {
    expect(posthogHostFromSettings({ posthog_host: 'https://eu.i.posthog.com' })).toBe(
      'https://eu.i.posthog.com',
    );
  });

  it('allows https and localhost only', () => {
    expect(isAllowedPosthogHost('https://us.i.posthog.com')).toBe(true);
    expect(isAllowedPosthogHost('http://localhost:8000')).toBe(true);
    expect(isAllowedPosthogHost('http://evil.example.com')).toBe(false);
  });
});

describe('initPostHogReplayFromSettings', () => {
  afterEach(() => {
    setPostHogReplayClient(null);
  });

  it('does not create a client when the flag is off', async () => {
    const createClient = jest.fn();
    const client = await initPostHogReplayFromSettings(
      { posthog_session_replay_enabled: false, posthog_api_key: 'phc_test' },
      createClient,
    );
    expect(client).toBeNull();
    expect(createClient).not.toHaveBeenCalled();
  });

  it('creates a client with masked replay when flag and key are set', async () => {
    const fake: PostHogReplayClient = {
      identify: jest.fn(),
      stopSessionRecording: jest.fn(),
      startSessionRecording: jest.fn(),
    };
    const createClient = jest.fn().mockResolvedValue(fake);
    const client = await initPostHogReplayFromSettings(
      {
        posthog_session_replay_enabled: true,
        posthog_api_key: 'phc_test',
        posthog_host: 'https://eu.i.posthog.com',
      },
      createClient,
    );
    expect(client).toBe(fake);
    expect(createClient).toHaveBeenCalledWith('phc_test', {
      host: 'https://eu.i.posthog.com',
      enableSessionReplay: true,
      disableGeoip: true,
      captureAppLifecycleEvents: false,
      disableRemoteFeatureFlags: true,
      preloadFeatureFlags: false,
      capturePushNotificationSubscriptions: false,
      capturePushNotificationOpened: false,
      disableSurveys: true,
      sessionReplayConfig: {
        maskAllTextInputs: true,
        maskAllImages: true,
        captureLog: false,
        captureNetworkTelemetry: false,
      },
    });
  });

  it('does not create a client when the host is not https', async () => {
    const createClient = jest.fn();
    const client = await initPostHogReplayFromSettings(
      {
        posthog_session_replay_enabled: true,
        posthog_api_key: 'phc_test',
        posthog_host: 'http://evil.example.com',
      },
      createClient,
    );
    expect(client).toBeNull();
    expect(createClient).not.toHaveBeenCalled();
  });
});

describe('identifyPostHogUser', () => {
  afterEach(() => {
    setPostHogReplayClient(null);
  });

  it('identifies with user_id and role only', () => {
    const identify = jest.fn();
    setPostHogReplayClient({
      identify,
      stopSessionRecording: jest.fn(),
      startSessionRecording: jest.fn(),
    });
    identifyPostHogUser('user-1', 'rider');
    expect(identify).toHaveBeenCalledWith('user-1', { role: 'rider' });
  });

  it('no-ops without a client or user id', () => {
    expect(() => identifyPostHogUser('user-1', 'driver')).not.toThrow();
    setPostHogReplayClient({
      identify: jest.fn(),
      stopSessionRecording: jest.fn(),
      startSessionRecording: jest.fn(),
    });
    expect(() => identifyPostHogUser('', 'driver')).not.toThrow();
  });
});

describe('pause and resume', () => {
  afterEach(() => {
    setPostHogReplayClient(null);
  });

  it('stops recording on pause and resumes on resume', () => {
    const stopSessionRecording = jest.fn();
    const startSessionRecording = jest.fn();
    setPostHogReplayClient({
      identify: jest.fn(),
      stopSessionRecording,
      startSessionRecording,
    });
    pausePostHogReplay();
    resumePostHogReplay();
    expect(stopSessionRecording).toHaveBeenCalledTimes(1);
    expect(startSessionRecording).toHaveBeenCalledWith(true);
  });

  it('swallows a rejected stopSessionRecording promise', async () => {
    const stopSessionRecording = jest.fn().mockRejectedValue(new Error('native'));
    setPostHogReplayClient({
      identify: jest.fn(),
      stopSessionRecording,
      startSessionRecording: jest.fn(),
    });
    expect(() => pausePostHogReplay()).not.toThrow();
    await Promise.resolve();
    await Promise.resolve();
    expect(stopSessionRecording).toHaveBeenCalledTimes(1);
  });
});

describe('resetPostHogReplay', () => {
  afterEach(() => {
    setPostHogReplayClient(null);
  });

  it('calls reset on the live client', async () => {
    const reset = jest.fn().mockResolvedValue(undefined);
    setPostHogReplayClient({
      identify: jest.fn(),
      stopSessionRecording: jest.fn(),
      startSessionRecording: jest.fn(),
      reset,
    });
    await resetPostHogReplay();
    expect(reset).toHaveBeenCalledTimes(1);
  });

  it('no-ops without a client', async () => {
    await expect(resetPostHogReplay()).resolves.toBeUndefined();
  });
});
