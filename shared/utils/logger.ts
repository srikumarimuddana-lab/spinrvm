/**
 * Lightweight logger for React Native apps.
 *
 * - Adds a consistent `[Tag]` prefix to every message.
 * - No-ops in production builds (__DEV__ === false) to prevent
 *   information leakage via console output.
 * - Drop-in replacement for console.log / console.warn / console.error.
 *
 * Usage:
 *   import { createLogger } from '@shared/utils/logger';
 *   const log = createLogger('Index');
 *   log.info('Profile incomplete, clearing session');
 *   log.warn('Unexpected state');
 *   log.error('Failed to load', err);
 *
 * TODO (pre-production): Add remote logging transport (Sentry/Datadog/custom backend)
 * so that log.error() calls in production are captured centrally, not just in Crashlytics.
 * Also migrate all console.log/warn/error calls across driver-app and rider-app to use
 * this logger for consistent behavior.
 */

// @ts-ignore – __DEV__ is injected by Metro / Expo at build time
const isDev = typeof __DEV__ !== 'undefined' ? __DEV__ : process.env.NODE_ENV !== 'production';

export interface Logger {
  info: (...args: any[]) => void;
  warn: (...args: any[]) => void;
  error: (...args: any[]) => void;
}

export function createLogger(tag: string): Logger {
  const prefix = `[${tag}]`;

  if (!isDev) {
    // In production, only expose error (useful for crash reporters like Crashlytics)
    return {
      info: () => {},
      warn: () => {},
      error: (...args: any[]) => console.error(prefix, ...args),
    };
  }

  return {
    info: (...args: any[]) => console.log(prefix, ...args),
    warn: (...args: any[]) => console.warn(prefix, ...args),
    error: (...args: any[]) => console.error(prefix, ...args),
  };
}
