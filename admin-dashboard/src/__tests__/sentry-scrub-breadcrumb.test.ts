/**
 * W7 (2026-09-10 admin portal security/RBAC audit,
 * docs/audit/2026-09-10-admin-portal-security-rbac-audit.md): beforeSend's
 * (scrubEvent) request/extra/contexts/user scrubbing never touched
 * event.breadcrumbs, and Sentry's default console/fetch/xhr/dom
 * integrations are all active in the 3 config files (integrations: [...]
 * adds to, not replaces, the defaults). scrubBreadcrumb closes that gap.
 *
 * sentry.scrub.ts had no test file at all before this one.
 */
import { describe, it, expect } from 'vitest';
import { scrubBreadcrumb } from '../../sentry.scrub';
import type { Breadcrumb } from '@sentry/nextjs';

function bc(overrides: Partial<Breadcrumb>): Breadcrumb {
  return { type: 'default', category: 'default', ...overrides };
}

describe('scrubBreadcrumb', () => {
  it('strips the query string from a fetch/xhr breadcrumb URL', () => {
    const result = scrubBreadcrumb(
      bc({
        category: 'fetch',
        data: { url: '/api/admin/users?email=jane@spinr.ca&phone=3065551234', method: 'GET' },
      }),
    );
    expect(result?.data?.url).toBe('/api/admin/users');
    // Non-URL fields in data are left alone.
    expect(result?.data?.method).toBe('GET');
  });

  it('leaves a breadcrumb with no query string unchanged', () => {
    const result = scrubBreadcrumb(bc({ category: 'fetch', data: { url: '/api/admin/drivers' } }));
    expect(result?.data?.url).toBe('/api/admin/drivers');
  });

  it('redacts PII-keyed fields anywhere in breadcrumb.data', () => {
    const result = scrubBreadcrumb(
      bc({
        category: 'fetch',
        data: { url: '/api/x', response: { email: 'jane@spinr.ca', driver_id: 'd-123' } },
      }),
    );
    const responseData = result?.data?.response as Record<string, unknown>;
    expect(responseData.email).toBe('[Filtered]');
    expect(responseData.driver_id).toBe('[Filtered]');
  });

  it('filters a console breadcrumb message entirely (arbitrary, untrusted content)', () => {
    const result = scrubBreadcrumb(bc({ category: 'console', level: 'log', message: 'user jane@spinr.ca logged in' }));
    expect(result?.message).toBe('[Filtered]');
  });

  it('does not filter a non-console breadcrumb message', () => {
    const result = scrubBreadcrumb(bc({ category: 'ui.click', message: "button[aria-label='Save']" }));
    expect(result?.message).toBe("button[aria-label='Save']");
  });

  it('does not filter a navigation breadcrumb message', () => {
    const result = scrubBreadcrumb(bc({ category: 'navigation', message: 'route change' }));
    expect(result?.message).toBe('route change');
  });

  it('handles a breadcrumb with no data and no message without throwing', () => {
    const result = scrubBreadcrumb(bc({ category: 'ui.click' }));
    expect(result).toBeTruthy();
  });

  it('handles a non-string data.url without throwing', () => {
    const result = scrubBreadcrumb(bc({ category: 'fetch', data: { url: 123 as unknown as string } }));
    expect(result?.data?.url).toBe(123);
  });
});
