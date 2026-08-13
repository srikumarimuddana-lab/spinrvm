import { test, expect, type Page } from '@playwright/test';
import { setupAdminMocks } from './admin-mocks';

/**
 * Pickup Venues table + map editor.
 *
 * The venue seed (migrations 307/308/309) ships every row inactive, so an admin
 * verifying ~40 dark venues depends on three things working: filtering by
 * status, seeing how far each pickup point sits from its venue centre, and
 * being told when the map is too degraded to judge that by eye.
 */

const VENUES = [
    {
        id: 'v1', name: 'Midtown Plaza (Saskatoon)', service_area_id: 'saskatoon',
        center_lat: 52.123, center_lng: -106.6585, radius_m: 250, is_active: true,
        pickup_points: [
            { name: '1st Avenue entrance', lat: 52.1233, lng: -106.658 },
            { name: '2nd Avenue entrance', lat: 52.1227, lng: -106.6592 },
            // ~700m out — well beyond the 250m radius, so it can never be offered.
            { name: 'Overflow lot (too far)', lat: 52.118, lng: -106.665 },
        ],
    },
    {
        id: 'v2', name: 'Royal University Hospital', service_area_id: 'saskatoon',
        center_lat: 52.13069, center_lng: -106.6408, radius_m: 300, is_active: false,
        pickup_points: [{ name: 'Emergency department entrance', lat: 52.1302, lng: -106.6412 }],
    },
    {
        id: 'v3', name: 'Preston Crossing', service_area_id: 'saskatoon',
        center_lat: 52.14883, center_lng: -106.61854, radius_m: 350, is_active: false, pickup_points: [],
    },
    {
        id: 'v4', name: 'SaskTel Centre', service_area_id: 'saskatoon',
        center_lat: 52.189, center_lng: -106.679, radius_m: 400, is_active: false, pickup_points: [],
    },
];

// The delete button carries an aria-label of `Delete <venue name>`, so a bare
// name matches both the venue cell and the actions cell — every locator here is
// exact for that reason.
const cell = (page: Page, name: string) => page.getByRole('cell', { name, exact: true });

async function mockVenues(page: Page) {
    await setupAdminMocks(page, {
        extra: async (route: any, url: string, method: string, json: any) => {
            if (url.includes('/service-areas')) return json(200, [{ id: 'saskatoon', name: 'Saskatoon' }]);
            // getVenues() returns a wrapped { venues: [...] } shape.
            if (url.includes('/venues')) return json(200, { venues: VENUES });
            return null;
        },
    });
}

test.describe('admin dashboard: pickup venues', () => {
    test('status filter narrows the list to active or inactive', async ({ page }) => {
        await mockVenues(page);
        await page.goto('/dashboard/venues');
        await expect(cell(page, 'SaskTel Centre')).toBeVisible({ timeout: 20000 });

        await page.getByLabel('Filter by status').selectOption('active');
        await expect(cell(page, 'Midtown Plaza (Saskatoon)')).toBeVisible();
        await expect(cell(page, 'SaskTel Centre')).toBeHidden();

        await page.getByLabel('Filter by status').selectOption('inactive');
        await expect(cell(page, 'Midtown Plaza (Saskatoon)')).toBeHidden();
        await expect(cell(page, 'SaskTel Centre')).toBeVisible();

        await page.getByLabel('Filter by status').selectOption('all');
        await expect(cell(page, 'Midtown Plaza (Saskatoon)')).toBeVisible();
        await expect(cell(page, 'SaskTel Centre')).toBeVisible();
    });

    test('search filters by venue name', async ({ page }) => {
        await mockVenues(page);
        await page.goto('/dashboard/venues');
        await expect(cell(page, 'SaskTel Centre')).toBeVisible({ timeout: 20000 });

        await page.getByLabel('Search venues').fill('hospital');
        await expect(cell(page, 'Royal University Hospital')).toBeVisible();
        await expect(cell(page, 'SaskTel Centre')).toBeHidden();
    });

    test('sorting by radius reorders the rows', async ({ page }) => {
        await mockVenues(page);
        await page.goto('/dashboard/venues');
        await expect(cell(page, 'SaskTel Centre')).toBeVisible({ timeout: 20000 });

        await page.getByRole('button', { name: /Sort by Radius/i }).click();
        await expect(page.locator('tbody tr').first()).toContainText('250 m');
        await page.getByRole('button', { name: /Sort by Radius/i }).click();
        await expect(page.locator('tbody tr').first()).toContainText('400 m');
    });

    test('editor flags a pickup point outside the detection radius', async ({ page }) => {
        await mockVenues(page);
        await page.goto('/dashboard/venues');
        await cell(page, 'Midtown Plaza (Saskatoon)').click();
        await expect(page.getByPlaceholder('Cornwall Centre')).toBeVisible({ timeout: 10000 });

        // A point the rider could never be offered must say so, not just show a number.
        await expect(page.getByText(/m outside/i)).toBeVisible();
    });

    test('editor discloses a degraded basemap instead of rendering a partial map', async ({ page }) => {
        // Forced, not incidental: without this the assertion would only pass on a
        // network that happens to block the tile host, and would fail in CI.
        await page.route('**/tiles.openfreemap.org/**', (route) => route.abort());
        await mockVenues(page);
        await page.goto('/dashboard/venues');
        await cell(page, 'Midtown Plaza (Saskatoon)').click();
        await expect(page.getByPlaceholder('Cornwall Centre')).toBeVisible({ timeout: 10000 });

        // The radius circle is a style layer, so it silently vanishes when the
        // style fails — the admin has to be told the view is not trustworthy.
        await expect(page.getByText(/Base map tiles failed to load/i)).toBeVisible({ timeout: 15000 });
    });
});
