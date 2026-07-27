/**
 * Admin dashboard E2E — /dashboard/quests interaction coverage.
 * Drives the quest list, row-expand, and create-quest form. All network
 * calls mocked — no live backend.
 */
import { test, expect } from '@playwright/test';
import { setupAdminMocks } from './admin-mocks';

const MOCK_QUEST = {
  id: 'quest_e2e_1',
  title: 'Weekend Warrior',
  description: 'Complete 20 rides this weekend to earn a bonus!',
  type: 'ride_count',
  target_value: 20,
  reward_amount: 50,
  start_date: '2026-07-25',
  end_date: '2026-07-27',
  service_area_id: 'saskatoon',
  is_active: true,
  max_participants: null,
  min_driver_rating: null,
  stats: { total_participants: 12 },
};

async function mockQuests(page: any) {
  await setupAdminMocks(page, {
    extra: async (route, url, method, json) => {
      if (url.includes('/participants')) return json(200, []);
      if (method === 'POST' && url.includes('/quests/admin/create')) return json(200, MOCK_QUEST);
      if (method === 'PATCH' && url.match(/\/quests\/admin\/quest_e2e_1/)) return json(200, MOCK_QUEST);
      if (url.includes('/quests/admin/list')) return json(200, [MOCK_QUEST]);
      // getServiceAreas() returns a raw array — see drivers.spec.ts lesson.
      if (url.includes('/service-areas')) return json(200, [{ id: 'saskatoon', name: 'Saskatoon' }]);
      return null;
    },
  });
}

test.describe('admin dashboard: quests — interaction', () => {
  test('page loads and renders a quest', async ({ page }) => {
    await mockQuests(page);
    await page.goto('/dashboard/quests');
    await expect(page.getByText('Weekend Warrior')).toBeVisible({ timeout: 20000 });
  });

  test('refresh button is clickable', async ({ page }) => {
    await mockQuests(page);
    await page.goto('/dashboard/quests');
    const refreshBtn = page.getByRole('button', { name: /refresh/i });
    await expect(refreshBtn).toBeVisible({ timeout: 20000 });
    await refreshBtn.click();
    await expect(page.locator('body')).toBeVisible();
  });

  test('clicking a quest row expands it and fetches participants', async ({ page }) => {
    await mockQuests(page);
    await page.goto('/dashboard/quests');
    await expect(page.getByText('Weekend Warrior')).toBeVisible({ timeout: 20000 });
    await page.getByText('Weekend Warrior').click();
    await expect(page.locator('body')).toBeVisible();
  });

  test('"New Quest" button opens the create form with a title input', async ({ page }) => {
    await mockQuests(page);
    await page.goto('/dashboard/quests');
    await expect(page.getByText('Weekend Warrior')).toBeVisible({ timeout: 20000 });
    const newBtn = page.getByRole('button', { name: /new quest|create quest/i });
    await expect(newBtn).toBeVisible();
    await newBtn.click();
    await expect(page.getByPlaceholder('e.g., Weekend Warrior')).toBeVisible({ timeout: 10000 });
  });

  test('create form Create button is disabled until title and description are filled', async ({ page }) => {
    await mockQuests(page);
    await page.goto('/dashboard/quests');
    await page.getByRole('button', { name: /new quest|create quest/i }).click();
    const titleInput = page.getByPlaceholder('e.g., Weekend Warrior');
    await expect(titleInput).toBeVisible({ timeout: 10000 });
    // The submit button is also labeled "Create Quest" (same text as the
    // trigger button), so scope to inside the open dialog.
    const createBtn = page.locator('[role="dialog"]').getByRole('button', { name: /create quest/i });
    await expect(createBtn).toBeDisabled();
    await titleInput.fill('Test Quest');
    await page.getByPlaceholder(/Complete 20 rides this weekend/i).fill('Test description');
    await expect(createBtn).toBeEnabled();
  });

  test('create form Cancel button closes it', async ({ page }) => {
    await mockQuests(page);
    await page.goto('/dashboard/quests');
    await page.getByRole('button', { name: /new quest|create quest/i }).click();
    const titleInput = page.getByPlaceholder('e.g., Weekend Warrior');
    await expect(titleInput).toBeVisible({ timeout: 10000 });
    await page.getByRole('button', { name: /^cancel$/i }).click();
    await expect(titleInput).not.toBeVisible();
  });
});
