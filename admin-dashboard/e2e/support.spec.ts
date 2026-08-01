/**
 * Admin dashboard E2E — /dashboard/support interaction coverage.
 * The page is a 7-tab shell (Tickets/Disputes/Complaints/Lost & Found/
 * Flags/FAQs/Legal) each lazy-loaded via next/dynamic; drives the default
 * Tickets tab's search/create/reply flow plus tab-switch smoke checks for
 * the rest. All network calls mocked — no live backend.
 */
import { test, expect } from '@playwright/test';
import { setupAdminMocks } from './admin-mocks';

const MOCK_TICKET = {
  id: 'ticket_e2e_1',
  subject: 'App crashes on login',
  category: 'general',
  priority: 'medium',
  status: 'open',
  message: 'The app crashes every time I try to log in.',
  user_name: 'Jane Rider',
  user_email: 'jane@example.com',
  created_at: '2026-07-20T10:00:00Z',
};

async function mockSupport(page: any) {
  await setupAdminMocks(page, {
    extra: async (route, url, method, json) => {
      if (method === 'POST' && url.includes('/tickets') && !url.includes('/reply') && !url.includes('/close')) {
        return json(200, MOCK_TICKET);
      }
      if (method === 'POST' && url.includes('/reply')) return json(200, { success: true });
      if (method === 'POST' && url.includes('/close')) return json(200, { success: true });
      if (url.includes('/tickets')) return json(200, [MOCK_TICKET]);
      if (url.includes('/disputes')) return json(200, []);
      if (url.includes('/complaints')) return json(200, []);
      if (url.includes('/lost-and-found') || url.includes('/lost-found')) return json(200, []);
      if (url.includes('/flags')) return json(200, []);
      if (url.includes('/faqs')) return json(200, []);
      if (url.includes('/legal')) return json(200, []);
      return null;
    },
  });
}

test.describe('admin dashboard: support — interaction', () => {
  test('page loads on the Tickets tab with a ticket listed', async ({ page }) => {
    await mockSupport(page);
    await page.goto('/dashboard/support');
    await expect(page.getByRole('heading', { name: 'Support & Issues' })).toBeVisible({ timeout: 20000 });
    await expect(page.getByText('App crashes on login')).toBeVisible({ timeout: 10000 });
  });

  test('search box accepts typed text', async ({ page }) => {
    await mockSupport(page);
    await page.goto('/dashboard/support');
    await expect(page.getByText('App crashes on login')).toBeVisible({ timeout: 20000 });
    const search = page.getByPlaceholder('Search...');
    await search.fill('crash');
    await expect(search).toHaveValue('crash');
  });

  test('clicking a ticket row opens the reply panel', async ({ page }) => {
    await mockSupport(page);
    await page.goto('/dashboard/support');
    await expect(page.getByText('App crashes on login')).toBeVisible({ timeout: 20000 });
    await page.getByText('App crashes on login').click();
    await expect(page.getByPlaceholder('Type a reply...')).toBeVisible({ timeout: 10000 });
  });

  test('"Create Ticket" button opens the create form', async ({ page }) => {
    await mockSupport(page);
    await page.goto('/dashboard/support');
    await expect(page.getByText('App crashes on login')).toBeVisible({ timeout: 20000 });
    await page.getByRole('button', { name: /create ticket/i }).click();
    await expect(page.getByPlaceholder('Issue title')).toBeVisible({ timeout: 10000 });
  });

  for (const tab of ['Disputes', 'Complaints', 'Lost & Found', 'Flags', 'FAQs', 'Legal']) {
    test(`${tab} tab switches without crashing`, async ({ page }) => {
      await mockSupport(page);
      await page.goto('/dashboard/support');
      await expect(page.getByRole('heading', { name: 'Support & Issues' })).toBeVisible({ timeout: 20000 });
      // The default Tickets tab renders its own nested "Tickets | FAQs" sub-tab bar
      // (_tabs/tickets.tsx), so a page-wide getByRole('button', { name: 'FAQs' })
      // matches two buttons and trips Playwright strict mode. Scope to the outer
      // tab bar via a label only it renders — deliberately not `.first()`, which
      // would pass only by DOM ordering and silently re-break if the bars moved.
      const pageTabBar = page.getByRole('button', { name: 'Lost & Found', exact: true }).locator('..');
      await pageTabBar.getByRole('button', { name: tab, exact: true }).click();
      await expect(page.locator('body')).toBeVisible({ timeout: 10000 });
    });
  }
});
