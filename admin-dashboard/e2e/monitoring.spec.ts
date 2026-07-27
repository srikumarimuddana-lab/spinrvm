/**
 * Admin dashboard E2E — /dashboard/monitoring (live map) and
 * /dashboard/monitoring/redis interaction coverage. The monitoring page
 * also opens a real WebSocket (useMonitoringSocket) which fails to connect
 * in this mocked-network setup — that's expected and handled gracefully
 * by the page (shows a "Live data paused" banner, doesn't crash). All
 * HTTP calls mocked — no live backend.
 */
import { test, expect } from '@playwright/test';
import { setupAdminMocks } from './admin-mocks';

const MOCK_RIDE = {
  id: 'ride_e2e_1',
  status: 'searching',
  pickup_address: '123 Main St, Saskatoon',
  dropoff_address: '456 Broadway Ave, Saskatoon',
  rider_name: 'Jane Rider',
  driver_name: null,
  total_fare: 18.5,
  lat: 52.13,
  lng: -106.67,
};

async function mockMonitoring(page: any) {
  await setupAdminMocks(page, {
    extra: async (route, url, method, json) => {
      if (url.includes('/monitoring/drivers')) return json(200, []);
      if (url.includes('/monitoring/rides')) return json(200, [MOCK_RIDE]);
      if (url.includes('/fare-configs')) return json(200, []);
      if (url.includes('/service-areas')) return json(200, [{ id: 'saskatoon', name: 'Saskatoon' }]);
      if (url.includes('/vehicle-types')) return json(200, [{ id: 'standard', name: 'Standard' }]);
      return null;
    },
  });
}

test.describe('admin dashboard: monitoring (live map) — interaction', () => {
  test('page loads and renders an active ride', async ({ page }) => {
    await mockMonitoring(page);
    await page.goto('/dashboard/monitoring');
    await expect(page.getByText('Jane Rider')).toBeVisible({ timeout: 20000 });
  });

  test('search input accepts typed text', async ({ page }) => {
    await mockMonitoring(page);
    await page.goto('/dashboard/monitoring');
    const search = page.getByPlaceholder('Driver name or ride ID');
    await expect(search).toBeVisible({ timeout: 20000 });
    await search.fill('Jane');
    await expect(search).toHaveValue('Jane');
  });

  test('service area and vehicle type filters are present', async ({ page }) => {
    await mockMonitoring(page);
    await page.goto('/dashboard/monitoring');
    await expect(page.getByLabel('Filter by service area')).toBeVisible({ timeout: 20000 });
    await expect(page.getByLabel('Filter by vehicle type')).toBeVisible();
  });

  test('clicking a ride opens its detail panel with a Close button', async ({ page }) => {
    await mockMonitoring(page);
    await page.goto('/dashboard/monitoring');
    await expect(page.getByText('Jane Rider')).toBeVisible({ timeout: 20000 });
    await page.getByText('Jane Rider').click();
    await expect(page.getByLabel('Close ride panel')).toBeVisible({ timeout: 10000 });
  });
});

test.describe('admin dashboard: monitoring/redis — interaction', () => {
  const MOCK_HEALTH = {
    stats: { total_keys: 100, hit_rate_percent: 95, connected_clients: 3 },
    prefix_counts: [{ prefix: 'spinr:ws:', count: 10, description: 'WebSocket presence', flushable: true }],
    flushable_prefixes: ['spinr:ws:'],
  };
  const MOCK_WS_HEALTH = {
    fanout: { active: true, channel: 'spinr:ws:dispatch', backend_scheme: 'redis', configured: true, last_error: null },
    connections: { total: 5, admins: 1, drivers: 3, riders: 1 },
    replica_hostname: 'e2e-host', worker_pid: 1, workers_hint: 1, per_worker: false,
  };
  const MOCK_CONNECTIVITY = { connectivity: [{ label: 'primary', configured: true, status: 'ok', ping_ms: 2 }] };
  const MOCK_INFRA = {
    replica: { hostname: 'e2e-host', pid: 1, uptime_seconds: 3600, python_version: '3.12' },
    process: { rss_bytes: 1000000, rss_human: '1 MB', cpu_user_seconds: 1, cpu_system_seconds: 1 },
    thread_pool: { max_workers: 10, note: 'ok' },
    db_circuit_breaker: { state: 'closed', recent_failures: 0, opened_at_monotonic: null },
    redis: { connected: true, used_memory_bytes: 1000, used_memory_human: '1 KB', maxmemory_human: '100 MB', used_memory_percent: 1, total_keys: 100, evicted_keys_total: 0 },
    metrics: {},
  };

  async function mockRedis(page: any) {
    await setupAdminMocks(page, {
      extra: async (route, url, method, json) => {
        if (url.includes('/monitoring/redis/connectivity')) return json(200, MOCK_CONNECTIVITY);
        if (url.includes('/monitoring/redis')) return json(200, MOCK_HEALTH);
        if (url.includes('/monitoring/websockets')) return json(200, MOCK_WS_HEALTH);
        if (url.includes('/monitoring/infrastructure')) return json(200, MOCK_INFRA);
        return null;
      },
    });
  }

  test('page loads with Redis & Infrastructure heading', async ({ page }) => {
    await mockRedis(page);
    await page.goto('/dashboard/monitoring/redis');
    await expect(page.getByText('Redis & Infrastructure')).toBeVisible({ timeout: 20000 });
  });

  test('refresh button is clickable', async ({ page }) => {
    await mockRedis(page);
    await page.goto('/dashboard/monitoring/redis');
    const refreshBtn = page.getByRole('button', { name: /refresh/i });
    await expect(refreshBtn).toBeVisible({ timeout: 20000 });
    await refreshBtn.click();
    await expect(page.locator('body')).toBeVisible();
  });
});

test.describe('admin dashboard: surge — redirect', () => {
  test('/dashboard/surge redirects to /dashboard/service-areas', async ({ page }) => {
    await setupAdminMocks(page, {
      extra: async (route, url, method, json) => {
        if (url.includes('/service-areas')) return json(200, []);
        return null;
      },
    });
    await page.goto('/dashboard/surge');
    await expect(page).toHaveURL(/dashboard\/service-areas/, { timeout: 10000 });
  });
});
