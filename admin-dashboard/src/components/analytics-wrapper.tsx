'use client';

import { Analytics } from '@vercel/analytics/react';

export function AnalyticsWrapper() {
  return (
    <Analytics
      beforeSend={(event) => ({
        ...event,
        url: event.url.replace(/\/[0-9a-f-]{8,}(?=\/|$)/gi, '/[id]'),
      })}
    />
  );
}
