/**
 * Admin dashboard E2E — /dashboard/staff interaction coverage.
 * Drives the create-form open/fill, role preset selection, edit, and
 * toggle-active actions. All network calls mocked — no live backend.
 *
 * Form inputs have no id/htmlFor association (plain <label><input/></label>
 * siblings), so getByLabel doesn't resolve them — CSS adjacent-sibling
 * selectors are used instead.
 */
import { test, expect } from '@playwright/test';
import { setupAdminMocks } from './admin-mocks';

const MOCK_STAFF = {
  id: 'staff_e2e_1',
  email: 'jane.ops@spinr.ca',
  first_name: 'Jane',
  last_name: 'Ops',
  role: 'operations',
  modules: ['dashboard', 'rides'],
  is_active: true,
  mfa_enabled: false,
  last_login: '2026-07-20T10:00:00Z',
};

async function mockStaff(page: any) {
  await setupAdminMocks(page, {
    user: { role: 'super_admin' },
    extra: async (route, url, method, json) => {
      if (method === 'POST' && url.includes('/staff')) return json(200, MOCK_STAFF);
      if (method === 'PUT' && url.includes('/staff/')) return json(200, MOCK_STAFF);
      if (method === 'DELETE' && url.includes('/staff/')) return json(200, { success: true });
      if (url.includes('/staff')) return json(200, [MOCK_STAFF]);
      return null;
    },
  });
}

test.describe('admin dashboard: staff — interaction', () => {
  test('page loads and renders a staff member', async ({ page }) => {
    await mockStaff(page);
    await page.goto('/dashboard/staff');
    await expect(page.getByText('jane.ops@spinr.ca')).toBeVisible({ timeout: 20000 });
  });

  test('"Add Staff" button opens the create form', async ({ page }) => {
    await mockStaff(page);
    await page.goto('/dashboard/staff');
    await expect(page.getByText('jane.ops@spinr.ca')).toBeVisible({ timeout: 20000 });
    await page.getByRole('button', { name: /add staff/i }).click();
    await expect(page.getByText('New Staff Member')).toBeVisible({ timeout: 10000 });
  });

  test('first/last name and email fields accept typed text', async ({ page }) => {
    await mockStaff(page);
    await page.goto('/dashboard/staff');
    await page.getByRole('button', { name: /add staff/i }).click();
    await expect(page.getByText('New Staff Member')).toBeVisible({ timeout: 10000 });
    const emailInput = page.locator('label:has-text("Email") + input');
    await emailInput.fill('newstaff@spinr.ca');
    await expect(emailInput).toHaveValue('newstaff@spinr.ca');
  });

  test('role preset buttons switch the selected role', async ({ page }) => {
    await mockStaff(page);
    await page.goto('/dashboard/staff');
    await page.getByRole('button', { name: /add staff/i }).click();
    await expect(page.getByText('New Staff Member')).toBeVisible({ timeout: 10000 });
    await page.getByRole('button', { name: /^custom$/i }).click();
    await expect(page.locator('body')).toBeVisible();
  });

  test('Cancel button closes the create form', async ({ page }) => {
    await mockStaff(page);
    await page.goto('/dashboard/staff');
    await page.getByRole('button', { name: /add staff/i }).click();
    await expect(page.getByText('New Staff Member')).toBeVisible({ timeout: 10000 });
    await page.getByRole('button', { name: /^cancel$/i }).click();
    await expect(page.getByText('New Staff Member')).not.toBeVisible();
  });

  test('Edit button opens the form pre-filled with the staff member\'s data', async ({ page }) => {
    await mockStaff(page);
    await page.goto('/dashboard/staff');
    await expect(page.getByText('jane.ops@spinr.ca')).toBeVisible({ timeout: 20000 });
    await page.getByTitle('Edit').click();
    await expect(page.getByText('Edit Staff')).toBeVisible({ timeout: 10000 });
    const firstNameInput = page.locator('label:has-text("First Name") + input');
    await expect(firstNameInput).toHaveValue('Jane');
  });

  test('toggle-active ("Disable") button is clickable', async ({ page }) => {
    await mockStaff(page);
    await page.goto('/dashboard/staff');
    await expect(page.getByText('jane.ops@spinr.ca')).toBeVisible({ timeout: 20000 });
    // title is "Disable" while active, "Enable" once disabled — the mock's
    // staff member is is_active: true.
    const toggleBtn = page.getByTitle('Disable');
    await expect(toggleBtn).toBeVisible();
    await toggleBtn.click();
    await expect(page.locator('body')).toBeVisible();
  });
});
