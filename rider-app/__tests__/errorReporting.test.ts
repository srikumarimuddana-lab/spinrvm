/**
 * errorReporting facade — Sentry primary path (SPR-04).
 *
 * Pins the PIPEDA-critical init options and that capture/setUser route to
 * Sentry once initialised. The @sentry/react-native module is the Jest stub
 * from __mocks__/sentry-react-native.js (mapped in jest.config.js).
 */
import * as Sentry from '@sentry/react-native';
import {
  initErrorReporting,
  isSentryActive,
  captureException,
  captureMessage,
  setUser,
  addBreadcrumb,
  _resetForTests,
} from '@shared/services/errorReporting';

const DSN = 'https://key@o0.ingest.sentry.io/0';

describe('initErrorReporting', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    _resetForTests();
  });

  it('no DSN → Sentry stays inactive (Crashlytics/console fallback)', () => {
    initErrorReporting({ surface: 'rider-app' });
    expect(Sentry.init).not.toHaveBeenCalled();
    expect(isSentryActive()).toBe(false);
  });

  it('initialises with PIPEDA-safe options and the surface tag', () => {
    initErrorReporting({ dsn: DSN, surface: 'rider-app' });
    expect(isSentryActive()).toBe(true);
    const options = (Sentry.init as jest.Mock).mock.calls[0][0];
    expect(options.sendDefaultPii).toBe(false);
    expect(options.attachScreenshot).toBe(false);
    expect(options.attachViewHierarchy).toBe(false);
    expect(options.initialScope.tags.surface).toBe('rider-app');
  });

  it('drops console breadcrumbs (they carry GPS coords from debug logs)', () => {
    initErrorReporting({ dsn: DSN, surface: 'rider-app' });
    const options = (Sentry.init as jest.Mock).mock.calls[0][0];
    expect(options.beforeBreadcrumb({ category: 'console', message: 'lat=52.13' })).toBeNull();
    expect(options.beforeBreadcrumb({ category: 'http' })).toEqual({ category: 'http' });
  });

  it('beforeSend strips the user to id-only', () => {
    initErrorReporting({ dsn: DSN, surface: 'rider-app' });
    const options = (Sentry.init as jest.Mock).mock.calls[0][0];
    const event = options.beforeSend({
      user: { id: 'u-1', email: 'rider@example.com', username: 'Full Name' },
    });
    expect(event.user).toEqual({ id: 'u-1' });
  });
});

describe('facade routing once Sentry is active', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    _resetForTests();
    initErrorReporting({ dsn: DSN, surface: 'rider-app' });
  });

  it('captureException sends CLAUDE.md tag keys as indexed tags, not extra', () => {
    // `domain`/`ride_id`/`driver_id`/`rider_id` must be tags so events are
    // filterable and correlate with the backend's Sentry events.
    const err = new Error('boom');
    captureException(err, { ride_id: 'r-1', domain: 'dispatch' });
    expect(Sentry.captureException).toHaveBeenCalledWith(err, {
      tags: { ride_id: 'r-1', domain: 'dispatch' },
    });
  });

  it('captureException keeps non-tag context in extra', () => {
    const err = new Error('boom');
    captureException(err, { domain: 'safety', attempt: 3 });
    expect(Sentry.captureException).toHaveBeenCalledWith(err, {
      tags: { domain: 'safety' },
      extra: { attempt: 3 },
    });
  });

  it('captureMessage maps log → info', () => {
    captureMessage('cold start', 'log');
    expect(Sentry.captureMessage).toHaveBeenCalledWith('cold start', 'info');
  });

  it('setUser sends id only; empty id clears the user', () => {
    setUser('u-1', { email: 'leak@example.com' });
    expect(Sentry.setUser).toHaveBeenCalledWith({ id: 'u-1' });
    setUser('');
    expect(Sentry.setUser).toHaveBeenCalledWith(null);
  });

  it('addBreadcrumb routes to Sentry', () => {
    addBreadcrumb('tapped book');
    expect(Sentry.addBreadcrumb).toHaveBeenCalledWith({ message: 'tapped book', level: 'info' });
  });
});
