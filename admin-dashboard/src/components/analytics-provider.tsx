'use client';

import { Analytics } from '@vercel/analytics/next';

// Scrubs dynamic entity IDs from page-view paths before Vercel ingests them,
// preventing UUIDs and hex IDs in admin routes from appearing in analytics.
function beforeSend(event: Parameters<NonNullable<React.ComponentProps<typeof Analytics>['beforeSend']>>[0]) {
  return { ...event, url: event.url.replace(/\/[a-f0-9-]{8,}/gi, '/[id]') };
}

export function AnalyticsProvider() {
  return <Analytics beforeSend={beforeSend} />;
}
