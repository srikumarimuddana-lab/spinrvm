/**
 * Query-string contract for the Compliance page's page-level Service Area
 * multi-select (admin-dashboard/src/lib/api/data-transfer.ts).
 *
 * The distinction under test is not cosmetic. An omitted `service_area_ids`
 * makes the backend take its "every area" path and issue exactly the query
 * it issued before this filter existed; an empty-string param would be
 * parsed, found blank, and take the same path — but only by luck, and the
 * audit row would then record a filter that was never applied. So the param
 * must be absent, not empty.
 *
 * Runs against the real download helpers with fetch stubbed, so it covers
 * the actual URL each one builds rather than a re-implementation of it.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('@/store/authStore', () => ({
    useAuthStore: { getState: () => ({ token: 'test-token' }) },
}));

import {
    downloadAirportTrips,
    downloadDriverRoster,
    downloadGstPstRemittance,
    downloadInsuranceBillingKnightArcher,
    downloadInsuranceBillingSgi,
    downloadT4aFilerHandoff,
} from '@/lib/api/data-transfer';

let lastUrl = '';

beforeEach(() => {
    lastUrl = '';
    vi.stubGlobal(
        'fetch',
        vi.fn(async (url: string) => {
            lastUrl = url;
            return {
                ok: true,
                status: 200,
                headers: new Headers({ 'content-type': 'application/pdf' }),
                blob: async () => new Blob([new Uint8Array([0x25, 0x50, 0x44, 0x46])]),
            } as unknown as Response;
        }),
    );
});

const params = () => new URL(lastUrl, 'http://localhost').searchParams;

/** Every Compliance report that accepts a service-area scope, paired with a
 *  caller that passes the ids through in that report's own argument order. */
const SCOPED_REPORTS: [string, (ids?: string[]) => Promise<unknown>][] = [
    ['gst-pst-remittance', (ids) => downloadGstPstRemittance('pdf', '2026-07-01', '2026-07-31', ids)],
    ['driver-roster', (ids) => downloadDriverRoster('pdf', undefined, ids)],
    ['insurance-billing-sgi', (ids) => downloadInsuranceBillingSgi('pdf', '2026-07-01', '2026-07-31', ids)],
    [
        'insurance-billing-knight-archer',
        (ids) => downloadInsuranceBillingKnightArcher('pdf', '2026-07-01', '2026-07-31', ids),
    ],
    ['airport-trips', (ids) => downloadAirportTrips('pdf', '2026-07-01', '2026-07-31', ids)],
];

describe('compliance downloads — service area scope', () => {
    it.each(SCOPED_REPORTS)('%s sends the selected areas comma-separated', async (path, call) => {
        await call(['a1', 'a2']);
        expect(lastUrl).toContain(`/api/admin/compliance/${path}?`);
        expect(params().get('service_area_ids')).toBe('a1,a2');
    });

    it.each(SCOPED_REPORTS)('%s omits the param entirely when nothing is selected', async (_path, call) => {
        await call([]);
        expect(params().has('service_area_ids')).toBe(false);
    });

    it.each(SCOPED_REPORTS)('%s omits the param entirely when the arg is undefined', async (_path, call) => {
        await call(undefined);
        expect(params().has('service_area_ids')).toBe(false);
    });

    it('keeps the other query params intact alongside the scope', async () => {
        await downloadGstPstRemittance('csv', '2026-07-01', '2026-07-31', ['a1']);
        expect(params().get('format')).toBe('csv');
        expect(params().get('date_from')).toBe('2026-07-01');
        expect(params().get('date_to')).toBe('2026-07-31');
        expect(params().get('service_area_ids')).toBe('a1');
    });

    it('driver roster keeps its status filter alongside the scope', async () => {
        await downloadDriverRoster('pdf', 'active', ['a1']);
        expect(params().get('status')).toBe('active');
        expect(params().get('service_area_ids')).toBe('a1');
    });

    it('T4A filer handoff has no service-area scope at all', async () => {
        // Deliberate: a T4A / Part XX.1 return is per-driver and Canada-wide,
        // so an area-scoped slice is never a valid filing. The helper takes no
        // such argument — this asserts the URL it builds carries none either.
        await downloadT4aFilerHandoff(2025, 'xlsx');
        expect(params().has('service_area_ids')).toBe(false);
        expect(params().get('year')).toBe('2025');
    });
});
