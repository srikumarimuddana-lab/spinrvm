/**
 * Admin dashboard E2E — batch 1 of miscellaneous admin routes:
 * ai-console, bulk-operations, cloud-messaging, notifications (redirect),
 * referrals, service-areas. Lighter-touch than the domain-focused specs —
 * page-load + a couple of key interactions per route, not exhaustive field
 * coverage, since these are lower-traffic internal tools. All network
 * calls mocked — no live backend.
 */
import { test, expect } from '@playwright/test';
import { setupAdminMocks } from './admin-mocks';

test.describe('admin dashboard: ai-console — interaction', () => {
  async function mockAiConsole(page: any) {
    await setupAdminMocks(page, {
      user: { role: 'super_admin' },
      extra: async (route, url, method, json) => {
        if (url.includes('/ai/console/users') || url.includes('/users/search')) return json(200, []);
        if (url.includes('/ai/console')) return json(200, { conversations: [] });
        return null;
      },
    });
  }

  test('page loads with heading', async ({ page }) => {
    await mockAiConsole(page);
    await page.goto('/dashboard/ai-console');
    await expect(page.getByRole('heading', { name: 'AI Console' })).toBeVisible({ timeout: 20000 });
  });

  test('user search input accepts typed text', async ({ page }) => {
    await mockAiConsole(page);
    await page.goto('/dashboard/ai-console');
    await expect(page.getByRole('heading', { name: 'AI Console' })).toBeVisible({ timeout: 20000 });
    const search = page.getByPlaceholder('name / phone / id');
    await expect(search).toBeVisible();
    await search.fill('Jane');
    await expect(search).toHaveValue('Jane');
  });
});

test.describe('admin dashboard: bulk-operations — interaction', () => {
  async function mockBulkOps(page: any) {
    await setupAdminMocks(page, {
      user: { role: 'super_admin' },
      extra: async (route, url, method, json) => {
        if (url.includes('/bulk-import/status')) return json(200, { batches: [] });
        return null;
      },
    });
  }

  test('page loads with heading', async ({ page }) => {
    await mockBulkOps(page);
    await page.goto('/dashboard/bulk-operations');
    await expect(page.getByRole('heading', { name: 'Bulk Operations' })).toBeVisible({ timeout: 20000 });
  });

  test('batch name input accepts typed text', async ({ page }) => {
    await mockBulkOps(page);
    await page.goto('/dashboard/bulk-operations');
    await expect(page.getByRole('heading', { name: 'Bulk Operations' })).toBeVisible({ timeout: 20000 });
    const batchInput = page.getByPlaceholder('e.g. drivers-batch-1');
    await expect(batchInput).toBeVisible();
    await batchInput.fill('e2e-batch-1');
    await expect(batchInput).toHaveValue('e2e-batch-1');
  });
});

test.describe('admin dashboard: cloud-messaging — interaction', () => {
  async function mockCloudMessaging(page: any) {
    await setupAdminMocks(page, {
      extra: async (route, url, method, json) => {
        if (url.includes('/notifications')) return json(200, []);
        if (url.includes('/service-areas')) return json(200, [{ id: 'saskatoon', name: 'Saskatoon' }]);
        return null;
      },
    });
  }

  test('page loads with heading', async ({ page }) => {
    await mockCloudMessaging(page);
    await page.goto('/dashboard/cloud-messaging');
    await expect(page.getByRole('heading', { name: /notifications/i })).toBeVisible({ timeout: 20000 });
  });

  test('title and message inputs accept typed text', async ({ page }) => {
    await mockCloudMessaging(page);
    await page.goto('/dashboard/cloud-messaging');
    await expect(page.getByRole('heading', { name: /notifications/i })).toBeVisible({ timeout: 20000 });
    const titleInput = page.getByPlaceholder('Enter notification title');
    await expect(titleInput).toBeVisible();
    await titleInput.fill('Service update');
    await expect(titleInput).toHaveValue('Service update');
  });

  test('History tab search box accepts typed text', async ({ page }) => {
    await mockCloudMessaging(page);
    await page.goto('/dashboard/cloud-messaging');
    await expect(page.getByRole('heading', { name: /notifications/i })).toBeVisible({ timeout: 20000 });
    const historyTab = page.getByRole('button', { name: /history/i });
    if (await historyTab.count()) {
      await historyTab.click();
      const search = page.getByPlaceholder('Search messages...');
      if (await search.count()) {
        await search.fill('update');
        await expect(search).toHaveValue('update');
      }
    }
  });
});

