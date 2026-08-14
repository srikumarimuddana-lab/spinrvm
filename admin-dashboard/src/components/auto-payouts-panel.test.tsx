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

vi.mock('@/lib/api', () => ({
  getAutoPayoutBatches: (...a: unknown[]) => getAutoPayoutBatches(...a),
  getBlockedPayoutDrivers: (...a: unknown[]) => getBlockedPayoutDrivers(...a),
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
};

const BLOCKED = {
  driver_id: 'drv-gst-1',
  reason: 'missing_gst',
  pending_amount: '120.00',
};

beforeEach(() => {
  vi.clearAllMocks();
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

  it('surfaces a load failure with a retry that refetches', async () => {
    getAutoPayoutBatches.mockRejectedValueOnce(new Error('Service unavailable'));
    render(<AutoPayoutsPanel />);
    await waitFor(() => expect(screen.getByText('Service unavailable')).toBeInTheDocument());

    getAutoPayoutBatches.mockResolvedValue({ batches: [BATCH], count: 1 });
    fireEvent.click(screen.getByText(/try again/i));
    await waitFor(() => expect(screen.getAllByText('2026-W33').length).toBeGreaterThan(0));
  });
});
