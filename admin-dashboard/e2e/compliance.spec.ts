/**
 * Admin dashboard E2E — /dashboard/compliance (Compliance & Tax Reporting).
 * All network calls mocked — no live backend. Fixes gap G4 from
 * reports/audits/2026-07-28-compliance-reporting-module-lifecycle-audit-v1.md
 * (module had zero E2E coverage).
 *
 * Both report endpoints return binary file bytes (PDF/CSV/Excel/Word), not
 * JSON, so the mock fulfills a minimal-but-real PDF body directly rather
 * than going through admin-mocks.ts's json() helper (built for JSON only).
 */
import { test, expect } from '@playwright/test';
import { setupAdminMocks } from './admin-mocks';

// Minimal-but-valid PDF bytes — enough for the browser's download flow
// (Blob creation, object URL, anchor click) to succeed without needing a
// real fpdf2-rendered file.
const FAKE_PDF_BYTES = Buffer.from('%PDF-1.4\n%%EOF');

// Two named areas so the page-level Service Area multi-select has real
// options — admin-mocks.ts's default /api/admin/service-areas mock returns
// a bare [], which is correct for pages that only need the call not to
// throw but leaves nothing to tick here.
const SERVICE_AREAS = [
  { id: 'area-saskatoon', name: 'Saskatoon' },
  { id: 'area-regina', name: 'Regina' },
];

async function mockCompliance(page: any, opts: { serviceAreas?: unknown[] } = {}) {
  await setupAdminMocks(page, {
    extra: async (route, url, method) => {
      if (url.includes('/api/admin/service-areas') && method === 'GET') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(opts.serviceAreas ?? SERVICE_AREAS),
        });
        return 'handled';
      }
      if (url.includes('/api/admin/compliance/gst-pst-remittance') && method === 'GET') {
        await route.fulfill({
          status: 200,
          contentType: 'application/pdf',
          headers: { 'content-disposition': 'attachment; filename="gst_pst_remittance.pdf"' },
          body: FAKE_PDF_BYTES,
        });
        return 'handled';
      }
      if (url.includes('/api/admin/compliance/insurance-billing-sgi') && method === 'GET') {
        await route.fulfill({
          status: 200,
          contentType: 'application/pdf',
          headers: { 'content-disposition': 'attachment; filename="insurance_billing_sgi.pdf"' },
          body: FAKE_PDF_BYTES,
        });
        return 'handled';
      }
      return null;
    },
  });
}

test.describe('admin dashboard: compliance — interaction', () => {
  test('page loads on the GST/PST Remittance tab by default', async ({ page }) => {
    await mockCompliance(page);
    await page.goto('/dashboard/compliance');
    await expect(page.getByText('Compliance & Tax Reporting')).toBeVisible({ timeout: 20000 });
    await expect(page.getByRole('tab', { name: /gst\/pst remittance/i })).toBeVisible();
    await expect(page.getByText('GST/PST Remittance Summary')).toBeVisible();
  });

  test('From/To date pickers and format selector are present on the GST/PST tab', async ({ page }) => {
    await mockCompliance(page);
    await page.goto('/dashboard/compliance');
    await expect(page.getByText('GST/PST Remittance Summary')).toBeVisible({ timeout: 20000 });
    await expect(page.getByText('From', { exact: true })).toBeVisible();
    await expect(page.getByText('To', { exact: true })).toBeVisible();
    await expect(page.getByText('Format', { exact: true })).toBeVisible();
  });

  test('GST/PST Download button triggers a file download', async ({ page }) => {
    await mockCompliance(page);
    await page.goto('/dashboard/compliance');
    await expect(page.getByText('GST/PST Remittance Summary')).toBeVisible({ timeout: 20000 });

    const downloadPromise = page.waitForEvent('download');
    await page.getByRole('button', { name: /download/i }).first().click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toBe('gst_pst_remittance.pdf');
  });

  test('SGI Insurance Billing tab switches without crashing', async ({ page }) => {
    await mockCompliance(page);
    await page.goto('/dashboard/compliance');
    await expect(page.getByText('Compliance & Tax Reporting')).toBeVisible({ timeout: 20000 });
    await page.getByRole('tab', { name: /sgi insurance billing/i }).click();
    await expect(page.getByText('$0.11/km', { exact: false })).toBeVisible();
  });

  test('SGI Insurance Billing tab has no rate input — rate is fixed server-side', async ({ page }) => {
    await mockCompliance(page);
    await page.goto('/dashboard/compliance');
    await page.getByRole('tab', { name: /sgi insurance billing/i }).click();
    await expect(page.getByText('$0.11/km', { exact: false })).toBeVisible({ timeout: 20000 });
    // The old "Insurance Usage Billing" tab had a "Rate (cents/km)" text
    // input with this exact placeholder for the admin-entered rate; the
    // rate is now fixed server-side, so no such field should exist. (Not
    // asserting on the substring "rate" anywhere on the page — the
    // CardDescription prose legitimately mentions "SGI's contracted rate"
    // and the info hint does too, both case-insensitive matches for
    // getByText, which is what made this test flaky before.)
    await expect(page.getByPlaceholder('e.g. 45.00')).toHaveCount(0);
  });

  test('SGI Insurance Billing Download button triggers a file download', async ({ page }) => {
    await mockCompliance(page);
    await page.goto('/dashboard/compliance');
    await page.getByRole('tab', { name: /sgi insurance billing/i }).click();
    await expect(page.getByText('$0.11/km', { exact: false })).toBeVisible({ timeout: 20000 });

    const downloadPromise = page.waitForEvent('download');
    await page.getByRole('button', { name: /download/i }).click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toBe('insurance_billing_sgi.pdf');
  });

  test('failed report generation shows an error toast, not a crash', async ({ page }) => {
    await setupAdminMocks(page, {
      extra: async (route, url, method) => {
        if (url.includes('/api/admin/compliance/gst-pst-remittance') && method === 'GET') {
          await route.fulfill({
            status: 503,
            contentType: 'application/json',
            body: JSON.stringify({ detail: 'Compliance report data unavailable — database error' }),
          });
          return 'handled';
        }
        return null;
      },
    });
    await page.goto('/dashboard/compliance');
    await expect(page.getByText('GST/PST Remittance Summary')).toBeVisible({ timeout: 20000 });
    await page.getByRole('button', { name: /download/i }).first().click();
    await expect(page.getByText('Could not generate report', { exact: true })).toBeVisible({ timeout: 10000 });
  });
});

