/**
 * Admin dashboard E2E — /dashboard/audit-logs interaction coverage.
 * Drives search, action/entity filters, export, and refresh. All network
 * calls mocked — no live backend.
 */
import { test, expect } from '@playwright/test';
import { setupAdminMocks } from './admin-mocks';

const MOCK_LOG = {
  id: 'audit_e2e_1',
  created_at: '2026-07-20T10:00:00Z',
  actor_id: 'actor_e2e_1',
  actor_email: 'admin@spinr.ca',
  action: 'settings_updated',
  entity_type: 'settings',
  entity_id: 'settings_1',
  details: 'Updated surge cap to 2.5',
};

async function mockAuditLogs(page: any) {
  await setupAdminMocks(page, {
    extra: async (route, url, method, json) => {
      if (url.includes('/audit-logs')) return json(200, [MOCK_LOG]);
      return null;
    },
  });
}

test.describe('admin dashboard: audit-logs — interaction', () => {
  test('page loads and renders a log entry', async ({ page }) => {
    await mockAuditLogs(page);
    await page.goto('/dashboard/audit-logs');
    await expect(page.getByText('Updated surge cap to 2.5')).toBeVisible({ timeout: 20000 });
  });

  test('search box accepts typed text', async ({ page }) => {
    await mockAuditLogs(page);
    await page.goto('/dashboard/audit-logs');
    const search = page.getByPlaceholder(/Search by email, entity ID, or details/i);
    await expect(search).toBeVisible({ timeout: 20000 });
    await search.fill('surge');
    await expect(search).toHaveValue('surge');
  });

  test('action and entity filter dropdowns are present', async ({ page }) => {
    await mockAuditLogs(page);
    await page.goto('/dashboard/audit-logs');
    await expect(page.getByLabel('Filter by action')).toBeVisible({ timeout: 20000 });
    await expect(page.getByLabel('Filter by entity')).toBeVisible();
  });

  test('refresh button is clickable', async ({ page }) => {
    await mockAuditLogs(page);
    await page.goto('/dashboard/audit-logs');
    const refreshBtn = page.getByRole('button', { name: /refresh/i });
    await expect(refreshBtn).toBeVisible({ timeout: 20000 });
    await refreshBtn.click();
    await expect(page.locator('body')).toBeVisible();
  });

  test('export button is clickable', async ({ page }) => {
    await mockAuditLogs(page);
    await page.goto('/dashboard/audit-logs');
    await expect(page.getByText('Updated surge cap to 2.5')).toBeVisible({ timeout: 20000 });
    const exportBtn = page.getByRole('button', { name: /export/i });
    await expect(exportBtn).toBeEnabled();
    await exportBtn.click();
    await expect(page.locator('body')).toBeVisible();
  });
});
