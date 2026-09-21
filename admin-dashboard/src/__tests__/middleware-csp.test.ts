/**
 * The driver-approval document reviewer renders PDFs with
 * <embed src={signed Supabase storage URL}>, which CSP governs via object-src.
 * With no object-src directive the browser fell back to default-src 'self' and
 * blocked every PDF, leaving reviewers with a blank viewer.
 */

import { describe, it, expect } from 'vitest';
import { NextRequest } from 'next/server';
import { middleware } from '@/middleware';

function cspFor(path: string): Record<string, string> {
  const res = middleware(new NextRequest(`https://admin.example.test${path}`));
  const csp = res.headers.get('Content-Security-Policy') ?? '';
  return Object.fromEntries(
    csp
      .split(';')
      .map((d) => d.trim())
      .filter(Boolean)
      .map((d) => {
        const [name, ...values] = d.split(/\s+/);
        return [name, values.join(' ')];
      }),
  );
}

describe('admin CSP object-src', () => {
  it('allows Supabase storage so the document reviewer can embed PDFs', () => {
    const csp = cspFor('/login');
    expect(csp['object-src']).toBe("'self' https://*.supabase.co");
  });

  it('keeps default-src locked to self', () => {
    expect(cspFor('/login')['default-src']).toBe("'self'");
  });
});
