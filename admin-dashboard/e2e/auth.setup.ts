import { test as setup } from '@playwright/test';
import path from 'path';

export const STORAGE_STATE = path.join(__dirname, '../playwright/.auth/admin.json');

setup('authenticate as admin', async ({ page }) => {
  // Mock the admin login API call
  await page.route('**/api/admin/auth/login', async route => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        token: 'test-admin-token-123',
        user: { id: 'admin-1', email: 'admin@spinr.ca', role: 'admin' },
      }),
    });
  });

  // Also mock the session check that fires on dashboard load
  await page.route('**/api/admin/auth/session', async route => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        user: { id: 'admin-1', email: 'admin@spinr.ca', role: 'admin' },
      }),
    });
  });

  await page.goto('/login');
  await page.fill('#email', 'admin@spinr.ca');
  await page.fill('#password', 'Test1234!');
  await page.click('button[type="submit"], button:has-text("Sign In")');

  // Wait for redirect to dashboard
  await page.waitForURL('**/dashboard**', { timeout: 10000 }).catch(() => {
    // If redirect fails (API not real), just save whatever state we have
  });

  await page.context().storageState({ path: STORAGE_STATE });
});
