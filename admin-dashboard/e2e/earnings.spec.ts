/**
 * Admin dashboard E2E — /dashboard/earnings (tabs), /dashboard/earnings/payouts,
 * and /dashboard/earnings/payouts/[id] interaction coverage. All network calls
 * mocked — no live backend.
 */
import { test, expect } from '@playwright/test';
import { setupAdminMocks } from './admin-mocks';

// MetricWithDelta helper — every metrics field in EarningsOverview /
// PayoutsOverview needs this exact shape or the page's %-delta rendering throws.
function m(current = 0) {
  return { current, previous: 0, delta_pct: null };
}

const MOCK_PERIOD = {
  key: '30d', label: 'Last 30 days', days: 30,
  start: '2026-06-27', end: '2026-07-27', prev_start: '2026-05-28', prev_end: '2026-06-27',
};

const MOCK_EARNINGS_OVERVIEW = {
  period: MOCK_PERIOD,
  metrics: {
    gbv: m(50000), net_revenue: m(5000), take_rate_pct: m(10), completed_trips: m(1200),
    active_riders: m(300), active_drivers: m(80), avg_fare: m(18.5), spinr_pass_mrr: m(400),
    cancellation_rate_pct: m(5), cancellation_revenue: m(100), cancelled_trips: m(60),
    refund_amount: m(50), refund_count: m(5), promo_spend: m(200), promo_count: m(20),
    surge_revenue: m(300), gst_collected: m(250), pst_collected: m(300),
  },
  cancellation_breakdown: {
    current: { rider: 30, driver: 20, system: 10 },
    previous: { rider: 25, driver: 15, system: 8 },
  },
  ride_funnel: {
    price_searches: m(2000), requested: m(1400), reached_searching: m(1300), completed: m(1200),
    rider_cancelled: m(30), driver_cancelled: m(20), system_cancelled: m(10), cancelled_after_start: m(5),
  },
  daily_series: [{ date: '2026-07-20', gbv: 1500, trips: 40, net_revenue: 150 }],
};

const MOCK_PAYOUTS_OVERVIEW = {
  period: MOCK_PERIOD,
  metrics: {
    outstanding_payable: m(1000), total_paid_out: m(40000), pending_in_flight: m(500),
    failed_amount: m(0), success_rate_pct: m(99), median_time_to_payout_hours: m(4),
    avg_payout_amount: m(85), payouts_count: m(470),
  },
  daily_series: [{ date: '2026-07-20', paid_out: 1500, pending: 100, failed: 0 }],
  failure_reasons: [],
  stuck_over_48h: { count: 0, amount: 0 },
  blocked_drivers: { count: 0, outstanding_balance: 0 },
  top_drivers: [],
  at_risk_drivers: [],
  t4a_snapshot: {
    tax_year: 2026, drivers_with_earnings: 80,
    buckets: { under_500: 20, from_500_to_10k: 40, from_10k_to_30k: 15, over_30k: 5 },
    ytd_gross_earnings: 500000,
  },
  period_locks: [],
};

const MOCK_PAYOUT = {
  id: 'payout_e2e_1',
  driver_id: 'driver_e2e_1',
  driver_name: 'John Driver',
  driver_email: 'john.driver@example.com',
  driver_phone: '+13065550111',
  amount: 245.5,
  status: 'completed',
  bank_name: 'RBC',
  account_last4: '1234',
  stripe_transfer_id: 'tr_e2e_1',
  created_at: '2026-07-20T10:00:00Z',
  processed_at: '2026-07-20T10:05:00Z',
  period_start: '2026-07-13',
  period_end: '2026-07-20',
};

async function mockEarnings(page: any) {
  await setupAdminMocks(page, {
    extra: async (route, url, method, json) => {
      if (url.includes('/earnings/overview')) return json(200, MOCK_EARNINGS_OVERVIEW);
      if (url.includes('/earnings/rides')) {
        // Export CSV is disabled when there are zero rows — give it one so
        // the button under test is actually clickable, matching real usage.
        return json(200, {
          rides: [{
            ride_id: 'ride_e2e_1', ride_code: 'SPR-1001', status: 'completed',
            total_fare: 18.5, driver_earnings: 14.8, admin_earnings: 3.7,
          }],
          total: 1,
        });
      }
      if (url.includes('/subscription-stats')) return json(200, {});
      if (url.includes('/payouts/overview')) return json(200, MOCK_PAYOUTS_OVERVIEW);
      if (url.includes('/payouts/stats')) return json(200, {});
      if (url.match(/\/payouts\/payout_e2e_1(?:[/?]|$)/)) return json(200, MOCK_PAYOUT);
      if (method === 'POST' && url.includes('/retry')) return json(200, { success: true });
      if (url.includes('/payouts')) return json(200, [MOCK_PAYOUT]);
      if (url.includes('/earnings')) return json(200, []);
      // getServiceAreas() returns a raw array — see lesson from drivers.spec.ts.
      if (url.includes('/service-areas')) return json(200, [{ id: 'saskatoon', name: 'Saskatoon' }]);
      return null;
    },
  });
}

