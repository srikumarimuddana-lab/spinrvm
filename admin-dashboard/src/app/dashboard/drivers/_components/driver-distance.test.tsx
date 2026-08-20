/**
 * DriverDistance — insurance/ops distance-travelled table + per-day drill-down.
 *
 * Covers the `is_reconstructed` "Reconstructed" badge (legacy-migration-playbook.md
 * checklist item #5(b) — admin-dashboard read-only column for migration-332-backfilled
 * driver_insurance_periods spans) added alongside the existing phase badge in the
 * per-day log drill-down.
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

const getDriverDistanceTravelled = vi.fn();
const getDriverDistanceLogs = vi.fn();
const fetchDriverDistanceExport = vi.fn();

vi.mock('@/lib/api', () => ({
  getDriverDistanceTravelled: (...a: unknown[]) => getDriverDistanceTravelled(...a),
  getDriverDistanceLogs: (...a: unknown[]) => getDriverDistanceLogs(...a),
  fetchDriverDistanceExport: (...a: unknown[]) => fetchDriverDistanceExport(...a),
}));

import DriverDistance from './driver-distance';

const DAY_ROW = {
  date: '2026-08-10',
  driving_around_km: 7.25,
  driving_around_seconds: 3600,
  on_pickup_way_km: 3.1,
  on_pickup_way_seconds: 600,
  on_ride_km: 12.4,
  on_ride_seconds: 1800,
  total_km: 22.75,
  online_minutes: 100,
  rides_completed: 1,
  day_source: 'regina',
};

beforeEach(() => {
  vi.clearAllMocks();
  getDriverDistanceTravelled.mockResolvedValue({
    days: [DAY_ROW],
    totals: {
      driving_around_km: 7.25,
      driving_around_seconds: 3600,
      on_pickup_way_km: 3.1,
      on_pickup_way_seconds: 600,
      on_ride_km: 12.4,
      on_ride_seconds: 1800,
      total_km: 22.75,
    },
  });
});

describe('DriverDistance day-logs drill-down', () => {
  it('shows a "Reconstructed" badge with a text alternative for a backfilled span', async () => {
    getDriverDistanceLogs.mockResolvedValue({
      logs: [
        {
          from: '2026-08-10T08:00:00+00:00',
          to: '2026-08-10T09:00:00+00:00',
          seconds: 3600,
          phase: 'On pickup way',
          period: 2,
          ride_id: 'ride-1',
          ride_code: 'SPR-PE7TTB',
          distance_km: 3.1,
          distance_source: 'gps_measured',
          open: false,
          is_reconstructed: true,
        },
      ],
    });

    render(<DriverDistance driverId="drv-1" />);
    fireEvent.click(await screen.findByText('2026-08-10'));

    const badge = await screen.findByText('Reconstructed');
    expect(badge).toBeInTheDocument();
    expect(badge).toHaveAttribute(
      'aria-label',
      'Reconstructed: backfilled from timestamps during legacy migration, not logged live',
    );
  });

  it('renders no "Reconstructed" badge for a live-logged span', async () => {
    getDriverDistanceLogs.mockResolvedValue({
      logs: [
        {
          from: '2026-08-10T08:00:00+00:00',
          to: '2026-08-10T09:00:00+00:00',
          seconds: 3600,
          phase: 'On pickup way',
          period: 2,
          ride_id: 'ride-1',
          ride_code: 'SPR-PE7TTB',
          distance_km: 3.1,
          distance_source: 'gps_measured',
          open: false,
          is_reconstructed: false,
        },
      ],
    });

    render(<DriverDistance driverId="drv-1" />);
    fireEvent.click(await screen.findByText('2026-08-10'));

    await waitFor(() => expect(screen.getByText('On pickup way')).toBeInTheDocument());
    expect(screen.queryByText('Reconstructed')).not.toBeInTheDocument();
  });
});
