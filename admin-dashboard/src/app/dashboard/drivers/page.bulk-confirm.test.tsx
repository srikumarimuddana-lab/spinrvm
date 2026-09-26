// UX program W2.1: the Drivers page's "Fix Statement Totals" bulk action
// previews first, then rewrites stored money figures only after the in-app
// confirmation's "Rewrite totals" button. Cancel must never send the
// apply:true call. Mocking modelled on page.edit-lock.test.tsx.
import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const toastSpy = vi.hoisted(() => vi.fn());
const recompute = vi.hoisted(() => vi.fn());

vi.mock('@/hooks/useRequireModule', () => ({ useRequireModule: () => ({ allowed: true }) }));
vi.mock('@/hooks/useFeatureFlag', () => ({ useFeatureFlag: () => false }));
vi.mock('@/components/ui/use-toast', () => ({ useToast: () => ({ toast: toastSpy }) }));
vi.mock('@/store/authStore', () => ({
    useAuthStore: (selector: (state: unknown) => unknown) => selector({ user: { role: 'super_admin', modules: ['drivers'] } }),
}));
vi.mock('@/lib/api', async (importOriginal) => ({
    ...await importOriginal<Record<string, unknown>>(),
    getDrivers: vi.fn().mockResolvedValue([]),
    getDriverStats: vi.fn().mockResolvedValue({ service_areas: [] }),
    getServiceAreas: vi.fn().mockResolvedValue([]),
    getVehicleTypes: vi.fn().mockResolvedValue([]),
    getFareConfigs: vi.fn().mockResolvedValue([]),
    recomputeStatementTotals: recompute,
}));
vi.mock('./_components/document-reviewer', () => ({ DocumentReviewer: () => null }));
vi.mock('./_components/document-upload-dialog', () => ({ DocumentUploadDialog: () => null }));
vi.mock('./_components/driver-detail-sheet', () => ({ default: () => null }));
// Minimal stand-in for the list: exposes only the bulk action under test.
vi.mock('./_components/driver-list-table', () => ({
    default: (props: { handleRecomputeStatementTotals: () => void }) => (
        <button onClick={props.handleRecomputeStatementTotals}>Fix Statement Totals</button>
    ),
}));

import Page from './page';

async function openRewriteConfirm() {
    const user = userEvent.setup();
    render(<Page />);
    await user.click(await screen.findByRole('button', { name: 'Fix Statement Totals' }));
    await screen.findByRole('alertdialog');
    return user;
}

describe('Fix Statement Totals confirmation', () => {
    beforeEach(() => {
        toastSpy.mockClear();
        recompute.mockReset().mockImplementation(({ apply }: { apply: boolean }) => Promise.resolve(apply
            ? { corrected: 2, unchanged: 3, has_more: false, skipped: [] }
            : {
                corrected: 2, scanned: 5, delta_earnings: 1.5, delta_payouts: -2, has_more: false,
                changes: [{ period_type: 'weekly', period_start: '2026-09-01', before: { payouts_total: 10 }, after: { payouts_total: 8 } }],
            }));
    });

    it('previews but never applies on Cancel', async () => {
        const user = await openRewriteConfirm();
        expect(screen.getByRole('alertdialog')).toHaveAccessibleName('Rewrite stored totals for 2 of 5 statement(s)?');
        await user.click(screen.getByRole('button', { name: 'Cancel' }));
        await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull());
        expect(recompute).toHaveBeenCalledTimes(1);
        expect(recompute).toHaveBeenCalledWith({ apply: false });
    });

    it('applies after Rewrite totals', async () => {
        const user = await openRewriteConfirm();
        await user.click(screen.getByRole('button', { name: 'Rewrite totals' }));
        await waitFor(() => expect(recompute).toHaveBeenCalledWith({ apply: true }));
        expect(recompute).toHaveBeenCalledTimes(2);
    });
});
