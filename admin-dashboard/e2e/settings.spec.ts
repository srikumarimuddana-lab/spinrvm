/**
 * Admin dashboard E2E — /dashboard/settings interaction coverage.
 * Drives tab switching (Integrations/Email & Alerts/Operations/Company &
 * Apps/Security) and the Save button. getSettings() returns request<any>,
 * so an empty object is a valid, safe mock — every field read uses ?? or
 * optional chaining. All network calls mocked — no live backend.
 */
import { test, expect } from '@playwright/test';
import { setupAdminMocks } from './admin-mocks';

async function mockSettings(page: any) {
  await setupAdminMocks(page, {
    extra: async (route, url, method, json) => {
      if (url.includes('/ai/catalog')) return json(200, { providers: [] });
      if (url.includes('/auth/mfa/status')) return json(200, { mfa_enabled: false, available: true });
      if (method === 'PUT' && url.includes('/settings')) return json(200, { message: 'Saved' });
      if (url.includes('/settings')) return json(200, {});
      return null;
    },
  });
}

test.describe('admin dashboard: settings — interaction', () => {
  test('page loads on the Integrations tab', async ({ page }) => {
    await mockSettings(page);
    await page.goto('/dashboard/settings');
    await expect(page.getByText('Settings').first()).toBeVisible({ timeout: 20000 });
    await expect(page.getByRole('tab', { name: /integrations/i })).toBeVisible();
  });

  test('Email & Alerts tab switches without crashing', async ({ page }) => {
    await mockSettings(page);
    await page.goto('/dashboard/settings');
    await page.getByRole('tab', { name: /email.*alerts/i }).click();
    await expect(page.locator('body')).toBeVisible();
  });

  test('Operations tab switches without crashing', async ({ page }) => {
    await mockSettings(page);
    await page.goto('/dashboard/settings');
    await page.getByRole('tab', { name: /operations/i }).click();
    await expect(page.locator('body')).toBeVisible();
  });

  test('Company & Apps tab switches without crashing', async ({ page }) => {
    await mockSettings(page);
    await page.goto('/dashboard/settings');
    await page.getByRole('tab', { name: /company.*apps/i }).click();
    await expect(page.locator('body')).toBeVisible();
  });

  test('Security tab switches without crashing', async ({ page }) => {
    await mockSettings(page);
    await page.goto('/dashboard/settings');
    await page.getByRole('tab', { name: /security/i }).click();
    await expect(page.locator('body')).toBeVisible();
  });

  test('"Enable AI assistant" toggle is present and clickable', async ({ page }) => {
    await mockSettings(page);
    await page.goto('/dashboard/settings');
    const toggle = page.getByLabel('Enable AI assistant');
    await expect(toggle).toBeVisible({ timeout: 20000 });
    await toggle.click();
    await expect(page.locator('body')).toBeVisible();
  });

  test('Save button is clickable', async ({ page }) => {
    await mockSettings(page);
    await page.goto('/dashboard/settings');
    const saveBtn = page.getByRole('button', { name: /^save$|save changes/i });
    await expect(saveBtn).toBeVisible({ timeout: 20000 });
    await saveBtn.click();
    await expect(page.locator('body')).toBeVisible();
  });
});
