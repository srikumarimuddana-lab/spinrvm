/**
 * AutoPayoutsPanel — weekly Sunday batch history + blocked-driver preflight,
 * inside Earnings & Payouts → Weekly Payouts.
 *
 * Covers the four async states the design guidelines require (loading,
 * error, empty, populated) plus the two things an operator actually acts
 * on: who is blocked with money held, and why a week was only partial.
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const getAutoPayoutBatches = vi.fn();
const getBlockedPayoutDrivers = vi.fn();
const getServiceAreas = vi.fn();

vi.mock('@/lib/api', () => ({
  getAutoPayoutBatches: (...a: unknown[]) => getAutoPayoutBatches(...a),
  getBlockedPayoutDrivers: (...a: unknown[]) => getBlockedPayoutDrivers(...a),
  getServiceAreas: (...a: unknown[]) => getServiceAreas(...a),
}));

// Radix Select renders through a portal and needs pointer APIs jsdom lacks;
// stub it as a native <select> so the area filter stays testable.
vi.mock('@/components/ui/select', () => ({
  Select: ({ value, onValueChange, children }: React.PropsWithChildren<{ value: string; onValueChange: (v: string) => void }>) => (
    <select aria-label="Filter by service area" value={value} onChange={(e) => onValueChange(e.target.value)}>
      {children}
    </select>
  ),
  SelectTrigger: ({ children }: React.PropsWithChildren) => <>{children}</>,
  SelectValue: () => null,
  SelectContent: ({ children }: React.PropsWithChildren) => <>{children}</>,
  SelectItem: ({ value, children }: React.PropsWithChildren<{ value: string }>) => (
    <option value={value}>{children}</option>
  ),
}));

vi.mock('next/link', () => ({
  default: ({ children, href }: React.PropsWithChildren<{ href: string }>) => <a href={href}>{children}</a>,
}));

vi.mock('@/components/ui/button', () => ({
  Button: ({ children, ...p }: React.PropsWithChildren<React.ButtonHTMLAttributes<HTMLButtonElement>>) => (
    <button {...p}>{children}</button>
  ),
}));

vi.mock('lucide-react', () => ({
  AlertTriangle: () => <span />,
  CalendarClock: () => <span />,
  Filter: () => <span />,
  CheckCircle: () => <span />,
  ChevronDown: () => <span />,
  ChevronRight: () => <span />,
  RefreshCw: () => <span />,
  UserX: () => <span />,
  Wallet: () => <span />,
  XCircle: () => <span />,
}));

import AutoPayoutsPanel from './auto-payouts-panel';

const BATCH = {
  id: 'auto-batch-2026-W33',
  week_key: '2026-W33',
  status: 'partial' as const,
  drivers_eligible: 12,
  drivers_paid: 10,
  drivers_failed: 2,
  total_amount: 1450.5,
  error_summary: 'driver_x: stripe_account_invalid',
  completed_at: '2026-08-16T13:05:00Z',
  skipped_summary: {
    counts: { missing_gst: 3, suspended: 1 },
    drivers_with_balance: { missing_gst: ['drv-gst-1'] },
  },
  area_summary: {
    sa_regina: { paid: 7, failed: 2, skipped: 3, amount: '980.50' },
    sa_saskatoon: { paid: 3, failed: 0, skipped: 1, amount: '470.00' },
  },
};

const BLOCKED = {
  driver_id: 'drv-gst-1',
  reason: 'missing_gst',
  pending_amount: '120.00',
  service_area_id: 'sa_regina',
};

const AREAS = [
  { id: 'sa_regina', name: 'Regina' },
  { id: 'sa_saskatoon', name: 'Saskatoon' },
];

beforeEach(() => {
  vi.clearAllMocks();
  getServiceAreas.mockResolvedValue(AREAS);
  getAutoPayoutBatches.mockResolvedValue({ batches: [BATCH], count: 1 });
  getBlockedPayoutDrivers.mockResolvedValue({
    blocked: [BLOCKED],
    count: 1,
    by_reason: { missing_gst: 1 },
  });
});

describe('AutoPayoutsPanel', () => {
  it('shows a loading state before data arrives', () => {
    getAutoPayoutBatches.mockReturnValue(new Promise(() => {}));
    getBlockedPayoutDrivers.mockReturnValue(new Promise(() => {}));
    render(<AutoPayoutsPanel />);
    expect(screen.getByText(/loading weekly payout data/i)).toBeInTheDocument();
  });

  it('surfaces a blocked driver with the reason and the amount held', async () => {
    render(<AutoPayoutsPanel />);
    await waitFor(() => expect(screen.getByText('drv-gst-1')).toBeInTheDocument());
    // Human-readable reason, not the raw backend enum.
    expect(screen.getAllByText(/missing gst\/hst number/i).length).toBeGreaterThan(0);
    // Appears twice by design: the "money held up" tile and the row itself.
    expect(screen.getAllByText('$120.00').length).toBeGreaterThan(0);
    // Clickable through to the driver record.
    expect(screen.getByText('drv-gst-1').closest('a')).toHaveAttribute(
      'href',
      '/dashboard/drivers?id=drv-gst-1',
    );
  });

  it('renders a partial week distinctly from a clean one', async () => {
    render(<AutoPayoutsPanel />);
    await waitFor(() => expect(screen.getAllByText('2026-W33').length).toBeGreaterThan(0));
    expect(screen.getAllByText('Partial').length).toBeGreaterThan(0);
    expect(screen.queryByText('Completed')).not.toBeInTheDocument();
  });

  it('expands a run to show skip reasons and errors', async () => {
    render(<AutoPayoutsPanel />);
    await waitFor(() => expect(screen.getAllByText('2026-W33').length).toBeGreaterThan(0));

    // The week key renders in the summary tile first and the history row
    // second — click the row, which is the expandable one.
    const weekCells = screen.getAllByText('2026-W33');
    fireEvent.click(weekCells[weekCells.length - 1]);

    await waitFor(() =>
      expect(screen.getByText(/stripe_account_invalid/)).toBeInTheDocument(),
    );
    // Skipped-driver ids are only listed for drivers who had money waiting.
    expect(screen.getByText(/3 drivers/)).toBeInTheDocument();
  });

  it('shows an all-clear empty state when nobody is blocked', async () => {
    getBlockedPayoutDrivers.mockResolvedValue({ blocked: [], count: 0, by_reason: {} });
    render(<AutoPayoutsPanel />);
    await waitFor(() =>
      expect(screen.getByText(/no blocked drivers/i)).toBeInTheDocument(),
    );
  });

  it('shows an empty state before the first run instead of a bare table', async () => {
    getAutoPayoutBatches.mockResolvedValue({ batches: [], count: 0 });
    render(<AutoPayoutsPanel />);
    await waitFor(() =>
      expect(screen.getByText(/no runs recorded yet/i)).toBeInTheDocument(),
    );
  });

  it('scopes the blocked-driver query to the chosen service area', async () => {
    render(<AutoPayoutsPanel />);
    await waitFor(() => expect(screen.getByText('drv-gst-1')).toBeInTheDocument());
    // Default view is fleet-wide.
    expect(getBlockedPayoutDrivers).toHaveBeenLastCalledWith(50, undefined);

    fireEvent.change(screen.getByLabelText(/filter by service area/i), {
      target: { value: 'sa_regina' },
    });

    // Refetches server-side rather than filtering the already-capped page.
    await waitFor(() =>
      expect(getBlockedPayoutDrivers).toHaveBeenLastCalledWith(50, 'sa_regina'),
    );
  });

  it('reports the selected area figures, not the fleet-wide totals', async () => {
    render(<AutoPayoutsPanel />);
    await waitFor(() => expect(screen.getAllByText('2026-W33').length).toBeGreaterThan(0));
    // Fleet-wide to start (BATCH.total_amount).
    expect(screen.getAllByText('$1,450.50').length).toBeGreaterThan(0);

    fireEvent.change(screen.getByLabelText(/filter by service area/i), {
      target: { value: 'sa_regina' },
    });

    // Regina's slice only — the fleet-wide total must disappear.
    await waitFor(() => expect(screen.getAllByText('$980.50').length).toBeGreaterThan(0));
    expect(screen.queryByText('$1,450.50')).not.toBeInTheDocument();
    // Area name, not the raw id.
    expect(screen.getAllByText(/Regina/).length).toBeGreaterThan(0);
  });

  it('says "not recorded" for a run predating per-area tracking', async () => {
    getAutoPayoutBatches.mockResolvedValue({
      batches: [{ ...BATCH, area_summary: null }],
      count: 1,
    });
    render(<AutoPayoutsPanel />);
    await waitFor(() => expect(screen.getAllByText('2026-W33').length).toBeGreaterThan(0));

    fireEvent.change(screen.getByLabelText(/filter by service area/i), {
      target: { value: 'sa_regina' },
    });

    // Must not imply the market earned $0.00 that week.
    await waitFor(() => expect(screen.getAllByText(/not recorded/i).length).toBeGreaterThan(0));
    expect(screen.queryByText('$0.00')).not.toBeInTheDocument();
  });

  it('breaks a run down by service area when expanded', async () => {
    render(<AutoPayoutsPanel />);
    await waitFor(() => expect(screen.getAllByText('2026-W33').length).toBeGreaterThan(0));

    const weekCells = screen.getAllByText('2026-W33');
    fireEvent.click(weekCells[weekCells.length - 1]);

    await waitFor(() => expect(screen.getByText(/by service area/i)).toBeInTheDocument());
    expect(screen.getByText('$980.50')).toBeInTheDocument();
    expect(screen.getByText('$470.00')).toBeInTheDocument();
  });

  it('surfaces a load failure with a retry that refetches', async () => {
    getAutoPayoutBatches.mockRejectedValueOnce(new Error('Service unavailable'));
    render(<AutoPayoutsPanel />);
    await waitFor(() => expect(screen.getByText('Service unavailable')).toBeInTheDocument());

    getAutoPayoutBatches.mockResolvedValue({ batches: [BATCH], count: 1 });
    fireEvent.click(screen.getByText(/try again/i));
    await waitFor(() => expect(screen.getAllByText('2026-W33').length).toBeGreaterThan(0));
  });
});
