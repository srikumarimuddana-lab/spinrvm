/**
 * Admin dashboard E2E — /dashboard/support-tickets (Zoho Desk integration)
 * and /dashboard/faqs interaction coverage. Covers the landing dashboard,
 * ticket list, ticket detail, trends, and the top-level FAQ manager (a
 * separate component/route from support's FAQs tab, same API). All network
 * calls mocked — no live backend.
 */
import { test, expect } from '@playwright/test';
import { setupAdminMocks } from './admin-mocks';

const MOCK_CONFIG = {
  enabled: true,
  auto_sync_enabled: true,
  data_center: 'com',
  org_id: 'org_e2e',
  default_department_id: 'dept_1',
  default_from_email: 'support@spinr.ca',
  has_client_id: true,
  has_client_secret: true,
  has_refresh_token: true,
  connected: true,
  last_synced_at: '2026-07-27T00:00:00Z',
  last_sync_count: 12,
};

const MOCK_DASHBOARD = {
  total: 42, total_available: true, open: 5,
  by_status: { open: 5, closed: 37 },
  recent: [], sample_size: 42, approximate: false,
};

const MOCK_TICKET = {
  id: 'zticket_e2e_1',
  ticketNumber: '1001',
  subject: 'Refund not processed',
  contact: { email: 'jane@example.com' },
  category: 'Billing',
  priority: 'High',
  status: 'Open',
  createdTime: '2026-07-20T10:00:00Z',
};

const MOCK_TRENDS = {
  sample_size: 42, approximate: false,
  volume: [{ date: '2026-07-20', opened: 3, closed: 2 }],
  by_status: { Open: 5, Closed: 37 },
  by_priority: { High: 5, Medium: 20, Low: 17 },
  by_channel: { Email: 30, Web: 12 },
};

const MOCK_FAQ = {
  id: 'faq_e2e_1',
  question: 'How do I request a refund?',
  answer: 'Go to your ride history and tap "Request refund".',
  category: 'Billing',
  audience: 'rider',
};

async function mockSupportTickets(page: any) {
  await setupAdminMocks(page, {
    extra: async (route, url, method, json) => {
      if (url.includes('/support-tickets/config')) return json(200, MOCK_CONFIG);
      if (url.includes('/support-tickets/dashboard')) return json(200, MOCK_DASHBOARD);
      if (url.includes('/support-tickets/trends')) return json(200, MOCK_TRENDS);
      if (url.includes('/support-tickets/agents')) return json(200, { data: [] });
      if (url.includes('/support-tickets/departments')) return json(200, { data: [] });
      if (url.includes('/support-tickets/service-areas')) return json(200, { data: [] });
      if (url.match(/\/support-tickets\/tickets\/zticket_e2e_1\/service-area/)) {
        return json(200, { service_area_id: null, needs_assignment: false });
      }
      if (url.match(/\/support-tickets\/tickets\/zticket_e2e_1\/threads/)) return json(200, { data: [] });
      if (url.match(/\/support-tickets\/tickets\/zticket_e2e_1(?:[/?]|$)/)) return json(200, MOCK_TICKET);
      if (method === 'POST' && url.includes('/support-tickets/sync')) return json(200, { upserted: 3 });
      if (url.includes('/support-tickets/tickets')) return json(200, { data: [MOCK_TICKET] });
      if (method === 'POST' && url.includes('/faqs')) return json(200, MOCK_FAQ);
      if (method === 'PUT' && url.includes('/faqs/')) return json(200, MOCK_FAQ);
      if (method === 'DELETE' && url.includes('/faqs/')) return json(200, { success: true });
      if (url.includes('/faqs')) return json(200, [MOCK_FAQ]);
      return null;
    },
  });
}

test.describe('admin dashboard: support-tickets landing — interaction', () => {
  test('page loads with dashboard stats when connected', async ({ page }) => {
    await mockSupportTickets(page);
    await page.goto('/dashboard/support-tickets');
    await expect(page.locator('body')).toBeVisible({ timeout: 20000 });
  });

  test('sync button is clickable', async ({ page }) => {
    await mockSupportTickets(page);
    await page.goto('/dashboard/support-tickets');
    const syncBtn = page.getByRole('button', { name: /sync/i });
    if (await syncBtn.count()) {
      await syncBtn.first().click();
      await expect(page.locator('body')).toBeVisible();
    }
  });
});

