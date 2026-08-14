/**
 * DriverStatementsPanel — sent-statement ledger + custom date-range
 * download / email-to-driver, inside the driver Payouts tab.
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const getDriverStatements = vi.fn();
const downloadDriverStatement = vi.fn();
const emailDriverStatement = vi.fn();

vi.mock('@/lib/api', () => ({
  getDriverStatements: (...a: unknown[]) => getDriverStatements(...a),
  downloadDriverStatement: (...a: unknown[]) => downloadDriverStatement(...a),
  emailDriverStatement: (...a: unknown[]) => emailDriverStatement(...a),
}));

// ---------- stub UI primitives (avoid Radix portal / next internals) ----------
vi.mock('@/components/ui/button', () => ({
  Button: ({ children, ...p }: React.PropsWithChildren<React.ButtonHTMLAttributes<HTMLButtonElement>>) => (
    <button {...p}>{children}</button>
  ),
}));
vi.mock('@/components/ui/input', () => ({
  Input: (p: React.InputHTMLAttributes<HTMLInputElement>) => <input {...p} />,
}));
vi.mock('@/components/ui/label', () => ({
  Label: ({ children, ...p }: React.PropsWithChildren<React.LabelHTMLAttributes<HTMLLabelElement>>) => (
    <label {...p}>{children}</label>
  ),
}));
// Factory is hoisted above the file body — define the stub inline, not as a
// top-level const (vi.mock cannot close over one).
//
// The panel itself only imports Download/Mail/FileText/RefreshCw, but it
// renders the real (unstubbed) shared <Select> for the page-size picker,
// and select.tsx imports CheckIcon/ChevronDownIcon/ChevronUpIcon directly
// from lucide-react — those need to be here too, or vitest's mock throws
// on the first missing export the moment SelectTrigger renders.
vi.mock('lucide-react', () => ({
  Download: () => <span />,
  Mail: () => <span />,
  FileText: () => <span />,
  RefreshCw: () => <span />,
  CheckIcon: () => <span />,
  ChevronDownIcon: () => <span />,
  ChevronUpIcon: () => <span />,
}));

import { DriverStatementsPanel } from './driver-statements-panel';

const SENT_STATEMENT = {
  id: 'stmt-weekly-2026-07-20-drv-1',
  period_type: 'weekly' as const,
  period_start: '2026-07-20',
  period_end: '2026-07-26',
  status: 'sent',
  totals: { earnings: { total: '224.90' }, payouts_total: '150.00', trips: 3 },
  email_sent_at: '2026-07-27T12:00:00+00:00',
  created_at: '2026-07-27T12:00:00+00:00',
};

const notify = vi.fn();

function renderPanel() {
  return render(
    <DriverStatementsPanel driverId="drv-1" driverName="Test Driver" notify={notify} />,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  getDriverStatements.mockResolvedValue({ statements: [SENT_STATEMENT] });
  downloadDriverStatement.mockResolvedValue({ blob: new Blob(['x']), filename: 'stmt.pdf' });
  emailDriverStatement.mockResolvedValue({ sent: true, period_label: 'Jul 20 - 26, 2026' });
  // jsdom has no object-URL implementation.
  global.URL.createObjectURL = vi.fn(() => 'blob:mock');
  global.URL.revokeObjectURL = vi.fn();
});

describe('DriverStatementsPanel', () => {
  it('lists sent statements with totals', async () => {
    renderPanel();
    expect(await screen.findByText(/Weekly · 2026-07-20 → 2026-07-26/)).toBeInTheDocument();
    expect(screen.getByText('Emailed')).toBeInTheDocument();
    expect(screen.getByText(/\$224\.90 earned · \$150\.00 paid out/)).toBeInTheDocument();
    expect(getDriverStatements).toHaveBeenCalledWith('drv-1');
  });

  it('downloads a sent statement by its anchored period', async () => {
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: 'PDF' }));
    await waitFor(() =>
      expect(downloadDriverStatement).toHaveBeenCalledWith('drv-1', {
        period_type: 'weekly',
        period_start: '2026-07-20',
      }),
    );
  });

  it('re-sends a sent statement to the driver and reloads the list', async () => {
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: /Resend/ }));
    await waitFor(() =>
      expect(emailDriverStatement).toHaveBeenCalledWith('drv-1', {
        period_type: 'weekly',
        period_start: '2026-07-20',
      }),
    );
    await waitFor(() => expect(getDriverStatements).toHaveBeenCalledTimes(2));
    expect(notify).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'Statement sent' }),
    );
  });

  it('downloads a custom date range using the filter inputs', async () => {
    renderPanel();
    await screen.findByText(/Weekly ·/);
    fireEvent.change(screen.getByLabelText('From'), { target: { value: '2026-07-01' } });
    fireEvent.change(screen.getByLabelText('To'), { target: { value: '2026-07-15' } });
    fireEvent.click(screen.getByRole('button', { name: /Download PDF/ }));
    await waitFor(() =>
      expect(downloadDriverStatement).toHaveBeenCalledWith('drv-1', {
        start: '2026-07-01',
        end: '2026-07-15',
      }),
    );
  });

  it('blocks an inverted date range instead of calling the API', async () => {
    renderPanel();
    await screen.findByText(/Weekly ·/);
    fireEvent.change(screen.getByLabelText('From'), { target: { value: '2026-07-20' } });
    fireEvent.change(screen.getByLabelText('To'), { target: { value: '2026-07-01' } });
    expect(screen.getByText(/end date cannot be before the start date/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Download PDF/ })).toBeDisabled();
    expect(downloadDriverStatement).not.toHaveBeenCalled();
  });

  it('surfaces a load failure instead of rendering an empty list', async () => {
    getDriverStatements.mockRejectedValueOnce(new Error('boom'));
    renderPanel();
    await waitFor(() =>
      expect(notify).toHaveBeenCalledWith(
        expect.objectContaining({ title: 'Could not load statements', variant: 'destructive' }),
      ),
    );
  });

  it('surfaces a download failure', async () => {
    downloadDriverStatement.mockRejectedValueOnce(new Error('backend 502'));
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: 'PDF' }));
    await waitFor(() =>
      expect(notify).toHaveBeenCalledWith(
        expect.objectContaining({ title: 'Could not download statement', variant: 'destructive' }),
      ),
    );
  });

  it('shows an empty state when no statements exist yet', async () => {
    getDriverStatements.mockResolvedValueOnce({ statements: [] });
    renderPanel();
    expect(await screen.findByText(/No statements sent yet/)).toBeInTheDocument();
  });
});