test.describe('admin dashboard: compliance — service area multi-select', () => {
  const areaTrigger = (page: any) => page.getByRole('button', { name: 'Filter reports by service area' });

  test('defaults to all service areas, so an untouched page exports what it always did', async ({ page }) => {
    await mockCompliance(page);
    await page.goto('/dashboard/compliance');
    await expect(page.getByText('Compliance & Tax Reporting')).toBeVisible({ timeout: 20000 });
    await expect(areaTrigger(page)).toHaveText(/All service areas/);
  });

  test('stays open across multiple ticks and sends every selected area', async ({ page }) => {
    await mockCompliance(page);
    await page.goto('/dashboard/compliance');
    await expect(page.getByText('GST/PST Remittance Summary')).toBeVisible({ timeout: 20000 });

    await areaTrigger(page).click();
    await page.getByRole('menuitemcheckbox', { name: 'Saskatoon' }).click();
    // The menu must survive the first tick — a dropdown that closes per
    // option makes a multi-select unusable, so this is the behaviour under
    // test, not incidental setup.
    await expect(page.getByRole('menuitemcheckbox', { name: 'Regina' })).toBeVisible();
    await page.getByRole('menuitemcheckbox', { name: 'Regina' }).click();
    await page.keyboard.press('Escape');

    await expect(areaTrigger(page)).toHaveText(/2 areas/);

    const [request] = await Promise.all([
      page.waitForRequest((r: any) => r.url().includes('/api/admin/compliance/gst-pst-remittance')),
      page.getByRole('button', { name: /download/i }).first().click(),
    ]);
    const ids = new URL(request.url()).searchParams.get('service_area_ids');
    expect(ids).toBe('area-saskatoon,area-regina');
  });

  test('the same selection applies to a different tab, since the filter is page-level', async ({ page }) => {
    await mockCompliance(page);
    await page.goto('/dashboard/compliance');
    await expect(page.getByText('Compliance & Tax Reporting')).toBeVisible({ timeout: 20000 });

    await areaTrigger(page).click();
    await page.getByRole('menuitemcheckbox', { name: 'Regina' }).click();
    await page.keyboard.press('Escape');
    await expect(areaTrigger(page)).toHaveText(/Regina/);

    await page.getByRole('tab', { name: /sgi insurance billing/i }).click();
    await expect(page.getByText('$0.11/km', { exact: false })).toBeVisible();

    const [request] = await Promise.all([
      page.waitForRequest((r: any) => r.url().includes('/api/admin/compliance/insurance-billing-sgi')),
      page.getByRole('button', { name: /download/i }).click(),
    ]);
    expect(new URL(request.url()).searchParams.get('service_area_ids')).toBe('area-regina');
  });

  test('clearing the selection drops the param entirely rather than sending an empty one', async ({ page }) => {
    await mockCompliance(page);
    await page.goto('/dashboard/compliance');
    await expect(page.getByText('GST/PST Remittance Summary')).toBeVisible({ timeout: 20000 });

    await areaTrigger(page).click();
    await page.getByRole('menuitemcheckbox', { name: 'Saskatoon' }).click();
    await page.getByRole('menuitem', { name: /clear selection/i }).click();
    await expect(areaTrigger(page)).toHaveText(/All service areas/);

    const [request] = await Promise.all([
      page.waitForRequest((r: any) => r.url().includes('/api/admin/compliance/gst-pst-remittance')),
      page.getByRole('button', { name: /download/i }).first().click(),
    ]);
    expect(new URL(request.url()).searchParams.has('service_area_ids')).toBe(false);
  });

  test('a failed service-areas fetch says so instead of showing an empty dropdown', async ({ page }) => {
    await setupAdminMocks(page, {
      extra: async (route, url, method) => {
        if (url.includes('/api/admin/service-areas') && method === 'GET') {
          await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({}) });
          return 'handled';
        }
        return null;
      },
    });
    await page.goto('/dashboard/compliance');
    await expect(page.getByText('Compliance & Tax Reporting')).toBeVisible({ timeout: 20000 });
    // An empty list and a failed fetch look identical to an admin; the
    // second one would otherwise leave them believing no areas exist.
    await expect(page.getByText(/Could not load service areas/i)).toBeVisible();
  });
});