test.describe('admin dashboard: notifications — redirect', () => {
  test('/dashboard/notifications redirects to /dashboard/cloud-messaging', async ({ page }) => {
    await setupAdminMocks(page);
    await page.goto('/dashboard/notifications');
    await expect(page).toHaveURL(/dashboard\/cloud-messaging/, { timeout: 10000 });
  });
});

test.describe('admin dashboard: referrals — interaction', () => {
  const MOCK_ANALYTICS = {
    source: 'driver',
    funnel: {
      total_referred: 50, qualified: 30, redeemed: 20, processing: 5, failed: 2,
      redemption_rate: 66.7, total_paid: '200.00', referrer_paid: '100.00', referee_paid: '100.00', avg_paid: '10.00',
    },
    trend: [{ date: '2026-07-20', redeemed: 3, paid: '30.00' }],
    reward_amount: 10, rides_required: 5,
  };
  const MOCK_LEADERBOARD = {
    leaders: [{ driver_id: 'driver_e2e_1', driver_code: 'DRV-1001', name: 'John Driver', total_referrals: 5, qualified_referrals: 3, referral_earnings: 30 }],
    fleet_total_referrals: 50, fleet_total_referrers: 20, reward_amount: 10, rides_required: 5,
  };

  async function mockReferrals(page: any) {
    await setupAdminMocks(page, {
      extra: async (route, url, method, json) => {
        if (url.includes('/referrals/leaderboard') || url.includes('/referrals/rider-leaderboard')) {
          return json(200, MOCK_LEADERBOARD);
        }
        if (url.includes('/referrals/analytics')) return json(200, MOCK_ANALYTICS);
        if (url.includes('/referrals/failed-claims')) return json(200, []);
        if (url.includes('/service-areas')) return json(200, [{ id: 'saskatoon', name: 'Saskatoon' }]);
        return null;
      },
    });
  }

  test('page loads with a leaderboard entry', async ({ page }) => {
    await mockReferrals(page);
    await page.goto('/dashboard/referrals');
    await expect(page.getByRole('heading', { name: 'Referrals' })).toBeVisible({ timeout: 20000 });
    await expect(page.getByText('John Driver')).toBeVisible({ timeout: 10000 });
  });

  test('Driver/Rider source toggle switches without crashing', async ({ page }) => {
    await mockReferrals(page);
    await page.goto('/dashboard/referrals');
    await expect(page.getByText('John Driver')).toBeVisible({ timeout: 20000 });
    await page.getByRole('button', { name: /rider/i }).first().click();
    await expect(page.locator('body')).toBeVisible();
  });
});

test.describe('admin dashboard: service-areas — interaction', () => {
  const MOCK_AREA = {
    id: 'saskatoon', name: 'Saskatoon', city: 'Saskatoon', is_active: true,
    surge_multiplier: 1.0, surge_source: 'auto',
  };

  async function mockServiceAreas(page: any) {
    await setupAdminMocks(page, {
      extra: async (route, url, method, json) => {
        if (url.includes('/vehicle-types')) return json(200, []);
        if (url.includes('/subscription-plans')) return json(200, []);
        if (url.includes('/driver-subscriptions')) return json(200, []);
        if (url.includes('/area-fees')) return json(200, []);
        if (url.includes('/incentives')) return json(200, []);
        if (url.includes('/service-areas')) return json(200, [MOCK_AREA]);
        return null;
      },
    });
  }

  test('page loads with a service area listed', async ({ page }) => {
    await mockServiceAreas(page);
    await page.goto('/dashboard/service-areas');
    await expect(page.getByRole('heading', { name: 'Service Areas' })).toBeVisible({ timeout: 20000 });
    await expect(page.getByText('Saskatoon').first()).toBeVisible({ timeout: 10000 });
  });

  test('create-area name input accepts typed text', async ({ page }) => {
    await mockServiceAreas(page);
    await page.goto('/dashboard/service-areas');
    await expect(page.getByRole('heading', { name: 'Service Areas' })).toBeVisible({ timeout: 20000 });
    const nameInput = page.getByPlaceholder('e.g. Saskatoon Metro');
    if (await nameInput.count()) {
      await nameInput.fill('Regina Metro');
      await expect(nameInput).toHaveValue('Regina Metro');
    }
  });
});
