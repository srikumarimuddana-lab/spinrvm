/**
 * Next.js client instrumentation hook — the browser-side entry point for Sentry.
 *
 * On @sentry/nextjs v9+, `sentry.client.config.ts` is no longer auto-loaded;
 * Next.js (15.3+) loads THIS file on the client instead. Importing the config
 * for its side effect keeps `Sentry.init` and the PII scrubbers in one reviewed
 * place rather than moving them and losing the history on that file.
 */
import * as Sentry from '@sentry/nextjs';

import '../sentry.client.config';

/**
 * Marks the start of a client-side navigation so App Router route changes are
 * traced as navigation transactions. Required export — omitting it does not
 * error, it just silently produces no navigation spans.
 */
export const onRouterTransitionStart = Sentry.captureRouterTransitionStart;