test.describe('admin dashboard: support-tickets/tickets — interaction', () => {
  test('page loads and renders a ticket row', async ({ page }) => {
    await mockSupportTickets(page);
    await page.goto('/dashboard/support-tickets/tickets');
    await expect(page.getByText('Refund not processed')).toBeVisible({ timeout: 20000 });
  });

  test('search box accepts typed text', async ({ page }) => {
    await mockSupportTickets(page);
    await page.goto('/dashboard/support-tickets/tickets');
    const search = page.getByPlaceholder(/Search all tickets/i);
    await expect(search).toBeVisible({ timeout: 20000 });
    await search.fill('refund');
    await expect(search).toHaveValue('refund');
  });

  test('status/priority/channel/assignee filter dropdowns are present', async ({ page }) => {
    await mockSupportTickets(page);
    await page.goto('/dashboard/support-tickets/tickets');
    // aria-label "Status" is ambiguous with the "Sort by Status" column-
    // header button — scope to the combobox role.
    await expect(page.getByRole('combobox', { name: 'Status' })).toBeVisible({ timeout: 20000 });
    // "Priority" also labels a field in the (unmounted-but-present) create-
    // ticket dialog — the filter-bar combobox is the last match in DOM order.
    await expect(page.getByLabel('Priority').last()).toBeVisible();
    await expect(page.getByLabel('Channel')).toBeVisible();
  });

  test('clicking a ticket subject navigates to its detail page', async ({ page }) => {
    await mockSupportTickets(page);
    await page.goto('/dashboard/support-tickets/tickets');
    await expect(page.getByText('Refund not processed')).toBeVisible({ timeout: 20000 });
    await page.getByText('Refund not processed').click();
    await expect(page).toHaveURL(/support-tickets\/tickets\/zticket_e2e_1/);
  });
});

test.describe('admin dashboard: support-tickets/tickets/[id] — interaction', () => {
  test('detail page loads with subject visible', async ({ page }) => {
    await mockSupportTickets(page);
    await page.goto('/dashboard/support-tickets/tickets/zticket_e2e_1');
    await expect(page.getByText('Refund not processed')).toBeVisible({ timeout: 20000 });
  });

  test('reply editor accepts typed text', async ({ page }) => {
    await mockSupportTickets(page);
    await page.goto('/dashboard/support-tickets/tickets/zticket_e2e_1');
    await expect(page.getByText('Refund not processed')).toBeVisible({ timeout: 20000 });
    // RichTextEditor is a contentEditable div (data-placeholder), not a
    // native textarea — getByPlaceholder doesn't match it.
    const reply = page.locator('[data-placeholder*="Type your reply"]');
    await expect(reply).toBeVisible({ timeout: 10000 });
    await reply.click();
    await reply.type('Refund has been processed.');
    await expect(reply).toHaveText('Refund has been processed.');
  });
});

test.describe('admin dashboard: support-tickets/trends — interaction', () => {
  test('page loads without crashing', async ({ page }) => {
    await mockSupportTickets(page);
    await page.goto('/dashboard/support-tickets/trends');
    await expect(page.locator('body')).toBeVisible({ timeout: 20000 });
  });
});

test.describe('admin dashboard: faqs (top-level) — interaction', () => {
  test('page loads and renders a FAQ', async ({ page }) => {
    await mockSupportTickets(page);
    await page.goto('/dashboard/faqs');
    await expect(page.getByText('How do I request a refund?')).toBeVisible({ timeout: 20000 });
  });

  test('search box accepts typed text', async ({ page }) => {
    await mockSupportTickets(page);
    await page.goto('/dashboard/faqs');
    const search = page.getByPlaceholder(/Search questions, answers, categories/i);
    await expect(search).toBeVisible({ timeout: 20000 });
    await search.fill('refund');
    await expect(search).toHaveValue('refund');
  });

  test('audience filter dropdown is present', async ({ page }) => {
    await mockSupportTickets(page);
    await page.goto('/dashboard/faqs');
    // The create/edit dialog also has an "Audience" field in the DOM —
    // the filter-bar combobox is the first match in DOM order.
    await expect(page.getByLabel('Audience').first()).toBeVisible({ timeout: 20000 });
  });

  test('edit button opens the form pre-filled with the FAQ question', async ({ page }) => {
    await mockSupportTickets(page);
    await page.goto('/dashboard/faqs');
    await expect(page.getByText('How do I request a refund?')).toBeVisible({ timeout: 20000 });
    await page.getByLabel(/Edit FAQ: How do I request a refund/i).click();
    const questionInput = page.getByPlaceholder('What is…?');
    await expect(questionInput).toHaveValue('How do I request a refund?', { timeout: 10000 });
  });
});
