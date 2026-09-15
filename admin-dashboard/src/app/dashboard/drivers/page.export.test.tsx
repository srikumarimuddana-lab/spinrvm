import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('@/hooks/useRequireModule', () => ({ useRequireModule: () => ({ allowed: true }) }));
vi.mock('@/hooks/useFeatureFlag', () => ({ useFeatureFlag: () => false }));
vi.mock('@/components/ui/use-toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));
vi.mock('@/store/authStore', () => ({
  useAuthStore: (selector: (state: unknown) => unknown) => selector({ user: { role: 'admin', modules: ['drivers'] } }),
}));
vi.mock('@/lib/api', async (importOriginal) => ({
  ...await importOriginal<Record<string, unknown>>(),
  getDrivers: vi.fn().mockResolvedValue([]),
  getDriverStats: vi.fn().mockResolvedValue({ service_areas: [] }),
  getServiceAreas: vi.fn().mockResolvedValue([]),
  getVehicleTypes: vi.fn().mockResolvedValue([]),
  getFareConfigs: vi.fn().mockResolvedValue([]),
  exportDrivers: vi.fn().mockResolvedValue({ drivers: [{ id: 'matching-driver' }], count: 1 }),
}));
vi.mock('@/lib/export-csv', () => ({ exportToCsv: vi.fn() }));
vi.mock('./_components/driver-detail-sheet', () => ({ default: () => null }));
vi.mock('./_components/document-reviewer', () => ({ DocumentReviewer: () => null }));
vi.mock('./_components/document-upload-dialog', () => ({ DocumentUploadDialog: () => null }));
// Drive the real page's filter callbacks; layout belongs to the browser spec.
vi.mock('./_components/driver-list-table', () => ({
  default: (props: React.ComponentProps<typeof import('./_components/driver-list-table').default>) => (
    <>
      <input aria-label="Search drivers" value={props.search} onChange={e => props.setSearch(e.target.value)} />
      <button onClick={() => { props.setStatusFilter('active'); props.setServiceAreaId('saskatoon'); }}>Select filters</button>
      <button onClick={() => props.setPage(2)}>Next page</button>
      <button onClick={props.handleExport}>Export</button>
      <button onClick={() => { props.setStatusFilter('all'); props.setServiceAreaId(''); }}>Clear filters</button>
    </>
  ),
}));

import Page from './page';
import { exportDrivers, getDrivers } from '@/lib/api';
import { exportToCsv } from '@/lib/export-csv';

describe('driver page export selection', () => {
  it('uses selected list filters across pages and clears them on the next export', async () => {
    render(<Page />);
    fireEvent.click(screen.getByText('Select filters'));
    await waitFor(() => expect(getDrivers).toHaveBeenLastCalledWith(expect.objectContaining({
      status: 'active', service_area_id: 'saskatoon', onboarding_complete: true,
    })));
    fireEvent.click(screen.getByText('Next page'));
    await waitFor(() => expect(getDrivers).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 100 })));
    fireEvent.click(screen.getByText('Export'));
    await waitFor(() => expect(exportDrivers).toHaveBeenLastCalledWith({
      status: 'active', service_area_id: 'saskatoon', onboarding_complete: true,
      sort_by: 'created_at', sort_dir: 'desc',
    }));
    expect(exportToCsv).toHaveBeenCalledWith('drivers', [{ id: 'matching-driver' }], expect.any(Array));

    // Export uses current input even during the list's 300 ms search debounce.
    fireEvent.change(screen.getByLabelText('Search drivers'), { target: { value: ' Alex ' } });
    fireEvent.click(screen.getByText('Export'));
    await waitFor(() => expect(exportDrivers).toHaveBeenLastCalledWith(expect.objectContaining({ search: 'Alex' })));
    await waitFor(() => expect(getDrivers).toHaveBeenLastCalledWith(expect.objectContaining({ search: 'Alex' })));
    fireEvent.change(screen.getByLabelText('Search drivers'), { target: { value: '' } });
    fireEvent.click(screen.getByText('Export'));
    await waitFor(() => expect(vi.mocked(exportDrivers).mock.lastCall?.[0]).not.toHaveProperty('search'));

    fireEvent.click(screen.getByText('Clear filters'));
    fireEvent.click(screen.getByText('Export'));
    await waitFor(() => expect(exportDrivers).toHaveBeenLastCalledWith({
      onboarding_complete: true, sort_by: 'created_at', sort_dir: 'desc',
    }));
  });
});