test.describe('admin dashboard: earnings — interaction', () => {
  test('page loads on the Ride Earnings tab by default', async ({ page }) => {
    await mockEarnings(page);
    await page.goto('/dashboard/earnings');
    await expect(page.getByText('Earnings & Payouts')).toBeVisible({ timeout: 20000 });
    await expect(page.getByRole('button', { name: /ride earnings/i })).toBeVisible();
  });

  test('Spinr Pass tab switches without crashing', async ({ page }) => {
    await mockEarnings(page);
    await page.goto('/dashboard/earnings');
    await expect(page.getByText('Earnings & Payouts')).toBeVisible({ timeout: 20000 });
    await page.getByRole('button', { name: /spinr pass/i }).click();
    await expect(page.locator('body')).toBeVisible();
  });

  test('Payouts tab switches without crashing', async ({ page }) => {
    await mockEarnings(page);
    await page.goto('/dashboard/earnings');
    await expect(page.getByText('Earnings & Payouts')).toBeVisible({ timeout: 20000 });
    await page.getByRole('button', { name: /^payouts$/i }).click();
    await expect(page.locator('body')).toBeVisible();
  });

  test('date-range filters accept input', async ({ page }) => {
    await mockEarnings(page);
    await page.goto('/dashboard/earnings');
    const startDate = page.getByLabel('Start date').first();
    await expect(startDate).toBeVisible({ timeout: 20000 });
    await startDate.fill('2026-07-01');
    await expect(startDate).toHaveValue('2026-07-01');
  });

  test('Export CSV button is clickable', async ({ page }) => {
    await mockEarnings(page);
    await page.goto('/dashboard/earnings');
    const exportBtn = page.getByRole('button', { name: /export/i }).first();
    await expect(exportBtn).toBeVisible({ timeout: 20000 });
    await exportBtn.click();
    await expect(page.locator('body')).toBeVisible();
  });
});

test.describe('admin dashboard: earnings/payouts (standalone) — interaction', () => {
  test('page loads and renders payout row', async ({ page }) => {
    await mockEarnings(page);
    await page.goto('/dashboard/earnings/payouts');
    await expect(page.getByText('Payout Management')).toBeVisible({ timeout: 20000 });
    await expect(page.getByText('John Driver')).toBeVisible({ timeout: 10000 });
  });

  test('search input accepts typed text', async ({ page }) => {
    await mockEarnings(page);
    await page.goto('/dashboard/earnings/payouts');
    const search = page.getByPlaceholder(/Driver, payout ID, Stripe ID/i);
    await expect(search).toBeVisible({ timeout: 20000 });
    await search.fill('John');
    await expect(search).toHaveValue('John');
  });

  test('status filter dropdown is present', async ({ page }) => {
    await mockEarnings(page);
    await page.goto('/dashboard/earnings/payouts');
    // aria-label "Status" is ambiguous with the "Sort by Status" column
    // header button — target the combobox role specifically.
    await expect(page.getByRole('combobox', { name: 'Status' })).toBeVisible({ timeout: 20000 });
  });

  test('date filters accept input', async ({ page }) => {
    await mockEarnings(page);
    await page.goto('/dashboard/earnings/payouts');
    const startDate = page.getByLabel('Start date');
    await expect(startDate).toBeVisible({ timeout: 20000 });
    await startDate.fill('2026-07-01');
    await expect(startDate).toHaveValue('2026-07-01');
  });

  test('refresh button is clickable', async ({ page }) => {
    await mockEarnings(page);
    await page.goto('/dashboard/earnings/payouts');
    const refreshBtn = page.getByRole('button', { name: /refresh/i });
    await expect(refreshBtn).toBeVisible({ timeout: 20000 });
    await refreshBtn.click();
    await expect(page.locator('body')).toBeVisible();
  });
});

test.describe('admin dashboard: earnings/payouts/[id] — interaction', () => {
  test('detail page loads with driver name and amount', async ({ page }) => {
    await mockEarnings(page);
    await page.goto('/dashboard/earnings/payouts/payout_e2e_1');
    await expect(page.getByText('Payout Detail')).toBeVisible({ timeout: 20000 });
    await expect(page.getByText('John Driver')).toBeVisible({ timeout: 10000 });
  });

  test('refresh button is clickable', async ({ page }) => {
    await mockEarnings(page);
    await page.goto('/dashboard/earnings/payouts/payout_e2e_1');
    await expect(page.getByText('John Driver')).toBeVisible({ timeout: 20000 });
    const refreshBtn = page.getByRole('button', { name: /refresh/i });
    await expect(refreshBtn).toBeVisible();
    await refreshBtn.click();
    await expect(page.locator('body')).toBeVisible();
  });
});
