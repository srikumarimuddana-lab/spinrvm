/**
 * Sentry server-side configuration (SPR-03/3b).
 * Loaded by Next.js for Node.js runtime and Edge runtime.
 */
import * as Sentry from '@sentry/nextjs';
import { scrubEvent as beforeSend } from './sentry.scrub';

// PIPEDA A-PE-P2-5: warn at cold-start if the DSN routes to an unreviewed region.
// Sentry has no Canadian region. Spinr's org (`spinr-backend`) lives in the US
// region, which is fixed at organization-creation time and has no self-serve
// migration path — US ingestion is therefore a DOCUMENTED ACCEPTED RISK requiring
// a DPA addendum, not a passing grade. See
// docs/change-log/2026-08-05-sentry-us-region-accepted.md.
// An unrecognised host still errors: that means the DSN was repointed somewhere
// nobody reviewed, which is the case this check actually still catches.
// Error appears in Vercel Function logs; does not throw (Sentry still initialises).
const ACCEPTED_INGEST_HOSTS = ['ingest.de.sentry.io', 'ingest.us.sentry.io'];

function _checkSentryRegion(dsn: string | undefined): void {
  if (!dsn || process.env.NODE_ENV !== 'production') return;
  if (!ACCEPTED_INGEST_HOSTS.some((host) => dsn.includes(host))) {
    console.error(
      '[spinr-admin][PIPEDA] Sentry DSN routes to an unreviewed region. ' +
      'Expected ingest.de.sentry.io (EU) or ingest.us.sentry.io (US, accepted risk). ' +
      'See docs/vendor-register.md § Sentry and A-PE-P2-5 in the remediation plan.'
    );
  }
}
_checkSentryRegion(process.env.SENTRY_DSN || process.env.NEXT_PUBLIC_SENTRY_DSN);

// [22-2] PIPEDA data residency: Sentry has no Canadian region. EU
// (o<org>.ingest.de.sentry.io) is the closest compliant option and remains the
// target. Spinr currently ingests to the US region as an accepted risk — see the
// region check above and its linked change-log entry before changing this.
Sentry.init({
  dsn: process.env.SENTRY_DSN || process.env.NEXT_PUBLIC_SENTRY_DSN,
  // Admin is low-traffic — 100% sampling catches all perf regressions ([21-2]).
  tracesSampleRate: 1.0,
  environment: process.env.NODE_ENV ?? 'development',
  release: process.env.VERCEL_GIT_COMMIT_SHA || process.env.NEXT_PUBLIC_VERCEL_GIT_COMMIT_SHA,
  enabled: !!(process.env.SENTRY_DSN || process.env.NEXT_PUBLIC_SENTRY_DSN),
  beforeSend,
  // Required tags per CLAUDE.md observability conventions ([21-4]).
  initialScope: {
    tags: {
      surface: 'admin',
      env: process.env.NODE_ENV ?? 'development',
    },
  },
});

Sentry.setTag('surface', 'admin');
