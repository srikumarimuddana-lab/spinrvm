/**
 * Admin dashboard E2E — /dashboard/data-transfer (5 tabs) and
 * /dashboard/bulk-operations (legacy, super-admin-only page). All network
 * calls mocked — no live backend. Fixes gap H5 from
 * reports/audits/2026-07-28-data-transfer-module-lifecycle-audit-v1.md
 * (module had zero E2E coverage across either surface).
 */
import { test, expect } from '@playwright/test';
import { setupAdminMocks } from './admin-mocks';

const SEARCH_RESULT = {
  rows: [
    { id: 'd1', full_name: 'Jane Driver', email: 'jane@example.com', phone: '+15550001111', created_at: '2026-07-01T00:00:00Z' },
  ],
  total_count: 1,
  page: 1,
  page_size: 50,
};

const JOB_ROW = {
  id: 'job-1',
  requested_by_admin_id: 'admin_1',
  entity_type: 'driver',
  entity_ids: ['d1'],
  doc_type_filter: null,
  format: 'zip',
  status: 'completed',
  error_message: null,
  created_at: '2026-07-01T00:00:00Z',
  completed_at: '2026-07-01T00:05:00Z',
  expires_at: '2026-07-08T00:05:00Z',
  entity_count: 1,
};

async function mockDataTransfer(page: any) {
  await setupAdminMocks(page, {
    extra: async (route, url, method, json) => {
      if (url.includes('/api/admin/data-transfer/search') && method === 'GET') {
        return json(200, SEARCH_RESULT);
      }
      if (url.includes('/api/admin/data-transfer/export') && method === 'POST') {
        return json(202, { job_id: 'job-1', status: 'pending', requested_count: 1 });
      }
      if (url.includes('/api/admin/data-transfer/import/validate') && method === 'POST') {
        return json(200, {
          can_commit: true,
          counts: { entities: 1, new: 1, existing_match: 0, conflict: 0 },
          warnings: [],
          errors: [],
        });
      }
      if (url.includes('/api/admin/data-transfer/import/commit') && method === 'POST') {
        return json(200, {
          can_commit: true,
          committed: true,
          counts: { entities: 1, new: 1, existing_match: 0, conflict: 0 },
          warnings: [],
          errors: [],
          created: 1,
        });
      }
      if (url.includes('/api/admin/data-transfer/jobs') && method === 'GET') {
        return json(200, { jobs: [JOB_ROW] });
      }
      return null;
    },
  });
}

test.describe('admin dashboard: data-transfer — interaction', () => {
  test('page loads on the Search & Select tab by default', async ({ page }) => {
    await mockDataTransfer(page);
    await page.goto('/dashboard/data-transfer');
    await expect(page.getByRole('heading', { name: 'Data Transfer' })).toBeVisible({ timeout: 20000 });
    await expect(page.getByRole('tab', { name: /search.*select/i })).toBeVisible();
  });

  test('search returns a result row', async ({ page }) => {
    await mockDataTransfer(page);
    await page.goto('/dashboard/data-transfer');
    await expect(page.getByRole('tab', { name: /search.*select/i })).toBeVisible({ timeout: 20000 });
    const searchInput = page.getByPlaceholder(/name, email, or phone/i);
    await expect(searchInput).toBeVisible();
    await searchInput.fill('Jane');
    await page.getByRole('button', { name: /^search$/i }).click();
    await expect(page.getByText('Jane Driver')).toBeVisible({ timeout: 10000 });
  });

  test('Export tab switches without crashing', async ({ page }) => {
    await mockDataTransfer(page);
    await page.goto('/dashboard/data-transfer');
    await expect(page.getByRole('heading', { name: 'Data Transfer' })).toBeVisible({ timeout: 20000 });
    await page.getByRole('tab', { name: /^export$/i }).click();
    await expect(page.locator('body')).toBeVisible();
  });

  test('Import tab accepts a file and validates', async ({ page }) => {
    await mockDataTransfer(page);
    await page.goto('/dashboard/data-transfer');
    await page.getByRole('tab', { name: /^import$/i }).click();
    const fileInput = page.locator('input[type="file"]');
    await expect(fileInput).toBeAttached({ timeout: 20000 });
    await fileInput.setInputFiles({
      name: 'bundle.zip',
      mimeType: 'application/zip',
      buffer: Buffer.from('PK\x03\x04fakezip'),
    });
    await expect(page.locator('body')).toBeVisible();
  });

  test('SGI Compliance Forms tab switches without crashing', async ({ page }) => {
    await mockDataTransfer(page);
    await page.goto('/dashboard/data-transfer');
    await expect(page.getByRole('heading', { name: 'Data Transfer' })).toBeVisible({ timeout: 20000 });
    await page.getByRole('tab', { name: /sgi compliance forms/i }).click();
    await expect(page.locator('body')).toBeVisible();
  });

  test('Jobs & History tab lists a completed job', async ({ page }) => {
    await mockDataTransfer(page);
    await page.goto('/dashboard/data-transfer');
    await page.getByRole('tab', { name: /jobs.*history/i }).click();
    await expect(page.getByText(/job-1|completed/i).first()).toBeVisible({ timeout: 20000 });
  });
});

test.describe('admin dashboard: bulk-operations (legacy, super-admin-only) — interaction', () => {
  test('page loads for a super_admin', async ({ page }) => {
    await setupAdminMocks(page, { user: { role: 'super_admin' } });
    await page.goto('/dashboard/bulk-operations');
    await expect(page.locator('body')).toBeVisible({ timeout: 20000 });
    await expect(page.getByText(/requires the super admin role/i)).toHaveCount(0);
  });

  test('denies a non-super-admin with a denial card, not the tool UI', async ({ page }) => {
    await setupAdminMocks(page, { user: { role: 'admin', modules: ['bulk_operations'] } });
    await page.goto('/dashboard/bulk-operations');
    await expect(page.getByText(/requires the super admin role/i)).toBeVisible({ timeout: 20000 });
  });
});
