// CONCURRENCY-001: PUT /admin/drivers/{id} now supports an optional
// `expected_updated_at` optimistic-concurrency check (backend/routes/admin/
// drivers.py admin_update_driver). These tests cover the admin-dashboard
// side: the edit form sends back the `updated_at` it was loaded with, and a
// 409 (someone else saved first) shows a reload-offering toast instead of
// silently retrying or leaving a stale form open. Modelled on the mocking
// pattern in page.export.test.tsx (same page, different feature).
import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

const DRIVER = vi.hoisted(() => ({
    id: 'drv-1', first_name: 'Alex', last_name: 'Doe', city: 'Saskatoon', updated_at: '2026-09-20T10:00:00+00:00',
}));
const toastSpy = vi.hoisted(() => vi.fn());

vi.mock('@/hooks/useRequireModule', () => ({ useRequireModule: () => ({ allowed: true }) }));
vi.mock('@/hooks/useFeatureFlag', () => ({ useFeatureFlag: () => false }));
vi.mock('@/components/ui/use-toast', () => ({ useToast: () => ({ toast: toastSpy }) }));
vi.mock('@/store/authStore', () => ({
    useAuthStore: (selector: (state: unknown) => unknown) => selector({ user: { role: 'super_admin', modules: ['drivers'] } }),
}));
vi.mock('@/lib/api', async (importOriginal) => ({
    ...await importOriginal<Record<string, unknown>>(),
    getDrivers: vi.fn().mockResolvedValue([DRIVER]),
    getDriverStats: vi.fn().mockResolvedValue({ service_areas: [] }),
    getServiceAreas: vi.fn().mockResolvedValue([]),
    getVehicleTypes: vi.fn().mockResolvedValue([]),
    getFareConfigs: vi.fn().mockResolvedValue([]),
    getDriverDocuments: vi.fn().mockResolvedValue([]),
    getDriverVehicleHistory: vi.fn().mockResolvedValue({ history: [] }),
    getDriverLiveStats: vi.fn().mockResolvedValue(null),
    getDriverPayoutsSummary: vi.fn().mockResolvedValue(null),
    updateDriver: vi.fn(),
}));
vi.mock('./_components/document-reviewer', () => ({ DocumentReviewer: () => null }));
vi.mock('./_components/document-upload-dialog', () => ({ DocumentUploadDialog: () => null }));
// Minimal stand-in for the list: exposes only what selects a driver.
vi.mock('./_components/driver-list-table', () => ({
    default: (props: { setSelected: (d: unknown) => void }) => (
        <button onClick={() => props.setSelected(DRIVER)}>Select driver</button>
    ),
}));
// Minimal stand-in for the detail sheet: exposes only the edit/save controls
// the real one wires to the same page.tsx state/callbacks (editing,
// startEditing, saveEdits, setEf) -- see driver-detail-sheet.tsx.
vi.mock('./_components/driver-detail-sheet', () => ({
    default: (props: {
        selected: any;
        editing: boolean;
        saving: boolean;
        startEditing: () => void;
        saveEdits: () => void;
        setEf: (field: string, value: string) => void;
    }) => {
        if (!props.selected) return null;
        return (
            <div>
                {!props.editing ? (
                    <button onClick={props.startEditing}>Edit</button>
                ) : (
                    <>
                        <button onClick={() => props.setEf('city', 'Regina')}>Change city</button>
                        <button onClick={props.saveEdits} disabled={props.saving}>Save</button>
                    </>
                )}
            </div>
        );
    },
}));

import Page from './page';
import { updateDriver, DriverConflictError, getDrivers } from '@/lib/api';

describe('driver edit form optimistic-lock (CONCURRENCY-001)', () => {
    it('sends the loaded updated_at and applies a normal save', async () => {
        vi.mocked(updateDriver).mockResolvedValueOnce({ message: 'ok', updated_fields: ['city'] });
        render(<Page />);
        fireEvent.click(await screen.findByText('Select driver'));
        fireEvent.click(await screen.findByText('Edit'));
        fireEvent.click(screen.getByText('Change city'));
        fireEvent.click(screen.getByText('Save'));
        await waitFor(() =>
            expect(updateDriver).toHaveBeenCalledWith(
                'drv-1',
                expect.objectContaining({ city: 'Regina', expected_updated_at: '2026-09-20T10:00:00+00:00' }),
            ),
        );
        // Back to the read-only view -- the save was accepted.
        await waitFor(() => expect(screen.getByText('Edit')).toBeInTheDocument());
    });

    it('shows a reload-offering toast and exits editing on a 409 conflict', async () => {
        vi.mocked(updateDriver).mockRejectedValueOnce(
            new DriverConflictError('This driver was changed by someone else. Reload and try again.'),
        );
        render(<Page />);
        fireEvent.click(await screen.findByText('Select driver'));
        fireEvent.click(await screen.findByText('Edit'));
        fireEvent.click(screen.getByText('Change city'));
        fireEvent.click(screen.getByText('Save'));

        await waitFor(() =>
            expect(toastSpy).toHaveBeenCalledWith(
                expect.objectContaining({
                    title: 'Driver changed by someone else',
                    description: 'This driver was changed by someone else. Reload and try again.',
                    variant: 'destructive',
                }),
            ),
        );
        // The stale edit form is discarded, not left open on data we know is wrong.
        await waitFor(() => expect(screen.getByText('Edit')).toBeInTheDocument());

        // The toast's action button reloads the driver list rather than
        // silently retrying the same (now-stale) save.
        const initialCalls = vi.mocked(getDrivers).mock.calls.length;
        const call = toastSpy.mock.calls.find((c) => c[0]?.title === 'Driver changed by someone else');
        const action = call?.[0]?.action as React.ReactElement<{ onClick: () => void }>;
        action.props.onClick();
        await waitFor(() => expect(vi.mocked(getDrivers).mock.calls.length).toBeGreaterThan(initialCalls));
    });
});
