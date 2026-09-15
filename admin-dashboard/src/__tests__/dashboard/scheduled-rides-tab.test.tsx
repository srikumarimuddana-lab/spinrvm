import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import ScheduledRidesTab from '@/app/dashboard/service-areas/_components/scheduled-rides-tab';
import { updateServiceArea } from '@/lib/api';
vi.mock('@/lib/api', () => ({updateServiceArea: vi.fn()}));
beforeEach(() => { vi.clearAllMocks(); });
const area = {id:'a1',name:'Regina'};
describe('Scheduled ride settings', () => {
  it('saves enabled area timing including zero matching lead', async () => {
    vi.mocked(updateServiceArea).mockResolvedValue({});
    const onSaved = vi.fn(); render(<ScheduledRidesTab area={area} onSaved={onSaved} />);
    fireEvent.click(screen.getByRole('checkbox'));
    fireEvent.change(screen.getByLabelText('Start matching before pickup (minutes)'), {target:{value:'0'}});
    fireEvent.click(screen.getByRole('button', {name:'Save scheduled ride settings'}));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(updateServiceArea).toHaveBeenCalledWith('a1',{scheduled_ride_config:{enabled:true,dispatch_lead_minutes:0,driver_reminder_minutes:10,rider_reminder_minutes:10}});
  });
  it('rejects blank minutes', async () => {
    render(<ScheduledRidesTab area={area} onSaved={vi.fn()} />);
    fireEvent.change(screen.getByLabelText('Driver reminder before pickup (minutes)'), {target:{value:''}});
    fireEvent.click(screen.getByRole('button', {name:'Save scheduled ride settings'}));
    expect(screen.getByRole('alert')).toBeTruthy(); expect(updateServiceArea).not.toHaveBeenCalled();
  });
  it('preserves edits and reports a failed save', async () => {
    vi.mocked(updateServiceArea).mockRejectedValue(new Error('network'));
    const onSaved = vi.fn(); render(<ScheduledRidesTab area={area} onSaved={onSaved} />);
    fireEvent.click(screen.getByRole('button', {name:'Save scheduled ride settings'}));
    expect(await screen.findByRole('alert')).toBeTruthy(); expect(onSaved).not.toHaveBeenCalled();
  });
  it('loads persisted area values', () => {
    render(<ScheduledRidesTab area={{...area,scheduled_ride_config:{enabled:true,driver_reminder_minutes:20}}} onSaved={vi.fn()} />);
    expect(screen.getByLabelText('Driver reminder before pickup (minutes)')).toHaveValue(20);
    expect(screen.getByRole('checkbox')).toBeChecked();
  });
});
