/**
 * PostHog session-replay client handle.
 *
 * Init is fail-closed: the admin flag must be on AND a project API key
 * (phc_...) must be present. LogRocket is a separate SDK and is not
 * replaced by this module.
 *
 * Identify only with user_id + role — never email, phone, GPS, or address
 * (PIPEDA / CLAUDE.md analytics rules). Constructor options also turn off
 * GeoIP, lifecycle events, remote flags, surveys, and push-token capture
 * so a flagged-on session is not a second analytics product.
 */

export type PostHogReplayClient = {
  identify: (distinctId: string, properties?: Record<string, string>) => void;
  stopSessionRecording: () => void | Promise<void>;
  startSessionRecording: (resumeCurrent?: boolean) => void | Promise<void>;
  reset?: () => void | Promise<void>;
  shutdown?: () => void | Promise<void>;
};

export type PostHogPublicSettings = {
  posthog_session_replay_enabled?: boolean;
  posthog_api_key?: string;
  posthog_host?: string;
};

const DEFAULT_HOST = 'https://us.i.posthog.com';

let instance: PostHogReplayClient | null = null;

export function setPostHogReplayClient(client: PostHogReplayClient | null): void {
  instance = client;
}

export function getPostHogReplayClient(): PostHogReplayClient | null {
  return instance;
}

export function shouldInitPostHogReplay(settings: PostHogPublicSettings | null | undefined): boolean {
  if (!settings?.posthog_session_replay_enabled) return false;
  const key = (settings.posthog_api_key || '').trim();
  // Personal API keys are phx_ and must never be used as the project token.
  return key.startsWith('phc_');
}

export function isAllowedPosthogHost(host: string): boolean {
  return (
    host.startsWith('https://') ||
    host.startsWith('http://localhost') ||
    host.startsWith('http://127.0.0.1')
  );
}

export function posthogHostFromSettings(settings: PostHogPublicSettings | null | undefined): string {
  const host = (settings?.posthog_host || '').trim();
  return host || DEFAULT_HOST;
}

function swallowPostHogPromise(label: string, result: void | Promise<void>): void {
  if (result != null && typeof (result as Promise<void>).then === 'function') {
    (result as Promise<void>).catch((e) => {
      console.log(`[PostHog] ${label} failed:`, e);
    });
  }
}

/** Identify with user_id + role only. No-op if replay is not initialised. */
export function identifyPostHogUser(userId: string | undefined | null, role: 'rider' | 'driver'): void {
  const client = instance;
  if (!client || !userId) return;
  try {
    client.identify(userId, { role });
  } catch (e) {
    console.log('[PostHog] identify failed:', e);
  }
}

export function pausePostHogReplay(): void {
  const client = instance;
  if (!client) return;
  try {
    swallowPostHogPromise('stopSessionRecording', client.stopSessionRecording());
  } catch (e) {
    console.log('[PostHog] stopSessionRecording failed:', e);
  }
}

export function resumePostHogReplay(): void {
  const client = instance;
  if (!client) return;
  try {
    swallowPostHogPromise('startSessionRecording', client.startSessionRecording(true));
  } catch (e) {
    console.log('[PostHog] startSessionRecording failed:', e);
  }
}

/** Drop the previous distinct id so rider A → logout → rider B does not stitch. */
export async function resetPostHogReplay(): Promise<void> {
  const client = instance;
  if (!client) return;
  try {
    if (typeof client.reset === 'function') {
      await client.reset();
    }
  } catch (e) {
    console.log('[PostHog] reset failed:', e);
  }
}

export type CreatePostHogClient = (
  apiKey: string,
  options: {
    host: string;
    enableSessionReplay: boolean;
    disableGeoip: boolean;
    captureAppLifecycleEvents: boolean;
    disableRemoteFeatureFlags: boolean;
    preloadFeatureFlags: boolean;
    capturePushNotificationSubscriptions: boolean;
    capturePushNotificationOpened: boolean;
    disableSurveys: boolean;
    sessionReplayConfig: {
      maskAllTextInputs: boolean;
      maskAllImages: boolean;
      captureLog: boolean;
      captureNetworkTelemetry: boolean;
    };
  },
) => PostHogReplayClient | Promise<PostHogReplayClient>;

/**
 * Build a PostHog client when the admin flag + key are present.
 * `createClient` is injected so tests (and Expo Go, where the native
 * module is missing) never import posthog-react-native at module load.
 */
export async function initPostHogReplayFromSettings(
  settings: PostHogPublicSettings | null | undefined,
  createClient: CreatePostHogClient,
): Promise<PostHogReplayClient | null> {
  if (!shouldInitPostHogReplay(settings)) {
    setPostHogReplayClient(null);
    return null;
  }
  const apiKey = (settings!.posthog_api_key || '').trim();
  const host = posthogHostFromSettings(settings);
  if (!isAllowedPosthogHost(host)) {
    setPostHogReplayClient(null);
    return null;
  }
  const client = await createClient(apiKey, {
    host,
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
  setPostHogReplayClient(client);
  return client;
}

/**
 * Runtime require of posthog-react-native so Expo Go / tests / web fail
 * closed instead of crashing at module load. Returns null when the native
 * module is not linked.
 */
export function tryCreateNativePostHogClient(): CreatePostHogClient | null {
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const mod = require('posthog-react-native');
    const PostHog = mod?.default ?? mod?.PostHog ?? mod;
    if (typeof PostHog !== 'function') return null;
    return (apiKey, options) => new PostHog(apiKey, options);
  } catch {
    return null;
  }
}
