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

async function mockCompliance(page: any) {
  await setupAdminMocks(page, {
    extra: async (route, url, method) => {
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
    await expect(page.getByText('Rate', { exact: false })).toHaveCount(0);
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
